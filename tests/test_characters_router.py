from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.artifact_activation import register_current_resource_artifact
from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import characters
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import FakeProjectAssetDeleteMixin

pytestmark = pytest.mark.unit


class _FakePM(FakeProjectAssetDeleteMixin):
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

    def _add_asset(self, asset_type, project_name, name, entry):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        bucket = self.projects[project_name].setdefault("characters", {})
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
    monkeypatch.setattr(characters, "get_project_manager", lambda: fake_pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(characters.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


class TestCharactersRouter:
    def test_clearing_character_sheet_forgets_its_formal_claim(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "lead")
        project_dir = pm.get_project_path("demo")
        buffer = BytesIO()
        Image.new("RGB", (8, 8), color=(10, 20, 30)).save(buffer, format="PNG")

        def _register_sheet(_target) -> None:
            register_current_resource_artifact(
                project_dir,
                resource_type="characters",
                resource_id="Alice",
            )

        pm.install_asset_sheet_bytes(
            "character",
            "demo",
            "Alice",
            "characters/Alice.png",
            buffer.getvalue(),
            on_commit=_register_sheet,
        )
        key = ArtifactKey.asset_sheet("character", "Alice")
        adapter = ProjectArtifactManifestAdapter(project_dir)
        assert adapter.get_entry(key) is not None

        with _client(monkeypatch, pm) as client:
            response = client.patch(
                "/api/v1/projects/demo/characters/Alice",
                json={"character_sheet": ""},
            )

        assert response.status_code == 200, response.text
        assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == ""
        assert adapter.get_entry(key) is None

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
