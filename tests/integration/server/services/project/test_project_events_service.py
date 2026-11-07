import asyncio
import contextlib
import json
import logging
import threading

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.artifacts.artifact_activation import register_current_artifact, register_current_resource_artifact
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.edit_timeline import EditTimelineService
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_change_hints import (
    emit_project_change_batch,
    emit_project_change_hint,
    project_change_source,
)
from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, quarantine_path
from lib.workflow.workflow_plan import WorkflowPlanRequest
from lib.workflow.workflow_state import WorkflowStateService, workflow_finished
from server.agent_toolset.edit_timelines import CREATE_TIMELINE
from server.dependencies import require_project_migration_ok
from server.routers import edit_timelines
from server.services.project.project_events import (
    ASSISTANT_SESSION_RESUMED_EVENT,
    PROJECT_DELETED_EVENT,
    ProjectEventService,
    read_project_state,
)
from server.services.project.project_state_projection import ProjectState
from server.services.project.workflow_planner import WorkflowPlanner
from tests.auth_deps import override_auth
from tests.factories import make_test_video
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


class _ControlledReader:
    """read_state seam test adapter delegates actual readonly loading; supplied ``hook`` releases each disk read."""

    def __init__(self, pm: ProjectManager):
        self._pm = pm
        self.hook = None

    def __call__(self, project_name: str) -> ProjectState:
        def read() -> ProjectState:
            return read_project_state(self._pm, project_name)

        return read() if self.hook is None else self.hook(read)


def _pending_assets() -> dict:
    return {
        "storyboard_image": None,
        "video_clip": None,
        "video_uri": None,
        "status": "pending",
    }


def _terminal_task_change(task_id: str) -> dict:
    """Task terminal events trigger explicit rebuilds without describing file-side entity changes."""
    return {
        "entity_type": "task",
        "action": "task_succeeded",
        "entity_id": task_id,
        "label": task_id,
        "focus": None,
        "important": False,
        "task_type": "video",
    }


async def _next_event(stream, *, timeout: float) -> tuple[str, dict]:  # noqa: ASYNC109 -- Test polling helper wait bounds, not production cancellation semantics
    """Pull the next real (event_name, payload) tuple, skipping ``_idle`` sentinels."""

    async def _pull() -> tuple[str, dict]:
        async for item in stream:
            if isinstance(item, dict):
                if item.get("type") == "_idle":
                    continue
                raise AssertionError(f"unexpected dict sentinel: {item}")
            return item
        raise AssertionError("stream ended before a real event arrived")

    return await asyncio.wait_for(_pull(), timeout=timeout)


