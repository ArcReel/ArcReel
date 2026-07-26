"""镜头尾帧设置/清除端点测试。

覆盖两条设置通道（上传 / 项目内选图）落到同一快照路径、换图原地覆盖、清除语义、
越界与缺失拒绝，以及快照与源图的解耦（源图被改写/删除不影响已定尾帧）。
"""

import asyncio
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.data_validator import DataValidator
from lib.project_manager import ProjectManager
from lib.script_editor import ScriptEditError
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import end_frames
from server.services import end_frame as end_frame_service
from server.services import upload_finalize

END_FRAME_REL = "end_frames/scene_E1S01.png"


def _img_bytes(fmt="JPEG", size=(8, 8), color=(255, 0, 0)):
    image = Image.new("RGB", size, color)
    buf = BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def _seed_project(tmp_path) -> ProjectManager:
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
                    "generated_assets": {"status": "pending"},
                },
                {
                    "segment_id": "E1S02",
                    "novel_text": "t2",
                    "duration_seconds": 5,
                    "generated_assets": {"status": "pending"},
                },
            ],
        },
        "episode_1.json",
        validate=False,
    )
    return pm


@pytest.fixture
def client(tmp_path, monkeypatch):
    pm = _seed_project(tmp_path)
    monkeypatch.setattr(end_frame_service, "get_project_manager", lambda: pm)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(end_frames.router, prefix="/api/v1")
    return TestClient(app), pm


def _upload(client, content: bytes, filename="frame.jpg", shot_id="E1S01"):
    return client.post(
        f"/api/v1/projects/demo/shots/{shot_id}/end-frame/upload?script_file=episode_1.json",
        files={"file": (filename, BytesIO(content), "application/octet-stream")},
    )


def _select(client, source_path: str, shot_id="E1S01"):
    return client.post(
        f"/api/v1/projects/demo/shots/{shot_id}/end-frame/select",
        json={"script_file": "episode_1.json", "source_path": source_path},
    )


def _delete(client, shot_id="E1S01"):
    return client.delete(f"/api/v1/projects/demo/shots/{shot_id}/end-frame?script_file=episode_1.json")


def _segment(pm: ProjectManager, index: int = 0) -> dict:
    return pm.load_script("demo", "episode_1.json")["segments"][index]


def _write_source_image(pm: ProjectManager, rel: str, content: bytes) -> None:
    target = pm.get_project_path("demo") / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


class TestUploadChannel:
    def test_upload_writes_normalized_png_snapshot_and_field(self, client):
        c, pm = client
        resp = _upload(c, _img_bytes("JPEG"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["end_frame_image"] == END_FRAME_REL

        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        # JPEG 入参统一归一为 PNG（与分镜图上传同口径）
        with Image.open(pm.get_project_path("demo") / END_FRAME_REL) as img:
            assert img.format == "PNG"

    def test_replacing_overwrites_in_place(self, client):
        c, pm = client
        _upload(c, _img_bytes("JPEG", size=(8, 8)))
        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 200, resp.text
        # 路径固定，字段值不变；文件内容被换掉
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        with Image.open(pm.get_project_path("demo") / END_FRAME_REL) as img:
            assert img.size == (16, 16)

    def test_undecodable_bytes_rejected(self, client):
        c, pm = client
        resp = _upload(c, b"not an image", filename="frame.png")
        assert resp.status_code == 400
        assert _segment(pm).get("end_frame_image") is None

    def test_unsupported_extension_rejected(self, client):
        c, _pm = client
        assert _upload(c, _img_bytes("PNG"), filename="frame.gif").status_code == 400

    def test_unknown_shot_returns_404(self, client):
        c, _pm = client
        assert _upload(c, _img_bytes("PNG"), shot_id="E9S99").status_code == 404


class TestSelectChannel:
    def test_select_project_image_snapshots_to_end_frames(self, client):
        c, pm = client
        _write_source_image(pm, "storyboards/scene_E1S02.png", _img_bytes("PNG", size=(12, 12)))

        resp = _select(c, "storyboards/scene_E1S02.png")
        assert resp.status_code == 200, resp.text
        assert resp.json()["end_frame_image"] == END_FRAME_REL
        # 字段存的是快照路径，不是源图路径 —— 引用关系从结构上就不存在
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        with Image.open(pm.get_project_path("demo") / END_FRAME_REL) as img:
            assert img.format == "PNG"
            assert img.size == (12, 12)

    def test_traversal_path_rejected(self, client):
        c, pm = client
        resp = _select(c, "../../../etc/passwd")
        assert resp.status_code == 400
        assert _segment(pm).get("end_frame_image") is None

    def test_missing_source_returns_404(self, client):
        c, _pm = client
        assert _select(c, "storyboards/scene_E1S99.png").status_code == 404


class TestClear:
    def test_clear_deletes_snapshot_and_nulls_field(self, client):
        c, pm = client
        _upload(c, _img_bytes("PNG"))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        assert snapshot.exists()

        resp = _delete(c)
        assert resp.status_code == 200, resp.text
        assert _segment(pm)["end_frame_image"] is None
        assert not snapshot.exists()

    def test_clear_without_end_frame_is_idempotent(self, client):
        c, pm = client
        assert _delete(c).status_code == 200
        assert _segment(pm)["end_frame_image"] is None

    def test_clear_unknown_shot_returns_404(self, client):
        c, _pm = client
        assert _delete(c, shot_id="E9S99").status_code == 404


class TestSnapshotDecoupling:
    """快照与源图彻底解耦：源图后续被改写或删除都动不到已定尾帧。"""

    def test_source_rewrite_and_delete_leave_end_frame_intact(self, client):
        c, pm = client
        source_rel = "storyboards/scene_E1S02.png"
        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(12, 12), color=(255, 0, 0)))
        _select(c, source_rel)

        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        # 源图重生成（内容与尺寸都变）后再删除，模拟版本回滚 / 资源清理
        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(24, 24), color=(0, 0, 255)))
        (pm.get_project_path("demo") / source_rel).unlink()

        assert snapshot.read_bytes() == before
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL

    def test_per_shot_snapshots_are_independent(self, client):
        c, pm = client
        _upload(c, _img_bytes("PNG", size=(8, 8)), shot_id="E1S01")
        _upload(c, _img_bytes("PNG", size=(16, 16)), shot_id="E1S02")

        assert _segment(pm, 0)["end_frame_image"] == "end_frames/scene_E1S01.png"
        assert _segment(pm, 1)["end_frame_image"] == "end_frames/scene_E1S02.png"

        # 清一个不影响另一个
        _delete(c, shot_id="E1S01")
        assert _segment(pm, 0)["end_frame_image"] is None
        assert _segment(pm, 1)["end_frame_image"] == "end_frames/scene_E1S02.png"
        assert (pm.get_project_path("demo") / "end_frames/scene_E1S02.png").exists()


