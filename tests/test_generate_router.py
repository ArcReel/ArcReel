from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import generate


class _FakeQueue:
    """Mock GenerationQueue that records enqueue calls."""

    def __init__(self):
        self.calls = []

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}


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
                    "segment_break": False,
                    "characters_in_segment": [],
                    "clues_in_segment": [],
                    "generated_assets": {},
                },
                {
                    "segment_id": "E1S02",
                    "duration_seconds": 4,
                    "segment_break": False,
                    "characters_in_segment": ["Alice"],
                    "clues_in_segment": ["玉佩"],
                    "generated_assets": {},
                },
                {
                    "segment_id": "E1S03",
                    "duration_seconds": 4,
                    "segment_break": True,
                    "characters_in_segment": ["Alice"],
                    "clues_in_segment": ["玉佩"],
                    "generated_assets": {},
                },
            ],
        }

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        return self.script


def _prepare_files(tmp_path: Path) -> Path:
    project_path = tmp_path / "projects" / "demo"
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "clues").mkdir(parents=True, exist_ok=True)

    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "clues" / "玉佩.png").write_bytes(b"png")
    return project_path


def _client(monkeypatch, fake_pm, fake_queue):
    monkeypatch.setattr(generate, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr("lib.generation_queue.get_generation_queue", lambda: fake_queue)
    monkeypatch.setattr(generate, "get_generation_queue", lambda: fake_queue)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1")
    return TestClient(app)


class TestGenerateRouter:
    def test_storyboard_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            sb = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {
                        "scene": "雨夜",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                },
            )
            assert sb.status_code == 200
            body = sb.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"
            assert "message" in body

            # Verify enqueue was called correctly
            call = fake_queue.calls[0]
            assert call["project_name"] == "demo"
            assert call["task_type"] == "storyboard"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "E1S02"
            assert call["source"] == "webui"

    def test_storyboard_enqueue_passes_extra_reference_images(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            sb = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={
                    "script_file": "episode_1.json",
                    "prompt": "test prompt",
                    "extra_reference_images": ["source/ref.png"],
                },
            )
            assert sb.status_code == 200

            call = fake_queue.calls[0]
            assert call["payload"]["extra_reference_images"] == ["source/ref.png"]

    def test_video_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
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
            body = video.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "video"
            assert call["media_type"] == "video"
            assert call["payload"]["duration_seconds"] == 5

    def test_character_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            character = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
                json={"prompt": "女主，冷静"},
            )
            assert character.status_code == 200
            body = character.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "character"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "Alice"

    def test_clue_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            clue = client.post(
                "/api/v1/projects/demo/generate/clue/玉佩",
                json={"prompt": "古朴玉佩"},
            )
            assert clue.status_code == 200
            body = clue.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "clue"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "玉佩"

    def test_error_paths(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            # Bad storyboard prompt (structured but missing scene)
            bad_prompt = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={"script_file": "episode_1.json", "prompt": {"composition": {}}},
            )
            assert bad_prompt.status_code == 400

            # Nonexistent segment
            not_found = client.post(
                "/api/v1/projects/demo/generate/storyboard/MISSING",
                json={"script_file": "episode_1.json", "prompt": "test"},
            )
            assert not_found.status_code == 404

            # Video without storyboard
            (project_path / "storyboards" / "scene_E1S01.png").unlink()
            no_storyboard = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "text"},
            )
            assert no_storyboard.status_code == 400

            # Bad video prompt
            bad_video_prompt = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": {"action": ""}},
            )
            assert bad_video_prompt.status_code in (400, 500)

            # Missing character
            fake_pm.project["characters"] = {}
            missing_char = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
                json={"prompt": "x"},
            )
            assert missing_char.status_code == 404

            # Missing clue
            fake_pm.project["clues"] = {}
            missing_clue = client.post(
                "/api/v1/projects/demo/generate/clue/玉佩",
                json={"prompt": "x"},
            )
            assert missing_clue.status_code == 404
