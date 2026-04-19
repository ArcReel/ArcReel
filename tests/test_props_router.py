from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import props


class _FakePM:
    def __init__(self):
        self.projects = {
            "demo": {
                "props": {
                    "玉佩": {"description": "old", "prop_sheet": ""},
                }
            }
        }

    def _add_asset(self, asset_type, project_name, name, entry):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        bucket = self.projects[project_name].setdefault("props", {})
        if name in bucket:
            return False
        bucket[name] = entry
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
    monkeypatch.setattr(props, "get_project_manager", lambda: fake_pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(props.router, prefix="/api/v1")
    return TestClient(app)


class TestPropsRouter:
    def test_add_update_delete(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            add_resp = client.post(
                "/api/v1/projects/demo/props",
                json={"name": "长剑", "description": "青铜"},
            )
            assert add_resp.status_code == 200
            assert add_resp.json()["prop"]["description"] == "青铜"

            patch_resp = client.patch(
                "/api/v1/projects/demo/props/玉佩",
                json={"description": "new", "prop_sheet": "props/玉佩.png"},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["prop"]["prop_sheet"].endswith("玉佩.png")

            delete_resp = client.delete("/api/v1/projects/demo/props/长剑")
            assert delete_resp.status_code == 200

    def test_error_mapping(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            dup_resp = client.post(
                "/api/v1/projects/demo/props",
                json={"name": "玉佩", "description": ""},
            )
            assert dup_resp.status_code == 409

            missing_resp = client.patch(
                "/api/v1/projects/demo/props/不存在",
                json={"description": "x"},
            )
            assert missing_resp.status_code == 404

            missing_del = client.delete("/api/v1/projects/demo/props/不存在")
            assert missing_del.status_code == 404
