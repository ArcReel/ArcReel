"""上传的分镜图与视频即成品：清单按上传字节登记，时效不随提示词与引用资产变化。"""

from io import BytesIO
from pathlib import Path
from typing import Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.config.resolver import ConfigResolver
from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_batch import GenerationBatchReadModel
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.media_tools.storyboards import GenerateStoryboardsRequest, generate_storyboards
from server.routers import asset_sheets, reference_videos, shot_uploads
from server.routers import versions as versions_router
from server.services.currency import upload_finalize
from server.services.project.workflow_planner import WorkflowPlanner
from server.services.tasks import generation_tasks, reference_video_tasks
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolRequest
from tests.auth_deps import AUTH_DEPENDENCIES

_STORYBOARD = "storyboards/scene_E1S01.png"
_VIDEO = "videos/scene_E1S01.mp4"
_STORYBOARD_KEY = ArtifactKey.episode_storyboard(1, "E1S01")
_VIDEO_KEY = ArtifactKey.episode_video(1, "E1S01")


def _png(color: tuple[int, int, int]) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _segment(image_prompt: object, characters: list[str]) -> dict:
    return {
        "segment_id": "E1S01",
        "novel_text": "t",
        "duration_seconds": 5,
        "characters_in_segment": characters,
        "scenes": [],
        "props": [],
        "image_prompt": image_prompt,
        "video_prompt": "镜头缓慢推进",
        "generated_assets": {
            "storyboard_image": None,
            "video_clip": None,
            "video_uri": None,
            "video_thumbnail": None,
            "grid_id": None,
            "grid_cell_index": None,
            "status": "pending",
        },
    }


async def _fake_thumbnail(_video_path: Path, thumbnail_path: Path):
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnail_path.write_bytes(b"jpg")  # noqa: ASYNC240 -- 测试内本地小文件写入，不在生产事件循环上
    return thumbnail_path


def _client(
    monkeypatch, tmp_path: Path, *, image_prompt: object = "安静的房间", characters: list[str] | None = None
) -> tuple[TestClient, ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "Alice", "银发少女")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [_segment(image_prompt, characters or [])],
        },
        "episode_1.json",
        validate=False,
    )
    for module in (shot_uploads, upload_finalize, generation_tasks, versions_router):
        monkeypatch.setattr(module, "get_project_manager", lambda: pm)
    monkeypatch.setattr(upload_finalize, "extract_video_thumbnail", _fake_thumbnail)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(shot_uploads.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(versions_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), pm, pm.get_project_path("demo")


def _upload(client: TestClient, kind: str, filename: str, content: bytes) -> None:
    response = client.post(
        f"/api/v1/projects/demo/shots/E1S01/upload/{kind}?script_file=episode_1.json",
        files={"file": (filename, BytesIO(content), "application/octet-stream")},
    )
    assert response.status_code == 200, response.text


def _edit_segment(pm: ProjectManager, **fields: object) -> None:
    with pm.locked_script("demo", "episode_1.json", validate=False) as script:
        script["segments"][0].update(fields)


def _status(project_dir: Path, key: ArtifactKey, path: str) -> ArtifactStatus:
    return ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=path).status


def test_uploaded_video_is_registered_current_and_prompt_edits_do_not_stale_it(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path)
    with client:
        _upload(client, "video", "clip.mp4", b"\x00" * 512)

    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_VIDEO_KEY)
    assert entry is not None
    assert entry.artifact_path == _VIDEO
    assert _status(project_dir, _VIDEO_KEY, _VIDEO) is ArtifactStatus.CURRENT

    _edit_segment(pm, image_prompt="黄昏的街角", video_prompt="快速横摇")
    assert _status(project_dir, _VIDEO_KEY, _VIDEO) is ArtifactStatus.CURRENT


def test_externally_replaced_uploaded_video_is_not_claimed_by_the_upload(tmp_path, monkeypatch):
    client, _pm, project_dir = _client(monkeypatch, tmp_path)
    with client:
        _upload(client, "video", "clip.mp4", b"\x00" * 512)

    (project_dir / _VIDEO).write_bytes(b"\x01" * 512)

    assert _status(project_dir, _VIDEO_KEY, _VIDEO) is not ArtifactStatus.CURRENT


def test_storyboard_uploaded_without_prompt_keeps_its_entry(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path, image_prompt="")
    with client:
        _upload(client, "storyboard", "board.png", _png((200, 10, 10)))

    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_STORYBOARD_KEY)
    assert entry is not None
    assert entry.artifact_path == _STORYBOARD
    assert _status(project_dir, _STORYBOARD_KEY, _STORYBOARD) is ArtifactStatus.CURRENT

    _edit_segment(pm, image_prompt="黄昏的街角")
    assert _status(project_dir, _STORYBOARD_KEY, _STORYBOARD) is ArtifactStatus.CURRENT


def test_storyboard_uploaded_while_a_referenced_asset_lacks_its_sheet_keeps_its_entry(tmp_path, monkeypatch):
    client, _pm, project_dir = _client(monkeypatch, tmp_path, characters=["Alice"])
    with client:
        _upload(client, "storyboard", "board.png", _png((10, 200, 10)))

    assert _status(project_dir, _STORYBOARD_KEY, _STORYBOARD) is ArtifactStatus.CURRENT


