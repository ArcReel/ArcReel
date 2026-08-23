"""Tests for model_settings_api (split from test_projects_router.py)."""

from tests.integration.server.routers.projects_router_support import (
    _client,
    _FakePM,
)


class TestModelSettingsApi:
    def test_create_project_with_model_settings(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "demo-res",
                    "title": "T",
                    "model_settings": {
                        "gemini-aistudio/veo-3.1-lite-generate-preview": {"resolution": "720p"},
                    },
                },
            )
            assert resp.status_code == 200
            # 直接从 create 返回值验证 model_settings 已持久化
            project = resp.json()["project"]
            assert project["model_settings"]["gemini-aistudio/veo-3.1-lite-generate-preview"]["resolution"] == "720p"
            # 也验证 fake_pm 内部存储
            stored = fake_pm.project_data["demo-res"]
            assert stored["model_settings"]["gemini-aistudio/veo-3.1-lite-generate-preview"]["resolution"] == "720p"

    def test_patch_project_model_settings(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            # 先创建（利用现有 ready 项目）
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "1080p"}}},
            )
            assert resp.status_code == 200
            # 直接从 patch 返回值验证 model_settings
            project = resp.json()["project"]
            assert project["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "1080p"
            # 也验证 fake_pm 内部存储
            stored = fake_pm.project_data["ready"]
            assert stored["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "1080p"
