import json
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, create_download_token, create_token, get_current_user
from server.routers import projects


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _create_demo_project(pm: ProjectManager) -> None:
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    project = pm.load_project("demo")
    project["episodes"] = [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}]
    project["characters"] = {"Hero": {"description": "Lead", "character_sheet": "characters/Hero.png"}}
    project["clues"] = {
        "Key": {
            "type": "prop",
            "description": "Important",
            "importance": "major",
            "clue_sheet": "clues/Key.png",
        }
    }
    pm.save_project("demo", project)

    project_dir = pm.get_project_path("demo")
    _write_text(project_dir / "source" / "chapter.txt", "source")
    _write_bytes(project_dir / "characters" / "Hero.png", b"png")
    _write_bytes(project_dir / "clues" / "Key.png", b"png")
    _write_bytes(project_dir / "storyboards" / "scene_E1S01.png", b"png")
    _write_bytes(project_dir / "videos" / "scene_E1S01.mp4", b"mp4")
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "novel": {"title": "Demo", "chapter": "第一章"},
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "原文",
                    "characters_in_segment": ["Hero"],
                    "clues_in_segment": ["Key"],
                    "image_prompt": "img",
                    "video_prompt": "vid",
                    "generated_assets": {
                        "storyboard_image": "storyboards/scene_E1S01.png",
                        "video_clip": "videos/scene_E1S01.mp4",
                        "video_uri": None,
                        "status": "completed",
                    },
                }
            ],
        },
    )


def _client(monkeypatch, pm: ProjectManager) -> TestClient:
    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(projects.router, prefix="/api/v1")
    return TestClient(app)


class TestProjectArchiveRoutes:
    def test_export_route_streams_zip(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_demo_project(pm)
        client = _client(monkeypatch, pm)

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            token = create_download_token("testuser", "demo")
            with client:
                response = client.get(f"/api/v1/projects/demo/export?download_token={token}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert 'filename="demo-' in response.headers["content-disposition"]

        archive_path = tmp_path / "download.zip"
        archive_path.write_bytes(response.content)
        with zipfile.ZipFile(archive_path) as archive:
            assert "demo/project.json" in archive.namelist()
            assert "demo/arcreel-export.json" in archive.namelist()

    def test_import_route_returns_structured_validation_errors(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        client = _client(monkeypatch, pm)
        archive_path = tmp_path / "broken.zip"

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("demo/source/chapter.txt", "source")

        with client:
            response = client.post(
                "/api/v1/projects/import",
                data={"conflict_policy": "rename"},
                files={"file": ("broken.zip", archive_path.read_bytes(), "application/zip")},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "导入包校验失败"
        assert any("project.json" in error for error in response.json()["errors"])
        assert "diagnostics" in response.json()

    def test_import_route_returns_conflict_payload_for_secondary_confirmation(
        self,
        tmp_path,
        monkeypatch,
    ):
        pm = ProjectManager(tmp_path / "projects")
        _create_demo_project(pm)
        client = _client(monkeypatch, pm)

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            token = create_download_token("testuser", "demo")
            with client:
                export_response = client.get(f"/api/v1/projects/demo/export?download_token={token}")
                response = client.post(
                    "/api/v1/projects/import",
                    files={
                        "file": (
                            "demo.zip",
                            export_response.content,
                            "application/zip",
                        )
                    },
                )

        assert response.status_code == 409
        assert response.json()["detail"] == "检测到项目编号冲突"
        assert response.json()["conflict_project_name"] == "demo"

    def test_export_token_endpoint(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_demo_project(pm)
        client = _client(monkeypatch, pm)

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            jwt_token = create_token("admin")
            with client:
                response = client.post(
                    "/api/v1/projects/demo/export/token",
                    headers={"Authorization": f"Bearer {jwt_token}"},
                )

        assert response.status_code == 200
        data = response.json()
        assert "download_token" in data
        assert data["expires_in"] == 300
        assert "diagnostics" in data

    def test_export_token_endpoint_project_not_found(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        client = _client(monkeypatch, pm)

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            jwt_token = create_token("admin")
            with client:
                response = client.post(
                    "/api/v1/projects/nonexistent/export/token",
                    headers={"Authorization": f"Bearer {jwt_token}"},
                )

        assert response.status_code == 404

    def test_export_with_download_token(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_demo_project(pm)
        client = _client(monkeypatch, pm)

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            download_token = create_download_token("admin", "demo")
            with client:
                response = client.get(
                    f"/api/v1/projects/demo/export?download_token={download_token}",
                )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"

    def test_export_with_wrong_project_token(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_demo_project(pm)
        client = _client(monkeypatch, pm)

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            download_token = create_download_token("admin", "other-project")
            with client:
                response = client.get(
                    f"/api/v1/projects/demo/export?download_token={download_token}",
                )

        assert response.status_code == 403

    def test_export_scope_current(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_demo_project(pm)
        client = _client(monkeypatch, pm)

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            with client:
                token_response = client.post(
                    "/api/v1/projects/demo/export/token?scope=current",
                    headers={"Authorization": f"Bearer {create_token('admin')}"},
                )
                assert token_response.status_code == 200
                download_token = token_response.json()["download_token"]
                response = client.get(
                    f"/api/v1/projects/demo/export?download_token={download_token}&scope=current",
                )

        assert response.status_code == 200

    def test_export_scope_invalid(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_demo_project(pm)
        client = _client(monkeypatch, pm)

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            download_token = create_download_token("admin", "demo")
            with client:
                response = client.get(
                    f"/api/v1/projects/demo/export?download_token={download_token}&scope=invalid",
                )

        assert response.status_code == 422
