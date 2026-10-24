"""剪辑时间线管理路由：复制、改名、修订历史、回滚、删除与从项目回收站恢复，在真实项目目录与任务队列上验证。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import SetReason
from lib.final_cut.basis import FinalCutVariant, final_cut_key
from lib.final_cut.service import FinalCutService
from lib.generation.generation_queue import GenerationQueue, get_generation_queue
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines
from server.routers import project_trash as trash_router
from tests.auth_deps import override_auth
from tests.factories import install_current_video, make_test_clip

AGENT = RevisionAuthor(kind="arcreel_agent")


@pytest.fixture
async def timeline_client(
    file_db_factory, timeline_project: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(trash_router, "get_project_manager", lambda: timeline_project)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(edit_timelines.router, prefix="/api/v1")
    app.include_router(trash_router.router, prefix="/api/v1")
    app.dependency_overrides[edit_timelines.get_edit_timeline_service] = lambda: EditTimelineService(timeline_project)
    app.dependency_overrides[edit_timelines.get_final_cut_service] = lambda: FinalCutService(timeline_project)
    app.dependency_overrides[get_generation_queue] = lambda: GenerationQueue(
        session_factory=file_db_factory, project_manager=timeline_project
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _install(timeline_project: ProjectManager, tmp_path: Path, unit_id: str) -> None:
    source = tmp_path / "media" / f"{unit_id}.mp4"
    make_test_clip(source, size="160x90", fps=30, seconds=0.5, tone=True)
    install_current_video(timeline_project.get_project_path("demo"), "reference_videos", unit_id, source)


async def _create(timeline_client: AsyncClient, name: str = "完整版") -> str:
    created = await timeline_client.post(
        "/api/v1/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": name}
    )
    assert created.status_code == 201
    return created.json()["timeline"]["id"]


def _url(timeline_id: str, suffix: str = "") -> str:
    return f"/api/v1/projects/demo/edit-timelines/{timeline_id}{suffix}"


async def _agent_edit(timeline_project: ProjectManager, timeline_id: str, base_revision: int, reason: str) -> None:
    await EditTimelineService(timeline_project).edit(
        "demo",
        timeline_id,
        base_revision=base_revision,
        summary=reason,
        operations=[SetReason(op="set_reason", clip="c1", reason=reason)],
        author=AGENT,
    )


async def test_copy_rename_and_listing_follow_each_other(timeline_client: AsyncClient) -> None:
    source_id = await _create(timeline_client)

    copied = await timeline_client.post(_url(source_id, "/copy"), json={"name": "快节奏版"})
    renamed = await timeline_client.patch(_url(source_id), json={"name": "定稿"})
    listed = await timeline_client.get("/api/v1/projects/demo/edit-timelines", params={"episode": 1})

    assert copied.status_code == 201
    assert (copied.json()["timeline"]["name"], copied.json()["revision"]) == ("快节奏版", 1)
    assert renamed.status_code == 200
    assert (renamed.json()["id"], renamed.json()["name"], renamed.json()["revision"]) == (source_id, "定稿", 1)
    assert {item["name"] for item in listed.json()["timelines"]} == {"定稿", "快节奏版"}


@pytest.mark.parametrize(
    ("method", "suffix", "body", "status"),
    [
        ("post", "/copy", {"name": "完整版"}, 409),
        ("post", "/copy", {"name": "副本", "revision": 9}, 404),
        ("post", "/copy", {"name": "  "}, 422),
        ("patch", "", {"name": "  "}, 422),
        ("post", "/restore", {"revision": 9}, 404),
        ("post", "/restore", {"revision": 1}, 409),
    ],
)
async def test_domain_errors_map_to_status_codes(
    timeline_client: AsyncClient, method: str, suffix: str, body: dict[str, Any], status: int
) -> None:
    timeline_id = await _create(timeline_client)

    response = await timeline_client.request(method, _url(timeline_id, suffix), json=body)

    assert response.status_code == status
    assert response.json()["detail"]


async def test_unknown_timelines_answer_404_on_every_management_route(timeline_client: AsyncClient) -> None:
    unknown = "tl-0000abcd"

    responses = [
        await timeline_client.post(_url(unknown, "/copy"), json={"name": "副本"}),
        await timeline_client.patch(_url(unknown), json={"name": "新名"}),
        await timeline_client.get(_url(unknown, "/revisions")),
        await timeline_client.post(_url(unknown, "/restore"), json={"revision": 1}),
        await timeline_client.delete(_url(unknown)),
    ]

    assert [response.status_code for response in responses] == [404] * 5


async def test_revision_history_and_rollback(timeline_client: AsyncClient, timeline_project: ProjectManager) -> None:
    timeline_id = await _create(timeline_client)
    await _agent_edit(timeline_project, timeline_id, 1, "保留开场")

    history = await timeline_client.get(_url(timeline_id, "/revisions"))
    restored = await timeline_client.post(_url(timeline_id, "/restore"), json={"revision": 1})
    after = await timeline_client.get(_url(timeline_id, "/revisions"))

    assert history.status_code == 200
    assert [(item["number"], item["author"]["kind"]) for item in history.json()["revisions"]] == [
        (1, "creator"),
        (2, "arcreel_agent"),
    ]
    assert restored.status_code == 200
    assert (restored.json()["revision"], restored.json()["base_revision"]) == (3, 2)
    last = after.json()["revisions"][-1]
    assert (last["number"], last["restored_from"], last["author"]["kind"]) == (3, 1, "creator")
    read = await timeline_client.get(_url(timeline_id))
    assert [clip["reason"] for clip in read.json()["clips"]] == [None, None]


async def test_delete_removes_the_timeline_and_the_agent_listing_follows(
    timeline_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    doomed = await _create(timeline_client)
    kept = await _create(timeline_client, "保留版")

    deleted = await timeline_client.delete(_url(doomed))
    read = await timeline_client.get(_url(doomed))
    agent_listing = await EditTimelineService(timeline_project).list_timelines("demo", episode=1)

    assert deleted.status_code == 204
    assert read.status_code == 404
    assert [item.id for item in agent_listing] == [kept]


async def test_delete_clears_the_rendered_final_cut_and_its_claim(
    tmp_path: Path, timeline_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    for unit_id in ("E1U1", "E1U2"):
        _install(timeline_project, tmp_path, unit_id)
    timeline_id = await _create(timeline_client)
    rendered = await FinalCutService(timeline_project).render(
        "demo", timeline_id, narration="without_narration", subtitles="no_subtitles"
    )
    project_dir = timeline_project.get_project_path("demo")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    key = final_cut_key(1, timeline_id, FinalCutVariant())
    assert (project_dir / rendered.artifact_path).is_file()
    assert adapter.get_entry(key) is not None

    deleted = await timeline_client.delete(_url(timeline_id))

    assert deleted.status_code == 204
    assert adapter.get_entry(key) is None
    assert not (project_dir / "renders" / "episode_1" / timeline_id).exists()


async def test_delete_is_refused_while_a_render_is_queued(
    tmp_path: Path, timeline_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    for unit_id in ("E1U1", "E1U2"):
        _install(timeline_project, tmp_path, unit_id)
    timeline_id = await _create(timeline_client)
    submitted = await timeline_client.post(_url(timeline_id, "/final-cut"))
    assert submitted.status_code == 202

    refused = await timeline_client.delete(_url(timeline_id))

    assert refused.status_code == 409
    assert submitted.json()["task_id"] in refused.json()["detail"]
    assert (await timeline_client.get(_url(timeline_id))).status_code == 200


async def _only_trash_record(timeline_client: AsyncClient) -> dict[str, Any]:
    listed = await timeline_client.get("/api/v1/projects/demo/trash")
    [record] = listed.json()["records"]
    return record


async def test_a_deleted_timeline_comes_back_from_the_trash_with_its_final_cut(
    tmp_path: Path, timeline_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    for unit_id in ("E1U1", "E1U2"):
        _install(timeline_project, tmp_path, unit_id)
    timeline_id = await _create(timeline_client)
    final_cut = FinalCutService(timeline_project)
    rendered = await final_cut.render("demo", timeline_id, narration="without_narration", subtitles="no_subtitles")
    project_dir = timeline_project.get_project_path("demo")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    key = final_cut_key(1, timeline_id, FinalCutVariant())
    claim = adapter.get_entry(key)
    project_json = (project_dir / "project.json").read_bytes()

    assert (await timeline_client.delete(_url(timeline_id))).status_code == 204
    record = await _only_trash_record(timeline_client)
    assert (record["object"]["kind"], record["object"]["id"], record["has_snapshot"]) == (
        "edit_timeline",
        timeline_id,
        True,
    )
    assert f"edit_timelines/episode_1/{timeline_id}.json" in record["files"]
    assert rendered.artifact_path in record["files"]

    restored = await timeline_client.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert restored.status_code == 200, restored.text
    listed = await timeline_client.get("/api/v1/projects/demo/edit-timelines", params={"episode": 1})
    assert [(item["id"], item["name"]) for item in listed.json()["timelines"]] == [(timeline_id, "完整版")]
    assert (await timeline_client.get(_url(timeline_id))).status_code == 200
    assert (project_dir / rendered.artifact_path).is_file()
    assert adapter.get_entry(key) == claim
    # 临时项目迁移得出的项目级状态不带回
    assert (project_dir / "project.json").read_bytes() == project_json
    assert (await timeline_client.get("/api/v1/projects/demo/trash")).json()["records"] == []
    rerendered = await final_cut.render("demo", timeline_id, narration="without_narration", subtitles="no_subtitles")
    assert rerendered.artifact_path == rendered.artifact_path


async def test_a_restored_timeline_takes_a_suffixed_name_when_its_name_is_taken(
    timeline_client: AsyncClient,
) -> None:
    deleted_id = await _create(timeline_client)
    assert (await timeline_client.delete(_url(deleted_id))).status_code == 204
    recreated_id = await _create(timeline_client)
    record = await _only_trash_record(timeline_client)

    restored = await timeline_client.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert restored.status_code == 200, restored.text
    listed = await timeline_client.get("/api/v1/projects/demo/edit-timelines", params={"episode": 1})
    names = {item["id"]: item["name"] for item in listed.json()["timelines"]}
    assert names == {recreated_id: "完整版", deleted_id: "完整版 (1)"}


async def test_a_failed_timeline_restore_leaves_the_project_and_the_record_as_they_were(
    tmp_path: Path, timeline_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    for unit_id in ("E1U1", "E1U2"):
        _install(timeline_project, tmp_path, unit_id)
    timeline_id = await _create(timeline_client)
    await FinalCutService(timeline_project).render(
        "demo", timeline_id, narration="without_narration", subtitles="no_subtitles"
    )
    project_dir = timeline_project.get_project_path("demo")
    assert (await timeline_client.delete(_url(timeline_id))).status_code == 204
    record = await _only_trash_record(timeline_client)
    # 文档能落回，渲染文件的途经目录却被换成了符号链接
    (tmp_path / "outside").mkdir()
    (project_dir / "renders" / "episode_1").rmdir()
    (project_dir / "renders" / "episode_1").symlink_to(tmp_path / "outside")
    restore = f"/api/v1/projects/demo/trash/{record['id']}/restore"

    blocked = await timeline_client.post(restore)

    assert blocked.status_code == 409
    assert not (project_dir / "edit_timelines" / "episode_1" / f"{timeline_id}.json").exists()
    assert (await _only_trash_record(timeline_client))["id"] == record["id"]

    (project_dir / "renders" / "episode_1").unlink()
    assert (await timeline_client.post(restore)).status_code == 200
    assert (await timeline_client.get(_url(timeline_id))).status_code == 200
