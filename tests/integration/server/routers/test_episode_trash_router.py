"""删一集再从项目回收站恢复：集管理、剪辑时间线与项目回收站路由串起来，在真实项目目录上验证。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.artifacts import artifact_activation
from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter
from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import EditTimelineService
from lib.episode.episode_deletion import delete_episode
from lib.final_cut.basis import FinalCutVariant, final_cut_key
from lib.final_cut.service import FinalCutService
from lib.project.project_manager import ProjectManager
from lib.project.project_trash import list_records
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.models import GridGeneration
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines, episode_management
from server.routers import project_trash as trash_router
from tests.auth_deps import override_auth
from tests.factories import install_current_video, make_test_clip
from tests.fakes import use_project_manager


@pytest.fixture
async def episode_client(
    timeline_project: ProjectManager, monkeypatch: pytest.MonkeyPatch, active_episode_tasks: dict[str, Any]
) -> AsyncIterator[AsyncClient]:
    use_project_manager(monkeypatch, timeline_project)
    monkeypatch.setattr(episode_management, "get_project_manager", lambda: timeline_project)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(episode_management.router, prefix="/api/v1")
    app.include_router(edit_timelines.router, prefix="/api/v1")
    app.include_router(trash_router.router, prefix="/api/v1")
    app.dependency_overrides[edit_timelines.get_edit_timeline_service] = lambda: EditTimelineService(timeline_project)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _install_videos(timeline_project: ProjectManager, tmp_path: Path) -> None:
    for unit_id in ("E1U1", "E1U2"):
        source = tmp_path / "media" / f"{unit_id}.mp4"
        make_test_clip(source, size="160x90", fps=30, seconds=0.5, tone=True)
        install_current_video(timeline_project.get_project_path("demo"), "reference_videos", unit_id, source)


async def _create_timeline(client: AsyncClient) -> str:
    created = await client.post(
        "/api/v1/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": "完整版"}
    )
    assert created.status_code == 201
    return created.json()["timeline"]["id"]


async def _delete_episode_1(client: AsyncClient) -> None:
    preview = await client.post("/api/v1/projects/demo/episodes/1/delete", json={})
    revision = preview.json()["impact"]["revision"]
    deleted = await client.post("/api/v1/projects/demo/episodes/1/delete", json={"revision": revision})
    assert deleted.json()["status"] == "deleted", deleted.text


async def _records(client: AsyncClient) -> list[dict[str, Any]]:
    return (await client.get("/api/v1/projects/demo/trash")).json()["records"]


def _episodes(timeline_project: ProjectManager) -> list[int]:
    return [entry["episode"] for entry in timeline_project.load_project("demo")["episodes"]]


async def test_a_deleted_episode_comes_back_with_its_timelines_media_versions_and_claims(
    tmp_path: Path, episode_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    _install_videos(timeline_project, tmp_path)
    timeline_id = await _create_timeline(episode_client)
    rendered = await FinalCutService(timeline_project).render(
        "demo", timeline_id, narration="without_narration", subtitles="no_subtitles"
    )
    project_dir = timeline_project.get_project_path("demo")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    key = final_cut_key(1, timeline_id, FinalCutVariant())
    claim = adapter.get_entry(key)
    versions = VersionManager(project_dir).get_versions("reference_videos", "E1U1")
    assert claim is not None
    assert versions["versions"]

    await _delete_episode_1(episode_client)

    assert _episodes(timeline_project) == []
    [record] = await _records(episode_client)
    assert (record["object"]["kind"], record["object"]["id"], record["has_snapshot"]) == ("episode", 1, True)
    assert {
        "scripts/episode_1.json",
        f"edit_timelines/episode_1/{timeline_id}.json",
        rendered.artifact_path,
        "reference_videos/E1U1.mp4",
        versions["versions"][0]["file"],
    } <= set(record["files"])
    assert not (project_dir / "scripts" / "episode_1.json").exists()
    assert adapter.get_entry(key) is None
    assert VersionManager(project_dir).get_versions("reference_videos", "E1U1")["versions"] == []

    restored = await episode_client.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert restored.status_code == 200, restored.text
    assert (restored.json()["object"]["kind"], restored.json()["degraded"]) == ("episode", False)
    assert _episodes(timeline_project) == [1]
    listed = await episode_client.get("/api/v1/projects/demo/edit-timelines", params={"episode": 1})
    assert [item["id"] for item in listed.json()["timelines"]] == [timeline_id]
    assert (project_dir / rendered.artifact_path).is_file()
    assert adapter.get_entry(key) == claim
    assert VersionManager(project_dir).get_versions("reference_videos", "E1U1") == versions
    assert await _records(episode_client) == []


async def test_an_episode_without_files_keeps_its_title_and_hook_when_restored(
    episode_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    created = await episode_client.post("/api/v1/projects/demo/episodes", json={"title": "开场", "hook": "伏笔"})
    assert created.status_code == 201, created.text
    episode = created.json()["episode"]
    before = next(e for e in timeline_project.load_project("demo")["episodes"] if e["episode"] == episode)
    url = f"/api/v1/projects/demo/episodes/{episode}/delete"
    preview = await episode_client.post(url, json={})
    deleted = await episode_client.post(url, json={"revision": preview.json()["impact"]["revision"]})
    assert deleted.json()["status"] == "deleted", deleted.text
    [record] = await _records(episode_client)
    assert (record["files"], record["has_snapshot"]) == ([], True)

    restored = await episode_client.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["degraded"] is False
    assert timeline_project.load_project("demo")["episodes"][-1] == before
    assert await _records(episode_client) == []


async def test_a_timeline_deleted_before_its_episode_restores_normally_once_the_episode_is_back(
    episode_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    timeline_id = await _create_timeline(episode_client)
    assert (await episode_client.delete(f"/api/v1/projects/demo/edit-timelines/{timeline_id}")).status_code == 204
    await _delete_episode_1(episode_client)
    by_kind = {record["object"]["kind"]: record for record in await _records(episode_client)}
    # 集还在项目回收站里：时间线只能随集恢复，不降级成只能下载。
    too_early = await episode_client.post(f"/api/v1/projects/demo/trash/{by_kind['edit_timeline']['id']}/restore")
    assert too_early.status_code == 409
    assert "只能随集恢复" in too_early.json()["detail"]

    episode_back = await episode_client.post(f"/api/v1/projects/demo/trash/{by_kind['episode']['id']}/restore")
    timeline_back = await episode_client.post(f"/api/v1/projects/demo/trash/{by_kind['edit_timeline']['id']}/restore")

    assert episode_back.status_code == 200, episode_back.text
    assert timeline_back.status_code == 200, timeline_back.text
    assert timeline_back.json()["degraded"] is False
    assert (await episode_client.get(f"/api/v1/projects/demo/edit-timelines/{timeline_id}")).status_code == 200


async def test_a_grid_deleted_before_its_episode_restores_normally_once_the_episode_is_back(
    episode_client: AsyncClient, timeline_project: ProjectManager
) -> None:
    project_dir = timeline_project.get_project_path("demo")
    grids = GridManager(project_dir)
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1U1"],
        rows=1,
        cols=2,
        grid_size="grid_2",
        provider="test",
        model="m",
        video_aspect_ratio="9:16",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    grids.save(grid)
    grids.image_path(grid.id).write_bytes(b"grid-image")
    VersionManager(project_dir).add_version("grids", grid.id, "prompt", source_file=grids.image_path(grid.id))
    assert grids.move_to_trash(grid.id)
    await _delete_episode_1(episode_client)
    by_kind = {record["object"]["kind"]: record for record in await _records(episode_client)}
    # 集还在项目回收站里：宫格只能随集恢复，不降级成没有归属的普通文件。
    too_early = await episode_client.post(f"/api/v1/projects/demo/trash/{by_kind['grid']['id']}/restore")
    assert too_early.status_code == 409
    assert "只能随集恢复" in too_early.json()["detail"]
    assert grids.get(grid.id) is None
    assert by_kind["grid"]["id"] in {record["id"] for record in await _records(episode_client)}

    episode_back = await episode_client.post(f"/api/v1/projects/demo/trash/{by_kind['episode']['id']}/restore")
    grid_back = await episode_client.post(f"/api/v1/projects/demo/trash/{by_kind['grid']['id']}/restore")

    assert episode_back.status_code == 200, episode_back.text
    assert grid_back.status_code == 200, grid_back.text
    assert grid_back.json()["degraded"] is False
    restored = grids.get(grid.id)
    assert restored is not None
    assert restored.grid_image_path == f"grids/{grid.id}.png"
    assert grids.image_path(grid.id).read_bytes() == b"grid-image"
    assert VersionManager(project_dir).has_versions("grids", grid.id)


@pytest.mark.parametrize(
    "failing_step",
    [
        (VersionManager, "detach_resources"),
        # 版本记录已摘掉之后才失败：版本历史也要回到删除前
        (artifact_activation, "register_artifact_entries_atomically"),
    ],
    ids=["detach-versions", "manifest-after-detach"],
)
def test_a_failed_deletion_commit_withdraws_the_record_and_keeps_the_episode(
    tmp_path: Path,
    timeline_project: ProjectManager,
    monkeypatch: pytest.MonkeyPatch,
    failing_step: tuple[object, str],
) -> None:
    _install_videos(timeline_project, tmp_path)
    project_dir = timeline_project.get_project_path("demo")
    project_json = (project_dir / "project.json").read_bytes()
    versions = VersionManager(project_dir).get_versions("reference_videos", "E1U1")
    preview = delete_episode(timeline_project, "demo", 1)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(*failing_step, fail)
    with pytest.raises(OSError, match="disk full"):
        delete_episode(timeline_project, "demo", 1, revision=preview.impact.revision)

    assert list_records(project_dir) == []
    assert (project_dir / "project.json").read_bytes() == project_json
    assert (project_dir / "scripts" / "episode_1.json").is_file()
    assert (project_dir / "reference_videos" / "E1U1.mp4").is_file()
    assert (project_dir / versions["versions"][0]["file"]).is_file()
    assert VersionManager(project_dir).get_versions("reference_videos", "E1U1") == versions
