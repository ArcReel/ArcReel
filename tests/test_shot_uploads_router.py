"""镜头级分镜图/视频上传路由 + 参考单元视频上传端点测试。"""

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.project_change_hints import get_project_change_source
from lib.project_manager import ProjectManager
from lib.version_manager import VersionManager
from server.auth import CurrentUserInfo, get_current_user
from server.routers import reference_videos, shot_uploads
from server.services import generation_tasks, reference_video_tasks, upload_finalize


def _img_bytes(fmt="JPEG", size=(8, 8)):
    image = Image.new("RGB", size, (255, 0, 0))
    buf = BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def _seed_shot_project(tmp_path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "novel_text": "t",
                    "duration_seconds": 5,
                    "generated_assets": {
                        "storyboard_image": None,
                        "video_clip": None,
                        "video_uri": None,
                        "video_thumbnail": None,
                        "grid_id": None,
                        "grid_cell_index": None,
                        "status": "pending",
                    },
                },
            ],
        },
        "episode_1.json",
        validate=False,
    )
    return pm


def _client(monkeypatch, tmp_path):
    pm = _seed_shot_project(tmp_path)
    monkeypatch.setattr(shot_uploads, "get_project_manager", lambda: pm)
    monkeypatch.setattr(upload_finalize, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(shot_uploads.router, prefix="/api/v1")
    return TestClient(app), pm


def _upload(client, kind: str, filename: str, content: bytes, shot_id="E1S01", script_file="episode_1.json"):
    return client.post(
        f"/api/v1/projects/demo/shots/{shot_id}/upload/{kind}?script_file={script_file}",
        files={"file": (filename, BytesIO(content), "application/octet-stream")},
    )


class TestShotStoryboardUpload:
    def test_upload_updates_metadata_versions_and_fingerprints(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload(client, "storyboard", "board.jpg", _img_bytes("JPEG"))
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["path"] == "storyboards/scene_E1S01.png"
            assert body["version"] == 1
            assert "storyboards/scene_E1S01.png" in body["asset_fingerprints"]

        # 元数据回写 + status 自动推导
        script = pm.load_script("demo", "episode_1.json")
        ga = script["segments"][0]["generated_assets"]
        assert ga["storyboard_image"] == "storyboards/scene_E1S01.png"
        assert ga["status"] == "storyboard_ready"

        # JPEG 入参也统一落盘为 PNG（canonical 扩展名）
        target = pm.get_project_path("demo") / "storyboards" / "scene_E1S01.png"
        with Image.open(target) as img:
            assert img.format == "PNG"

        # 版本记录带 manual_upload 来源标记
        vm = VersionManager(pm.get_project_path("demo"))
        info = vm.get_versions("storyboards", "E1S01")
        assert info["current_version"] == 1
        assert info["versions"][0]["source"] == "manual_upload"
        assert info["versions"][0]["prompt"] == ""
        assert info["versions"][0]["original_filename"] == "board.jpg"

    def test_upload_backfills_untracked_existing_file(self, tmp_path, monkeypatch):
        """磁盘已有旧分镜但无版本记录：上传前补登旧文件，旧字节不丢失。"""
        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        old = project_path / "storyboards" / "scene_E1S01.png"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(_img_bytes("PNG"))

        with client:
            resp = _upload(client, "storyboard", "new.png", _img_bytes("PNG", size=(16, 16)))
            assert resp.status_code == 200, resp.text
            assert resp.json()["version"] == 2

        vm = VersionManager(project_path)
        info = vm.get_versions("storyboards", "E1S01")
        assert info["current_version"] == 2
        assert len(info["versions"]) == 2
        assert info["versions"][0].get("source") is None  # 补登的旧文件
        assert info["versions"][1]["source"] == "manual_upload"

    def test_upload_resizes_long_edge(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload(client, "storyboard", "big.jpg", _img_bytes("JPEG", size=(4096, 2048)))
            assert resp.status_code == 200, resp.text

        target = pm.get_project_path("demo") / "storyboards" / "scene_E1S01.png"
        with Image.open(target) as img:
            assert max(img.size) <= 2048

    def test_grid_fields_untouched(self, tmp_path, monkeypatch):
        """宫格模式按镜头上传单元格图：grid_id / grid_cell_index 保持不变。"""
        client, pm = _client(monkeypatch, tmp_path)
        pm.update_scene_asset("demo", "episode_1.json", "E1S01", "grid_id", "grid_abc")
        pm.update_scene_asset("demo", "episode_1.json", "E1S01", "grid_cell_index", 2)

        with client:
            resp = _upload(client, "storyboard", "cell.png", _img_bytes("PNG"))
            assert resp.status_code == 200, resp.text

        ga = pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
        assert ga["grid_id"] == "grid_abc"
        assert ga["grid_cell_index"] == 2
        assert ga["storyboard_image"] == "storyboards/scene_E1S01.png"

    def test_invalid_image_rejected(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload(client, "storyboard", "bad.png", b"not-an-image")
            assert resp.status_code == 400

    def test_bad_extension_rejected(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload(client, "storyboard", "anim.gif", b"gif")
            assert resp.status_code == 400

    def test_unknown_shot_404(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload(client, "storyboard", "x.png", _img_bytes("PNG"), shot_id="E9S99")
            assert resp.status_code == 404

    def test_reference_script_guarded_404(self, tmp_path, monkeypatch):
        """reference_video 剧本无分镜概念，镜头上传一律 404。"""
        client, pm = _client(monkeypatch, tmp_path)
        pm.save_script(
            "demo",
            {
                "episode": 2,
                "title": "E2",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "video_units": [{"unit_id": "E2U1", "generated_assets": {"status": "pending"}}],
            },
            "episode_2.json",
            validate=False,
        )
        with client:
            resp = _upload(
                client, "storyboard", "x.png", _img_bytes("PNG"), shot_id="E2U1", script_file="episode_2.json"
            )
            assert resp.status_code == 404

    def test_emit_uses_webui_source(self, tmp_path, monkeypatch):
        """emit 在 project_change_source("webui") 上下文内被调用（SSE source 由 contextvar 决定）。"""
        client, _ = _client(monkeypatch, tmp_path)
        calls: list[dict] = []

        def _fake_emit(**kw):
            calls.append({**kw, "resolved_source": get_project_change_source()})
            return {}

        monkeypatch.setattr(shot_uploads, "emit_generation_success_batch", _fake_emit)
        with client:
            resp = _upload(client, "storyboard", "x.png", _img_bytes("PNG"))
            assert resp.status_code == 200

        assert len(calls) == 1
        assert calls[0]["resolved_source"] == "webui"
        assert calls[0]["task_type"] == "storyboard"
        assert calls[0]["payload"]["script_file"] == "episode_1.json"

    def test_missing_script_404_does_not_leak_server_path(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload(client, "storyboard", "x.png", _img_bytes("PNG"), script_file="nope.json")
            assert resp.status_code == 404
            assert str(tmp_path) not in resp.json()["detail"]


class TestShotVideoUpload:
    def test_upload_finalizes_and_clears_stale_video_uri(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        pm.update_scene_asset("demo", "episode_1.json", "E1S01", "video_uri", "https://stale-provider-uri")

        async def _fake_thumbnail(video_path: Path, thumbnail_path: Path):
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            thumbnail_path.write_bytes(b"jpg")
            return thumbnail_path

        monkeypatch.setattr(upload_finalize, "extract_video_thumbnail", _fake_thumbnail)

        with client:
            resp = _upload(client, "video", "clip.mp4", b"\x00" * 1024)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["path"] == "videos/scene_E1S01.mp4"
            assert body["version"] == 1
            assert "videos/scene_E1S01.mp4" in body["asset_fingerprints"]
            assert "thumbnails/scene_E1S01.jpg" in body["asset_fingerprints"]

        ga = pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
        assert ga["video_clip"] == "videos/scene_E1S01.mp4"
        assert ga["video_uri"] is None
        assert ga["video_thumbnail"] == "thumbnails/scene_E1S01.jpg"
        assert ga["status"] == "completed"

        vm = VersionManager(pm.get_project_path("demo"))
        info = vm.get_versions("videos", "E1S01")
        assert info["versions"][0]["source"] == "manual_upload"

    def test_thumbnail_failure_degrades_gracefully(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        async def _fail_thumbnail(video_path: Path, thumbnail_path: Path):
            return None

        monkeypatch.setattr(upload_finalize, "extract_video_thumbnail", _fail_thumbnail)

        with client:
            resp = _upload(client, "video", "clip.mp4", b"\x00" * 64)
            assert resp.status_code == 200, resp.text

        ga = pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
        assert ga["video_clip"] == "videos/scene_E1S01.mp4"
        assert ga["video_thumbnail"] is None
        assert ga["status"] == "completed"

    def test_bad_extension_rejected(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload(client, "video", "clip.avi", b"\x00" * 16)
            assert resp.status_code == 400

    def test_webm_rejected(self, tmp_path, monkeypatch):
        """webm 明确不收：字节按 canonical .mp4 存储，VP8/VP9 在 Safari/剪映侧不可解码"""
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload(client, "video", "clip.webm", b"\x00" * 16)
            assert resp.status_code == 400

    def test_oversized_upload_413(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        monkeypatch.setattr(upload_finalize, "UPLOAD_VIDEO_MAX_BYTES", 32)
        with client:
            resp = _upload(client, "video", "clip.mp4", b"\x00" * 64)
            assert resp.status_code == 413


class TestSaveUploadedVideoStream:
    def test_oversize_raises_and_cleans_tmp(self, tmp_path):
        target = tmp_path / "videos" / "scene_X.mp4"
        with pytest.raises(upload_finalize.UploadTooLargeError):
            asyncio.run(upload_finalize.save_uploaded_video_stream(BytesIO(b"\x00" * 64), target, max_bytes=16))
        assert not target.exists()
        assert list(target.parent.iterdir()) == []  # dot-tmp 已清理

    def test_writes_via_tmp_then_replace(self, tmp_path):
        target = tmp_path / "videos" / "scene_X.mp4"
        asyncio.run(upload_finalize.save_uploaded_video_stream(BytesIO(b"abc"), target, max_bytes=16))
        assert target.read_bytes() == b"abc"
        assert list(target.parent.iterdir()) == [target]

    def test_tmp_paths_unique_per_call(self, tmp_path):
        """tmp 文件名每次调用唯一：并发上传同一目标不会交错写同一个 tmp 文件。"""
        target = tmp_path / "videos" / "scene_X.mp4"
        assert upload_finalize._upload_tmp_path(target) != upload_finalize._upload_tmp_path(target)


# ==================== 参考单元视频上传 ====================


def _seed_reference_project(tmp_path) -> ProjectManager:
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
                    "generated_assets": {
                        "video_clip": None,
                        "video_uri": "https://stale",
                        "status": "pending",
                    },
                }
            ],
        },
        "episode_1.json",
        validate=False,
    )
    return pm


def _ref_client(monkeypatch, tmp_path):
    pm = _seed_reference_project(tmp_path)
    monkeypatch.setattr(reference_videos, "get_project_manager", lambda: pm)
    monkeypatch.setattr(reference_video_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)

    async def _fake_thumbnail(video_path: Path, thumbnail_path: Path):
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        thumbnail_path.write_bytes(b"jpg")
        return thumbnail_path

    monkeypatch.setattr(reference_video_tasks, "extract_video_thumbnail", _fake_thumbnail)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(reference_videos.router, prefix="/api/v1")
    return TestClient(app), pm


def _upload_unit(client, unit_id="E1U1", filename="clip.mp4", content=b"\x00" * 256, episode=1):
    return client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/{episode}/units/{unit_id}/upload-video",
        files={"file": (filename, BytesIO(content), "application/octet-stream")},
    )


class TestReferenceUnitVideoUpload:
    def test_upload_finalizes_unit(self, tmp_path, monkeypatch):
        client, pm = _ref_client(monkeypatch, tmp_path)
        with client:
            resp = _upload_unit(client)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["path"] == "reference_videos/E1U1.mp4"
            assert body["version"] == 1
            assert "reference_videos/E1U1.mp4" in body["asset_fingerprints"]

        ga = pm.load_script("demo", "episode_1.json")["video_units"][0]["generated_assets"]
        assert ga["video_clip"] == "reference_videos/E1U1.mp4"
        # 上传替换后旧 provider URI 不再对应本地文件，finalize 应清空
        assert "video_uri" not in ga
        assert ga["video_thumbnail"] == "reference_videos/thumbnails/E1U1.jpg"
        assert ga["status"] == "completed"

        vm = VersionManager(pm.get_project_path("demo"))
        info = vm.get_versions("reference_videos", "E1U1")
        assert info["current_version"] == 1
        assert info["versions"][0]["source"] == "manual_upload"

    def test_unit_not_found_404(self, tmp_path, monkeypatch):
        client, _ = _ref_client(monkeypatch, tmp_path)
        with client:
            resp = _upload_unit(client, unit_id="E1U9")
            assert resp.status_code == 404

    def test_non_reference_mode_409(self, tmp_path, monkeypatch):
        client, pm = _ref_client(monkeypatch, tmp_path)
        project = pm.load_project("demo")
        project["generation_mode"] = "storyboard"
        pm.save_project("demo", project)
        with client:
            resp = _upload_unit(client)
            assert resp.status_code == 409

    def test_bad_extension_rejected(self, tmp_path, monkeypatch):
        client, _ = _ref_client(monkeypatch, tmp_path)
        with client:
            resp = _upload_unit(client, filename="clip.avi")
            assert resp.status_code == 400

    def test_emit_uses_webui_source(self, tmp_path, monkeypatch):
        """emit 在 project_change_source("webui") 上下文内被调用（SSE source 由 contextvar 决定）。"""
        client, _ = _ref_client(monkeypatch, tmp_path)
        calls: list[dict] = []

        def _fake_emit(**kw):
            calls.append({**kw, "resolved_source": get_project_change_source()})
            return {}

        monkeypatch.setattr(reference_videos, "emit_generation_success_batch", _fake_emit)
        with client:
            resp = _upload_unit(client)
            assert resp.status_code == 200

        assert len(calls) == 1
        assert calls[0]["resolved_source"] == "webui"
        assert calls[0]["task_type"] == "reference_video"
