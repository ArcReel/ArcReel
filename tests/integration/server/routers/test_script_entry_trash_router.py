"""移除分镜再从项目回收站恢复：剧本条目路由与项目回收站路由串起来，在真实项目目录上验证。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.artifacts.artifact_registration import register_current_artifact
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_deletion import delete_episode
from lib.infra.api_errors import ApiError
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import resource_relative_path
from lib.script.script_batch_edit import ScriptBatchEditor
from server.routers import project_trash as trash_router
from server.routers import projects
from tests.integration.server.routers.projects_router_support import build_projects_client, override

_SHOT = {
    "duration_seconds": 6,
    "novel_text": "风吹过旷野。",
    "characters_in_segment": [],
    "image_prompt": {
        "scene": "荒野",
        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
    },
    "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
}


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ProjectManager, TestClient]:
    """解说项目 ``demo``：第 1 集三个分镜，E1S02 与 E1S03 各有一张带版本历史的分镜图。"""
    manager = ProjectManager(str(tmp_path))
    manager.create_project("demo", content_mode="narration")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    project_dir = manager.get_project_path("demo")
    segments = []
    for item_id in ("E1S01", "E1S02", "E1S03"):
        assets: dict[str, Any] = {}
        if item_id != "E1S01":
            image = resource_relative_path("storyboards", item_id)
            (project_dir / image).parent.mkdir(parents=True, exist_ok=True)
            (project_dir / image).write_bytes(f"image-{item_id}".encode())
            VersionManager(project_dir).add_version(
                "storyboards", item_id, f"提示词 {item_id}", source_file=project_dir / image
            )
            assets = {"storyboard_image": image}
        segments.append({"segment_id": item_id, **_SHOT, "generated_assets": assets})
    manager.save_script(
        "demo", {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": segments}, "episode_1.json"
    )
    http = build_projects_client(monkeypatch, manager)
    override(http, projects.get_script_batch_editor_factory, lambda: lambda pm=None: ScriptBatchEditor(pm or manager))
    cast(FastAPI, http.app).include_router(trash_router.router, prefix="/api/v1")
    return manager, http


def _segments(manager: ProjectManager) -> list[dict[str, Any]]:
    return manager.load_script("demo", "episode_1.json")["segments"]


def _remove(http: TestClient, item_id: str) -> dict[str, Any]:
    removed = http.delete(f"/api/v1/projects/demo/script-items/{item_id}?script_file=episode_1.json")
    assert removed.status_code == 200, removed.text
    [record] = [r for r in http.get("/api/v1/projects/demo/trash").json()["records"] if r["object"]["id"] == item_id]
    return record


@pytest.mark.parametrize("changed_input", [False, True])
def test_a_removed_shot_comes_back_after_its_predecessor_with_media_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_input: bool
) -> None:
    manager, http = _project(tmp_path, monkeypatch)
    project_dir = manager.get_project_path("demo")
    history = VersionManager(project_dir).get_versions("storyboards", "E1S02")
    key = ArtifactKey.episode_storyboard(1, "E1S02")
    register_current_artifact(project_dir, key)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    claim = adapter.get_entry(key)
    assert claim is not None
    with http:
        record = _remove(http, "E1S02")

        assert [s["segment_id"] for s in _segments(manager)] == ["E1S01", "E1S03"]
        assert (record["object"]["kind"], record["object"]["episode"], record["has_snapshot"]) == (
            "script_entry",
            1,
            True,
        )
        assert {"storyboards/scene_E1S02.png", history["versions"][0]["file"]} <= set(record["files"])
        assert not (project_dir / "storyboards" / "scene_E1S02.png").exists()
        assert not VersionManager(project_dir).has_versions("storyboards", "E1S02")
        assert adapter.get_entry(key) is None
        if changed_input:
            manager.update_project("demo", lambda project: project.update(style="水彩"))

        restored = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

        assert restored.status_code == 200, restored.text
        assert (restored.json()["object"]["id"], restored.json()["degraded"]) == ("E1S02", False)
        segments = _segments(manager)
        assert [s["segment_id"] for s in segments] == ["E1S01", "E1S02", "E1S03"]
        assert segments[1]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S02.png"
        assert (project_dir / "storyboards" / "scene_E1S02.png").read_bytes() == b"image-E1S02"
        assert VersionManager(project_dir).get_versions("storyboards", "E1S02") == history
        assert adapter.get_entry(key) == claim
        status = ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=claim.artifact_path).status
        assert status == (ArtifactStatus.STALE if changed_input else ArtifactStatus.CURRENT)
        assert http.get("/api/v1/projects/demo/trash").json()["records"] == []


def test_a_shot_whose_id_was_handed_out_again_comes_back_under_a_new_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, http = _project(tmp_path, monkeypatch)
    project_dir = manager.get_project_path("demo")
    with http:
        record = _remove(http, "E1S03")
        # 末尾条目删掉后再新增会取回它的编号：新条目是另一个身份。
        inserted = http.post(
            "/api/v1/projects/demo/script-items",
            json={"script_file": "episode_1.json", "after_id": "E1S01", "novel_text": "新的一镜。"},
        )
        assert inserted.json()["item"]["segment_id"] == "E1S03"

        restored = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["object"]["id"] == "E1S04"
    segments = _segments(manager)
    assert [s["segment_id"] for s in segments] == ["E1S01", "E1S03", "E1S02", "E1S04"]
    assert segments[3]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S04.png"
    assert segments[1]["generated_assets"] == {}
    assert (project_dir / "storyboards" / "scene_E1S04.png").read_bytes() == b"image-E1S03"
    assert not (project_dir / "storyboards" / "scene_E1S03.png").exists()
    versions = VersionManager(project_dir)
    assert not versions.has_versions("storyboards", "E1S03")
    [snapshot] = versions.get_versions("storyboards", "E1S04")["versions"]
    assert Path(snapshot["file"]).name.startswith("E1S04_v1_")
    assert (project_dir / snapshot["file"]).read_bytes() == b"image-E1S03"


def test_a_renumbered_grid_member_comes_back_as_a_plain_shot_that_the_planner_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, http = _project(tmp_path, monkeypatch)
    project_dir = manager.get_project_path("demo")
    script = manager.load_script("demo", "episode_1.json")
    script["segments"][2]["generated_assets"] |= {"grid_id": "grid_1", "grid_cell_index": 2}
    manager.save_script("demo", script, "episode_1.json")
    with http:
        record = _remove(http, "E1S03")
        http.post(
            "/api/v1/projects/demo/script-items",
            json={"script_file": "episode_1.json", "after_id": "E1S01", "novel_text": "新的一镜。"},
        )
        restored = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert restored.json()["object"]["id"] == "E1S04", restored.text
    assets = _segments(manager)[3]["generated_assets"]
    # 宫格的成员仍是旧编号：新编号的分镜不再属于它，按普通分镜规划。
    assert (assets.get("grid_id"), assets.get("grid_cell_index")) == (None, None)
    assert TargetStatePlanner(project_dir).resolve_basis(ArtifactKey.episode_storyboard(1, "E1S04")) is not None


@pytest.mark.parametrize("degraded", [False, True])
def test_two_concurrent_restores_of_one_record_put_it_back_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, degraded: bool
) -> None:
    manager, http = _project(tmp_path, monkeypatch)
    project_dir = manager.get_project_path("demo")
    source = project_dir / "source" / "race.txt"
    source.write_text("恢复时的迁移屏障", encoding="utf-8")
    with http:
        if degraded:
            impact = delete_episode(manager, "demo", 1)
            delete_episode(manager, "demo", 1, revision=impact.impact.revision)
            [record] = http.get("/api/v1/projects/demo/trash").json()["records"]
        else:
            record = _remove(http, "E1S03")
    outcomes: list[object] = []
    staged = threading.Barrier(2)
    read_bytes = Path.read_bytes

    def read_staged_source(path: Path) -> bytes:
        if path.name == source.name and any(p.name.startswith(".restore-") for p in path.parents):
            # 编码迁移在铺设完成、释放项目锁之后读源文：两次恢复都到达后才允许继续。
            staged.wait(timeout=30)
            if degraded:
                raise OSError("source read failed")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_staged_source)

    def restore() -> None:
        try:
            outcomes.append(project_trash.restore(project_dir, record["id"]))
        except ApiError as exc:
            outcomes.append(exc)

    racers = [threading.Thread(target=restore) for _ in range(2)]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join(timeout=30)
        assert not racer.is_alive()

    [failure] = [o for o in outcomes if isinstance(o, ApiError)]
    assert failure.key == ("trash_restore_download_only" if degraded else "trash_record_not_found")
    assert len(outcomes) == 2
    if degraded:
        [kept] = project_trash.list_records(project_dir)
        assert kept.download_only
        assert kept.files == ("scripts/episode_1.json",)
        assert not (project_dir / "scripts/episode_1.json").exists()
        assert manager.load_project("demo")["episodes"] == []
    else:
        assert [s["segment_id"] for s in _segments(manager)] == ["E1S01", "E1S02", "E1S03"]
    assert sorted(p.name for p in (project_dir / "storyboards").iterdir()) == ["scene_E1S02.png", "scene_E1S03.png"]
    assert not VersionManager(project_dir).has_versions("storyboards", "E1S04")


def test_a_shot_whose_episode_was_deleted_too_can_only_come_back_with_the_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, http = _project(tmp_path, monkeypatch)
    with http:
        record = _remove(http, "E1S02")
        impact = delete_episode(manager, "demo", 1)
        delete_episode(manager, "demo", 1, revision=impact.impact.revision)

        refused = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

        assert refused.status_code == 409
        assert "只能随集恢复" in refused.json()["detail"]
        records = {r["object"]["kind"]: r for r in http.get("/api/v1/projects/demo/trash").json()["records"]}
        assert records["script_entry"]["id"] == record["id"]

        episode_back = http.post(f"/api/v1/projects/demo/trash/{records['episode']['id']}/restore")
        shot_back = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert episode_back.status_code == 200, episode_back.text
    assert shot_back.status_code == 200, shot_back.text
    assert shot_back.json()["degraded"] is False
    assert [s["segment_id"] for s in _segments(manager)] == ["E1S01", "E1S02", "E1S03"]


def test_a_failure_while_moving_the_media_leaves_the_script_media_and_trash_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, http = _project(tmp_path, monkeypatch)
    project_dir = manager.get_project_path("demo")
    script_before = (project_dir / "scripts" / "episode_1.json").read_bytes()
    versions_before = (project_dir / "versions" / "versions.json").read_bytes()

    def broken_detach(self: VersionManager, resources: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(VersionManager, "detach_resources", broken_detach)
    with http:
        removed = http.delete("/api/v1/projects/demo/script-items/E1S02?script_file=episode_1.json")
        records = http.get("/api/v1/projects/demo/trash").json()["records"]

    assert removed.status_code != 200
    assert (project_dir / "scripts" / "episode_1.json").read_bytes() == script_before
    assert (project_dir / "versions" / "versions.json").read_bytes() == versions_before
    assert (project_dir / "storyboards" / "scene_E1S02.png").read_bytes() == b"image-E1S02"
    assert records == []


def test_a_removed_shot_without_media_still_becomes_a_record_and_returns_to_the_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, http = _project(tmp_path, monkeypatch)
    with http:
        record = _remove(http, "E1S01")
        assert record["files"] == []

        restored = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert restored.status_code == 200, restored.text
    assert [s["segment_id"] for s in _segments(manager)] == ["E1S01", "E1S02", "E1S03"]


def test_a_shot_without_media_that_cannot_be_taken_out_keeps_its_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有被删文件的记录降级后什么都加回不了：恢复失败、记录保留，修好后可以重试。"""
    manager, http = _project(tmp_path, monkeypatch)
    project_dir = manager.get_project_path("demo")
    with http:
        record = _remove(http, "E1S01")
        snapshot_script = (
            project_trash.trash_dir(project_dir)
            / record["id"]
            / project_trash.SNAPSHOT_DIRNAME
            / "scripts"
            / "episode_1.json"
        )
        intact = snapshot_script.read_bytes()
        snapshot_script.write_text("{broken", encoding="utf-8")

        refused = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")
        kept = [r["id"] for r in http.get("/api/v1/projects/demo/trash").json()["records"]]
        snapshot_script.write_bytes(intact)
        retried = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert refused.status_code == 409, refused.text
    assert kept == [record["id"]]
    assert retried.status_code == 200, retried.text
    assert [s["segment_id"] for s in _segments(manager)] == ["E1S01", "E1S02", "E1S03"]


def test_a_renumbered_shot_does_not_take_the_history_its_new_id_had_when_it_was_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删除时的快照里，新编号还属于另一个分镜：换编号恢复不能把那个分镜的版本历史带回来。"""
    manager, http = _project(tmp_path, monkeypatch)
    project_dir = manager.get_project_path("demo")
    with http:
        record = _remove(http, "E1S01")
        _remove(http, "E1S02")
        _remove(http, "E1S03")
        inserted = http.post(
            "/api/v1/projects/demo/script-items", json={"script_file": "episode_1.json", "novel_text": "新的一镜。"}
        )
        assert inserted.json()["item"]["segment_id"] == "E1S01", inserted.text

        restored = http.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["object"]["id"] == "E1S02"
    assert [s["segment_id"] for s in _segments(manager)] == ["E1S02", "E1S01"]
    assert not VersionManager(project_dir).has_versions("storyboards", "E1S02")
