from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


def test_main_defaults_to_episode_mode_without_flag(monkeypatch):
    """未指定 --scene/--scenes/--all 时，默认走整集模式；集号不再由 CLI 传入。"""
    module = _load_module()
    captured = {}

    def _fake_generate(script_filename, resume=False):
        captured["script_filename"] = script_filename
        captured["resume"] = resume

    monkeypatch.setattr(module, "generate_episode_video", _fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_video.py", "episode_2.json", "--resume"],
    )

    module.main()

    assert captured == {
        "script_filename": "episode_2.json",
        "resume": True,
    }


def _make_fake_pm(tmp_path, script_payload):
    class FakePM:
        @classmethod
        def from_cwd(cls):
            return cls(), "proj"

        @staticmethod
        def resolve_episode_from_script(script, script_filename):
            from lib.project_manager import ProjectManager

            return ProjectManager.resolve_episode_from_script(script, script_filename)

        def get_project_path(self, name):
            return tmp_path

        def load_project(self, name):
            return {}

        def load_script(self, project_name, filename):
            return script_payload

    return FakePM


def test_generate_episode_video_keeps_scenes_without_episode_field(monkeypatch, tmp_path):
    """回归：script 文件按集分开，场景不携带 episode 字段；集号由 resolver 推导，所有场景应进入生成流水线。"""
    module = _load_module()

    fake_script = {
        "episode": 2,
        "content_mode": "drama",
        "scenes": [
            {"scene_id": "E2S01", "video_prompt": "p1"},
            {"scene_id": "E2S02", "video_prompt": "p2"},
        ],
    }
    monkeypatch.setattr(module, "ProjectManager", _make_fake_pm(tmp_path, fake_script))

    captured: dict = {}

    def fake_scan(items, id_field, item_type, completed_scenes, videos_dir):
        captured["scanned"] = list(items)
        return [None] * len(items), []

    monkeypatch.setattr(module, "_scan_completed_items", fake_scan)

    def fake_build(*, items, **kwargs):
        captured["built"] = list(items)
        return [], {}

    monkeypatch.setattr(module, "_build_video_specs", fake_build)

    with pytest.raises(RuntimeError):
        module.generate_episode_video("episode_2.json")

    assert [it["scene_id"] for it in captured["scanned"]] == ["E2S01", "E2S02"]
    assert [it["scene_id"] for it in captured["built"]] == ["E2S01", "E2S02"]


def test_generate_episode_video_resolves_episode_from_filename(monkeypatch, tmp_path, capsys):
    """script 无顶层 episode 字段时，集号从文件名正则 fallback 推导。"""
    module = _load_module()

    fake_script = {
        "content_mode": "drama",
        "scenes": [{"scene_id": "E3S01", "video_prompt": "p"}],
    }
    monkeypatch.setattr(module, "ProjectManager", _make_fake_pm(tmp_path, fake_script))
    monkeypatch.setattr(module, "_scan_completed_items", lambda *a, **kw: ([None], []))
    monkeypatch.setattr(module, "_build_video_specs", lambda **kw: ([], {}))

    with pytest.raises(RuntimeError):
        module.generate_episode_video("episode_3.json")

    # 日志中出现 "第 3 集" 证明 episode 由文件名成功推导
    assert "第 3 集" in capsys.readouterr().out
