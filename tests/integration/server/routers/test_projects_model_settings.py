"""Projects-route model_settings read/write."""

from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    build_projects_client,
)


class TestModelSettingsApi:
    def test_create_project_with_model_settings(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
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
            # Verify persisted model_settings directly from creation returns.
            project = resp.json()["project"]
            assert project["model_settings"]["gemini-aistudio/veo-3.1-lite-generate-preview"]["resolution"] == "720p"
            # Also inspect fake_pm internal storage.
            stored = fake_pm.project_data["demo-res"]
            assert stored["model_settings"]["gemini-aistudio/veo-3.1-lite-generate-preview"]["resolution"] == "720p"

    def test_patch_project_model_settings(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            # Create first using existing ready projects.
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "1080p"}}},
            )
            assert resp.status_code == 200
            # Verify model_settings directly from PATCH returns.
            project = resp.json()["project"]
            assert project["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "1080p"
            # Also inspect fake_pm internal storage.
            stored = fake_pm.project_data["ready"]
            assert stored["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "1080p"
