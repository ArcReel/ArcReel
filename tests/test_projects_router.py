import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import projects


class _FakePM:
    def __init__(self, base: Path):
        self.base = base
        self.project_data = {
            "ready": {
                "title": "Ready",
                "style": "Anime",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
                "overview": {"synopsis": "old"},
            },
            "broken": {
                "title": "Broken",
                "style": "",
                "episodes": [],
            },
        }
        self.scripts = {
            ("ready", "episode_1.json"): {
                "content_mode": "drama",
                "scenes": [{"scene_id": "001", "duration_seconds": 8}],
            },
            ("ready", "narration.json"): {
                "content_mode": "narration",
                "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
            },
        }
        self.created = set()
        self.generated_names = ["project-aa11bb22", "project-cc33dd44"]
        (self.base / "ready" / "storyboards").mkdir(parents=True, exist_ok=True)
        (self.base / "ready" / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        (self.base / "empty").mkdir(parents=True, exist_ok=True)
        (self.base / "remove-me").mkdir(parents=True, exist_ok=True)

    def list_projects(self):
        return ["ready", "empty", "broken"]

    def project_exists(self, name):
        return name in {"ready", "broken"}

    def load_project(self, name):
        if name == "broken":
            raise RuntimeError("broken")
        if name not in self.project_data:
            raise FileNotFoundError(name)
        return self.project_data[name]

    def get_project_path(self, name):
        path = self.base / name
        if not path.exists():
            raise FileNotFoundError(name)
        return path

    def get_project_status(self, name):
        return {"current_stage": "source_ready"}

    def create_project(self, name):
        if not name or not re.fullmatch(r"[A-Za-z0-9-]+", name):
            raise ValueError("项目标识仅允许英文字母、数字和中划线")
        if name == "exists":
            raise FileExistsError(name)
        self.created.add(name)
        (self.base / name).mkdir(parents=True, exist_ok=True)

    def generate_project_name(self, title):
        return self.generated_names.pop(0)

    def create_project_metadata(self, name, title, style, content_mode):
        payload = {"title": (title or name), "style": style or "", "content_mode": content_mode, "episodes": []}
        self.project_data[name] = payload
        return payload

    def save_project(self, name, payload):
        self.project_data[name] = payload

    def load_script(self, name, script_file):
        if script_file.startswith("scripts/"):
            script_file = script_file[len("scripts/") :]
        key = (name, script_file)
        if key not in self.scripts:
            raise FileNotFoundError(script_file)
        return self.scripts[key]

    def save_script(self, name, payload, script_file):
        self.scripts[(name, script_file)] = payload

    async def generate_overview(self, name):
        if name == "ready":
            return {"synopsis": "generated"}
        raise ValueError("source missing")


class _FakeCalc:
    def calculate_project_status(self, name, project):
        return {
            "current_phase": "production",
            "phase_progress": 0.5,
            "characters": {"total": 1, "completed": 0},
            "clues": {"total": 1, "completed": 0},
            "episodes_summary": {"total": 1, "scripted": 1, "in_production": 1, "completed": 0},
        }

    def enrich_project(self, name, project):
        project = dict(project)
        project["status"] = self.calculate_project_status(name, project)
        return project

    def enrich_script(self, script):
        script = dict(script)
        script["metadata"] = {"total_scenes": 1, "estimated_duration_seconds": 8}
        return script


def _client(monkeypatch, fake_pm, fake_calc):
    monkeypatch.setattr(projects, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(projects, "get_status_calculator", lambda: fake_calc)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(projects.router, prefix="/api/v1")
    return TestClient(app)


class TestProjectsRouter:
    def test_list_and_create_and_delete(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            listed = client.get("/api/v1/projects")
            assert listed.status_code == 200
            names = [p["name"] for p in listed.json()["projects"]]
            assert names == ["ready", "empty", "broken"]
            broken = [p for p in listed.json()["projects"] if p["name"] == "broken"][0]
            assert broken["status"] == {}
            assert "error" in broken

            create_ok = client.post(
                "/api/v1/projects",
                json={"title": "New", "style": "Real", "content_mode": "narration"},
            )
            assert create_ok.status_code == 200
            assert create_ok.json()["name"] == "project-aa11bb22"
            assert create_ok.json()["project"]["title"] == "New"

            create_manual_name = client.post(
                "/api/v1/projects",
                json={"name": "manual-project", "style": "Anime", "content_mode": "narration"},
            )
            assert create_manual_name.status_code == 200
            assert create_manual_name.json()["name"] == "manual-project"
            assert create_manual_name.json()["project"]["title"] == "manual-project"

            create_exists = client.post(
                "/api/v1/projects",
                json={"name": "exists", "title": "Dup", "style": "", "content_mode": "narration"},
            )
            assert create_exists.status_code == 400

            create_invalid = client.post(
                "/api/v1/projects",
                json={"name": "bad_name", "title": "Bad", "style": "", "content_mode": "narration"},
            )
            assert create_invalid.status_code == 400

            create_missing_title = client.post(
                "/api/v1/projects",
                json={"style": "", "content_mode": "narration"},
            )
            assert create_missing_title.status_code == 400

            delete_ok = client.delete("/api/v1/projects/remove-me")
            assert delete_ok.status_code == 200

    def test_project_details_and_updates(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            detail = client.get("/api/v1/projects/ready")
            assert detail.status_code == 200
            assert "status" in detail.json()["project"]
            assert "episode_1.json" in detail.json()["scripts"]

            missing = client.get("/api/v1/projects/missing")
            assert missing.status_code == 404

            update = client.patch(
                "/api/v1/projects/ready",
                json={"title": "Updated", "style": "Noir"},
            )
            assert update.status_code == 200
            assert update.json()["project"]["title"] == "Updated"

            rejected_mode = client.patch(
                "/api/v1/projects/ready",
                json={"content_mode": "drama"},
            )
            assert rejected_mode.status_code == 400

            rejected_ratio = client.patch(
                "/api/v1/projects/ready",
                json={"aspect_ratio": {"videos": "16:9"}},
            )
            assert rejected_ratio.status_code == 400

            get_script = client.get("/api/v1/projects/ready/scripts/episode_1.json")
            assert get_script.status_code == 200

            get_script_missing = client.get("/api/v1/projects/ready/scripts/missing.json")
            assert get_script_missing.status_code == 404

    def test_scene_segment_and_overview_endpoints(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [{"scene_id": "001", "duration_seconds": 8, "image_prompt": {}, "video_prompt": {}}],
        }
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
        }

        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            patch_scene = client.patch(
                "/api/v1/projects/ready/scenes/001",
                json={"script_file": "episode_1.json", "updates": {"duration_seconds": 6, "segment_break": True}},
            )
            assert patch_scene.status_code == 200
            assert patch_scene.json()["scene"]["duration_seconds"] == 6

            patch_scene_missing = client.patch(
                "/api/v1/projects/ready/scenes/404",
                json={"script_file": "episode_1.json", "updates": {}},
            )
            assert patch_scene_missing.status_code == 404

            patch_segment = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "narration.json", "duration_seconds": 8, "segment_break": True},
            )
            assert patch_segment.status_code == 200

            not_narration = client.patch(
                "/api/v1/projects/ready/segments/001",
                json={"script_file": "episode_1.json", "duration_seconds": 8},
            )
            assert not_narration.status_code == 400

            segment_missing = client.patch(
                "/api/v1/projects/ready/segments/E9S99",
                json={"script_file": "narration.json", "duration_seconds": 8},
            )
            assert segment_missing.status_code == 404

            gen_overview_ok = client.post("/api/v1/projects/ready/generate-overview")
            assert gen_overview_ok.status_code == 200

            gen_overview_bad = client.post("/api/v1/projects/bad/generate-overview")
            assert gen_overview_bad.status_code == 400

            update_overview = client.patch(
                "/api/v1/projects/ready/overview",
                json={"synopsis": "new synopsis", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            )
            assert update_overview.status_code == 200
            assert update_overview.json()["overview"]["synopsis"] == "new synopsis"

    def test_get_project_includes_asset_fingerprints(self, tmp_path, monkeypatch):
        """项目 API 应返回 asset_fingerprints 字段"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert "asset_fingerprints" in data
            assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
            assert isinstance(data["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)
