from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import clues


class _FakePM:
    def __init__(self):
        self.projects = {
            "demo": {
                "clues": {
                    "玉佩": {
                        "type": "prop",
                        "description": "old",
                        "importance": "major",
                        "clue_sheet": "",
                    }
                }
            }
        }

    def add_clue(self, project_name, name, clue_type, description, importance):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        if clue_type not in ("prop", "location"):
            raise ValueError("invalid")
        self.projects[project_name]["clues"][name] = {
            "type": clue_type,
            "description": description,
            "importance": importance,
        }
        return self.projects[project_name]

    def load_project(self, project_name):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        return self.projects[project_name]

    def save_project(self, project_name, project):
        self.projects[project_name] = project


def _client(monkeypatch, fake_pm):
    monkeypatch.setattr(clues, "get_project_manager", lambda: fake_pm)
    app = FastAPI()
    app.include_router(clues.router, prefix="/api/v1")
    return TestClient(app)


class TestCluesRouter:
    def test_add_update_delete(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            add_resp = client.post(
                "/api/v1/projects/demo/clues",
                json={"name": "祠堂", "clue_type": "location", "description": "阴森", "importance": "major"},
            )
            assert add_resp.status_code == 200
            assert add_resp.json()["clue"]["type"] == "location"

            patch_resp = client.patch(
                "/api/v1/projects/demo/clues/玉佩",
                json={"clue_type": "prop", "description": "new", "importance": "minor", "clue_sheet": "clues/a.png"},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["clue"]["importance"] == "minor"

            delete_resp = client.delete("/api/v1/projects/demo/clues/祠堂")
            assert delete_resp.status_code == 200

    def test_error_mapping(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            bad_type = client.post(
                "/api/v1/projects/demo/clues",
                json={"name": "x", "clue_type": "bad", "description": "x", "importance": "major"},
            )
            assert bad_type.status_code == 400

            missing = client.patch(
                "/api/v1/projects/demo/clues/missing",
                json={"description": "x"},
            )
            assert missing.status_code == 404

            bad_patch_type = client.patch(
                "/api/v1/projects/demo/clues/玉佩",
                json={"clue_type": "bad"},
            )
            assert bad_patch_type.status_code == 400

            bad_importance = client.patch(
                "/api/v1/projects/demo/clues/玉佩",
                json={"importance": "bad"},
            )
            assert bad_importance.status_code == 400
