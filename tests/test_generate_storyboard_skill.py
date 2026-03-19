from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from lib.generation_queue_client import WorkerOfflineError


SCRIPT_PATH = Path(
    Path(__file__).resolve().parents[1]
    / "agent_runtime_profile"
    / ".claude"
    / "skills"
    / "generate-storyboard"
    / "scripts"
    / "generate_storyboard.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("test_generate_storyboard_skill_module", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeProjectManager:
    """Fake that supports both from_cwd() and load_script()."""

    @classmethod
    def from_cwd(cls):
        return cls(), "demo"

    def load_script(self, project_name: str, script_file: str):
        return {"content_mode": "narration"}


def test_main_supports_scene_flag(monkeypatch):
    module = _load_module()
    captured = {}

    monkeypatch.setattr(module, "ProjectManager", _FakeProjectManager)

    def _fake_generate(script_filename, segment_ids=None):
        captured["script_filename"] = script_filename
        captured["segment_ids"] = segment_ids
        return [], []

    monkeypatch.setattr(module, "generate_storyboard_direct", _fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_storyboard.py", "episode_1.json", "--scene", "E1S05"],
    )

    module.main()

    assert captured == {
        "script_filename": "episode_1.json",
        "segment_ids": ["E1S05"],
    }


class _FakeQueueProjectManager:
    def __init__(self):
        self.project = {
            "content_mode": "narration",
            "style": "Anime",
            "style_description": "cinematic",
            "characters": {},
            "clues": {},
        }
        self.script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S05",
                    "image_prompt": "雨夜街道",
                    "generated_assets": {},
                }
            ],
        }

    @classmethod
    def from_cwd(cls):
        return cls(), "demo"

    def load_script(self, project_name: str, script_file: str):
        return self.script

    def get_project_path(self, project_name: str):
        return Path("/tmp/demo")

    def project_exists(self, project_name: str) -> bool:
        return True

    def load_project(self, project_name: str):
        return self.project


def test_generate_storyboard_direct_requires_online_worker(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "ProjectManager", _FakeQueueProjectManager)

    def _raise_worker_offline(**kwargs):
        raise WorkerOfflineError("queue worker is offline")

    monkeypatch.setattr(module, "batch_enqueue_and_wait_sync", _raise_worker_offline)

    with pytest.raises(WorkerOfflineError):
        module.generate_storyboard_direct(
            "episode_1.json",
            segment_ids=["E1S05"],
        )