@pytest.mark.parametrize("entry", ["agent", "http"])
async def test_timeline_creation_refreshes_project_events_and_completes_workflow(tmp_path, db_factory, entry):
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo", "Demo", "", "ad", target_duration=4, extras={"generation_mode": "reference_video"}
    )
    project_path = pm.get_project_path("demo")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "video_units": [
                {
                    "unit_id": "E1U01",
                    "text": "镜头",
                    "duration_seconds": 4,
                    "generated_assets": {"video_clip": "reference_videos/E1U01.mp4"},
                }
            ],
        },
        "episode_1.json",
    )
    register_current_artifact(project_path, ArtifactKey.episode_script(1))
    staged = tmp_path / "video.mp4"
    make_test_video(staged, duration_sec=1)
    VersionManager(project_path).commit_staged_version(
        "reference_videos",
        "E1U01",
        "",
        staged_file=staged,
        current_file=project_path / "reference_videos/E1U01.mp4",
        source=MANUAL_UPLOAD_VERSION_SOURCE,
    )
    register_current_resource_artifact(
        project_path, resource_type="reference_videos", resource_id="E1U01", script_file="scripts/episode_1.json"
    )
    queue = GenerationQueue(session_factory=db_factory, project_manager=pm)
    planner = WorkflowPlanner(pm)
    request = WorkflowPlanRequest(episode_id=1)
    before = await planner.get_plan("demo", request, queue=queue)
    assert before.next_action.type == "create_edit_timeline"
    assert before.steps[-1].state == "ready"

    service = ProjectEventService(tmp_path, poll_interval=30)
    await service.start()
    try:
        async with service.stream_events("demo") as stream:
            assert (await anext(stream))[0] == "snapshot"
            # Web routes mark their own sources; tests never supply wrappers for them.
            with project_change_source("worker") if entry == "agent" else contextlib.nullcontext():
                if entry == "agent":
                    created = await run_declared_tool(
                        CREATE_TIMELINE,
                        ToolHarness("demo", tmp_path / "projects", pm, queue=queue),
                        {"from": "script", "episode": 1, "name": "完整版"},
                    )
                    assert created.problem is None
                else:
                    app = FastAPI()
                    override_auth(app)
                    app.dependency_overrides[require_project_migration_ok] = lambda: None
                    app.dependency_overrides[edit_timelines.get_edit_timeline_service] = lambda: EditTimelineService(pm)
                    app.dependency_overrides[edit_timelines.get_workflow_state_service] = lambda: WorkflowStateService(
                        pm
                    )
                    app.include_router(edit_timelines.router)
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post(
                            "/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": "完整版"}
                        )
                    assert response.status_code == 201
            event_name, payload = await _next_event(stream, timeout=1)
            assert event_name == "changes"
            assert payload["source"] == ("worker" if entry == "agent" else "webui")
            assert payload["changes"][0]["entity_type"] == "episode"
            assert payload["changes"][0]["episode"] == 1
            after = await planner.get_plan("demo", request, queue=queue)
            assert after.next_action.type == "none"
            assert workflow_finished(after.status)
            assert after.steps[-1].state == "completed"
    finally:
        await service.shutdown()


