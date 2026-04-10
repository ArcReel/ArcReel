"""剪映草稿导出路由的集成测试"""

import json
import zipfile
from io import BytesIO

from fastapi.testclient import TestClient

from lib.project_manager import ProjectManager
from server.auth import create_download_token
from tests.conftest import make_test_video


def _setup_project(pm: ProjectManager):
    """创建测试项目 + 剧本 + 视频"""
    project_dir = pm.projects_root / "demo"
    project_dir.mkdir(parents=True)

    videos_dir = project_dir / "videos"
    videos_dir.mkdir()
    make_test_video(videos_dir / "segment_S1.mp4")

    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir()

    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "测试",
                "content_mode": "narration",
                "aspect_ratio": {"video": "16:9"},
                "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        )
    )

    (scripts_dir / "episode_1.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "S1",
                        "duration_seconds": 8,
                        "novel_text": "测试文本",
                        "generated_assets": {"video_clip": "videos/segment_S1.mp4", "status": "completed"},
                    }
                ],
            },
            ensure_ascii=False,
        )
    )


def _client(monkeypatch, pm: ProjectManager) -> TestClient:
    """创建绑定到指定 ProjectManager 的 TestClient"""
    from server.routers import projects as proj_mod

    monkeypatch.setattr(proj_mod, "pm", pm)

    from server.app import app

    return TestClient(app)


class TestJianyingDraftExport:
    """剪映草稿导出端点测试"""

    def test_export_returns_zip(self, tmp_path, monkeypatch):
        """正常导出返回 ZIP"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={
                "episode": 1,
                "draft_path": "/Users/test/drafts",
                "download_token": token,
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        from urllib.parse import unquote

        disposition = unquote(response.headers.get("content-disposition", ""))
        assert "jianying_draft" in disposition

        zf = zipfile.ZipFile(BytesIO(response.content))
        names = zf.namelist()
        assert any("draft_info.json" in n for n in names)

    def test_missing_episode_returns_404(self, tmp_path, monkeypatch):
        """集数不存在返回 404"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 99, "draft_path": "/tmp", "download_token": token},
        )
        assert response.status_code == 404

    def test_no_videos_returns_422(self, tmp_path, monkeypatch):
        """无已完成视频返回 422"""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = pm.projects_root / "empty"
        project_dir.mkdir(parents=True)

        (project_dir / "project.json").write_text(
            json.dumps(
                {
                    "title": "空",
                    "content_mode": "narration",
                    "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
                },
                ensure_ascii=False,
            )
        )
        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "episode_1.json").write_text(
            json.dumps(
                {
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": "S1",
                            "duration_seconds": 8,
                            "novel_text": "",
                            "generated_assets": {"status": "pending"},
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )

        client = _client(monkeypatch, pm)
        token = create_download_token("testuser", "empty")
        response = client.get(
            "/api/v1/projects/empty/export/jianying-draft",
            params={"episode": 1, "draft_path": "/tmp", "download_token": token},
        )
        assert response.status_code == 422

    def test_invalid_token_returns_401(self, tmp_path, monkeypatch):
        """无效 token 返回 401"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "/tmp", "download_token": "bad_token"},
        )
        assert response.status_code == 401

    def test_empty_draft_path_returns_422(self, tmp_path, monkeypatch):
        """draft_path 为空返回 422"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "", "download_token": token},
        )
        assert response.status_code == 422

    def test_control_chars_in_draft_path_returns_422(self, tmp_path, monkeypatch):
        """draft_path 含控制字符返回 422"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "/tmp/\x00bad", "download_token": token},
        )
        assert response.status_code == 422

    def test_long_draft_path_returns_422(self, tmp_path, monkeypatch):
        """draft_path 超过 1024 字符返回 422"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "x" * 1025, "download_token": token},
        )
        assert response.status_code == 422

    def test_mismatched_token_returns_403(self, tmp_path, monkeypatch):
        """token 与项目不匹配返回 403"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "other_project")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "/tmp", "download_token": token},
        )
        assert response.status_code == 403
