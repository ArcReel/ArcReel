import json
from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.routers import files


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
    return _FakeTextBackend()


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

    monkeypatch.setattr(files, "get_project_manager", lambda: pm)
    monkeypatch.setattr("lib.text_generator.create_text_backend_for_task", _fake_create_backend)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(files.router, prefix="/api/v1")
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

            storyboard = client.post(
                "/api/v1/projects/demo/upload/storyboard?name=E1S01",
                files={"file": ("storyboard.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert storyboard.status_code == 200
            assert storyboard.json()["path"] == "storyboards/scene_E1S01.jpg"

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

            list_drafts = client.get("/api/v1/projects/demo/drafts")
            assert list_drafts.status_code == 200
            assert "1" in list_drafts.json()["drafts"]

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

            storyboard_no_name = client.post(
                "/api/v1/projects/demo/upload/storyboard",
                files={"file": ("board.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert storyboard_no_name.status_code == 200
            assert storyboard_no_name.json()["path"] == "storyboards/board.jpg"

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

            update_drama = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content="drama draft",
                headers={"content-type": "text/plain"},
            )
            assert update_drama.status_code == 200
            assert update_drama.json()["path"] == "drafts/episode_2/step1_normalized_script.md"

            missing_step = client.delete("/api/v1/projects/demo/drafts/2/step9")
            assert missing_step.status_code == 400

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
        from tests.conftest import make_translator

        _t = make_translator()
        assert files._extract_step_number("step12_x.md") == 12
        assert files._extract_step_number("not-match.md") == 0
        assert files._get_step_files("narration") == {1: "step1_segments.md"}
        assert files._get_step_files("drama") == {1: "step1_normalized_script.md"}
        # reference_video 走独立的 step1 文件
        assert files._get_step_files("drama", "reference_video") == {1: "step1_reference_units.md"}
        assert files._get_step_files("narration", "reference_video") == {1: "step1_reference_units.md"}
        # 其他 generation_mode 回落到 content_mode
        assert files._get_step_files("narration", "storyboard") == {1: "step1_segments.md"}
        assert files._get_step_title("step1_segments.md", _t) == "片段拆分"
        assert files._get_step_title("step1_normalized_script.md", _t) == "规范化剧本"
        assert files._get_step_title("step1_reference_units.md", _t) == "片段拆分"
        assert files._get_step_title("unknown.md", _t) == "unknown.md"

    def test_draft_content_reference_video_mode(self, tmp_path, monkeypatch):
        """参考生视频模式下读/写 step1_reference_units.md，避免被按 content_mode 错误路由"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")

        # 设置项目为 reference_video 模式（content_mode 仍是 narration 测试正交性）
        project_json = project_dir / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["generation_mode"] = "reference_video"
        project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        drafts_dir = project_dir / "drafts" / "episode_1"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        (drafts_dir / "step1_reference_units.md").write_text("E1U1 stub", encoding="utf-8")

        with client:
            resp = client.get("/api/v1/projects/demo/drafts/1/step1")
            assert resp.status_code == 200
            assert resp.text == "E1U1 stub"

            # 写入时按 generation_mode 路由到 step1_reference_units.md
            update = client.put(
                "/api/v1/projects/demo/drafts/1/step1",
                content="E1U1 edited",
                headers={"content-type": "text/plain"},
            )
            assert update.status_code == 200
            assert update.json()["path"] == "drafts/episode_1/step1_reference_units.md"

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

    def test_draft_content_episode_level_mode_override(self, tmp_path, monkeypatch):
        """项目级 generation_mode=storyboard 但集级覆盖 reference_video，应按集级路由"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")

        project_json = project_dir / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["generation_mode"] = "storyboard"
        payload["episodes"] = [{"episode": 2, "generation_mode": "reference_video"}]
        project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        drafts_dir = project_dir / "drafts" / "episode_2"
        drafts_dir.mkdir(parents=True, exist_ok=True)

        with client:
            update = client.put(
                "/api/v1/projects/demo/drafts/2/step1",
                content="ep2 reference units",
                headers={"content-type": "text/plain"},
            )
            assert update.status_code == 200
            assert update.json()["path"] == "drafts/episode_2/step1_reference_units.md"

        # _load_project_modes 走 load_project：不存在项目 → ("drama", None) 回退
        content_mode, gen_mode = files._load_project_modes("no-such-project", 1)
        assert content_mode == "drama"
        assert gen_mode is None
        # demo 项目 content_mode=narration（fixture 默认），且项目级 storyboard + ep2 覆盖 reference_video
        content_mode, gen_mode = files._load_project_modes("demo", 2)
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