class TestProjectEventService:
    @pytest.mark.asyncio
    async def test_emitted_batch_is_broadcast_without_waiting_for_snapshot_diff(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=1.0)
        await service.start()

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            event_name, snapshot = await anext(stream)
            assert event_name == "snapshot"
            assert snapshot["fingerprint"]

            emit_project_change_batch(
                "demo",
                [
                    {
                        "entity_type": "segment",
                        "action": "storyboard_ready",
                        "entity_id": "E1S01",
                        "label": "分镜「E1S01」",
                        "focus": None,
                        "important": True,
                    }
                ],
                source="worker",
            )

            event_name, payload = await _next_event(stream, timeout=1.0)
            assert event_name == "changes"
            assert payload["source"] == "worker"
            assert payload["fingerprint"] == snapshot["fingerprint"]
            assert payload["changes"][0]["action"] == "storyboard_ready"

        await service.shutdown()

    async def test_assistant_session_resumed_is_broadcast_to_project_subscribers(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=1.0)
        await service.start()
        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                service.publish_assistant_session_resumed("demo", "sess-1")

                event_name, payload = await _next_event(stream, timeout=1.0)
                assert event_name == ASSISTANT_SESSION_RESUMED_EVENT
                assert payload == {"project_name": "demo", "session_id": "sess-1", "status": "running"}
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_stale_rebuild_does_not_broadcast_reverse_diff(self, tmp_path):
        """Out-of-order settlement from differently timed rebuild reads must not broadcast reverse changes from old snapshots.

        Supplemental scans diff baselines; stale snapshots written back would classify existing entities ``deleted``.
        Rebuild locks serialize disk reads → baseline settlement → broadcasts into one read-modify-write section;
        later rebuilds read only after prior settlement, necessarily seeing newer data.
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()

        release_first = asyncio.Event()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                calls = 0
                first_read_done = asyncio.Event()
                second_read_done = asyncio.Event()
                loop = asyncio.get_running_loop()

                def _read_holding_first(read):
                    nonlocal calls
                    calls += 1
                    index = calls
                    result = read()
                    if index == 1:
                        # Initial disk reads finished but unsettled: yield until orchestration releases them.
                        loop.call_soon_threadsafe(first_read_done.set)
                        asyncio.run_coroutine_threadsafe(release_first.wait(), loop).result(timeout=5)
                    else:
                        loop.call_soon_threadsafe(second_read_done.set)
                    return result

                reader.hook = _read_holding_first

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")
                await asyncio.wait_for(first_read_done.wait(), timeout=5.0)

                # Persist new characters after initial reads by directly rewriting project.json, without hints.
                project_json = pm.get_project_path("demo") / "project.json"
                data = json.loads(project_json.read_text(encoding="utf-8"))
                data.setdefault("characters", {})["新角色"] = {"name": "新角色", "description": "d"}
                project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

                # Second rebuilds without locks read new disk and settle first; await read-completion signals,
                # never fixed sleeps that can finish before slow scheduling starts rebuilds, hiding lock-removal regressions.
                # With locks, rebuilds wait outside and signals cannot arrive; timeout itself proves locking.
                emit_project_change_batch("demo", [_terminal_task_change("task-2")], source="worker")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(second_read_done.wait(), timeout=1.0)
                release_first.set()

                # Collect both rebuild batch broadcasts and possible supplemental scans before inspecting complete character changes.
                broadcast_changes = []
                for _ in range(2):
                    event_name, payload = await _next_event(stream, timeout=5.0)
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])
                while True:
                    try:
                        event_name, payload = await _next_event(stream, timeout=0.5)
                    except TimeoutError:
                        break
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])

                character_changes = [change for change in broadcast_changes if change["entity_type"] == "character"]
                assert [change["action"] for change in character_changes] == ["created"]
        finally:
            # Early assertion/_next_event failures leave deliberately suspended threads at release_first;
            # release before shutdown can collect watch tasks/global project_change_hints listeners.
            release_first.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_emitted_batch_broadcasts_concurrent_change_swept_into_rebuild(self, tmp_path):
        """Explicit rebuilds incidentally reading other batches' file changes broadcast them through immediately following supplemental scans.

        Explicit rebuilds replace baselines. Changes persisted before reads but absent from batch changes enter
        baselines without broadcasts; later polling sees no diff, leaving no mechanism
        to deliver missing changes. Trigger rebuilds with unrelated terminal events to cover this path.

        Supplemental scans use separate payloads: source is payload-level, and incidental changes may have different
        sources. Merging would misdirect frontend webui suppression.

        Concurrent edits rewrite indexed script files in place, without episode-index synchronization
        or incidental hints waking polling; missed broadcasts would be permanent.
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "narration_text": "开场",
                    "image_prompt": "p",
                    "video_prompt": "v",
                    "characters_in_scene": [],
                    "scenes": [],
                    "props": [],
                    "generated_assets": _pending_assets(),
                }
            ],
        }
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        # poll_interval far exceeds test duration, isolating explicit rebuilds from earlier polling broadcasts.
        service = ProjectEventService(tmp_path, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                # Persist directly without hints, simulating changes reaching disk before rebuild reads.
                script["segments"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
                script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")

                # Batch terminal events retain dedicated payloads, preserving normal ordering/structure despite supplemental scans.
                event_name, payload = await _next_event(stream, timeout=2.0)
                assert event_name == "changes"
                assert [change["action"] for change in payload["changes"]] == ["task_succeeded"]
                assert payload["source"] == "worker"

                # Incidental changes broadcast separately afterward, resolving sources through their own hints.
                event_name, swept = await _next_event(stream, timeout=2.0)
                assert event_name == "changes"
                assert any(
                    change["action"] == "storyboard_ready" and change["entity_id"] == "E1S01"
                    for change in swept["changes"]
                )
                assert swept["source"] == "filesystem"
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_emitted_batch_does_not_duplicate_change_it_already_describes(self, tmp_path):
        """Persist assets before batch events, matching actual worker success order; supplemental scans never duplicate broadcasts."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "narration_text": "开场",
                    "image_prompt": "p",
                    "video_prompt": "v",
                    "characters_in_scene": [],
                    "scenes": [],
                    "props": [],
                    "generated_assets": _pending_assets(),
                }
            ],
        }
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        service = ProjectEventService(tmp_path, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                # Worker ordering: persist assets, then emit completion events.
                script["segments"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
                script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

                emit_project_change_batch(
                    "demo",
                    [
                        {
                            "entity_type": "segment",
                            "action": "storyboard_ready",
                            "entity_id": "E1S01",
                            "label": "分镜「E1S01」",
                            "focus": None,
                            "important": True,
                            "script_file": "episode_1.json",
                            "episode": 1,
                        }
                    ],
                    source="worker",
                )

                # Drain all payloads before counting: supplemental scans use separate payloads; duplicate regressions appear
                # immediately afterward, so first-payload-only assertions would provide no guard.
                broadcast_changes = []
                event_name, payload = await _next_event(stream, timeout=2.0)
                assert event_name == "changes"
                broadcast_changes.extend(payload["changes"])
                while True:
                    try:
                        event_name, payload = await _next_event(stream, timeout=0.5)
                    except TimeoutError:
                        break
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])

                ready = [
                    change
                    for change in broadcast_changes
                    if change["action"] == "storyboard_ready" and change["entity_id"] == "E1S01"
                ]
                assert len(ready) == 1
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_reference_video_completion_is_broadcast_once(self, tmp_path):
        """Reference-video publishers call completion ``reference_video_ready``, while snapshot diffs use ``video_ready``;
        supplemental scans identify identical changes, yielding to batches and broadcasting once."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration", extras={"generation_mode": "reference_video"})

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_units": [
                {"unit_id": "E1U01", "duration_seconds": 8, "text": "开场", "generated_assets": _pending_assets()}
            ],
        }
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        service = ProjectEventService(tmp_path, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                script["video_units"][0]["generated_assets"]["video_clip"] = "videos/E1U01.mp4"
                script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
                emit_project_change_batch(
                    "demo",
                    [
                        {
                            "entity_type": "reference_unit",
                            "action": "reference_video_ready",
                            "entity_id": "E1U01",
                            "label": "视频单元「E1U01」",
                            "focus": None,
                            "important": True,
                        }
                    ],
                    source="worker",
                )

                broadcast_changes = []
                while True:
                    try:
                        event_name, payload = await _next_event(stream, timeout=0.5)
                    except TimeoutError:
                        break
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])

                assert [change["action"] for change in broadcast_changes if change["entity_id"] == "E1U01"] == [
                    "reference_video_ready"
                ]
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_project_manager_writes_outside_the_old_snapshot_fields_are_broadcast(self, tmp_path):
        """Project-manager product additions/dialogue edits deliver corresponding project events to subscribers."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "drama")
        scene = {
            "scene_id": "E1S01",
            "duration_seconds": 8,
            "utterances": [{"kind": "dialogue", "speaker": "甲", "text": "旧台词"}],
            "generated_assets": _pending_assets(),
        }
        with project_change_source("filesystem"):
            pm.save_script(
                "demo",
                {"episode": 1, "title": "第一集", "content_mode": "drama", "scenes": [scene]},
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                pm.upsert_assets("demo", "products", {"咖啡": {"description": "冷萃"}})
                with pm.locked_script("demo", "episode_1.json", validate=False) as script:
                    script["scenes"][0]["utterances"][0]["text"] = "新台词"

                expected = {("product", "created", "咖啡"), ("drama_scene", "updated", "E1S01")}
                seen: set[tuple] = set()
                while not expected <= seen:
                    event_name, payload = await _next_event(stream, timeout=2.0)
                    assert event_name == "changes"
                    seen |= {(c["entity_type"], c["action"], c["entity_id"]) for c in payload["changes"]}
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_data_root_kwarg_overrides_default_subdir(self, tmp_path):
        """Explicit data_root, as production passes configured roots, makes streams read projects under that root."""
        custom_projects = tmp_path / "external-data"
        pm = ProjectManager(custom_projects)
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, data_root=custom_projects, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, snapshot = await anext(stream)
                assert event_name == "snapshot"
                assert snapshot["fingerprint"]
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_sweep_leaves_change_described_by_another_inflight_batch(self, tmp_path):
        """Incidental other-batch outputs yield supplemental broadcasts to their own batches, delivering once.

        Near-simultaneous completion lets first lock-holders read other persisted outputs. Supplemental broadcasts would
        duplicate unconditional later queued batches; frontend never dedupes across batches, showing two completions.
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "narration_text": "开场",
                    "image_prompt": "p",
                    "video_prompt": "v",
                    "characters_in_scene": [],
                    "scenes": [],
                    "props": [],
                    "generated_assets": _pending_assets(),
                }
            ],
        }
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()

        release_first = asyncio.Event()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                calls = 0
                first_read_done = asyncio.Event()
                loop = asyncio.get_running_loop()

                def _read_holding_first(read):
                    nonlocal calls
                    calls += 1
                    index = calls
                    if index == 1:
                        # Pause before reads so orchestration persists B outputs and emits B batches first,
                        # ensuring this read incidentally includes B outputs.
                        loop.call_soon_threadsafe(first_read_done.set)
                        asyncio.run_coroutine_threadsafe(release_first.wait(), loop).result(timeout=5)
                    return read()

                reader.hook = _read_holding_first

                # Batch A: unrelated terminal events trigger rebuilds only.
                emit_project_change_batch("demo", [_terminal_task_change("task-a")], source="worker")
                await asyncio.wait_for(first_read_done.wait(), timeout=5.0)

                # Persist B outputs before emitting its batch, matching real worker success order.
                script["segments"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
                script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
                emit_project_change_batch(
                    "demo",
                    [
                        {
                            "entity_type": "segment",
                            "action": "storyboard_ready",
                            "entity_id": "E1S01",
                            "label": "分镜「E1S01」",
                            "focus": None,
                            "important": True,
                            "script_file": "episode_1.json",
                            "episode": 1,
                        }
                    ],
                    source="worker",
                )
                release_first.set()

                broadcast_changes = []
                while True:
                    try:
                        event_name, payload = await _next_event(stream, timeout=1.0)
                    except TimeoutError:
                        break
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])

                ready = [
                    change
                    for change in broadcast_changes
                    if change["action"] == "storyboard_ready" and change["entity_id"] == "E1S01"
                ]
                assert len(ready) == 1
                # B's own batch broadcasts: explicit focus=None differs from supplemental-copy diff-derived focus.
                assert ready[0]["focus"] is None
        finally:
            release_first.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_of_a_write_the_scan_read_missed_settles_the_next_scan(self, tmp_path):
        """Later multistep Web writes after scan reads, with hints before settlement, retain webui sources on next scans.

        Earlier hints wake scans; after reads but before settlement, subsequent writes/hints arrive. Since scans missed later writes,
        retain their source markers for next rounds, never clear on current settlement and misclassify later changes as filesystem.
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()
        loop = asyncio.get_running_loop()
        read_done = asyncio.Event()
        release = threading.Event()

        def _read_then_hold(read):
            reader.hook = None
            state = read()
            loop.call_soon_threadsafe(read_done.set)
            assert release.wait(timeout=5)
            return state

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                assert (await anext(stream))[0] == "snapshot"
                reader.hook = _read_then_hold

                emit_project_change_hint("demo", source="webui")
                await asyncio.wait_for(read_done.wait(), timeout=5)
                # Write threads send hints before returning; awaiting returns ensures hints arrive before settlement.
                with project_change_source("webui"):
                    await asyncio.to_thread(pm.update_project, "demo", lambda project: project.update(style="水彩"))
                release.set()

                event_name, payload = await _next_event(stream, timeout=5)
                assert event_name == "changes"
                assert [change["entity_type"] for change in payload["changes"]] == ["project"]
                assert payload["source"] == "webui"
        finally:
            release.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_arriving_during_a_rebuild_read_settles_the_change_it_swept(self, tmp_path):
        """Writes read during explicit rebuilds whose hints arrive before settlement broadcast supplementally with hint sources."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()
        loop = asyncio.get_running_loop()
        read_started = asyncio.Event()
        release = threading.Event()

        def _hold_then_read(read):
            reader.hook = None
            loop.call_soon_threadsafe(read_started.set)
            assert release.wait(timeout=5)
            return read()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                assert (await anext(stream))[0] == "snapshot"
                reader.hook = _hold_then_read

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")
                await asyncio.wait_for(read_started.wait(), timeout=5)
                with project_change_source("webui"):
                    await asyncio.to_thread(pm.update_project, "demo", lambda project: project.update(style="水彩"))
                release.set()

                event_name, own = await _next_event(stream, timeout=5)
                assert (event_name, own["source"]) == ("changes", "worker")
                assert [change["entity_type"] for change in own["changes"]] == ["task"]
                event_name, swept = await _next_event(stream, timeout=5)
                assert event_name == "changes"
                assert [change["entity_type"] for change in swept["changes"]] == ["project"]
                assert swept["source"] == "webui"
        finally:
            release.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_source_repeated_during_rebuild_read_is_not_dropped(self, tmp_path):
        """Repeated source markers during rebuild reads survive writeback, informing next-scan source resolution.

        Swap out preread markers entirely, never subtract afterward: repeated same-source window hints would otherwise
        be removed by set subtraction, misclassifying next-scan changes as filesystem
        and bypassing frontend suppression of WebUI users' own edits.
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()

        release_read = asyncio.Event()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                channel = service._channels["demo"]
                # Initial subscription scans clear markers; await completion before preloading, or rebuild captures empty sets.
                await asyncio.sleep(0.1)
                assert channel.pending_sources == set()
                # Markers registered before rebuild initiation
                channel.pending_sources.add("webui")

                read_started = asyncio.Event()
                loop = asyncio.get_running_loop()

                def _read_waiting(read):
                    loop.call_soon_threadsafe(read_started.set)
                    asyncio.run_coroutine_threadsafe(release_read.wait(), loop).result(timeout=5)
                    return read()

                reader.hook = _read_waiting

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")
                await asyncio.wait_for(read_started.wait(), timeout=5.0)

                # Same-source hints recur during disk-read windows.
                channel.pending_sources.add("webui")
                release_read.set()

                event_name, _payload = await _next_event(stream, timeout=5.0)
                assert event_name == "changes"

                # Retain window markers; next scans resolve webui rather than filesystem.
                assert channel.pending_sources == {"webui"}
                assert service._resolve_batch_source(channel.pending_sources) == "webui"
        finally:
            release_read.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_change_landing_after_rebuild_read_is_still_broadcast(self, tmp_path):
        """Changes persisted after rebuild reads but before state writes still reach subscribers.

        Inject at read seams: persist after real reads return, precisely constructing this timing window.
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=0.05, read_state=reader)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                armed = False

                def _read_then_land(read):
                    nonlocal armed
                    result = read()
                    if armed:
                        armed = False
                        project_json = pm.get_project_path("demo") / "project.json"
                        data = json.loads(project_json.read_text(encoding="utf-8"))
                        data.setdefault("characters", {})["新角色"] = {"description": "d"}
                        project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    return result

                reader.hook = _read_then_land
                armed = True

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")

                # Window-persisted changes still broadcast eventually; rebuild missed them, requiring fallback scans.
                deadline = 3.0
                for _ in range(10):
                    event_name, payload = await _next_event(stream, timeout=deadline)
                    assert event_name == "changes"
                    if any(
                        (change["entity_type"], change["action"], change["entity_id"])
                        == ("character", "created", "新角色")
                        for change in payload["changes"]
                    ):
                        break
                else:
                    raise AssertionError("窗口内落盘的角色变更从未广播")
        finally:
            await service.shutdown()

    @pytest.mark.parametrize("edited_input", ["project_json", "existing_script", "new_script", "draft"])
    async def test_polling_pushes_change_to_any_input_file(self, tmp_path, edited_input):
        """After initial snapshots, disk input-file changes bypassing hints still push through polling input-identity comparisons."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "drama")
        scene = {
            "scene_id": "E1S01",
            "duration_seconds": 8,
            "utterances": [{"kind": "dialogue", "speaker": "甲", "text": "旧台词"}],
            "generated_assets": _pending_assets(),
        }
        script = {"episode": 1, "title": "第一集", "content_mode": "drama", "scenes": [scene]}
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)
        project_path = pm.get_project_path("demo")

        def edit_on_disk() -> None:
            if edited_input == "project_json":
                path = project_path / ProjectManager.PROJECT_FILE
                data = json.loads(path.read_text(encoding="utf-8"))
                data.setdefault("characters", {})["新角色"] = {"description": "d"}
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            elif edited_input == "existing_script":
                path = project_path / "scripts" / "episode_1.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                data["scenes"][0]["utterances"][0]["text"] = "新台词，比旧台词长"
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            elif edited_input == "new_script":
                other = {**script, "episode": 2, "title": "第二集"}
                (project_path / "scripts" / "episode_2.json").write_text(
                    json.dumps(other, ensure_ascii=False), encoding="utf-8"
                )
            else:
                path = quarantine_path(project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
                path.parent.mkdir(parents=True)
                path.write_text("{}", encoding="utf-8")

        expected_entity_type = {
            "project_json": "character",
            "existing_script": "drama_scene",
            "new_script": "episode",
            "draft": "draft",
        }[edited_input]

        service = ProjectEventService(tmp_path, poll_interval=0.1)
        await service.start()
        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                await asyncio.to_thread(edit_on_disk)

                seen: set[str] = set()
                while expected_entity_type not in seen:
                    event_name, payload = await _next_event(stream, timeout=3.0)
                    assert event_name == "changes"
                    seen |= {change["entity_type"] for change in payload["changes"]}
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_subscribe_cancellation_cleans_up_subscriber(self, tmp_path, monkeypatch):
        """Disconnect during initial scans cancels _subscribe, leaking neither subscribers nor watch tasks."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        # Simulate stuck initial scans: watch tasks never set ready_event, parking _subscribe in wait().
        async def _never_ready(project_name, channel):
            await asyncio.sleep(3600)

        monkeypatch.setattr(service, "_watch_project", _never_ready)

        task = asyncio.create_task(service._subscribe("demo"))
        await asyncio.sleep(0.05)  # Let _subscribe register queues and park.
        channel = service._channels["demo"]
        assert channel.sse.has_subscribers  # Registered
        watch_task = channel.task

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Cancellation cleans subscribers/removes channels/cancels watch tasks without leaks.
        assert "demo" not in service._channels
        await asyncio.sleep(0)  # Await watch-task cancellation completion.
        assert watch_task.cancelled() or watch_task.done()

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_new_subscriber_entering_during_stop_watch_stays_registered(self, tmp_path, monkeypatch):
        """While last-subscriber cleanup waits for watch-task cleanup, concurrent new subscribers join current registered channels,
        receive registry-routed hints, and old watch tasks retire without unregistered tasks."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=1.0)
        await service.start()

        entered_cancel = asyncio.Event()
        allow_exit = asyncio.Event()
        stop_task: asyncio.Task | None = None

        try:

            async def _controlled_watch(project_name, channel):
                # Release _subscribe readiness immediately; cancelled tasks pause at cleanup, widening races.
                channel.ready_event.set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    entered_cancel.set()
                    await allow_exit.wait()
                    raise

            monkeypatch.setattr(service, "_watch_project", _controlled_watch)

            # Subscriber A creates old channels/watch tasks.
            _sse_a, queue_a, _snap_a = await service._subscribe("demo")
            old_channel = service._channels["demo"]
            old_watch_task = old_channel.task

            # A unsubscribes, triggering last-subscriber hooks waiting for cancelled watch-task cleanup.
            stop_task = asyncio.create_task(service._unsubscribe("demo", queue_a))
            await asyncio.wait_for(entered_cancel.wait(), timeout=1.0)

            # New subscriber B enters during this window.
            _sse_b, queue_b, _snap_b = await service._subscribe("demo")

            # Release old watch tasks so last-subscriber hooks finish cleanup.
            allow_exit.set()
            await asyncio.wait_for(stop_task, timeout=1.0)

            # B belongs to current registered channels; old channels retired.
            assert "demo" in service._channels
            assert service._channels["demo"] is not old_channel

            # Registry-routed hint broadcasts reach B.
            service._apply_emitted_batch(
                "demo",
                "worker",
                (
                    {
                        "entity_type": "segment",
                        "action": "storyboard_ready",
                        "entity_id": "E1S01",
                        "label": "分镜「E1S01」",
                        "focus": None,
                        "important": True,
                    },
                ),
            )
            await asyncio.gather(*list(service._pending_batch_tasks), return_exceptions=True)
            event_name, _payload = queue_b.get_nowait()
            assert event_name == "changes"

            # Old watch tasks retire, never keep running outside registries.
            assert old_watch_task.done()
        finally:
            # Early assertion/wait_for failures still require releasing deliberately suspended watch/stop tasks,
            # preventing cross-test background-task/global project_change_hints listener leaks.
            allow_exit.set()
            if stop_task is not None and not stop_task.done():
                await asyncio.gather(stop_task, return_exceptions=True)
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_watch_terminates_stream_when_project_directory_deleted(self, tmp_path, caplog):
        """Project-directory deletion during subscriptions terminates within one poll: terminal broadcasts,
        normal stream ending, channel removal, one INFO log, no ERROR/traceback."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        with caplog.at_level(logging.INFO, logger="server.services.project.project_events"):
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                first = await anext(stream)
                assert first[0] == "snapshot"

                pm.delete_project_directory("demo")

                event_name, payload = await _next_event(stream, timeout=1.5)
                assert event_name == PROJECT_DELETED_EVENT
                assert payload == {"project_name": "demo"}

                # Streams end normally after terminal events, never due to active consumer disconnects.
                with pytest.raises(StopAsyncIteration):
                    await anext(stream)

        assert "demo" not in service._channels
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)
        info_records = [
            record for record in caplog.records if record.levelno == logging.INFO and "已被删除" in record.message
        ]
        assert len(info_records) == 1

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_watch_keeps_error_logging_when_only_project_json_missing(self, tmp_path, caplog):
        """Existing directories lacking only project.json remain outside this repair scope: preserve generic
        ERROR fallback without channel termination, continuing poll retries."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        with caplog.at_level(logging.INFO, logger="server.services.project.project_events"):
            async with service.stream_events("demo", idle_timeout=0.1):
                (pm.get_project_path("demo") / ProjectManager.PROJECT_FILE).unlink()
                # Wait at least one poll interval to detect missing project.json.
                await asyncio.sleep(0.3)
                assert "demo" in service._channels  # Channels remain alive/registered.

        assert any(record.levelno >= logging.ERROR for record in caplog.records)
        assert not any("已被删除" in record.message for record in caplog.records)

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_rebuild_terminates_channel_without_error_when_project_deleted(self, tmp_path, caplog):
        """Hint-triggered explicit rebuilds also terminate deleted projects without ERROR logs."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        # Long poll intervals isolate hints, excluding polling detecting deletion first.
        service = ProjectEventService(tmp_path, poll_interval=5.0)
        await service.start()

        with caplog.at_level(logging.INFO, logger="server.services.project.project_events"):
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                first = await anext(stream)
                assert first[0] == "snapshot"

                pm.delete_project_directory("demo")

                emit_project_change_batch(
                    "demo",
                    [
                        {
                            "entity_type": "segment",
                            "action": "updated",
                            "entity_id": "E1S01",
                            "label": "分镜「E1S01」",
                            "focus": None,
                            "important": False,
                        }
                    ],
                    source="worker",
                )

                event_name, payload = await _next_event(stream, timeout=1.5)
                assert event_name == PROJECT_DELETED_EVENT
                assert payload == {"project_name": "demo"}

        assert "demo" not in service._channels
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_new_subscriber_after_project_recreated_gets_fresh_channel(self, tmp_path):
        """Deletion terminates original channels; recreated same-name projects use fresh subscriptions, preserving current behavior."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            first = await anext(stream)
            assert first[0] == "snapshot"

            pm.delete_project_directory("demo")

            event_name, _payload = await _next_event(stream, timeout=1.5)
            assert event_name == PROJECT_DELETED_EVENT

        assert "demo" not in service._channels

        # Recreated same-name projects get fresh subscription channels, receiving snapshots/changes, never dying channels.
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo Reborn", "Anime", "narration")

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            first = await anext(stream)
            assert first[0] == "snapshot"
            assert "demo" in service._channels

        await service.shutdown()
