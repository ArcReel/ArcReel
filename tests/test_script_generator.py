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
        "novel": {"title": "小说", "chapter": "1"},
        "segments": [
            {
                "segment_id": "E1S01",
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
                "_supported_durations": [4, 6, 8],
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_segments.md", "E1S01 | 片段")

        generator = ScriptGenerator(project_path)  # 无 client
        prompt = await generator.build_prompt(1)

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
                "_supported_durations": [4, 6, 8],
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

    async def test_generate_overrides_hallucinated_episode_field(self, tmp_path):
        """AI 返回带错误 episode 字段时，CLI 参数 episode 必须胜出。

        回归：AI 幻觉在 episode_10.json 内部写 episode=1，导致 project.json 第 1 集
        条目被覆盖。修复后 schema 已移除 episode 字段，_add_metadata 强制盖章 CLI 值。
        """
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
                "_supported_durations": [4, 6, 8],
            },
        )
        _write(project_path / "drafts" / "episode_10" / "step1_segments.md", "E10S01 | 片段")

        # 模拟 AI 响应：内部错误地填了 episode=1
        hallucinated = _valid_narration_response()
        hallucinated["episode"] = 1
        hallucinated["title"] = "第十集"
        fake = _FakeTextGenerator(json.dumps(hallucinated, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        output = await generator.generate(10)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert output == project_path / "scripts" / "episode_10.json"
        assert payload["episode"] == 10

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
                "_supported_durations": [4, 6, 8],
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "E1S01 | 场景")

        from lib.script_models import DramaEpisodeScript

        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake)
        # generate 会因验证失败但 schema 已传入，检查传入的 schema 是否为类
        await generator.generate(1)
        assert fake.backend.last_request.response_schema is DramaEpisodeScript

    async def test_generate_sets_script_max_output_tokens(self, tmp_path):
        """generate 应在 TextGenerationRequest 上设置 SCRIPT_MAX_OUTPUT_TOKENS。"""
        from lib.script_generator import SCRIPT_MAX_OUTPUT_TOKENS

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
                "_supported_durations": [4, 6, 8],
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "E1S01 | 场景")

        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake)
        await generator.generate(1)

        assert fake.backend.last_request.max_output_tokens == SCRIPT_MAX_OUTPUT_TOKENS
        assert SCRIPT_MAX_OUTPUT_TOKENS >= 16000

    async def test_generate_without_backend_raises(self, tmp_path):
        """未注入 backend 时调用 generate() 应抛 RuntimeError。"""
        project_path = tmp_path / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})
        _write(project_path / "drafts" / "episode_1" / "step1_segments.md", "content")

        generator = ScriptGenerator(project_path)  # 无 backend
        with pytest.raises(RuntimeError, match="TextGenerator 未初始化"):
            await generator.generate(1)


class TestAddMetadataRewritesEpisodePrefix:
    """_add_metadata 兜底改写 segment/scene/unit ID 的 E\\d+ 前缀（#574）。"""

    @staticmethod
    def _make_generator(tmp_path: Path, content_mode: str = "narration") -> ScriptGenerator:
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": content_mode,
                "_supported_durations": [4, 6, 8],
            },
        )
        return ScriptGenerator(project_path)

    def test_drama_rewrites_scene_ids(self, tmp_path: Path) -> None:
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {
            "scenes": [
                {"scene_id": "E1S01", "other": "keep"},
                {"scene_id": "E1S04_2"},
            ],
        }
        out = sg._add_metadata(data, episode=2)
        assert out["scenes"][0]["scene_id"] == "E2S01"
        assert out["scenes"][1]["scene_id"] == "E2S04_2"
        assert out["scenes"][0]["other"] == "keep"

    def test_narration_rewrites_segment_ids(self, tmp_path: Path) -> None:
        sg = self._make_generator(tmp_path, content_mode="narration")
        data = {
            "segments": [
                {"segment_id": "E1S01"},
                {"segment_id": "E1S02_1"},
            ],
        }
        out = sg._add_metadata(data, episode=3)
        assert out["segments"][0]["segment_id"] == "E3S01"
        assert out["segments"][1]["segment_id"] == "E3S02_1"

    def test_reference_video_rewrites_unit_ids(self, tmp_path: Path) -> None:
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "_supported_durations": [8],
            },
        )
        sg = ScriptGenerator(project_path)
        data = {
            "video_units": [
                {"unit_id": "E1U01"},
                {"unit_id": "E1U02_1"},
            ],
        }
        out = sg._add_metadata(data, episode=2)
        assert out["video_units"][0]["unit_id"] == "E2U01"
        assert out["video_units"][1]["unit_id"] == "E2U02_1"

    def test_idempotent_when_prefix_already_correct(self, tmp_path: Path) -> None:
        """ID 前缀已经匹配 episode 时，rewrite 不应改动（不破坏正确数据）。"""
        sg = self._make_generator(tmp_path, content_mode="narration")
        data = {"segments": [{"segment_id": "E2S01"}, {"segment_id": "E2S02_3"}]}
        out = sg._add_metadata(data, episode=2)
        assert out["segments"][0]["segment_id"] == "E2S01"
        assert out["segments"][1]["segment_id"] == "E2S02_3"

    def test_unknown_id_format_unchanged(self, tmp_path: Path) -> None:
        """ID 不带 `E\\d+[SU]` 前缀时不应被改写（避免误伤）。"""
        sg = self._make_generator(tmp_path, content_mode="narration")
        data = {"segments": [{"segment_id": "G01"}, {"segment_id": "scene_1"}]}
        out = sg._add_metadata(data, episode=2)
        assert out["segments"][0]["segment_id"] == "G01"
        assert out["segments"][1]["segment_id"] == "scene_1"


