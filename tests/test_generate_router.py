from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from webui.server.routers import generate


class _FakeVersions:
    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"created_at": "2026-02-01T00:00:00Z"}]}


class _FakeGenerator:
    def __init__(self):
        self.versions = _FakeVersions()
        self.image_calls = []
        self.video_calls = []

    async def generate_image_async(self, **kwargs):
        self.image_calls.append(kwargs)
        return Path("/tmp/out.png"), 1

    async def generate_video_async(self, **kwargs):
        self.video_calls.append(kwargs)
        return Path("/tmp/out.mp4"), 2, "ref", "video-uri"


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {
            "style": "Anime",
            "style_description": "cinematic",
            "content_mode": "narration",
            "characters": {
                "Alice": {
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice_ref.png",
                    "description": "hero",
                }
            },
            "clues": {
                "玉佩": {
                    "type": "prop",
                    "clue_sheet": "clues/玉佩.png",
                    "description": "clue",
                }
            },
        }
        self.script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "characters_in_segment": ["Alice"],
                    "clues_in_segment": ["玉佩"],
                    "generated_assets": {},
                }
            ],
        }
        self.updated = []

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        return self.script

    def update_scene_asset(self, **kwargs):
        self.updated.append(kwargs)

    def save_project(self, project_name, project):
        self.project = project



def _prepare_files(tmp_path: Path) -> Path:
    project_path = tmp_path / "projects" / "demo"
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "characters" / "refs").mkdir(parents=True, exist_ok=True)
    (project_path / "clues").mkdir(parents=True, exist_ok=True)

    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "characters" / "refs" / "Alice_ref.png").write_bytes(b"png")
    (project_path / "clues" / "玉佩.png").write_bytes(b"png")
    return project_path


def _client(monkeypatch, fake_pm, fake_generator):
    monkeypatch.setattr(generate, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(generate, "get_media_generator", lambda _project: fake_generator)
    monkeypatch.setattr(generate, "_get_video_semaphore", lambda: __import__("asyncio").Semaphore(1))

    app = FastAPI()
    app.include_router(generate.router, prefix="/api/v1")
    return TestClient(app)


class TestGenerateRouter:
    def test_storyboard_video_character_clue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        client = _client(monkeypatch, fake_pm, fake_generator)

        with client:
            sb = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {
                        "scene": "雨夜",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                },
            )
            assert sb.status_code == 200
            assert sb.json()["version"] == 1

            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "duration_seconds": 5,
                    "prompt": {
                        "action": "奔跑",
                        "camera_motion": "Static",
                        "ambiance_audio": "雨声",
                        "dialogue": [{"speaker": "Alice", "line": "快走"}],
                    },
                },
            )
            assert video.status_code == 200
            assert video.json()["version"] == 2

            character = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
                json={"prompt": "女主，冷静"},
            )
            assert character.status_code == 200
            assert character.json()["file_path"] == "characters/Alice.png"

            clue = client.post(
                "/api/v1/projects/demo/generate/clue/玉佩",
                json={"prompt": "古朴玉佩"},
            )
            assert clue.status_code == 200
            assert clue.json()["file_path"] == "clues/玉佩.png"

            assert fake_pm.updated

    def test_error_paths(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        client = _client(monkeypatch, fake_pm, fake_generator)

        with client:
            bad_prompt = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": {"composition": {}}},
            )
            assert bad_prompt.status_code == 400

            # remove storyboard so video endpoint hits pre-check error
            (project_path / "storyboards" / "scene_E1S01.png").unlink()
            no_storyboard = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "text"},
            )
            assert no_storyboard.status_code == 400

            bad_video_prompt = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": {"action": ""}},
            )
            assert bad_video_prompt.status_code in (400, 500)

            fake_pm.project["characters"] = {}
            missing_char = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
                json={"prompt": "x"},
            )
            assert missing_char.status_code == 404

            fake_pm.project["clues"] = {}
            missing_clue = client.post(
                "/api/v1/projects/demo/generate/clue/玉佩",
                json={"prompt": "x"},
            )
            assert missing_clue.status_code == 404

    def test_helper_functions(self):
        assert generate.get_aspect_ratio({"content_mode": "narration"}, "storyboards") == "9:16"
        assert generate.get_aspect_ratio({"content_mode": "drama"}, "storyboards") == "16:9"
        assert generate.get_aspect_ratio({"aspect_ratio": {"videos": "4:3"}}, "videos") == "4:3"

        assert generate.normalize_veo_duration_seconds(None) == "4"
        assert generate.normalize_veo_duration_seconds(6) == "6"
        assert generate.normalize_veo_duration_seconds(99) == "8"