@pytest.mark.parametrize("source", ["embedded", "mcp"])
async def test_missing_only_batch_does_not_regenerate_an_uploaded_storyboard(
    tmp_path, monkeypatch, session_factory, source: Literal["embedded", "mcp"]
):
    client, pm, project_dir = _client(monkeypatch, tmp_path, image_prompt="", characters=["Alice"])
    with client:
        _upload(client, "storyboard", "board.png", _png((10, 10, 200)))
    _edit_segment(pm, image_prompt="黄昏的街角")

    queue = GenerationQueue(session_factory=session_factory, project_manager=pm)
    services = Services(pm, WorkflowPlanner(pm), ConfigResolver(session_factory), queue)

    async def unreachable_batch(**_kwargs):
        raise AssertionError("上传分镜图不该进入付费生成")

    outcome = await generate_storyboards(
        ToolRequest(GenerateStoryboardsRequest(script="episode_1.json")),
        ProjectScope("demo", tmp_path),
        CallerContext(DEFAULT_USER_ID, source, batch_waiter=unreachable_batch),
        services,
    )
    assert outcome.problem is None
    assert outcome.value is not None
    result = (
        outcome.value.generation_result
        if isinstance(outcome.value, GenerationBatchReadModel)
        else outcome.value["generation_result"]
    )
    assert result is not None
    assert result.requested == []
    assert [item.unit_id for item in result.skipped] == ["E1S01"]
    assert await queue.claim_next_task("image") is None
    assert _status(project_dir, _STORYBOARD_KEY, _STORYBOARD) is ArtifactStatus.CURRENT


async def test_episode_asset_batch_keeps_uploaded_storyboard_while_generating_its_missing_asset(
    tmp_path, monkeypatch, session_factory
):
    client, pm, project_dir = _client(monkeypatch, tmp_path, image_prompt="", characters=["Alice"])
    with client:
        _upload(client, "storyboard", "board.png", _png((10, 10, 200)))
    uploaded = (project_dir / _STORYBOARD).read_bytes()
    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_STORYBOARD_KEY)
    queue = GenerationQueue(session_factory=session_factory, project_manager=pm)
    await queue.acquire_or_renew_worker_lease(name="default", owner_id="worker-a", ttl_seconds=60)
    monkeypatch.setattr(asset_sheets, "get_project_manager", lambda: pm)
    monkeypatch.setattr(asset_sheets, "get_generation_queue", lambda: queue)

    response = await asset_sheets.submit_asset_sheet_batch_route(
        "demo",
        asset_sheets.AssetSheetBatchRequest(episode_id=1),
        CurrentUserInfo(id=DEFAULT_USER_ID, sub="testuser", role="admin"),
    )

    assert [member["unit_id"] for member in response["members"]] == ["character/Alice"]
    task = await queue.claim_next_task("image")
    assert task is not None
    assert (task["task_type"], task["resource_id"]) == ("character", "Alice")
    assert await queue.claim_next_task("image") is None
    assert (project_dir / _STORYBOARD).read_bytes() == uploaded
    assert ProjectArtifactManifestAdapter(project_dir).get_entry(_STORYBOARD_KEY) == entry
    assert _status(project_dir, _STORYBOARD_KEY, _STORYBOARD) is ArtifactStatus.CURRENT


def test_externally_replaced_uploaded_storyboard_returns_to_its_generation_basis(tmp_path, monkeypatch):
    client, _pm, project_dir = _client(monkeypatch, tmp_path)
    with client:
        _upload(client, "storyboard", "board.png", _png((90, 90, 10)))

    (project_dir / _STORYBOARD).write_bytes(_png((1, 2, 3)))

    assert _status(project_dir, _STORYBOARD_KEY, _STORYBOARD) is ArtifactStatus.STALE


def test_restoring_an_uploaded_storyboard_claims_it_by_the_upload_again(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path, image_prompt="")
    first = _png((200, 200, 10))
    with client:
        _upload(client, "storyboard", "first.png", first)
        _upload(client, "storyboard", "second.png", _png((10, 200, 200)))
        restored = client.post("/api/v1/projects/demo/versions/storyboards/E1S01/restore/1")
        assert restored.status_code == 200, restored.text

    assert (project_dir / _STORYBOARD).read_bytes() == first
    _edit_segment(pm, image_prompt="黄昏的街角")
    assert _status(project_dir, _STORYBOARD_KEY, _STORYBOARD) is ArtifactStatus.CURRENT


def _reference_client(monkeypatch, tmp_path: Path) -> tuple[TestClient, ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    project = pm.load_project("demo")
    project["generation_mode"] = "reference_video"
    project["episodes"] = [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}]
    pm.save_project("demo", project)
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_units": [
                {
                    "unit_id": "E1U1",
                    "shots": [{"duration": 4, "text": "t"}],
                    "references": [],
                    "duration_seconds": 4,
                    "generated_assets": {"video_clip": None, "video_uri": None, "status": "pending"},
                }
            ],
        },
        "episode_1.json",
        validate=False,
    )
    for module in (reference_videos, reference_video_tasks, generation_tasks):
        monkeypatch.setattr(module, "get_project_manager", lambda: pm)
    monkeypatch.setattr(upload_finalize, "extract_video_thumbnail", _fake_thumbnail)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(reference_videos.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), pm, pm.get_project_path("demo")


def test_uploaded_reference_video_is_registered_current_and_unit_edits_do_not_stale_it(tmp_path, monkeypatch):
    client, pm, project_dir = _reference_client(monkeypatch, tmp_path)
    with client:
        response = client.post(
            "/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/upload-video",
            files={"file": ("clip.mp4", BytesIO(b"\x00" * 256), "application/octet-stream")},
        )
        assert response.status_code == 200, response.text

    key = ArtifactKey.episode_video(1, "E1U1")
    path = "reference_videos/E1U1.mp4"
    assert _status(project_dir, key, path) is ArtifactStatus.CURRENT

    with pm.locked_script("demo", "episode_1.json", validate=False) as script:
        script["video_units"][0]["shots"] = [{"duration": 4, "text": "改写后的镜头"}]
    assert _status(project_dir, key, path) is ArtifactStatus.CURRENT
