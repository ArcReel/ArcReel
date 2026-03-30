from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(
    Path(__file__).resolve().parents[1]
    / "agent_runtime_profile"
    / ".claude"
    / "skills"
    / "generate-video"
    / "scripts"
    / "generate_video.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("test_generate_video_skill_module", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_scene_dispatch_uses_script_and_scene_only(monkeypatch):
    module = _load_module()
    captured = {}

    def _fake_generate(script_filename, scene_id):
        captured["script_filename"] = script_filename
        captured["scene_id"] = scene_id

    monkeypatch.setattr(module, "generate_scene_video", _fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_video.py", "episode_1.json", "--scene", "E1S05"],
    )

    module.main()

    assert captured == {
        "script_filename": "episode_1.json",
        "scene_id": "E1S05",
    }


def test_main_scenes_dispatch_uses_script_once(monkeypatch):
    module = _load_module()
    captured = {}

    def _fake_generate(script_filename, scene_ids, resume=False):
        captured["script_filename"] = script_filename
        captured["scene_ids"] = scene_ids
        captured["resume"] = resume

    monkeypatch.setattr(module, "generate_selected_videos", _fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_video.py",
            "episode_1.json",
            "--scenes",
            "E1S01,E1S05",
            "--resume",
        ],
    )

    module.main()

    assert captured == {
        "script_filename": "episode_1.json",
        "scene_ids": ["E1S01", "E1S05"],
        "resume": True,
    }


def test_main_all_dispatch_uses_script_once(monkeypatch):
    module = _load_module()
    captured = {}

    def _fake_generate(script_filename):
        captured["script_filename"] = script_filename

    monkeypatch.setattr(module, "generate_all_videos", _fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_video.py", "episode_1.json", "--all"],
    )

    module.main()

    assert captured == {
        "script_filename": "episode_1.json",
    }


def test_main_episode_dispatch_uses_script_once(monkeypatch):
    module = _load_module()
    captured = {}

    def _fake_generate(script_filename, episode, resume=False):
        captured["script_filename"] = script_filename
        captured["episode"] = episode
        captured["resume"] = resume

    monkeypatch.setattr(module, "generate_episode_video", _fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_video.py",
            "episode_1.json",
            "--episode",
            "2",
            "--resume",
        ],
    )

    module.main()

    assert captured == {
        "script_filename": "episode_1.json",
        "episode": 2,
        "resume": True,
    }
