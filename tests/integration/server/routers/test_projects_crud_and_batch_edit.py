"""Tests for projects_crud_and_batch_edit."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n.zh import errors as zh_errors
from lib.project_change_hints import get_project_change_source
from lib.project_manager import ProjectManager
from lib.script_batch_edit import (
    ScriptBatchEditResult,
)
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import projects
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.integration.server.routers.projects_router_support import (
    _client,
    _FakePM,
)


class TestProjectsRouter:
    def test_list_and_create_and_delete(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            listed = client.get("/api/v1/projects")
            assert listed.status_code == 200
            names = [p["name"] for p in listed.json()["projects"]]
            assert names == ["ready", "empty", "broken"]
            broken = [p for p in listed.json()["projects"] if p["name"] == "broken"][0]
            assert broken["status"] == {}
            assert "error" not in broken

            create_ok = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "title": "New", "style": "Real", "content_mode": "narration"},
            )
            assert create_ok.status_code == 200
            assert create_ok.json()["name"] == "project-aa11bb22"
            assert create_ok.json()["project"]["title"] == "New"

            create_manual_name = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "manual-project",
                    "style": "Anime",
                    "content_mode": "narration",
                },
            )
            assert create_manual_name.status_code == 200
            assert create_manual_name.json()["name"] == "manual-project"
            assert create_manual_name.json()["project"]["title"] == "manual-project"

            create_exists = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "exists",
                    "title": "Dup",
                    "style": "",
                    "content_mode": "narration",
                },
            )
            assert create_exists.status_code == 400

            create_invalid = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "bad_name",
                    "title": "Bad",
                    "style": "",
                    "content_mode": "narration",
                },
            )
            assert create_invalid.status_code == 400

            create_missing_title = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "style": "", "content_mode": "narration"},
            )
            assert create_missing_title.status_code == 400

            delete_ok = client.delete("/api/v1/projects/remove-me")
            assert delete_ok.status_code == 200

    def test_create_persists_source_kind_and_defaults_novel(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            # 显式 screenplay 持久化于 project.json 顶层
            screenplay = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "scr",
                    "title": "剧本项目",
                    "content_mode": "drama",
                    "source_kind": "screenplay",
                },
            )
            assert screenplay.status_code == 200
            assert screenplay.json()["project"]["source_kind"] == "screenplay"

            # 缺省 source_kind 落 novel
            default_novel = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "name": "nov", "title": "默认项目", "content_mode": "drama"},
            )
            assert default_novel.status_code == 200
            assert default_novel.json()["project"]["source_kind"] == "novel"

            # 非法值被 Pydantic 拒（422，不是 500）
            invalid = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "bad",
                    "title": "X",
                    "content_mode": "drama",
                    "source_kind": "screen_play",
                },
            )
            assert invalid.status_code == 422

    def test_source_kind_silently_ignored_on_patch(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"source_kind": "screenplay"})
            assert resp.status_code == 200
            # 「不接受该字段」的实质保证：请求体里的值不得落进项目数据
            assert "source_kind" not in fake_pm.project_data["ready"]

    def test_project_details_and_updates(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

        with client:
            detail = client.get("/api/v1/projects/ready")
            assert detail.status_code == 200
            assert "status" in detail.json()["project"]
            assert "episode_1.json" in detail.json()["scripts"]

            missing = client.get("/api/v1/projects/missing")
            assert missing.status_code == 404

            update = client.patch(
                "/api/v1/projects/ready",
                json={"title": "Updated", "style": "Noir"},
            )
            assert update.status_code == 200
            assert update.json()["project"]["title"] == "Updated"

            ignored_mode = client.patch(
                "/api/v1/projects/ready",
                json={"content_mode": "drama"},
            )
            assert ignored_mode.status_code == 200
            assert "content_mode" not in fake_pm.project_data["ready"]

            # aspect_ratio 现在允许修改（字符串），dict 类型将被 Pydantic 拒绝（422）
            rejected_ratio_dict = client.patch(
                "/api/v1/projects/ready",
                json={"aspect_ratio": {"videos": "16:9"}},
            )
            assert rejected_ratio_dict.status_code == 422

            # aspect_ratio 字符串更新应成功
            updated_ratio = client.patch(
                "/api/v1/projects/ready",
                json={"aspect_ratio": "16:9"},
            )
            assert updated_ratio.status_code == 200
            assert updated_ratio.json()["project"]["aspect_ratio"] == "16:9"

            ignored_legacy = client.patch(
                "/api/v1/projects/ready",
                json={"image_backend": "gemini-aistudio/nano-banana"},
            )
            assert ignored_legacy.status_code == 200
            assert "image_backend" not in fake_pm.project_data["ready"]

            get_script = client.get("/api/v1/projects/ready/scripts/episode_1.json")
            assert get_script.status_code == 200
            assert get_script.json()["revision"].startswith("sha256-v1:")

            get_script_missing = client.get("/api/v1/projects/ready/scripts/missing.json")
            assert get_script_missing.status_code == 404

    def test_revisioned_batch_endpoint_returns_shared_result_and_rejects_stale_write(self, tmp_path, monkeypatch):
        pm = ProjectManager(str(tmp_path))
        pm.create_project("demo", content_mode="narration")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.save_script(
            "demo",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "summary": "摘要",
                "novel": {"title": "小说", "chapter": "第一章"},
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 4,
                        "novel_text": "风吹过旷野。",
                        "characters_in_segment": [],
                        "scenes": [],
                        "props": [],
                        "image_prompt": {
                            "scene": "荒野",
                            "composition": {
                                "shot_type": "Medium Shot",
                                "lighting": "暖光",
                                "ambiance": "薄雾",
                            },
                        },
                        "video_prompt": {
                            "action": "转身",
                            "camera_motion": "Static",
                            "ambiance_audio": "风声",
                        },
                        "generated_assets": {},
                    }
                ],
            },
            "episode_1.json",
        )
        monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(
            id="default",
            sub="testuser",
            role="admin",
        )
        app.include_router(projects.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        client = TestClient(app)
        with client:
            snapshot = client.get("/api/v1/projects/demo/scripts/episode_1.json").json()
            command = {
                "script": "episode_1.json",
                "expected_revision": snapshot["revision"],
                "operations": [{"op": "update", "id": "E1S01", "fields": {"note": "first"}}],
            }

            edited = client.post("/api/v1/projects/demo/script-edits", json=command)

            assert edited.status_code == 200, edited.text
            result = edited.json()
            assert result["success"] is True
            assert result["before_revision"] == snapshot["revision"]
            assert result["revision"] != snapshot["revision"]
            assert result["affected_ids"] == ["E1S01"]

            command["operations"] = [{"op": "update", "id": "E1S01", "fields": {"note": "stale"}}]
            stale = client.post("/api/v1/projects/demo/script-edits", json=command)

            assert stale.status_code == 409
            assert stale.json()["problems"][0]["code"] == "revision_conflict"
            assert pm.load_script("demo", "episode_1.json")["segments"][0]["note"] == "first"

    def test_revisioned_batch_endpoint_tags_webui_source_across_worker_thread(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        observed_sources: list[str] = []
        revision = f"sha256-v1:{'0' * 64}"

        class CapturingEditor:
            def execute(self, _project_name, _command):
                observed_sources.append(get_project_change_source())
                return ScriptBatchEditResult(
                    success=True,
                    script="episode_1.json",
                    episode=1,
                    before_revision=revision,
                    revision=revision,
                )

        client = _client(monkeypatch, fake_pm)
        client.app.dependency_overrides[projects.get_script_batch_editor_factory] = lambda: (
            lambda _manager=None: CapturingEditor()
        )

        with client:
            response = client.post(
                "/api/v1/projects/ready/script-edits",
                json={
                    "script": "episode_1.json",
                    "expected_revision": revision,
                    "operations": [{"op": "update", "id": "E1S01", "fields": {"note": "first"}}],
                },
            )

        assert response.status_code == 200
        assert observed_sources == ["webui"]

    def test_revisioned_batch_endpoint_rejects_invalid_project_name(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))

        with client:
            response = client.post(
                "/api/v1/projects/illegal-name/script-edits",
                json={
                    "script": "episode_1.json",
                    "expected_revision": f"sha256-v1:{'0' * 64}",
                    "operations": [{"op": "update", "id": "E1S01", "fields": {"note": "first"}}],
                },
            )

        assert response.status_code == 400
        assert response.json()["detail"] == zh_errors.MESSAGES["invalid_project_name"].format(name="illegal-name")
