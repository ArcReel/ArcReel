from fastapi import FastAPI
from fastapi.testclient import TestClient

from webui.server.routers import versions


class _FakePM:
    def __init__(self):
        self.updated = []

    def get_project_path(self, project_name):
        from pathlib import Path

        return Path("/tmp") / project_name

    def update_project_character_sheet(self, *args):
        self.updated.append(("character", args))

    def update_clue_sheet(self, *args):
        self.updated.append(("clue", args))

    def update_scene_asset(self, *args, **kwargs):
        self.updated.append(("scene", args, kwargs))


class _FakeVM:
    def __init__(self, project_path=None):
        self.project_path = project_path

    def get_versions(self, resource_type, resource_id):
        if resource_type == "bad":
            raise ValueError("bad type")
        return {
            "current_version": 1,
            "versions": [{"version": 1, "file": f"versions/{resource_type}/{resource_id}.png"}],
        }

    def restore_version(self, resource_type, resource_id, version, current_file):
        if version == 404:
            raise FileNotFoundError("missing")
        if version == 400:
            raise ValueError("bad")
        return {
            "restored_version": version,
            "new_current_version": version + 1,
            "prompt": "p",
        }


class _StoryboardSyncPM:
    def __init__(self, project_path):
        self.project_path = project_path
        self.update_calls = []

    def get_project_path(self, project_name):
        return self.project_path

    def update_scene_asset(self, project_name, script_filename, scene_id, asset_type, asset_path):
        self.update_calls.append(script_filename)
        if script_filename == "a.json":
            raise KeyError("missing scene")
        if script_filename == "b.json":
            raise RuntimeError("bad script")


def _client(monkeypatch):
    fake_pm = _FakePM()
    monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

    app = FastAPI()
    app.include_router(versions.router, prefix="/api/v1")
    return TestClient(app), fake_pm


class TestVersionsRouter:
    def test_get_versions_and_restore(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        with client:
            get_resp = client.get("/api/v1/projects/demo/versions/characters/Alice")
            assert get_resp.status_code == 200
            assert get_resp.json()["current_version"] == 1

            restore_resp = client.post("/api/v1/projects/demo/versions/characters/Alice/restore/1")
            assert restore_resp.status_code == 200
            assert restore_resp.json()["new_current_version"] == 2
            assert any(item[0] == "character" for item in fake_pm.updated)

    def test_restore_error_mapping(self, monkeypatch):
        client, _ = _client(monkeypatch)
        with client:
            bad_type = client.get("/api/v1/projects/demo/versions/bad/Alice")
            assert bad_type.status_code == 400

            not_found = client.post("/api/v1/projects/demo/versions/characters/Alice/restore/404")
            assert not_found.status_code == 404

            bad_value = client.post("/api/v1/projects/demo/versions/characters/Alice/restore/400")
            assert bad_value.status_code == 400

            unsupported = client.post("/api/v1/projects/demo/versions/unknown/Alice/restore/1")
            assert unsupported.status_code == 400

    def test_storyboard_restore_syncs_scripts_with_error_tolerance(self, tmp_path, monkeypatch):
        project_path = tmp_path / "demo"
        scripts_dir = project_path / "scripts"
        scripts_dir.mkdir(parents=True)
        for name in ("a.json", "b.json", "c.json"):
            (scripts_dir / name).write_text("{}", encoding="utf-8")

        fake_pm = _StoryboardSyncPM(project_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.include_router(versions.router, prefix="/api/v1")
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/storyboards/E1S01/restore/1")
            assert resp.status_code == 200
            assert resp.json()["file_path"] == "storyboards/scene_E1S01.png"

        assert sorted(fake_pm.update_calls) == ["a.json", "b.json", "c.json"]

    def test_get_versions_unexpected_error_maps_to_500(self, monkeypatch):
        fake_pm = _FakePM()
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            versions,
            "get_version_manager",
            lambda project_name: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        app = FastAPI()
        app.include_router(versions.router, prefix="/api/v1")
        with TestClient(app) as client:
            resp = client.get("/api/v1/projects/demo/versions/characters/Alice")
            assert resp.status_code == 500
            assert "boom" in resp.json()["detail"]
