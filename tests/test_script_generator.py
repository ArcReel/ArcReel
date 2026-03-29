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


class _FakeTextBackend:
    def __init__(self, response_text: str = "{}"):
        self._response_text = response_text
        self.last_request = None

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    @property
    def capabilities(self):
        return set()

    async def generate(self, request):
        self.last_request = request
        from lib.text_backends.base import TextGenerationResult
        return TextGenerationResult(text=self._response_text, provider="fake", model="fake-model")


class _FakeTextGenerator:
    """模拟 TextGenerator，包装 _FakeTextBackend。"""

    def __init__(self, response_text: str = "{}"):
        self.backend = _FakeTextBackend(response_text)
        self.model = self.backend.model

    async def generate(self, request, project_name=None):
        return await self.backend.generate(request)


class TestScriptGenerator:
    async def test_build_prompt_uses_step1_content(self, tmp_path):
        """build_prompt 无需 client 即可使用（dry-run 模式）。"""
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

        generator = ScriptGenerator(project_path)  # 无 client
        prompt = generator.build_prompt(1)

        assert "E1S01 | 片段" in prompt
        assert "姜月茴" in prompt

    async def test_load_step1_falls_back_when_primary_missing(self, tmp_path):
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

        generator = ScriptGenerator(project_path)
        content = generator._load_step1(1)
        assert content == "fallback"

    async def test_parse_response_invalid_json_raises(self, tmp_path):
        project_path = tmp_path / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})

        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError):
            generator._parse_response("not-json", 1)

    async def test_parse_response_validation_error_returns_raw_data(self, tmp_path):
        project_path = tmp_path / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})

        generator = ScriptGenerator(project_path)
        parsed = generator._parse_response('{"foo": "bar"}', 1)
        assert parsed == {"foo": "bar"}

    async def test_generate_writes_script_and_metadata(self, tmp_path):
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

        fake = _FakeTextGenerator(json.dumps(_valid_narration_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        output = await generator.generate(1)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert output == project_path / "scripts" / "episode_1.json"
        assert payload["episode"] == 1
        assert payload["duration_seconds"] == 4
        assert payload["metadata"]["generator"] == "fake-model"
        assert "created_at" in payload["metadata"]

    async def test_generate_passes_pydantic_class_as_schema(self, tmp_path):
        """generate 应传入 Pydantic 类而非 model_json_schema() dict。"""
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": "drama",
                "overview": {},
                "characters": {"姜月茴": {}},
                "clues": {"玉佩": {}},
                "style": "古风",
                "style_description": "cinematic",
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "E1S01 | 场景")

        from lib.script_models import DramaEpisodeScript

        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake)
        # generate 会因验证失败但 schema 已传入，检查传入的 schema 是否为类
        await generator.generate(1)
        assert fake.backend.last_request.response_schema is DramaEpisodeScript

    async def test_generate_without_backend_raises(self, tmp_path):
        """未注入 backend 时调用 generate() 应抛 RuntimeError。"""
        project_path = tmp_path / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})
        _write(project_path / "drafts" / "episode_1" / "step1_segments.md", "content")

        generator = ScriptGenerator(project_path)  # 无 backend
        with pytest.raises(RuntimeError, match="TextGenerator 未初始化"):
            await generator.generate(1)
