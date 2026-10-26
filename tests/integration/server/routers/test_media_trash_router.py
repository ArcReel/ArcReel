"""宫格、尾帧与角色参考音频进项目回收站：删除成为删除记录，恢复时原位置空闲则回原位，被占用则换新名。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.version_manager import VersionManager
from lib.infra.data_root_layout import DataRootLayout
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.models import GridGeneration
from server.error_handlers import register_error_handlers
from server.routers import project_trash as trash_router
from server.services.project import end_frame as end_frame_service
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import fail_project_json_write, use_project_manager

TRASH = "/api/v1/projects/demo/trash"
END_FRAME = "end_frames/scene_E1S01.png"
AUDIO = "characters/refs_audio/Alice.wav"


@pytest.fixture
def media_trash_pm(tmp_path, monkeypatch) -> ProjectManager:
    manager = ProjectManager(DataRootLayout(tmp_path / "data").root)
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E1S01", "novel_text": "t", "duration_seconds": 5, "generated_assets": {}},
            ],
        },
        "episode_1.json",
        validate=False,
    )
    manager.add_project_character("demo", "Alice", "hero", "soft")
    use_project_manager(monkeypatch, manager)
    monkeypatch.setattr(end_frame_service, "get_project_manager", lambda: manager)
    return manager


@pytest.fixture
def media_trash_client(media_trash_pm) -> TestClient:
    app = FastAPI()
    app.include_router(trash_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    override_auth(app)
    return TestClient(app)


def _restore_only_record(media_trash_client: TestClient) -> dict:
    (record,) = media_trash_client.get(TRASH).json()["records"]
    response = media_trash_client.post(f"{TRASH}/{record['id']}/restore")
    assert response.status_code == 200, response.text
    return response.json()


def _segment(media_trash_pm: ProjectManager) -> dict:
    return media_trash_pm.load_script("demo", "episode_1.json")["segments"][0]


def _set_end_frame(media_trash_pm: ProjectManager, content: bytes) -> None:
    (media_trash_pm.get_project_path("demo") / END_FRAME).parent.mkdir(exist_ok=True)
    (media_trash_pm.get_project_path("demo") / END_FRAME).write_bytes(content)
    with media_trash_pm.locked_script("demo", "episode_1.json", validate=False) as script:
        script["segments"][0]["end_frame_image"] = END_FRAME


def _clear_end_frame(media_trash_pm: ProjectManager) -> None:
    project_dir = media_trash_pm.get_project_path("demo")
    end_frame_service._clear_snapshot_and_field("demo", "episode_1.json", "E1S01", project_dir / END_FRAME)


def test_a_superseded_grid_comes_back_with_its_version_history_and_claim(media_trash_pm, media_trash_client):
    project_dir = media_trash_pm.get_project_path("demo")
    grids = GridManager(project_dir)
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01"],
        rows=1,
        cols=2,
        grid_size="grid_2",
        provider="test",
        model="m",
        video_aspect_ratio="9:16",
    )
    grid.status = "completed"
    grids.save(grid)
    image = grids.image_path(grid.id)
    image.write_bytes(b"grid-image")
    versions = VersionManager(project_dir)
    versions.add_version("grids", grid.id, "prompt", source_file=image)
    history = versions.get_versions("grids", grid.id)["versions"]
    key = ArtifactKey.episode_grid(1, grid.id)
    claim = ArtifactManifestEntry(f"grids/{grid.id}.png", f"sha256-v1:{'a' * 64}")
    ProjectArtifactManifestAdapter(project_dir).put_entry(key, claim)

    assert grids.cleanup_superseded("episode_1.json", 1, {"E1S01"}) == 1
    assert grids.get(grid.id) is None
    assert versions.get_versions("grids", grid.id)["versions"] == []
    assert ProjectArtifactManifestAdapter(project_dir).get_entry(key) is None

    result = _restore_only_record(media_trash_client)

    assert result["degraded"] is False
    assert grids.get(grid.id) is not None
    assert image.read_bytes() == b"grid-image"
    assert [v["file"] for v in versions.get_versions("grids", grid.id)["versions"]] == [v["file"] for v in history]
    assert all((project_dir / v["file"]).is_file() for v in history)
    assert ProjectArtifactManifestAdapter(project_dir).get_entry(key) == claim


def test_a_cleared_end_frame_returns_to_its_shot(media_trash_pm, media_trash_client):
    _set_end_frame(media_trash_pm, b"frame")
    _clear_end_frame(media_trash_pm)
    assert _segment(media_trash_pm)["end_frame_image"] is None
    assert not (media_trash_pm.get_project_path("demo") / END_FRAME).exists()

    result = _restore_only_record(media_trash_client)

    assert result["object"]["path"] == END_FRAME
    assert _segment(media_trash_pm)["end_frame_image"] == END_FRAME
    assert (media_trash_pm.get_project_path("demo") / END_FRAME).read_bytes() == b"frame"


def test_an_end_frame_record_names_its_shot_by_position_and_text(media_trash_pm):
    """回收站按分镜在集里的位置与正文摘录称呼尾帧，不露分镜的内部编号。"""
    _set_end_frame(media_trash_pm, b"frame")
    _clear_end_frame(media_trash_pm)

    [record] = project_trash.list_records(media_trash_pm.get_project_path("demo"))
    assert (record.object["episode"], record.object["item_position"], record.object["item_excerpt"]) == (1, 1, "t")


def test_an_end_frame_record_takes_its_episode_from_the_script_file_when_the_script_has_none(media_trash_pm):
    """剧本内没有整数 episode 时，尾帧记录的集号取自规范文件名，回收站仍按位置称呼分镜。"""
    _set_end_frame(media_trash_pm, b"frame")
    with media_trash_pm.locked_script("demo", "episode_1.json", validate=False) as script:
        del script["episode"]
    _clear_end_frame(media_trash_pm)

    [record] = project_trash.list_records(media_trash_pm.get_project_path("demo"))
    assert (record.object["episode"], record.object["item_position"], record.object["item_excerpt"]) == (1, 1, "t")


def test_an_end_frame_restored_onto_a_newer_one_takes_a_new_name(media_trash_pm, media_trash_client):
    _set_end_frame(media_trash_pm, b"old")
    _clear_end_frame(media_trash_pm)
    _set_end_frame(media_trash_pm, b"new")

    result = _restore_only_record(media_trash_client)

    renamed = "end_frames/scene_E1S01 (1).png"
    assert result["restored"] == [{"original": END_FRAME, "path": renamed, "as_history": False}]
    assert _segment(media_trash_pm)["end_frame_image"] == END_FRAME
    project_dir = media_trash_pm.get_project_path("demo")
    assert (project_dir / END_FRAME).read_bytes() == b"new"
    assert (project_dir / renamed).read_bytes() == b"old"


def test_a_cleared_reference_audio_is_relinked_to_its_character(media_trash_pm, media_trash_client):
    media_trash_pm.install_character_reference_audio("demo", "Alice", AUDIO, b"voice")
    media_trash_pm.clear_character_reference_audio("demo", "Alice")
    assert not (media_trash_pm.get_project_path("demo") / AUDIO).exists()

    _restore_only_record(media_trash_client)

    assert media_trash_pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == AUDIO
    assert (media_trash_pm.get_project_path("demo") / AUDIO).read_bytes() == b"voice"


def test_reference_audio_of_a_deleted_character_restores_only_its_file(media_trash_pm, media_trash_client):
    media_trash_pm.install_character_reference_audio("demo", "Alice", AUDIO, b"voice")
    media_trash_pm.clear_character_reference_audio("demo", "Alice")
    media_trash_pm.update_project("demo", lambda project: project["characters"].pop("Alice"))

    result = _restore_only_record(media_trash_client)

    assert result["degraded"] is True
    assert (media_trash_pm.get_project_path("demo") / AUDIO).read_bytes() == b"voice"
    assert project_trash.list_records(media_trash_pm.get_project_path("demo")) == []


def test_a_reference_audio_restored_to_a_new_name_does_not_relink_to_another_characters_file(
    media_trash_pm, media_trash_client
):
    project_dir = media_trash_pm.get_project_path("demo")
    media_trash_pm.add_project_character("demo", "Alice (1)", "another", "soft")
    record_ids: list[str] = []
    for content in (b"first", b"second"):
        media_trash_pm.install_character_reference_audio("demo", "Alice", AUDIO, content)
        media_trash_pm.clear_character_reference_audio("demo", "Alice")
        record_ids.append(
            next(record.id for record in project_trash.list_records(project_dir) if record.id not in record_ids)
        )
    first, second = record_ids
    mp3 = "characters/refs_audio/Alice.mp3"
    media_trash_pm.install_character_reference_audio("demo", "Alice", mp3, b"current")
    # 当前音色仍有效：先恢复的一份只作为未引用文件加回原位。
    project_trash.restore(project_dir, first)
    media_trash_pm.clear_character_reference_audio("demo", "Alice")

    restored = media_trash_client.post(f"{TRASH}/{second}/restore")

    assert restored.status_code == 200, restored.text
    renamed = "characters/refs_audio/Alice (1).wav"
    assert restored.json()["object"]["path"] == renamed
    assert (project_dir / renamed).read_bytes() == b"second"
    assert (project_dir / AUDIO).read_bytes() == b"first"
    assert media_trash_pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == ""


@pytest.mark.parametrize("kind", ["end_frame", "reference_audio"])
def test_failed_relink_keeps_the_record_and_retracts_the_restored_file(
    media_trash_pm, media_trash_client, monkeypatch, kind
):
    project_dir = media_trash_pm.get_project_path("demo")
    formal = project_dir / "project.json"
    if kind == "end_frame":
        _set_end_frame(media_trash_pm, b"frame")
        _clear_end_frame(media_trash_pm)
        rel = END_FRAME
    else:
        media_trash_pm.install_character_reference_audio("demo", "Alice", AUDIO, b"voice")
        media_trash_pm.clear_character_reference_audio("demo", "Alice")
        rel = AUDIO
    [record] = project_trash.list_records(project_dir)
    before = formal.read_bytes()
    with monkeypatch.context() as patch:
        fail_project_json_write(patch, formal)
        with pytest.raises(OSError, match="injected project write failure"):
            project_trash.restore(project_dir, record.id)

    assert formal.read_bytes() == before
    assert not (project_dir / rel).exists()
    assert project_trash.require_record(project_dir, record.id) == record
    _restore_only_record(media_trash_client)
    if kind == "end_frame":
        assert _segment(media_trash_pm)["end_frame_image"] == rel
    else:
        assert media_trash_pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == rel
