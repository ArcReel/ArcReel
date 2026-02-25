import json
from pathlib import Path

import pytest

from lib.script_generator import ScriptGenerator


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict):
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _valid_narration_response() -> dict:
    return {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "duration_seconds": 4,
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "1", "source_file": "source.md"},
        "characters_in_episode": ["姜月茴"],
        "clues_in_episode": ["玉佩"],
        "segments": [
            {
                "segment_id": "E1S01",
                "episode": 1,
                "duration_seconds": 4,
                "segment_break": False,
                "novel_text": "原文",
                "characters_in_segment": ["姜月茴"],
                "clues_in_segment": ["玉佩"],
                "image_prompt": {
                    "scene": "场景",
                    "composition": {
                        "shot_type": "Medium Shot",
                        "lighting": "暖光",
                        "ambiance": "薄雾",
                    },
                },
                "video_prompt": {
                    "action": "转身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [],
                },
            }
        ],
    }


class _FakeGeminiClient:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.calls = []

    def generate_text(self, prompt, model, response_schema):
        self.calls.append((prompt, model, response_schema))
        return self._response_text


class TestScriptGenerator:
    def test_build_prompt_uses_step1_content(self, tmp_path, monkeypatch):
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {"synopsis": "概述"},
                "characters": {"姜月茴": {}},
                "clues": {"玉佩": {}},
                "style": "古风",
                "style_description": "cinematic",
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_segments.md", "E1S01 | 片段")

        fake = _FakeGeminiClient(json.dumps(_valid_narration_response(), ensure_ascii=False))
        monkeypatch.setattr("lib.script_generator.GeminiClient", lambda: fake)

        generator = ScriptGenerator(project_path)
        prompt = generator.build_prompt(1)

        assert "E1S01 | 片段" in prompt
        assert "姜月茴" in prompt

    def test_load_step1_falls_back_when_primary_missing(self, tmp_path, monkeypatch):
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {},
                "characters": {},
                "clues": {},
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "fallback")

        fake = _FakeGeminiClient(json.dumps(_valid_narration_response(), ensure_ascii=False))
        monkeypatch.setattr("lib.script_generator.GeminiClient", lambda: fake)

        generator = ScriptGenerator(project_path)
        content = generator._load_step1(1)
        assert content == "fallback"

    def test_parse_response_invalid_json_raises(self, tmp_path, monkeypatch):
        project_path = tmp_path / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})
        fake = _FakeGeminiClient("{}")
        monkeypatch.setattr("lib.script_generator.GeminiClient", lambda: fake)

        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError):
            generator._parse_response("not-json", 1)

    def test_parse_response_validation_error_returns_raw_data(self, tmp_path, monkeypatch):
        project_path = tmp_path / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})
        fake = _FakeGeminiClient("{}")
        monkeypatch.setattr("lib.script_generator.GeminiClient", lambda: fake)

        generator = ScriptGenerator(project_path)
        parsed = generator._parse_response('{"foo": "bar"}', 1)
        assert parsed == {"foo": "bar"}

    def test_generate_writes_script_and_metadata(self, tmp_path, monkeypatch):
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {},
                "characters": {"姜月茴": {}},
                "clues": {"玉佩": {}},
                "style": "古风",
                "style_description": "cinematic",
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_segments.md", "E1S01 | 片段")

        fake = _FakeGeminiClient(json.dumps(_valid_narration_response(), ensure_ascii=False))
        monkeypatch.setattr("lib.script_generator.GeminiClient", lambda: fake)

        generator = ScriptGenerator(project_path)
        output = generator.generate(1)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert output == project_path / "scripts" / "episode_1.json"
        assert payload["episode"] == 1
        assert payload["duration_seconds"] == 4
        assert payload["metadata"]["generator"] == ScriptGenerator.MODEL
        assert "created_at" in payload["metadata"]
