from fastapi import FastAPI
from fastapi.testclient import TestClient

from webui.server.routers import characters


class _FakePM:
    def __init__(self):
        self.projects = {
            "demo": {
                "characters": {
                    "Alice": {
                        "description": "old",
                        "voice_style": "soft",
                        "character_sheet": "",
                        "reference_image": "",
                    }
                }
            }
        }

    def add_project_character(self, project_name, name, description, voice_style):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        self.projects[project_name]["characters"][name] = {
            "description": description,
            "voice_style": voice_style,
        }
        return self.projects[project_name]

    def load_project(self, project_name):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        return self.projects[project_name]

    def save_project(self, project_name, project):
        self.projects[project_name] = project


def _client(monkeypatch, fake_pm):
    monkeypatch.setattr(characters, "get_project_manager", lambda: fake_pm)
    app = FastAPI()
    app.include_router(characters.router, prefix="/api/v1")
    return TestClient(app)


class TestCharactersRouter:
    def test_add_update_delete_character(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            add_resp = client.post(
                "/api/v1/projects/demo/characters",
                json={"name": "Bob", "description": "new char", "voice_style": "calm"},
            )
            assert add_resp.status_code == 200
            assert add_resp.json()["character"]["description"] == "new char"

            patch_resp = client.patch(
                "/api/v1/projects/demo/characters/Alice",
                json={
                    "description": "updated",
                    "voice_style": "strong",
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice.png",
                },
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["character"]["description"] == "updated"

            delete_resp = client.delete("/api/v1/projects/demo/characters/Bob")
            assert delete_resp.status_code == 200
            assert "已删除" in delete_resp.json()["message"]

    def test_error_mapping(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            not_found = client.post(
                "/api/v1/projects/missing/characters",
                json={"name": "Bob", "description": "x", "voice_style": "y"},
            )
            assert not_found.status_code == 404

            missing_char = client.patch(
                "/api/v1/projects/demo/characters/Nope",
                json={"description": "x"},
            )
            assert missing_char.status_code == 404

            missing_delete = client.delete("/api/v1/projects/demo/characters/Nope")
            assert missing_delete.status_code == 404
