"""Real project events broadcast Web writes as ``webui``; equivalent Agent writes retain original sources.

Frontend uses sources to identify own/external changes: non-``webui`` new drafts navigate to episodes, others notify/queue focus.
Subscribe, write once, collect all caused batches, then assert sources: beyond explicit batches,
incidental draft/registration changes found through supplemental reads/hint scans must retain initiating sources.

Sources settle from hints received before scan reads. Multistep writes may let earlier hints wake scans reading later
files before their hints arrive, yielding ``filesystem``; polling between writes/hints behaves likewise.
This known scan-window boundary is outside these tests: disable polling and hold event-service reads while writes run;
after writes return, every hint has arrived before scans read.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.artifacts.artifact_activation import register_current_artifact, register_current_resource_artifact
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import SetReason
from lib.generation.generation_queue import GenerationQueue, get_generation_queue
from lib.project.project_change_hints import emit_project_change_batch, project_change_source
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import (
    MigrationAdjustment,
    build_migration_report,
    visible_migration_report,
    write_migration_report,
)
from lib.project.project_trash import list_records
from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, quarantine_path, write_quarantine
from lib.workflow.workflow_state import WorkflowStateService
from server.draft_workflow import DraftContext, DraftWorkflow
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines, episode_drafts, files, project_migration, reference_videos
from server.routers import project_events as project_events_router
from server.services.project.project_events import ProjectEventService, read_project_state
from tests.auth_deps import override_auth
from tests.factories import install_current_video, make_test_clip
from tests.fakes import fake_reference_request_projector, use_project_manager, use_reference_projection
from tests.integration.lib.project.project_migrations.test_project_migration_v7_v8 import _project as _old_project

pytestmark = pytest.mark.usefixtures("video_request_facts")

_NOVEL = "张三在村口等人。"
_SEGMENT = {
    "segment_id": "E1S01",
    "novel_text": _NOVEL,
    "duration_seconds": 4,
    "segment_break": False,
    "characters_in_segment": [],
    "scenes": [],
    "props": [],
}
_DOC_TYPE = "narration_script_plan"
# Disable polling: hints/explicit batches alone wake scans; explicit batch barriers establish complete events.
_POLL_SECONDS = 3600.0
_AGENT = RevisionAuthor(kind="arcreel_agent")


class _ConnectedRequest:
    def __init__(self, service: ProjectEventService):
        self.app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))

    async def is_disconnected(self) -> bool:
        return False


@dataclass
class _Env:
    pm: ProjectManager
    project_path: Path
    client: AsyncClient
    batches: asyncio.Queue[dict]
    reads_open: threading.Event

    @contextlib.contextmanager
    def reads_held(self) -> Generator[None]:
        """Hold event reads during writes; after exit every hint has arrived before woken scans read."""
        self.reads_open.clear()
        try:
            yield
        finally:
            self.reads_open.set()

    async def batches_until(self, predicate: Callable[[dict], bool]) -> list[dict]:
        """Await real changes, then collect incidental batches through two serial rebuild barriers."""
        collected: list[dict] = []

        async def _pull_until_match() -> None:
            while True:
                data = await self.batches.get()
                collected.append(data)
                if any(predicate(change) for change in data["changes"]):
                    return

        await asyncio.wait_for(_pull_until_match(), timeout=5)
        return collected + await self.batches_after_barrier()

    async def batches_after_barrier(self) -> list[dict]:
        """Two serial rebuild barriers collect all unconsumed batches caused by prior writes."""
        collected: list[dict] = []
        # Batches broadcast themselves then supplement; second arrival proves first supplement finished. Hint-free barriers preserve real sources.
        for marker in ("scan", "done"):
            emit_project_change_batch(
                "demo",
                [{"entity_type": "test_scan_barrier", "action": "updated", "entity_id": marker}],
                source="filesystem",
            )
        async with asyncio.timeout(5):
            while True:
                data = await self.batches.get()
                if data["changes"][0]["entity_type"] == "test_scan_barrier":
                    if data["changes"][0]["entity_id"] == "done":
                        return collected
                else:
                    collected.append(data)


def _is(entity_type: str, action: str, entity_id: str | None = None) -> Callable[[dict], bool]:
    def predicate(change: dict) -> bool:
        return (
            change["entity_type"] == entity_type
            and change["action"] == action
            and (entity_id is None or change["entity_id"] == entity_id)
        )

    return predicate


def _sources(batches: list[dict]) -> set[str]:
    return {batch["source"] for batch in batches}


def _changes(batches: list[dict]) -> list[tuple[str, str, str]]:
    return [(c["entity_type"], c["action"], c["entity_id"]) for batch in batches for c in batch["changes"]]


#: App dependency overrides map functions to fake factories.
_Overrides = dict[Callable[..., Any], Callable[[], Any]]


@pytest.fixture
def open_env(monkeypatch) -> Callable[..., contextlib.AbstractAsyncContextManager[_Env]]:
    """Subscribe to *pm* demo events, mount routes, yield :class:`_Env`."""

    @contextlib.asynccontextmanager
    async def _open(pm: ProjectManager, overrides: _Overrides | None = None) -> AsyncGenerator[_Env]:
        use_project_manager(monkeypatch, pm)
        app = FastAPI()
        register_error_handlers(app)
        override_auth(app)
        app.dependency_overrides.update(overrides or {})
        for module in (files, episode_drafts, reference_videos, edit_timelines, project_migration):
            app.include_router(module.router)

        reads_open = threading.Event()
        reads_open.set()

        def _read_state_when_open(project_name: str):
            assert reads_open.wait(timeout=5), "写入在途过久，事件服务读盘一直被扣住"
            return read_project_state(pm, project_name)

        service = ProjectEventService(
            data_root=pm.data_root, poll_interval=_POLL_SECONDS, read_state=_read_state_when_open
        )
        await service.start()
        stream = project_events_router.stream_project_events("demo", _ConnectedRequest(service), service=service)
        assert (await anext(stream)).event == "snapshot"
        # Background tasks forward streams into queues; stream-level timeouts would cancel generators/subscriptions.
        batches: asyncio.Queue[dict] = asyncio.Queue()

        async def _pump() -> None:
            async for event in stream:
                batches.put_nowait(event.data)

        pump = asyncio.create_task(_pump())
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                yield _Env(pm, pm.get_project_path("demo"), client, batches, reads_open)
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            await stream.aclose()
            await service.shutdown()

    return _open


@pytest.fixture
async def env(tmp_path: Path, open_env) -> AsyncIterator[_Env]:
    """Subscribed demo narration projects: episode 1 owns sources/has formal planning."""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    pm.update_project("demo", lambda project: project["episodes"][0].update(source_origin="own"))
    project_path = pm.get_project_path("demo")
    (project_path / "source").mkdir(exist_ok=True)
    (project_path / "source" / "episode_1.txt").write_text(_NOVEL, encoding="utf-8")
    formal = project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    formal.parent.mkdir(parents=True)
    formal.write_text(json.dumps({"segments": [_SEGMENT]}, ensure_ascii=False), encoding="utf-8")

    async with open_env(pm) as opened:
        yield opened


def _write_narration_draft(project_path: Path, segment: dict) -> None:
    write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [segment]},
        violations=[],
        meta={"source": None},
    )


async def _draft_revision(env: _Env) -> str:
    response = await env.client.get(f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}")
    assert response.status_code == 200
    return response.json()["revision"]


# ---------------------------------------------------------------- files.py


async def test_creating_an_unregistered_source_file_broadcasts_as_webui(env: _Env) -> None:
    with env.reads_held():
        response = await env.client.put(
            "/projects/demo/source/novel.txt", content="甲乙丙丁", headers={"content-type": "text/plain"}
        )
    assert response.status_code == 200

    batches = await env.batches_until(lambda change: True)

    assert env.pm.load_project("demo")["whole_source_files"] == [{"source_file": "source/novel.txt"}]
    assert _sources(batches) == {"webui"}


async def test_deleting_an_episode_own_source_broadcasts_as_webui(env: _Env) -> None:
    with env.reads_held():
        response = await env.client.delete("/projects/demo/source/episode_1.txt")
    assert response.status_code == 200

    batches = await env.batches_until(_is("episode", "updated"))

    assert env.pm.load_project("demo")["episodes"][0]["source_origin"] == "none"
    assert ("trash", "updated", "trash") in _changes(batches)
    assert _sources(batches) == {"webui"}


async def test_saving_a_new_plain_draft_broadcasts_as_webui(env: _Env) -> None:
    (env.project_path / "drafts" / "episode_1" / "script_plan_segments.json").unlink()

    with env.reads_held():
        response = await env.client.put(
            "/projects/demo/drafts/1/script_plan",
            content=json.dumps({"segments": [_SEGMENT]}, ensure_ascii=False),
            headers={"content-type": "text/plain"},
        )
    assert response.status_code == 200

    batches = await env.batches_until(_is("draft", "created", "episode_1_script_plan"))

    assert _sources(batches) == {"webui"}


async def test_deleting_a_plain_draft_broadcasts_as_webui(env: _Env) -> None:
    with env.reads_held():
        response = await env.client.delete("/projects/demo/drafts/1/script_plan")
    assert response.status_code == 200

    batches = await env.batches_until(_is("trash", "updated"))

    assert _sources(batches) == {"webui"}


async def test_saving_a_reference_plan_broadcasts_as_webui(env: _Env) -> None:
    env.pm.update_project("demo", lambda project: project.update(generation_mode="reference_video"))
    await env.batches_until(lambda change: True)

    with env.reads_held():
        response = await env.client.put(
            "/projects/demo/drafts/1/script_plan",
            content=json.dumps({"units": [{"unit_id": "E1U01", "text": "镜头描述", "duration_seconds": 8}]}),
            headers={"content-type": "text/plain"},
        )
    assert response.status_code == 200
    assert response.json()["path"] == "drafts/episode_1/script_plan_reference_units.json"

    batches = await env.batches_until(_is("draft", "created", "episode_1_script_plan"))

    assert _sources(batches) == {"webui"}


# ---------------------------------------------------------------- episode_drafts.py


async def test_saving_an_episode_draft_that_stays_a_draft_broadcasts_as_webui(env: _Env) -> None:
    _write_narration_draft(env.project_path, {**_SEGMENT, "characters_in_segment": ["王五"]})
    await env.batches_until(_is("draft", "created"))
    revision = await _draft_revision(env)

    with env.reads_held():
        response = await env.client.put(
            f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}",
            json={
                "content": {"segments": [{**_SEGMENT, "characters_in_segment": ["李四"]}]},
                "base_revision": revision,
            },
        )
    assert response.status_code == 200
    assert response.json()["adopted"] is False

    batches = await env.batches_until(_is("draft", "updated", "episode_1_script_plan"))

    assert _sources(batches) == {"webui"}


async def test_saving_an_episode_draft_that_is_adopted_broadcasts_as_webui(env: _Env) -> None:
    _write_narration_draft(env.project_path, {**_SEGMENT, "characters_in_segment": ["王五"]})
    await env.batches_until(_is("draft", "created"))
    revision = await _draft_revision(env)

    with env.reads_held():
        response = await env.client.put(
            f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}",
            json={"content": {"segments": [{**_SEGMENT, "duration_seconds": 6}]}, "base_revision": revision},
        )
    assert response.status_code == 200
    assert response.json()["adopted"] is True

    batches = await env.batches_until(_is("draft", "deleted", "episode_1_script_plan"))

    assert not quarantine_path(env.project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN).exists()
    assert _sources(batches) == {"webui"}


async def test_discarding_an_episode_draft_broadcasts_as_webui(env: _Env) -> None:
    _write_narration_draft(env.project_path, {**_SEGMENT, "characters_in_segment": ["王五"]})
    await env.batches_until(_is("draft", "created"))
    revision = await _draft_revision(env)

    with env.reads_held():
        response = await env.client.delete(
            f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}", params={"base_revision": revision}
        )
    assert response.status_code == 200

    batches = await env.batches_until(_is("draft", "deleted", "episode_1_script_plan"))

    assert ("trash", "updated", "trash") in _changes(batches)
    assert _sources(batches) == {"webui"}


# ---------------------------------------------------------------- reference_videos.py

_UNITS = "/projects/demo/reference-videos/episodes/1/units"


@pytest.fixture
async def reference_env(timeline_project: ProjectManager, tmp_path: Path, open_env, monkeypatch) -> AsyncIterator[_Env]:
    """Reference projects: episode 1 has E1U1/E1U2, with E1U2 current video."""
    use_reference_projection(monkeypatch, fake_reference_request_projector(durations=(4, 8)))
    _install_video(timeline_project, tmp_path, "E1U2")
    async with open_env(timeline_project) as opened:
        yield opened


def _install_video(pm: ProjectManager, tmp_path: Path, unit_id: str) -> None:
    source = tmp_path / "media" / f"{unit_id}.mp4"
    make_test_clip(source, size="160x90", fps=30, seconds=0.5, tone=True)
    install_current_video(pm.get_project_path("demo"), "reference_videos", unit_id, source)


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        ("post", _UNITS, {"prompt": "新镜头", "duration_seconds": 4}, _is("reference_unit", "created")),
        ("patch", f"{_UNITS}/E1U1", {"prompt": "改过的镜头"}, _is("reference_unit", "updated", "E1U1")),
    ],
    ids=["add", "patch"],
)
async def test_editing_reference_units_broadcasts_as_webui(
    reference_env: _Env, method: str, path: str, body: dict[str, Any], expected: Callable[[dict], bool]
) -> None:
    with reference_env.reads_held():
        response = await reference_env.client.request(method, path, json=body)
    assert response.status_code < 300, response.text

    batches = await reference_env.batches_until(expected)

    assert _sources(batches) == {"webui"}


async def test_deleting_a_reference_unit_with_its_video_broadcasts_as_webui(reference_env: _Env) -> None:
    """Units/videos move into trash; unit deletion/trash batches broadcast webui with user-deletion records."""
    with reference_env.reads_held():
        response = await reference_env.client.delete(f"{_UNITS}/E1U2")
    assert response.status_code == 204

    batches = await reference_env.batches_until(_is("reference_unit", "deleted", "E1U2"))

    assert ("trash", "updated", "trash") in _changes(batches)
    assert _sources(batches) == {"webui"}
    assert [record.origin for record in list_records(reference_env.project_path)] == ["user"]


async def test_moving_a_reference_unit_broadcasts_nothing(reference_env: _Env) -> None:
    """Snapshots compare IDs without order; reordered units produce no changes/external notifications."""
    with reference_env.reads_held():
        response = await reference_env.client.post(f"{_UNITS}/E1U2/move", json={"after_unit_id": None})
    assert [unit["unit_id"] for unit in response.json()["units"]] == ["E1U2", "E1U1"]

    assert await reference_env.batches_after_barrier() == []


# ---------------------------------------------------------------- edit_timelines.py

_TIMELINES = "/projects/demo/edit-timelines"


@pytest.fixture
async def timeline_env(
    timeline_project: ProjectManager, tmp_path: Path, open_env, file_db_factory
) -> AsyncIterator[_Env]:
    """Reference projects with uploaded/current E1U1 videos can create script-derived timelines."""
    timeline_project.update_script_locked(
        "demo",
        "episode_1.json",
        lambda script: script["video_units"][0].update(generated_assets={"video_clip": "reference_videos/E1U1.mp4"}),
    )
    project_path = timeline_project.get_project_path("demo")
    register_current_artifact(project_path, ArtifactKey.episode_script(1))
    staged = tmp_path / "media" / "E1U1.mp4"
    make_test_clip(staged, size="160x90", fps=30, seconds=0.5, tone=True)
    VersionManager(project_path).commit_staged_version(
        "reference_videos",
        "E1U1",
        "",
        staged_file=staged,
        current_file=project_path / "reference_videos/E1U1.mp4",
        source=MANUAL_UPLOAD_VERSION_SOURCE,
    )
    register_current_resource_artifact(
        project_path, resource_type="reference_videos", resource_id="E1U1", script_file="scripts/episode_1.json"
    )
    overrides: _Overrides = {
        edit_timelines.get_edit_timeline_service: lambda: EditTimelineService(timeline_project),
        edit_timelines.get_workflow_state_service: lambda: WorkflowStateService(timeline_project),
        get_generation_queue: lambda: GenerationQueue(
            session_factory=file_db_factory, project_manager=timeline_project
        ),
    }
    async with open_env(timeline_project, overrides) as opened:
        yield opened


async def _agent_timeline(env: _Env) -> str:
    """Agents create timelines/revision 2, collect resulting batches, return timeline IDs."""
    service = EditTimelineService(env.pm)
    created = await service.create_from_script("demo", episode=1, name="完整版", author=_AGENT)
    timeline_id = created.timeline.id
    await service.edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="保留开场",
        operations=[SetReason(op="set_reason", clip="c1", reason="保留开场")],
        author=_AGENT,
    )
    await env.batches_until(_is("episode", "updated"))
    return timeline_id


async def test_creating_an_edit_timeline_broadcasts_as_webui(timeline_env: _Env) -> None:
    with timeline_env.reads_held():
        response = await timeline_env.client.post(
            "/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": "完整版"}
        )
    assert response.status_code == 201, response.text

    batches = await timeline_env.batches_until(_is("episode", "updated"))

    assert _sources(batches) == {"webui"}


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("post", "/copy", {"name": "快节奏版"}),
        ("patch", "", {"name": "定稿"}),
        ("post", "/restore", {"revision": 1}),
        ("delete", "", None),
    ],
    ids=["copy", "rename", "restore", "delete"],
)
async def test_managing_an_edit_timeline_broadcasts_as_webui(
    timeline_env: _Env, method: str, suffix: str, body: dict[str, Any] | None
) -> None:
    timeline_id = await _agent_timeline(timeline_env)

    with timeline_env.reads_held():
        response = await timeline_env.client.request(method, f"{_TIMELINES}/{timeline_id}{suffix}", json=body)
    assert response.status_code < 300, response.text

    batches = await timeline_env.batches_until(_is("episode", "updated"))

    assert _sources(batches) == {"webui"}


# ---------------------------------------------------------------- project_migration.py


async def test_retrying_a_migration_broadcasts_its_changes_as_webui(tmp_path: Path, open_env) -> None:
    """Migration edits project.json/scripts without hints; retry entries emit webui hints for those changes."""
    _old_project(tmp_path)

    async with open_env(ProjectManager(tmp_path)) as env:
        with env.reads_held():
            response = await env.client.post("/projects/demo/migration/retry")
        assert response.status_code == 200, response.text

        batches = await env.batches_until(_is("project", "updated"))

    assert _sources(batches) == {"webui"}


async def test_acknowledging_a_migration_report_broadcasts_nothing_as_an_outside_change(env: _Env) -> None:
    """Report confirmation changes upgrade-report files only, outside state inputs, emitting no batches."""
    write_migration_report(
        env.project_path,
        build_migration_report(
            None,
            from_schema_version=14,
            to_schema_version=18,
            adjusted=[MigrationAdjustment(code="placeholder_asset_added", params={"asset_type": "prop", "name": "伞"})],
        ),
    )
    shown = visible_migration_report(env.project_path)
    assert shown is not None

    with env.reads_held():
        response = await env.client.post(
            "/projects/demo/migration-report/acknowledge", json={"migrated_at": shown.migrated_at}
        )
    assert response.json()["acknowledged"] is True

    assert await env.batches_after_barrier() == []


# ---------------------------------------------------------------- Equivalent Agent writes


@pytest.mark.parametrize("source", ["filesystem", "worker"])
async def test_agent_draft_writes_keep_their_original_source(env: _Env, source) -> None:
    """Agent/task draft writes/edits/discards retain sources, preserving creator notices."""
    workflow = DraftWorkflow(DraftContext(project_name="demo", data_root=env.pm.data_root, pm=env.pm))

    with project_change_source(source):
        with env.reads_held():
            opened = await workflow.open(1, _DOC_TYPE)
        created = await env.batches_until(_is("draft", "created"))
        with env.reads_held():
            await workflow.patch(1, _DOC_TYPE, {"segments": [{**_SEGMENT, "duration_seconds": 6}]}, opened["revision"])
        updated = await env.batches_until(_is("draft", "updated"))
        reread = await workflow.open(1, _DOC_TYPE)
        with env.reads_held():
            await workflow.discard(1, _DOC_TYPE, reread["revision"])
        discarded = await env.batches_until(_is("draft", "deleted"))

    assert ("trash", "updated", "trash") in _changes(discarded)
    assert _sources(created) == _sources(updated) == _sources(discarded) == {source}