class TestAddMetadataInjectsHiddenFields:
    """LLM schema 隐藏 content_mode / novel 之后,_add_metadata 必须保证持久化 JSON 仍带这些字段。

    下游消费方(status_calculator / files router / jianying / compose-video)读 dict,不读 model,
    所以兜底必须落在 dict 层。
    """

    @staticmethod
    def _make_generator(tmp_path: Path, content_mode: str = "drama") -> ScriptGenerator:
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目标题",
                "content_mode": content_mode,
                "_supported_durations": [4, 6, 8],
            },
        )
        return ScriptGenerator(project_path)

    def test_drama_injects_content_mode_and_novel_when_llm_omits(self, tmp_path: Path) -> None:
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {"title": "第一集", "scenes": [{"scene_id": "E1S01"}]}
        out = sg._add_metadata(data, episode=1)
        assert out["content_mode"] == "drama"
        assert out["novel"] == {"title": "项目标题", "chapter": "第1集"}

    def test_narration_injects_content_mode_and_novel_when_llm_omits(self, tmp_path: Path) -> None:
        sg = self._make_generator(tmp_path, content_mode="narration")
        data = {"title": "第一集", "segments": [{"segment_id": "E1S01"}]}
        out = sg._add_metadata(data, episode=1)
        assert out["content_mode"] == "narration"
        assert out["novel"]["chapter"] == "第1集"

    def test_setdefault_does_not_overwrite_existing_values(self, tmp_path: Path) -> None:
        """LLM 若主动填了 content_mode / novel(理论上不会,但兜底要稳),setdefault 不应覆盖。"""
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {
            "title": "第一集",
            "content_mode": "drama",
            "novel": {"title": "用户的小说", "chapter": "卷一·风起"},
            "scenes": [{"scene_id": "E1S01"}],
        }
        out = sg._add_metadata(data, episode=1)
        assert out["content_mode"] == "drama"
        assert out["novel"] == {"title": "用户的小说", "chapter": "卷一·风起"}

    def test_drama_overrides_empty_novel_after_model_dump(self, tmp_path: Path) -> None:
        """e2e: model_validate → model_dump 后 novel 永远存在但为空字典,_add_metadata
        必须按"内容是否为空"判断而非"key 是否存在",否则 compose-video 输出文件名将退化为
        '_final.mp4',save_script 退化为 '_script.json',多集互相覆盖。
        """
        from lib.script_models import DramaEpisodeScript

        sg = self._make_generator(tmp_path, content_mode="drama")
        llm_response = {
            "title": "第一集",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "characters_in_scene": ["A"],
                    "image_prompt": {
                        "scene": "s",
                        "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                    },
                    "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                }
            ],
        }
        # 完整模拟 _parse_response: model_validate → model_dump
        dumped = DramaEpisodeScript.model_validate(llm_response).model_dump()
        # 守卫前提:model_dump 已塞入空 NovelInfo
        assert dumped["novel"] == {"title": "", "chapter": ""}

        out = sg._add_metadata(dumped, episode=1)
        assert out["novel"] == {"title": "项目标题", "chapter": "第1集"}

    def test_narration_overrides_empty_novel_after_model_dump(self, tmp_path: Path) -> None:
        from lib.script_models import NarrationEpisodeScript

        sg = self._make_generator(tmp_path, content_mode="narration")
        llm_response = {
            "title": "第一集",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "x",
                    "characters_in_segment": [],
                    "image_prompt": {
                        "scene": "s",
                        "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                    },
                    "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                }
            ],
        }
        dumped = NarrationEpisodeScript.model_validate(llm_response).model_dump()
        assert dumped["novel"] == {"title": "", "chapter": ""}

        out = sg._add_metadata(dumped, episode=2)
        assert out["novel"] == {"title": "项目标题", "chapter": "第2集"}

    def test_partial_novel_only_title_is_also_reinjected(self, tmp_path: Path) -> None:
        """半填 novel(只有 title 或只有 chapter)也应触发重注入,避免 compose-video 文件名残缺。"""
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {
            "title": "第一集",
            "novel": {"title": "残缺标题", "chapter": ""},
            "scenes": [{"scene_id": "E1S01"}],
        }
        out = sg._add_metadata(data, episode=1)
        assert out["novel"]["chapter"] == "第1集"
        assert out["novel"]["title"] == "项目标题"


def test_resolve_supported_durations_raises_when_unset(tmp_path):
    """caps、project.json、registry 三处都查不到时应抛 ValueError，不再 silent fallback。"""
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        '{"video_backend": "nonexistent-provider/nonexistent-model"}', encoding="utf-8"
    )
    sg = ScriptGenerator.__new__(ScriptGenerator)
    sg.project_path = project_dir
    sg.project_json = {"video_backend": "nonexistent-provider/nonexistent-model"}

    with pytest.raises(ValueError, match="supported_durations"):
        sg._resolve_supported_durations(None)