class TestErrorMapping:
    """领域错误到 HTTP 的映射：三个端点行为一致，不泄漏服务器路径。"""

    def test_unknown_script_file_returns_404(self, client):
        c, _pm = client
        assert (
            c.post(
                "/api/v1/projects/demo/shots/E1S01/end-frame/upload?script_file=missing.json",
                files={"file": ("f.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
            ).status_code
            == 404
        )
        assert (
            c.post(
                "/api/v1/projects/demo/shots/E1S01/end-frame/select",
                json={"script_file": "missing.json", "source_path": "storyboards/x.png"},
            ).status_code
            == 404
        )
        assert c.delete("/api/v1/projects/demo/shots/E1S01/end-frame?script_file=missing.json").status_code == 404

    def test_unknown_project_returns_404(self, client):
        c, _pm = client
        resp = c.post(
            "/api/v1/projects/nope/shots/E1S01/end-frame/upload?script_file=episode_1.json",
            files={"file": ("f.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
        )
        assert resp.status_code == 404

    def test_empty_source_path_rejected(self, client):
        c, pm = client
        assert _select(c, "   ").status_code == 400
        assert _segment(pm).get("end_frame_image") is None

    def test_oversized_upload_rejected(self, client, monkeypatch):
        c, pm = client
        monkeypatch.setattr(upload_finalize, "UPLOAD_IMAGE_MAX_BYTES", 16)
        resp = _upload(c, _img_bytes("PNG", size=(64, 64)))
        assert resp.status_code == 413
        assert _segment(pm).get("end_frame_image") is None

    def test_traversal_shot_id_rejected_before_write(self, client):
        """剧本里存在越界形状的镜头 id 时，快照路径解析必须拒绝而非写出项目目录。"""
        _c, pm = client
        script = pm.load_script("demo", "episode_1.json")
        script["segments"][0]["segment_id"] = "../../../../evil"
        pm.save_script("demo", script, "episode_1.json", validate=False)

        with pytest.raises(end_frame_service.EndFrameError) as exc:
            asyncio.run(
                end_frame_service.set_end_frame_from_bytes(
                    project_name="demo",
                    script_file="episode_1.json",
                    shot_id="../../../../evil",
                    content=_img_bytes("PNG"),
                )
            )
        assert exc.value.key == "invalid_resource_id"

    def test_body_exceeding_limit_rejected_after_read(self, client, monkeypatch):
        """Content-Length 缺失/被绕过时，按实际读入字节数兜底拒绝。"""
        c, pm = client
        monkeypatch.setattr(end_frames, "validate_upload", lambda *a, **k: 16)
        assert _upload(c, _img_bytes("PNG", size=(64, 64))).status_code == 413
        assert _segment(pm).get("end_frame_image") is None

    def test_script_edit_error_maps_to_400(self, client, monkeypatch):
        c, _pm = client

        async def _boom(**_kwargs):
            raise ScriptEditError("坏掉的剧本结构")

        monkeypatch.setattr(end_frames, "set_end_frame_from_bytes", _boom)
        assert _upload(c, _img_bytes("PNG")).status_code == 400

    def test_unexpected_error_maps_to_500_without_internals(self, client, monkeypatch):
        c, _pm = client

        async def _boom(**_kwargs):
            raise RuntimeError("/absolute/server/path/leaked")

        monkeypatch.setattr(end_frames, "set_end_frame_from_bytes", _boom)
        resp = _upload(c, _img_bytes("PNG"))
        assert resp.status_code == 500
        assert "leaked" not in resp.text


class TestValidatorAcceptsWrittenSnapshot:
    def test_written_project_passes_tree_validation(self, client):
        c, pm = client
        _upload(c, _img_bytes("PNG"))
        # end_frames 已登记为允许的项目根目录条目，不被判为未知目录
        assert "end_frames" in DataValidator.ALLOWED_ROOT_ENTRIES
        result = DataValidator(projects_root=str(pm.projects_root)).validate_project_tree("demo")
        assert not [e for e in result.errors if "end_frames" in e]
