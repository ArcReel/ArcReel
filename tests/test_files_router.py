import dataclasses
import json
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.i18n.en import assets as en_assets
from lib.i18n.vi import assets as vi_assets
from lib.i18n.zh import assets as zh_assets
from lib.i18n.zh import errors as zh_errors
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import files
from tests.conftest import _wav_bytes


class _FakeTextBackend:
    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    @property
    def capabilities(self):
        return set()

    async def generate(self, request):
        from lib.text_backends.base import TextGenerationResult

        return TextGenerationResult(text="cinematic, high contrast", provider="fake", model="fake-model")


async def _fake_create_backend(*args, **kwargs):
    return _FakeTextBackend(), "fake"


def _img_bytes(fmt="JPEG"):
    image = Image.new("RGB", (8, 8), (255, 0, 0))
    buf = BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def _client(monkeypatch, tmp_path):
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "Alice", "desc")
    pm.add_prop("demo", "玉佩", "古玉")
    pm.add_product("demo", "保温杯", "不锈钢保温杯")

    monkeypatch.setattr(files, "get_project_manager", lambda: pm)
    monkeypatch.setattr("lib.text_generator.create_text_backend_for_task", _fake_create_backend)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(files.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(files.public_router, prefix="/api/v1")
    return TestClient(app), pm


class TestFilesRouter:
    def test_source_and_file_endpoints(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)

        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("chapter.txt", "hello", "text/plain")},
            )
            assert upload.status_code == 200
            path = upload.json()["path"]
            assert path == "source/chapter.txt"

            listed = client.get("/api/v1/projects/demo/files")
            assert listed.status_code == 200
            assert any(item["name"] == "chapter.txt" for item in listed.json()["files"]["source"])

            served = client.get("/api/v1/files/demo/source/chapter.txt")
            assert served.status_code == 200
            assert served.text == "hello"

            get_source = client.get("/api/v1/projects/demo/source/chapter.txt")
            assert get_source.status_code == 200
            assert get_source.text == "hello"

            update_source = client.put(
                "/api/v1/projects/demo/source/chapter.txt",
                content="updated",
                headers={"content-type": "text/plain"},
            )
            assert update_source.status_code == 200

            delete_source = client.delete("/api/v1/projects/demo/source/chapter.txt")
            assert delete_source.status_code == 200

            missing = client.get("/api/v1/projects/demo/source/missing.txt")
            assert missing.status_code == 404

    def test_source_upload_race_project_deleted_reports_project_not_found(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)

        class _RaceLoader:
            @staticmethod
            def load(*args, **kwargs):
                shutil.rmtree(tmp_path / "projects" / "demo")
                raise FileNotFoundError("/server/projects/demo/source gone")

        monkeypatch.setattr(files, "SourceLoader", _RaceLoader)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("chapter.txt", BytesIO(b"hello"), "text/plain")},
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == zh_errors.MESSAGES["project_not_found"].format(name="demo")

    def test_source_upload_loader_file_missing_with_project_intact_stays_generic(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)

        class _BrokenLoader:
            @staticmethod
            def load(*args, **kwargs):
                raise FileNotFoundError("upload-tmp gone")

        monkeypatch.setattr(files, "SourceLoader", _BrokenLoader)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("chapter.txt", BytesIO(b"hello"), "text/plain")},
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == zh_errors.MESSAGES["resource_not_found"]

    def test_upload_assets_and_drafts(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        with client:
            character = client.post(
                "/api/v1/projects/demo/upload/character?name=Alice",
                files={"file": ("alice.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert character.status_code == 200
            assert character.json()["path"] == "characters/Alice.jpg"

            character_ref = client.post(
                "/api/v1/projects/demo/upload/character_ref?name=Alice",
                files={"file": ("alice_ref.webp", _img_bytes("WEBP"), "image/webp")},
            )
            assert character_ref.status_code == 200
            assert character_ref.json()["path"] == "characters/refs/Alice.webp"

            clue = client.post(
                "/api/v1/projects/demo/upload/prop?name=玉佩",
                files={"file": ("prop.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert clue.status_code == 200
            assert clue.json()["path"] == "props/玉佩.jpg"

            # 分镜/视频上传走 shot_uploads 路由，通用上传不再支持 storyboard 类型
            legacy_storyboard = client.post(
                "/api/v1/projects/demo/upload/storyboard?name=E1S01",
                files={"file": ("storyboard.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert legacy_storyboard.status_code == 400

            invalid_ext = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("bad.exe", b"x", "application/octet-stream")},
            )
            assert invalid_ext.status_code == 400

            bad_type = client.post(
                "/api/v1/projects/demo/upload/unknown",
                files={"file": ("x.txt", b"x", "text/plain")},
            )
            assert bad_type.status_code == 400

            # 无效图片格式仍应被拒绝（即使小于 2MB）
            bad_image = client.post(
                "/api/v1/projects/demo/upload/character?name=Alice",
                files={"file": ("bad.png", b"not-image", "image/png")},
            )
            assert bad_image.status_code == 400

            # drafts API
            update_draft = client.put(
                "/api/v1/projects/demo/drafts/1/step1",
                content="draft content",
                headers={"content-type": "text/plain"},
            )
            assert update_draft.status_code == 200

            get_draft = client.get("/api/v1/projects/demo/drafts/1/step1")
            assert get_draft.status_code == 200
            assert "draft content" in get_draft.text

            bad_step = client.get("/api/v1/projects/demo/drafts/1/step99")
            assert bad_step.status_code == 400

            delete_draft = client.delete("/api/v1/projects/demo/drafts/1/step1")
            assert delete_draft.status_code == 200

            missing_draft = client.get("/api/v1/projects/demo/drafts/1/step1")
            assert missing_draft.status_code == 404

            # confirm metadata updated for character/prop
            project = pm.load_project("demo")
            assert project["characters"]["Alice"]["character_sheet"] == "characters/Alice.jpg"
            assert project["characters"]["Alice"]["reference_image"] == "characters/refs/Alice.webp"
            assert project["props"]["玉佩"]["prop_sheet"] == "props/玉佩.jpg"

    def test_character_audio_ref_upload_success(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", _wav_bytes(3), "audio/wav")},
            )
            assert resp.status_code == 200
            assert resp.json()["path"] == "characters/refs_audio/Alice.wav"
            project = pm.load_project("demo")
            assert project["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.wav"

    def test_character_audio_ref_rejects_bad_extension(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.m4a", b"noop", "audio/mp4")},
            )
            assert resp.status_code == 400
            assert "音频类型" in resp.json()["detail"] or "audio type" in resp.json()["detail"].lower()

    def test_character_audio_ref_rejects_oversized(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        oversized = b"\x00" * (files.AUDIO_REFERENCE_MAX_BYTES + 1)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", oversized, "audio/wav")},
            )
            assert resp.status_code == 400
            assert resp.json()["detail"] == zh_errors.MESSAGES["upload_too_large"].format(max_mb=15)

    def test_character_audio_ref_rejects_duration_out_of_range(self, tmp_path, monkeypatch):
        import shutil as _shutil

        if _shutil.which("ffprobe") is None:
            import pytest

            pytest.skip("ffprobe not available")

        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", _wav_bytes(1), "audio/wav")},
            )
            assert resp.status_code == 400
            assert resp.json()["detail"] == zh_errors.MESSAGES["audio_duration_out_of_range"].format(
                min_seconds=2, max_seconds=10
            )
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio", "") == ""

    def test_character_audio_ref_replace_removes_old_extension(self, tmp_path, monkeypatch):
        """替换参考音频且新旧扩展名不同（wav -> mp3）时旧文件应被清理，不留孤儿。"""
        client, pm = _client(monkeypatch, tmp_path)

        async def _fake_duration(content, suffix):
            return 3.0

        monkeypatch.setattr(files, "probe_audio_duration_seconds", _fake_duration)

        with client:
            first = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v1.wav", b"fake-wav-bytes", "audio/wav")},
            )
            assert first.status_code == 200
            project_dir = pm.get_project_path("demo")
            old_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            assert old_path.exists()

            second = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v2.mp3", b"fake-mp3-bytes", "audio/mpeg")},
            )
            assert second.status_code == 200
            assert second.json()["path"] == "characters/refs_audio/Alice.mp3"
            assert not old_path.exists()
            new_path = project_dir / "characters" / "refs_audio" / "Alice.mp3"
            assert new_path.exists()

    def test_character_audio_ref_replace_succeeds_when_stale_cleanup_fails(self, tmp_path, monkeypatch):
        """替换成功但旧文件物理删除失败（权限/IO 错误）时，请求仍应成功——新文件与字段已提交，
        不应因清理失败误报整次替换失败并诱导重试（重试时旧文件已找不到指针，成为孤儿）。"""
        client, pm = _client(monkeypatch, tmp_path)

        async def _fake_duration(content, suffix):
            return 3.0

        monkeypatch.setattr(files, "probe_audio_duration_seconds", _fake_duration)

        with client:
            first = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v1.wav", b"fake-wav-bytes", "audio/wav")},
            )
            assert first.status_code == 200
            project_dir = pm.get_project_path("demo")
            old_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            assert old_path.exists()

            original_unlink = Path.unlink

            def _boom(self, *args, **kwargs):
                if self == old_path:
                    raise PermissionError("simulated unlink failure")
                return original_unlink(self, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", _boom)

            second = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v2.mp3", b"fake-mp3-bytes", "audio/mpeg")},
            )
            assert second.status_code == 200
            assert second.json()["path"] == "characters/refs_audio/Alice.mp3"
            new_path = project_dir / "characters" / "refs_audio" / "Alice.mp3"
            assert new_path.exists()
            assert old_path.exists()  # 清理失败，旧文件残留（已记告警日志），但不影响本次请求成功
            assert (
                pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.mp3"
            )

    def test_delete_character_reference_audio(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", _wav_bytes(3), "audio/wav")},
            )
            assert upload.status_code == 200
            project_dir = pm.get_project_path("demo")
            audio_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            assert audio_path.exists()

            delete = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert delete.status_code == 200
            assert not audio_path.exists()
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio") == ""

    def test_delete_character_reference_audio_preserves_pointer_on_unlink_failure(self, tmp_path, monkeypatch):
        """物理删除失败（权限/IO 错误，含 Windows 文件占用）时保留字段指针，允许重试发现并清理该文件。"""
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", _wav_bytes(3), "audio/wav")},
            )
            assert upload.status_code == 200
            project_dir = pm.get_project_path("demo")
            audio_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            assert audio_path.exists()

            original_unlink = Path.unlink

            def _boom(self, *args, **kwargs):
                if self == audio_path:
                    raise PermissionError("simulated unlink failure")
                return original_unlink(self, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", _boom)

            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 500
            assert (
                pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.wav"
            )
            assert audio_path.exists()

    def test_delete_character_reference_audio_unknown_character_404(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.delete("/api/v1/projects/demo/characters/Ghost/reference-audio")
            assert resp.status_code == 404

    def test_delete_character_reference_audio_noop_when_no_audio(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 200
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio", "") == ""

    def test_delete_character_reference_audio_ignores_out_of_project_path(self, tmp_path, monkeypatch):
        """reference_audio 可经资产 PATCH 写成任意字符串；越界路径只清字段，不得删项目外文件。"""
        client, pm = _client(monkeypatch, tmp_path)
        outsider = tmp_path / "outsider.wav"
        outsider.write_bytes(b"do-not-delete")
        pm.update_character_reference_audio("demo", "Alice", f"../../{outsider.name}")

        with client:
            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 200
            assert outsider.exists()
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio") == ""

    def test_character_audio_ref_replace_ignores_out_of_project_old_path(self, tmp_path, monkeypatch):
        """替换时的旧文件清理同样受项目目录约束，越界的存量值不触发删除。"""
        client, pm = _client(monkeypatch, tmp_path)
        outsider = tmp_path / "outsider.wav"
        outsider.write_bytes(b"do-not-delete")
        pm.update_character_reference_audio("demo", "Alice", f"../../{outsider.name}")

        async def _fake_duration(content, suffix):
            return 3.0

        monkeypatch.setattr(files, "probe_audio_duration_seconds", _fake_duration)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v2.mp3", b"fake-mp3-bytes", "audio/mpeg")},
            )
            assert resp.status_code == 200
            assert outsider.exists()
            assert (
                pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.mp3"
            )

    def test_delete_character_reference_audio_ignores_path_outside_refs_audio(self, tmp_path, monkeypatch):
        """reference_audio 越权指向项目内 refs_audio 之外的文件（如 project.json）时，只清字段，不得删该文件。"""
        client, pm = _client(monkeypatch, tmp_path)
        project_json = pm.get_project_path("demo") / "project.json"
        assert project_json.exists()
        pm.update_character_reference_audio("demo", "Alice", "project.json")

        with client:
            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 200
            assert project_json.exists()
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio") == ""

    def test_character_audio_ref_replace_ignores_stale_path_outside_refs_audio(self, tmp_path, monkeypatch):
        """替换时的旧文件清理同样限定在 refs_audio 目录内，落在项目目录内其它位置的存量值不触发删除。"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")
        project_json = project_dir / "project.json"
        pm.update_character_reference_audio("demo", "Alice", "project.json")

        async def _fake_duration(content, suffix):
            return 3.0

        monkeypatch.setattr(files, "probe_audio_duration_seconds", _fake_duration)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v2.mp3", b"fake-mp3-bytes", "audio/mpeg")},
            )
            assert resp.status_code == 200
            assert project_json.exists()
            assert (
                pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.mp3"
            )

    def test_product_ref_upload_preserves_original_bytes(self, tmp_path, monkeypatch):
        """产品原图是保真验收锚点：保存管线保留原件字节，不做阈值压缩/重编码。"""
        client, pm = _client(monkeypatch, tmp_path)

        # 构造一张 >2MB 的 PNG（其他资产上传在该阈值会被压成 JPEG q85）：
        # 噪声像素不可压缩，保证体积越过阈值
        import os as _os

        image = Image.frombytes("RGB", (1200, 1200), _os.urandom(1200 * 1200 * 3))
        buf = BytesIO()
        image.save(buf, format="PNG")
        original = buf.getvalue()
        assert len(original) > 2 * 1024 * 1024

        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/product_ref?name=保温杯",
                files={"file": ("photo.png", original, "image/png")},
            )
            assert resp.status_code == 200
            path = resp.json()["path"]
            assert path.startswith("products/refs/")
            assert path.endswith(".png")

            saved = pm.get_project_path("demo") / path
            assert saved.read_bytes() == original

            project = pm.load_project("demo")
            assert project["products"]["保温杯"]["reference_images"] == [path]

    def test_product_ref_multiple_uploads_accumulate(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            paths = []
            for fname in ("front.jpg", "back.jpg"):
                resp = client.post(
                    "/api/v1/projects/demo/upload/product_ref?name=保温杯",
                    files={"file": (fname, _img_bytes("JPEG"), "image/jpeg")},
                )
                assert resp.status_code == 200
                paths.append(resp.json()["path"])

            assert len(set(paths)) == 2
            project = pm.load_project("demo")
            assert project["products"]["保温杯"]["reference_images"] == paths
            project_dir = pm.get_project_path("demo")
            for p in paths:
                assert (project_dir / p).exists()

    def test_product_ref_unknown_product_404(self, tmp_path, monkeypatch):
        """原图列表是文件的唯一指针：产品不存在时拒收，避免落下孤儿文件。"""
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/product_ref?name=不存在",
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 404
            refs_dir = pm.get_project_path("demo") / "products" / "refs"
            assert not refs_dir.exists() or not any(refs_dir.iterdir())

    def test_product_ref_invalid_image_rejected(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/product_ref?name=保温杯",
                files={"file": ("bad.png", b"not-image", "image/png")},
            )
            assert resp.status_code == 400

    def test_product_sheet_upload_updates_metadata(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/product?name=保温杯",
                files={"file": ("sheet.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 200
            assert resp.json()["path"] == "products/保温杯.jpg"
            project = pm.load_project("demo")
            assert project["products"]["保温杯"]["product_sheet"] == "products/保温杯.jpg"

    def test_list_files_includes_products(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            client.post(
                "/api/v1/projects/demo/upload/product?name=保温杯",
                files={"file": ("sheet.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            listed = client.get("/api/v1/projects/demo/files")
            assert listed.status_code == 200
            assert any(item["name"] == "保温杯.jpg" for item in listed.json()["files"]["products"])

    def test_style_image_endpoints(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        # 预置 style_template_id + 展开后的 style prompt，验证上传后被强制清掉（互斥）
        project = pm.load_project("demo")
        project["style_template_id"] = "live_premium_drama"
        project["style"] = "画风：真人电视剧风格，精品短剧画风，大师级构图"
        pm.save_project("demo", project)

        with client:
            upload_style = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert upload_style.status_code == 200
            assert upload_style.json()["style_description"] == "cinematic, high contrast"
            after = pm.load_project("demo")
            assert after.get("style_image", "").startswith("style_reference")
            assert "style_template_id" not in after
            # 互斥语义关键断言：模板展开到 style 的 prompt 也要被清空，
            # 否则生成链路会把模板 prompt 与 style_description 一起喂给 LLM。
            assert after.get("style", "") == ""

            bad_style_ext = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.gif", b"gif", "image/gif")},
            )
            assert bad_style_ext.status_code == 400

    def test_security_and_error_paths(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)

        outside = tmp_path / "projects" / "outside.txt"
        outside.write_text("outside", encoding="utf-8")

        with client:
            traverse = client.get("/api/v1/files/demo/%2E%2E/outside.txt")
            assert traverse.status_code == 403

            missing_project = client.get("/api/v1/projects/missing/files")
            assert missing_project.status_code == 404

            missing_source = client.put(
                "/api/v1/projects/missing/source/a.txt",
                content="x",
                headers={"content-type": "text/plain"},
            )
            assert missing_source.status_code == 404

    def test_upload_without_name_and_keyerror_tolerance(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            ref_no_name = client.post(
                "/api/v1/projects/demo/upload/character_ref",
                files={"file": ("no_name.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert ref_no_name.status_code == 200
            assert ref_no_name.json()["path"] == "characters/refs/no_name.jpg"

            clue_missing_entity = client.post(
                "/api/v1/projects/demo/upload/prop?name=不存在道具",
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert clue_missing_entity.status_code == 200
            assert clue_missing_entity.json()["path"] == "props/不存在道具.jpg"

            character_missing_entity = client.post(
                "/api/v1/projects/demo/upload/character?name=不存在角色",
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert character_missing_entity.status_code == 200
            assert character_missing_entity.json()["path"] == "characters/不存在角色.jpg"

    @pytest.mark.integration
    def test_upload_rejects_unsafe_name_for_every_type(self, tmp_path, monkeypatch):
        """name 会被拼进落盘路径：含分隔符 / .. / 控制字符的名字在所有上传类型下都应被 400 拒绝。"""
        client, _ = _client(monkeypatch, tmp_path)

        # 快照范围取 tmp_path 而非 projects 根：越界名的目标本就在 projects 之外，只扫项目内看不见。
        # 连同内容一起快照：越界写入若命中既有文件（如 ../project.json），路径集合不变，只比路径发现不了
        def _snapshot() -> dict[Path, bytes]:
            return {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

        # 预置 "../existing" 会解析到的既有文件：没有真实的覆写目标时，内容快照验证不到覆写这一维。
        # 逐 subdir 铺哨兵——嵌套类型（characters/refs 等）的 ".." 落在 characters/ 而非项目根
        project_dir = tmp_path / "projects" / "demo"
        for spec in files.UPLOAD_SPECS.values():
            parent = project_dir.joinpath(*spec.subdir).parent
            parent.mkdir(parents=True, exist_ok=True)
            for ext in (".png", ".jpg", ".wav", ".txt"):
                (parent / f"existing{ext}").write_bytes(b"original")

        before = _snapshot()
        unsafe_names = [
            "../existing",
            "../../evil",
            "../../../evil",
            str(tmp_path / "absolute-escape"),
            "sub/dir",
            "back\\slash",
            "..",
            "trailing.",
            "CON",
            "ctrl\x01char",
        ]
        payloads = {
            "character_audio_ref": ("v.wav", _wav_bytes(3), "audio/wav"),
            # source 不使用 name，但校验在其早返分支之前，同样应拒
            "source": ("novel.txt", b"chapter one", "text/plain"),
        }
        default_payload = ("x.jpg", _img_bytes("JPEG"), "image/jpeg")

        with client:
            for upload_type in files.UPLOAD_SPECS:
                for unsafe in unsafe_names:
                    resp = client.post(
                        f"/api/v1/projects/demo/upload/{upload_type}",
                        params={"name": unsafe},
                        files={"file": payloads.get(upload_type, default_payload)},
                    )
                    assert resp.status_code == 400, (upload_type, unsafe, resp.status_code)
                    assert resp.json()["detail"] == zh_assets.MESSAGES["asset_invalid_name"].format(name=unsafe)

            # 越界名字不得留下任何落盘产物：项目内外都不得新增文件，既有文件的内容也不得被改写
            assert _snapshot() == before

    @pytest.mark.integration
    def test_upload_unsafe_name_message_is_localized(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        expected = {
            "en": en_assets.MESSAGES["asset_invalid_name"],
            "vi": vi_assets.MESSAGES["asset_invalid_name"],
            "zh": zh_assets.MESSAGES["asset_invalid_name"],
        }

        with client:
            for locale, template in expected.items():
                resp = client.post(
                    "/api/v1/projects/demo/upload/character",
                    params={"name": "../evil"},
                    files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
                    headers={"Accept-Language": locale},
                )
                assert resp.status_code == 400
                assert resp.json()["detail"] == template.format(name="../evil")

    @pytest.mark.integration
    def test_upload_name_is_stripped_before_use(self, tmp_path, monkeypatch):
        """校验谓词会 strip 名字，落盘路径与元数据都应使用规范化后的值。"""
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character",
                params={"name": "  Alice  "},
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 200
            assert resp.json()["path"] == "characters/Alice.jpg"
            assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == "characters/Alice.jpg"

    @pytest.mark.integration
    def test_upload_empty_name_falls_back_to_filename(self, tmp_path, monkeypatch):
        """空串 name 等同未提供：校验只对真值生效，落盘仍回退到原文件名 stem。"""
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character",
                params={"name": ""},
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 200
            assert resp.json()["path"] == "characters/x.jpg"

    @pytest.mark.unit
    def test_upload_spec_table_drives_extensions(self):
        """ALLOWED_EXTENSIONS 由 UPLOAD_SPECS 派生，两者不得漂移。"""
        assert set(files.ALLOWED_EXTENSIONS) == set(files.UPLOAD_SPECS)
        for upload_type, spec in files.UPLOAD_SPECS.items():
            assert files.ALLOWED_EXTENSIONS[upload_type] == list(spec.allowed_exts)
        # source 一项被 frontend/src/utils/source-files.ts 镜像，取值变动需同步前端
        assert files.ALLOWED_EXTENSIONS["source"] == [".txt", ".md", ".docx", ".epub", ".pdf"]

    @pytest.mark.unit
    def test_upload_spec_host_fields_must_be_paired(self):
        """登记宿主约束却漏配 404 文案时，构造期即失败，而非拒收时取到空翻译 key。"""
        with pytest.raises(ValueError):
            files.UploadSpec(
                allowed_exts=(".png",),
                subdir=("x",),
                naming="stable_png",
                content_check="validate_image",
                host_bucket="products",
            )
        # 反方向同样是配置错误：登记了文案却没有宿主约束，该文案永远取不到
        with pytest.raises(ValueError):
            files.UploadSpec(
                allowed_exts=(".png",),
                subdir=("x",),
                naming="stable_png",
                content_check="validate_image",
                host_not_found_key="product_not_found",
            )

    @pytest.mark.integration
    def test_upload_rejects_oversized_payload_for_any_type(self, tmp_path, monkeypatch):
        """max_bytes 是通用请求体闸门：登记了上限的类型无论 content_check 为何都应拒收超限请求。"""
        client, _ = _client(monkeypatch, tmp_path)
        limit = 1024 * 1024
        monkeypatch.setitem(
            files.UPLOAD_SPECS,
            "prop",
            dataclasses.replace(files.UPLOAD_SPECS["prop"], max_bytes=limit),
        )
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/prop",
                params={"name": "道具"},
                files={"file": ("x.jpg", b"\x00" * (limit + 1), "image/jpeg")},
            )
            assert resp.status_code == 400
            # 上限取整 MB，文案里的 max_mb 才是对用户有意义的数字
            assert resp.json()["detail"] == zh_errors.MESSAGES["upload_too_large"].format(max_mb=1)

    def test_source_decode_and_draft_mode_helpers(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")
        source_dir = project_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "binary.txt").write_bytes(b"\xff\xfe")

        with client:
            bad_encoding = client.get("/api/v1/projects/demo/source/binary.txt")
            assert bad_encoding.status_code == 400

            # switch content_mode to drama so step files use normalized-script mapping
            project_json = project_dir / "project.json"
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            payload["content_mode"] = "drama"
            project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            # drama step1 落 .json：任意文本被拒（400）
            reject_text = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content="drama draft",
                headers={"content-type": "text/plain"},
            )
            assert reject_text.status_code == 400

            # 合法 JSON 但 scenes 为空 → 被拒（400）：与 _load_drama_step1_content 的非空 scenes 契约同口径
            reject_empty = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content='{"title": "第二集", "scenes": []}',
                headers={"content-type": "text/plain"},
            )
            assert reject_empty.status_code == 400

            # scenes 含非对象项（数字 / 字符串）→ 被拒（400）：与 _load_drama_step1_content 的逐项对象契约同口径
            reject_non_dict_scene = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content='{"title": "第二集", "scenes": [{"scene_id": "E2S01"}, 42]}',
                headers={"content-type": "text/plain"},
            )
            assert reject_non_dict_scene.status_code == 400

            # scene 缺 scene_id → 被拒（400）：与 _load_drama_step1_content 的 scene_id 非空字符串契约同口径，
            # 避免写入端放行、消费端必失败的"保存成功但生成必失败"断层
            reject_missing_scene_id = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content='{"title": "第二集", "scenes": [{}]}',
                headers={"content-type": "text/plain"},
            )
            assert reject_missing_scene_id.status_code == 400

            # scene_id 为空串 → 被拒（400）
            reject_empty_scene_id = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content='{"title": "第二集", "scenes": [{"scene_id": ""}]}',
                headers={"content-type": "text/plain"},
            )
            assert reject_empty_scene_id.status_code == 400

            # 含非空 scenes 的合法 JSON 被接受（200），落到结构化草稿路径
            update_drama = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content='{"title": "第二集", "scenes": [{"scene_id": "E2S01"}]}',
                headers={"content-type": "text/plain"},
            )
            assert update_drama.status_code == 200
            assert update_drama.json()["path"] == "drafts/episode_2/step1_normalized_script.json"

            missing_step = client.delete("/api/v1/projects/demo/drafts/2/step9")
            assert missing_step.status_code == 400

            # 未登记 content_mode 回落到 drama 结构化文件名时同样触发校验（按目标文件名而非 content_mode）：
            # 任意文本不再绕过校验被写成结构化 drama JSON，拖到生成阶段才失败
            payload["content_mode"] = "future_unregistered_mode"
            project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            reject_dirty_mode = client.put(
                "/api/v1/projects/demo/drafts/3/step1",
                content="not json at all",
                headers={"content-type": "text/plain"},
            )
            assert reject_dirty_mode.status_code == 400

            # step2 and step3 should now be invalid
            step2_resp = client.get("/api/v1/projects/demo/drafts/1/step2")
            assert step2_resp.status_code == 400

            step3_resp = client.put(
                "/api/v1/projects/demo/drafts/1/step3",
                content="test",
                headers={"content-type": "text/plain"},
            )
            assert step3_resp.status_code == 400

            unknown_draft = client.delete("/api/v1/projects/demo/drafts/9/step1")
            assert unknown_draft.status_code == 404

    def test_cache_control_immutable_with_version_param(self, tmp_path, monkeypatch):
        """带 ?v= 参数时应返回 immutable 缓存头"""
        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        (project_path / "storyboards").mkdir(exist_ok=True)
        (project_path / "storyboards" / "test.png").write_bytes(b"img")

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/test.png?v=1710288000")
            assert resp.status_code == 200
            assert "immutable" in resp.headers.get("cache-control", "")
            assert "max-age=31536000" in resp.headers.get("cache-control", "")

    def test_cache_control_immutable_for_version_files(self, tmp_path, monkeypatch):
        """versions/ 路径下的文件应返回 immutable 缓存头"""
        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        (project_path / "versions" / "storyboards").mkdir(parents=True)
        (project_path / "versions" / "storyboards" / "E1S01_v1.png").write_bytes(b"img")

        with client:
            resp = client.get("/api/v1/files/demo/versions/storyboards/E1S01_v1.png")
            assert resp.status_code == 200
            assert "immutable" in resp.headers.get("cache-control", "")

    def test_no_cache_control_without_version(self, tmp_path, monkeypatch):
        """无 ?v= 参数且非 versions 路径时不应有 immutable 头"""
        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        (project_path / "storyboards").mkdir(exist_ok=True)
        (project_path / "storyboards" / "test.png").write_bytes(b"img")

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/test.png")
            assert resp.status_code == 200
            assert "immutable" not in resp.headers.get("cache-control", "")

    def test_files_helper_functions(self, tmp_path):
        assert files._get_step_files("narration") == {1: "step1_segments.json"}
        assert files._get_step_files("drama") == {1: "step1_normalized_script.json"}
        # reference_video 走独立的结构化 step1 文件
        assert files._get_step_files("drama", "reference_video") == {1: "step1_reference_units.json"}
        assert files._get_step_files("narration", "reference_video") == {1: "step1_reference_units.json"}
        # 其他 generation_mode 回落到 content_mode
        assert files._get_step_files("narration", "storyboard") == {1: "step1_segments.json"}

    def test_resolve_step1_path_narration_prefers_own_legacy_md(self, tmp_path):
        """narration step1 缺 .json 时优先回落自家旧 .md，不被跨模式遗留 reference_units.md 抢占。"""
        drafts_dir = tmp_path / "drafts" / "episode_1"
        drafts_dir.mkdir(parents=True)
        (drafts_dir / "step1_reference_units.md").write_text("ref leftover", encoding="utf-8")
        (drafts_dir / "step1_segments.md").write_text("narration legacy", encoding="utf-8")
        resolved = files._resolve_step1_path(drafts_dir, 1, drafts_dir / "step1_segments.json")
        assert resolved.name == "step1_segments.md"

    def test_draft_content_reference_video_mode(self, tmp_path, monkeypatch):
        """参考生视频模式下读/写 step1_reference_units.json，避免被按 content_mode 错误路由；
        旧 .md 仅存量兼读，写入经 ScriptReviewService 单一出口做结构校验后落结构化 .json"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")

        # 设置项目为 reference_video 模式（content_mode 仍是 narration 测试正交性）
        project_json = project_dir / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["generation_mode"] = "reference_video"
        project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 分集须可登记（save_content 的写入前置）：派生源文在场即可经孤儿分集自愈补建条目
        (project_dir / "source").mkdir(parents=True, exist_ok=True)
        (project_dir / "source" / "episode_1.txt").write_text("原文", encoding="utf-8")

        drafts_dir = project_dir / "drafts" / "episode_1"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        # 主文件缺失时旧 .md 作为读取候选（存量在制品兼读）
        (drafts_dir / "step1_reference_units.md").write_text("E1U1 stub", encoding="utf-8")

        with client:
            resp = client.get("/api/v1/projects/demo/drafts/1/step1")
            assert resp.status_code == 200
            assert resp.text == "E1U1 stub"

            # 裸文本 / 非法结构不再直写正式 step1（旁路已收敛到单一写盘出口）
            bad = client.put(
                "/api/v1/projects/demo/drafts/1/step1",
                content="raw text",
                headers={"content-type": "text/plain"},
            )
            assert bad.status_code == 400
            invalid = client.put(
                "/api/v1/projects/demo/drafts/1/step1",
                content='{"units": [{"bogus": 1}]}',
                headers={"content-type": "text/plain"},
            )
            assert invalid.status_code == 422

            # 合法结构化内容按 generation_mode 路由到 step1_reference_units.json
            unit = {"unit_id": "E1U01", "shots": [{"text": "镜头描述"}], "duration_seconds": 8}
            update = client.put(
                "/api/v1/projects/demo/drafts/1/step1",
                content=json.dumps({"units": [unit]}, ensure_ascii=False),
                headers={"content-type": "text/plain"},
            )
            assert update.status_code == 200
            assert update.json()["path"] == "drafts/episode_1/step1_reference_units.json"

            # 结构化 .json 存在后优先于旧 .md；落盘的是校验后的结构化内容
            resp = client.get("/api/v1/projects/demo/drafts/1/step1")
            assert resp.status_code == 200
            saved = json.loads(resp.text)
            assert saved["units"][0]["unit_id"] == "E1U01"

    def test_draft_content_fallback_when_mode_mismatches_file(self, tmp_path, monkeypatch):
        """content_mode=narration 但磁盘上只有 reference_units 文件（集级模式切换/历史项目）也能读到"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")  # narration by default

        drafts_dir = project_dir / "drafts" / "episode_3"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        (drafts_dir / "step1_reference_units.md").write_text("fallback content", encoding="utf-8")

        with client:
            resp = client.get("/api/v1/projects/demo/drafts/3/step1")
            assert resp.status_code == 200
            assert resp.text == "fallback content"

    def test_draft_content_routes_by_project_generation_mode(self, tmp_path, monkeypatch):
        """草稿文件名按项目生成路线路由：参考路线全项目落 step1_reference_units.json。"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")

        project_json = project_dir / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["generation_mode"] = "reference_video"
        project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        drafts_dir = project_dir / "drafts" / "episode_2"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "source").mkdir(parents=True, exist_ok=True)
        (project_dir / "source" / "episode_2.txt").write_text("原文", encoding="utf-8")

        with client:
            unit = {"unit_id": "E2U01", "shots": [{"text": "镜头描述"}], "duration_seconds": 8}
            update = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content=json.dumps({"units": [unit]}, ensure_ascii=False),
                headers={"content-type": "text/plain"},
            )
            assert update.status_code == 200
            assert update.json()["path"] == "drafts/episode_2/step1_reference_units.json"

        # _load_project_modes 走 load_project：不存在项目 → ("drama", None) 回退
        content_mode, gen_mode = files._load_project_modes("no-such-project")
        assert content_mode == "drama"
        assert gen_mode is None
        # demo 项目 content_mode=narration（fixture 默认），生成路线取项目字段
        content_mode, gen_mode = files._load_project_modes("demo")
        assert content_mode == "narration"
        assert gen_mode == "reference_video"

    def test_draft_event_emission(self, tmp_path, monkeypatch):
        """PUT drafts 端点应发射 draft:created/updated 事件"""
        from unittest.mock import patch

        client, _ = _client(monkeypatch, tmp_path)

        with client, patch("server.routers.files.emit_project_change_batch") as mock_emit:
            # 首次创建 → action="created", important=True
            resp = client.put(
                "/api/v1/projects/demo/drafts/1/step1",
                content="new draft",
                headers={"content-type": "text/plain"},
            )
            assert resp.status_code == 200
            mock_emit.assert_called_once()
            args = mock_emit.call_args
            change = args[0][1][0]  # second positional arg, first item in list
            assert change["entity_type"] == "draft"
            assert change["action"] == "created"
            assert change["episode"] == 1
            assert change["important"] is True
            assert "片段拆分" in change["label"]

            mock_emit.reset_mock()

            # 再次更新 → action="updated", important=False
            resp2 = client.put(
                "/api/v1/projects/demo/drafts/1/step1",
                content="updated draft",
                headers={"content-type": "text/plain"},
            )
            assert resp2.status_code == 200
            mock_emit.assert_called_once()
            change2 = mock_emit.call_args[0][1][0]
            assert change2["action"] == "updated"
            assert change2["important"] is False

    def test_serve_global_asset_image(self, tmp_path, monkeypatch):
        """全局资产图片能够被正确读取返回"""
        client, pm = _client(monkeypatch, tmp_path)
        target = pm.get_global_assets_root() / "character" / "abc.png"
        target.write_bytes(b"img-bytes")

        with client:
            resp = client.get("/api/v1/global-assets/character/abc.png")
            assert resp.status_code == 200
            assert resp.content == b"img-bytes"

    def test_serve_global_asset_scene_and_prop(self, tmp_path, monkeypatch):
        """scene/prop 子目录也能正确读取"""
        client, pm = _client(monkeypatch, tmp_path)
        root = pm.get_global_assets_root()
        (root / "scene" / "s.png").write_bytes(b"scene-bytes")
        (root / "prop" / "p.png").write_bytes(b"prop-bytes")

        with client:
            r_scene = client.get("/api/v1/global-assets/scene/s.png")
            assert r_scene.status_code == 200
            assert r_scene.content == b"scene-bytes"

            r_prop = client.get("/api/v1/global-assets/prop/p.png")
            assert r_prop.status_code == 200
            assert r_prop.content == b"prop-bytes"

    def test_global_asset_invalid_type_returns_400(self, tmp_path, monkeypatch):
        """非法 asset_type 返回 400"""
        client, _ = _client(monkeypatch, tmp_path)

        with client:
            resp = client.get("/api/v1/global-assets/invalid/abc.png")
            assert resp.status_code == 400

    def test_global_asset_missing_file_returns_404(self, tmp_path, monkeypatch):
        """文件不存在时返回 404"""
        client, _ = _client(monkeypatch, tmp_path)

        with client:
            resp = client.get("/api/v1/global-assets/character/nonexistent.png")
            assert resp.status_code == 404

    def test_global_asset_path_traversal_rejected(self, tmp_path, monkeypatch):
        """filename 中包含 .. 应被阻止（400/403/404 均可接受）"""
        client, _ = _client(monkeypatch, tmp_path)

        with client:
            # URL 编码的 ../evil.png
            resp = client.get("/api/v1/global-assets/character/..%2Fevil.png")
            assert resp.status_code in (400, 403, 404)

    def test_global_asset_symlink_escape_returns_403(self, tmp_path, monkeypatch):
        """在 _global_assets/character/ 里放一个指向外部文件的 symlink,应被 resolve-relative 检查拦截为 403。"""
        import os
        import sys

        if sys.platform == "win32":
            import pytest

            pytest.skip("symlinks require admin on Windows")

        client, pm = _client(monkeypatch, tmp_path)

        # 在 tmp_path 下(但不在 _global_assets 里)创建一个外部目标文件
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"secret")

        # 在 _global_assets/character/ 下建立指向外部目标的 symlink
        global_dir = pm.get_global_assets_root() / "character"
        global_dir.mkdir(parents=True, exist_ok=True)
        link = global_dir / "evil.png"
        os.symlink(outside, link)

        with client:
            r = client.get("/api/v1/global-assets/character/evil.png")
            assert r.status_code == 403


# ==================== Source 多格式上传 ====================

import io  # noqa: E402

from tests.auth_deps import AUTH_DEPENDENCIES


def _upload_source(client, project_name: str, filename: str, content: bytes, on_conflict: str | None = None):
    url = f"/api/v1/projects/{project_name}/upload/source"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    return client.post(
        url,
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


class TestSourceMultiFormatUpload:
    def test_upload_source_utf8_txt_normalized(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload_source(client, "demo", "novel.txt", "纯 UTF-8".encode())
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["normalized"] is True
            assert body["used_encoding"] == "utf-8"
            assert body["original_kept"] is False
            assert body["chapter_count"] == 0

    def test_upload_source_gbk_txt_normalized_and_raw_kept(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            raw = ("第一章\n" * 30).encode("gbk")
            resp = _upload_source(client, "demo", "old.txt", raw)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["normalized"] is True
            assert body["used_encoding"] and body["used_encoding"].lower() != "utf-8"
            assert body["original_kept"] is True

    def test_upload_source_doc_rejected_with_400(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload_source(client, "demo", "x.doc", b"binary")
            assert resp.status_code == 400

    def test_upload_source_conflict_returns_409_with_suggestion(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            _upload_source(client, "demo", "novel.txt", "首次".encode())
            resp = _upload_source(client, "demo", "novel.txt", "再次".encode())
            assert resp.status_code == 409
            body = resp.json()
            assert body["detail"]["existing"] == "novel.txt"
            assert body["detail"]["suggested_name"] == "novel_1"

    def test_upload_source_on_conflict_replace(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            _upload_source(client, "demo", "novel.txt", "旧内容".encode())
            resp = _upload_source(client, "demo", "novel.txt", "新内容".encode(), on_conflict="replace")
            assert resp.status_code == 200, resp.text
            # 通过 GET 拉文本验证已替换
            get_resp = client.get("/api/v1/projects/demo/source/novel.txt")
            assert get_resp.status_code == 200
            assert get_resp.text == "新内容"

    def test_upload_source_on_conflict_rename(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            _upload_source(client, "demo", "novel.txt", "首次".encode())
            resp = _upload_source(client, "demo", "novel.txt", "新版".encode(), on_conflict="rename")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["filename"] == "novel_1.txt"

    def test_delete_source_cascades_raw(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            raw = ("第一章\n" * 30).encode("gbk")
            _upload_source(client, "demo", "to_delete.txt", raw)
            # 上传后应当存在 raw 备份
            project_dir = pm.get_project_path("demo")
            raw_path = project_dir / "source" / "raw" / "to_delete.txt"
            assert raw_path.exists()

            resp = client.delete("/api/v1/projects/demo/source/to_delete.txt")
            assert resp.status_code == 200
            assert not raw_path.exists()

    def test_upload_source_invalid_on_conflict_returns_422(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/source?on_conflict=bogus",
                files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")},
            )
            # FastAPI 用 Literal 自动校验 query param，非法值返回 422
            assert resp.status_code == 422

    def test_upload_source_rejects_oversized_upload_by_content_length(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        from lib.source_loader import SourceLoader

        # We don't actually send 50MB+ of data — instead post a small body with a fake
        # content-length header. Starlette validates content-length vs actual body length
        # for multipart, so we need to send a real oversized payload OR rely on the
        # natural stat-based check. Skip the header fake and exercise the stat path:
        body = b"a" * (SourceLoader.DEFAULT_MAX_BYTES + 1024)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("big.txt", io.BytesIO(body), "text/plain")},
            )
            assert resp.status_code == 413

    def test_list_files_source_includes_raw_filename(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            raw = ("第一章\n" * 30).encode("gbk")
            _upload_source(client, "demo", "old.txt", raw)
            resp = client.get("/api/v1/projects/demo/files")
            body = resp.json()
            source = body["files"]["source"]
            entry = next(e for e in source if e["name"] == "old.txt")
            assert entry["raw_filename"] == "old.txt"

    def test_list_files_source_raw_filename_none_for_pure_utf8(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            _upload_source(client, "demo", "novel.txt", "纯 UTF-8".encode())
            resp = client.get("/api/v1/projects/demo/files")
            body = resp.json()
            entry = next(e for e in body["files"]["source"] if e["name"] == "novel.txt")
            assert entry["raw_filename"] is None


def _client_with_pm_raising(monkeypatch, sentinel: str):
    """构造一个最小 app，其 get_project_manager 调用即抛 RuntimeError。

    RuntimeError 不属于 FileNotFoundError / ValueError / UnicodeDecodeError /
    HTTPException，会落到各路由的 except Exception 兜底分支，被映射成通用 500。
    """

    def _raise():
        raise RuntimeError(sentinel)

    monkeypatch.setattr(files, "get_project_manager", _raise)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(files.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(files.public_router, prefix="/api/v1")
    return TestClient(app)


class TestFilesUnexpectedErrorsMapTo500:
    """未预期异常应映射为通用 500，且不在响应体泄露内部异常细节。"""

    def test_upload_file_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "upload-boom-a1b2"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character?name=Alice",
                files={"file": ("alice.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_list_project_files_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "list-boom-c3d4"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.get("/api/v1/projects/demo/files")
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_get_source_file_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "get-source-boom-e5f6"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.get("/api/v1/projects/demo/source/chapter.txt")
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_update_source_file_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "update-source-boom-7890"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.put(
                "/api/v1/projects/demo/source/chapter.txt",
                content="updated",
                headers={"content-type": "text/plain"},
            )
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_delete_source_file_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "delete-source-boom-1a2b"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.delete("/api/v1/projects/demo/source/chapter.txt")
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_upload_style_image_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "style-image-boom-3c4d"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_upload_style_image_vision_unsupported_maps_to_localized_400(self, tmp_path, monkeypatch):
        """简单档模型不支持 vision 时，400 detail 走 i18n 翻译，不透出裸中文技术消息。"""
        from lib.config.resolver import VisionCapabilityError
        from lib.text_backends.base import TextTaskType

        async def _raise_vision_error(*args, **kwargs):
            raise VisionCapabilityError(
                task_type=TextTaskType.STYLE_ANALYSIS,
                provider_id="gemini-aistudio",
                model_id="gemini-3.1-flash-lite-preview",
            )

        client, _ = _client(monkeypatch, tmp_path)
        monkeypatch.setattr("lib.text_generator.create_text_backend_for_task", _raise_vision_error)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "gemini-aistudio/gemini-3.1-flash-lite-preview" in detail
        assert "vision" in detail
        # 英文 zh 环境默认无 Accept-Language，走中文翻译文案，而非 __str__ 的英文技术消息
        assert "不支持图像输入" in detail

    def test_upload_style_image_backend_value_error_maps_to_500_not_leaked(self, tmp_path, monkeypatch):
        """非 vision 校验的后端构造 ValueError（如凭证文件路径缺失 project_id）不得原样透出为 400。"""
        sentinel = "/secret/vertex_keys/service-account-9f8e.json"

        async def _raise_backend_error(*args, **kwargs):
            raise ValueError(f"凭证文件 {sentinel} 中未找到 project_id")

        client, _ = _client(monkeypatch, tmp_path)
        monkeypatch.setattr("lib.text_generator.create_text_backend_for_task", _raise_backend_error)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 500
        assert sentinel not in resp.text
