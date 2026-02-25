from pathlib import Path

import pytest

from webui.server.services import generation_tasks


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {
            "content_mode": "narration",
            "style": "Anime",
            "style_description": "cinematic",
            "characters": {
                "Alice": {
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice-ref.png",
                }
            },
            "clues": {"玉佩": {"type": "prop", "clue_sheet": "clues/玉佩.png"}},
        }
        self.script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "characters_in_segment": ["Alice"],
                    "clues_in_segment": ["玉佩"],
                    "image_prompt": {
                        "scene": "在雨夜街道",
                        "composition": {
                            "shot_type": "Medium Shot",
                            "lighting": "暖光",
                            "ambiance": "薄雾",
                        },
                    },
                }
            ],
        }
        self.updated_assets = []

    def load_project(self, project_name: str):
        return self.project

    def get_project_path(self, project_name: str):
        return self.project_path

    def load_script(self, project_name: str, script_file: str):
        return self.script

    def update_scene_asset(self, **kwargs):
        self.updated_assets.append(kwargs)

    def save_project(self, project_name: str, project: dict):
        self.project = project

    def project_exists(self, project_name: str) -> bool:
        return True


class _FakeGenerator:
    def __init__(self):
        self.image_calls = []
        self.video_calls = []
        self.versions = self

    def generate_image(self, **kwargs):
        self.image_calls.append(kwargs)
        return Path("/tmp/image.png"), 1

    def generate_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return Path("/tmp/video.mp4"), 2, "ref", "uri"

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"created_at": "2026-01-01T00:00:00Z"}]}


class _FakeGeminiClient:
    def __init__(self, rate_limiter=None):
        self.calls = []

    def generate_image(self, **kwargs):
        self.calls.append(kwargs)


def _prepare_files(tmp_path: Path):
    project_path = tmp_path / "projects" / "demo"
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "characters" / "refs").mkdir(parents=True, exist_ok=True)
    (project_path / "clues").mkdir(parents=True, exist_ok=True)
    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "characters" / "refs" / "Alice-ref.png").write_bytes(b"png")
    (project_path / "clues" / "玉佩.png").write_bytes(b"png")
    return project_path


class TestGenerationTasks:
    def test_helper_functions(self, tmp_path):
        assert generation_tasks.normalize_veo_duration_seconds(None) == "4"
        assert generation_tasks.normalize_veo_duration_seconds(5) == "6"
        assert generation_tasks.normalize_veo_duration_seconds(9) == "8"

        mode_items = generation_tasks._get_items_from_script({"content_mode": "drama", "scenes": []})
        assert mode_items[1] == "scene_id"

        prompt = generation_tasks._normalize_storyboard_prompt("text", "Anime")
        assert prompt == "text"

        with pytest.raises(ValueError):
            generation_tasks._normalize_storyboard_prompt({"scene": ""}, "Anime")

        video_yaml = generation_tasks._normalize_video_prompt(
            {
                "action": "行走",
                "camera_motion": "",
                "ambiance_audio": "风声",
                "dialogue": [{"speaker": "Alice", "line": "hello"}],
            }
        )
        assert "Camera_Motion" in video_yaml

        with pytest.raises(ValueError):
            generation_tasks._normalize_video_prompt({"action": ""})

        assert generation_tasks._get_grid_layout(4)[2] == "2x2 四宫格"
        assert generation_tasks._get_grid_layout(5)[2] == "2x3 六宫格"

    def test_execute_task_dispatch_and_storyboard_grid(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", lambda _p: fake_generator)
        monkeypatch.setattr(generation_tasks, "GeminiClient", _FakeGeminiClient)

        storyboard_result = generation_tasks.execute_storyboard_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": "direct prompt", "extra_reference_images": ["characters/Alice.png"]},
        )
        assert storyboard_result["resource_type"] == "storyboards"

        video_result = generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert video_result["resource_type"] == "videos"
        assert video_result["video_uri"] == "uri"

        character_result = generation_tasks.execute_character_task(
            "demo",
            "Alice",
            {"prompt": "角色描述"},
        )
        assert character_result["resource_type"] == "characters"
        assert fake_pm.project["characters"]["Alice"]["character_sheet"] == "characters/Alice.png"

        clue_result = generation_tasks.execute_clue_task(
            "demo",
            "玉佩",
            {"prompt": "线索描述"},
        )
        assert clue_result["resource_type"] == "clues"

        grid_result = generation_tasks.execute_storyboard_grid_task(
            "demo",
            {"script_file": "episode_1.json", "batch_id": 1, "scene_ids": ["E1S01"]},
        )
        assert grid_result["resource_type"] == "storyboard_grid"

        dispatch = generation_tasks.execute_generation_task(
            {
                "task_type": "storyboard",
                "project_name": "demo",
                "resource_id": "E1S01",
                "payload": {"script_file": "episode_1.json", "prompt": "text"},
            }
        )
        assert dispatch["resource_type"] == "storyboards"

        with pytest.raises(ValueError):
            generation_tasks.execute_generation_task({"task_type": "unknown", "project_name": "demo", "resource_id": "x", "payload": {}})

    def test_execute_task_validation_errors(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", lambda _p: _FakeGenerator())

        with pytest.raises(ValueError):
            generation_tasks.execute_storyboard_task("demo", "E1S01", {"prompt": "x"})

        with pytest.raises(ValueError):
            generation_tasks.execute_video_task("demo", "E1S01", {"script_file": "episode_1.json"})

        (project_path / "storyboards" / "scene_E1S01.png").unlink()
        with pytest.raises(ValueError):
            generation_tasks.execute_video_task(
                "demo", "E1S01", {"script_file": "episode_1.json", "prompt": "x"}
            )

        with pytest.raises(ValueError):
            generation_tasks.execute_character_task("demo", "Alice", {"prompt": ""})

        with pytest.raises(ValueError):
            generation_tasks.execute_clue_task("demo", "玉佩", {"prompt": ""})

        with pytest.raises(ValueError):
            generation_tasks.execute_storyboard_grid_task("demo", {"script_file": "episode_1.json", "batch_id": "bad", "scene_ids": []})
