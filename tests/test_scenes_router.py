from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import scenes


class _FakePM:
    def __init__(self):
        self.projects = {
            "demo": {
                "scenes": {
                    "祠堂": {"description": "old", "scene_sheet": ""},
                }
            }
        }

    def add_project_scene(self, project_name, name, description):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        scenes_dict = self.projects[project_name].setdefault("scenes", {})
        if name in scenes_dict:
            return False
        scenes_dict[name] = {"description": description, "scene_sheet": ""}
        return True

    def load_project(self, project_name):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        return self.projects[project_name]

    def save_project(self, project_name, project):
        self.projects[project_name] = project

    def update_project(self, project_name, mutate_fn):
        project = self.load_project(project_name)
        mutate_fn(project)
        self.save_project(project_name, project)


def _client(monkeypatch, fake_pm):
    monkeypatch.setattr(scenes, "get_project_manager", lambda: fake_pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(scenes.router, prefix="/api/v1")
    return TestClient(app)


class TestScenesRouter:
    def test_add_update_delete(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            add_resp = client.post(
                "/api/v1/projects/demo/scenes",
                json={"name": "雪山", "description": "冷峻"},
            )
            assert add_resp.status_code == 200
            assert add_resp.json()["scene"]["description"] == "冷峻"

            patch_resp = client.patch(
                "/api/v1/projects/demo/scenes/祠堂",
                json={"description": "new", "scene_sheet": "scenes/祠堂.png"},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["scene"]["scene_sheet"].endswith("祠堂.png")

            delete_resp = client.delete("/api/v1/projects/demo/scenes/雪山")
            assert delete_resp.status_code == 200

    def test_error_mapping(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            # 重复名 → 409
            dup_resp = client.post(
                "/api/v1/projects/demo/scenes",
                json={"name": "祠堂", "description": ""},
            )
            assert dup_resp.status_code == 409

            # 不存在的场景 → 404
            missing_resp = client.patch(
                "/api/v1/projects/demo/scenes/不存在",
                json={"description": "x"},
            )
            assert missing_resp.status_code == 404

            missing_del = client.delete("/api/v1/projects/demo/scenes/不存在")
            assert missing_del.status_code == 404
