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
    pm.add_clue("demo", "玉佩", "prop", "desc", "major")

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
                "/api/v1/projects/demo/upload/clue?name=玉佩",
                files={"file": ("clue.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert clue.status_code == 200
            assert clue.json()["path"] == "clues/玉佩.jpg"

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

            # confirm metadata updated for character/clue
            project = pm.load_project("demo")
            assert project["characters"]["Alice"]["character_sheet"] == "characters/Alice.jpg"
            assert project["characters"]["Alice"]["reference_image"] == "characters/refs/Alice.webp"
            assert project["clues"]["玉佩"]["clue_sheet"] == "clues/玉佩.jpg"

    def test_style_image_endpoints(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        # 预置 style_template_id，验证上传后被强制清掉（互斥）
        project = pm.load_project("demo")
        project["style_template_id"] = "live_premium_drama"
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
                "/api/v1/projects/demo/upload/clue?name=不存在线索",
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert clue_missing_entity.status_code == 200
            assert clue_missing_entity.json()["path"] == "clues/不存在线索.jpg"

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
        assert files._get_step_title("step1_segments.md", _t) == "片段拆分"
        assert files._get_step_title("step1_normalized_script.md", _t) == "规范化剧本"
        assert files._get_step_title("unknown.md", _t) == "unknown.md"

        assert files._get_content_mode(tmp_path) == "drama"
        project_json = tmp_path / "project.json"
        project_json.write_text('{"content_mode":"narration"}', encoding="utf-8")
        assert files._get_content_mode(tmp_path) == "narration"

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
