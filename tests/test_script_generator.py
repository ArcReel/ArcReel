import json
from pathlib import Path

import pytest

from lib.script_generator import ScriptGenerator
from lib.script_structure_validator import ScriptStructureValidationError


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


def _write_drama_ledger_project(project_path: Path, episodes: list[dict], characters: dict | None = None) -> None:
    """写一个带分集账本条目的最小 drama 项目 project.json。"""
    _write_json(
        project_path / "project.json",
        {
            "title": "项目",
            "content_mode": "drama",
            "overview": {},
            "characters": characters or {},
            "style": "古风",
            "style_description": "cinematic",
            "_supported_durations": [4, 6, 8],
            "episodes": episodes,
        },
    )


def _valid_drama_response() -> dict:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["姜月茴"],
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

    async def test_load_step1_narration_missing_raises_without_fallback(self, tmp_path):
        """narration 集缺 step1_segments.md 时显式报错并指明期望文件；
        即使 drama 模式的中间文件存在也不得降级改读。"""
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
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "其他模式中间文件")

        generator = ScriptGenerator(project_path)
        with pytest.raises(FileNotFoundError, match="step1_segments.md"):
            generator._load_step1(1)

    async def test_load_step1_drama_missing_raises_without_fallback(self, tmp_path):
        """drama 集缺 step1_normalized_script.md 时显式报错；不得降级改读 narration 的拆分表。"""
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": "drama",
                "overview": {},
                "characters": {},
                "clues": {},
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_segments.md", "其他模式中间文件")

        generator = ScriptGenerator(project_path)
        with pytest.raises(FileNotFoundError, match="step1_normalized_script.md"):
            generator._load_step1(1)

    async def test_drama_prompt_includes_current_and_next_episode_outlines(self, tmp_path):
        """drama 剧本生成输入须包含账本里本集大纲（故事节点/钩子/下集预告）与下集大纲。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [
                {
                    "episode": 1,
                    "title": "初入江湖",
                    "script_file": "scripts/episode_1.json",
                    "hook": "少年坠崖生死未卜",
                    "outline": {
                        "story_beats": ["少年下山", "初遇黑衣人"],
                        "next_episode_teaser": "崖底神秘人出手相救",
                    },
                    "ledger_status": "planned",
                },
                {
                    "episode": 2,
                    "title": "绝处逢生",
                    "script_file": "scripts/episode_2.json",
                    "hook": "神秘人身份揭晓",
                    "outline": {
                        "story_beats": ["崖底醒来", "拜师学艺"],
                        "next_episode_teaser": None,
                    },
                    "ledger_status": "planned",
                },
            ],
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "E1S01 | 场景")

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        # 本集大纲：本集标题 / 故事节点 / 集尾钩子 / 下集预告语
        assert "本集标题：初入江湖" in prompt
        assert "少年下山" in prompt
        assert "初遇黑衣人" in prompt
        assert "少年坠崖生死未卜" in prompt
        assert "崖底神秘人出手相救" in prompt
        # 下集大纲：用于衔接的下一集内容
        assert "下集标题：绝处逢生" in prompt
        assert "崖底醒来" in prompt

    async def test_drama_prompt_last_episode_without_next_outline(self, tmp_path):
        """末集（账本无下一集条目）正常生成 prompt：含本集大纲，不渲染下集大纲段。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [
                {
                    "episode": 1,
                    "title": "大结局",
                    "script_file": "scripts/episode_1.json",
                    "hook": "尘埃落定",
                    "outline": {"story_beats": ["决战", "告别"], "next_episode_teaser": None},
                    "ledger_status": "planned",
                },
            ],
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "E1S01 | 场景")

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        assert "决战" in prompt
        assert "尘埃落定" in prompt
        assert "<next_episode_outline>" not in prompt

    async def test_drama_prompt_without_ledger_outline_omits_outline_section(self, tmp_path):
        """旧式条目（账本无规划数据）：prompt 不渲染大纲段，生成不受影响。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "E1S01 | 场景")

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        assert "E1S01 | 场景" in prompt
        assert "<episode_outline>" not in prompt
        assert "<next_episode_outline>" not in prompt
        assert "末场" not in prompt

    async def test_drama_prompt_requires_hook_to_land_in_final_scene(self, tmp_path):
        """账本有钩子/预告时，prompt 须要求其落地到末场内容（而非只停留在规划文档）。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [
                {
                    "episode": 1,
                    "title": "初入江湖",
                    "script_file": "scripts/episode_1.json",
                    "hook": "少年坠崖生死未卜",
                    "outline": {
                        "story_beats": ["少年下山"],
                        "next_episode_teaser": "崖底神秘人出手相救",
                    },
                    "ledger_status": "planned",
                },
            ],
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "E1S01 | 场景")

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        # 落地要求与钩子内容须同时在场：仅有指引而无钩子（或反之）都不构成可执行要求
        assert "末场" in prompt
        assert "少年坠崖生死未卜" in prompt

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

    async def test_generate_injects_hook_and_teaser_from_ledger(self, tmp_path):
        """剧本 JSON 的集级 hook / next_episode_teaser 元数据来自分集账本（经写盘严格校验）。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [
                {
                    "episode": 1,
                    "title": "初入江湖",
                    "script_file": "scripts/episode_1.json",
                    "hook": "少年坠崖生死未卜",
                    "outline": {
                        "story_beats": ["少年下山"],
                        "next_episode_teaser": "崖底神秘人出手相救",
                    },
                    "ledger_status": "planned",
                },
            ],
            characters={"姜月茴": {}},
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.md", "E1S01 | 场景")

        fake = _FakeTextGenerator(json.dumps(_valid_drama_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        async def _fixed_caps():
            return {"supported_durations": [4, 6, 8]}

        generator._fetch_video_capabilities = _fixed_caps
        output = await generator.generate(1)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["hook"] == "少年坠崖生死未卜"
        assert payload["next_episode_teaser"] == "崖底神秘人出手相救"

    async def test_generate_without_ledger_hook_leaves_fields_null(self, tmp_path):
        """旧式条目（账本无钩子/预告）：字段为 null，写盘校验仍通过。"""
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {},
                "characters": {"姜月茴": {}},
                "style": "古风",
                "style_description": "cinematic",
                "_supported_durations": [4, 6, 8],
                "episodes": [
                    {"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"},
                ],
            },
        )
        _write(project_path / "drafts" / "episode_1" / "step1_segments.md", "E1S01 | 片段")

        fake = _FakeTextGenerator(json.dumps(_valid_narration_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        output = await generator.generate(1)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["hook"] is None
        assert payload["next_episode_teaser"] is None

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

    async def test_generate_passes_duration_constrained_schema(self, tmp_path):
        """generate 应传入 duration_seconds 被 supported_durations 枚举硬约束的 Pydantic 类。

        schema 是 DramaEpisodeScript 的动态约束子类（非静态类本身），其 scenes 时长字段在
        JSON schema 里渲染为 enum——LLM 结构化输出层即被卡死。
        """
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

        # caps 解析耦合本机 DB（已配置的视频供应商会盖过 project.json 兜底），此处固定能力
        # 让断言 hermetic：验证的是「按解析出的 supported_durations 构造枚举约束」这条机制。
        async def _fixed_caps():
            return {"supported_durations": [4, 6, 8]}

        generator._fetch_video_capabilities = _fixed_caps

        # 结构非法的响应在写盘统一入口被严格校验拒绝；但模型调用已发生，
        # 仍可断言传入的 schema 形态。
        with pytest.raises(ScriptStructureValidationError):
            await generator.generate(1)

        schema = fake.backend.last_request.response_schema
        assert isinstance(schema, type) and issubclass(schema, DramaEpisodeScript)
        duration_enums = [
            props["duration_seconds"].get("enum")
            for props in (d.get("properties", {}) for d in schema.model_json_schema().get("$defs", {}).values())
            if "duration_seconds" in props
        ]
        assert [4, 6, 8] in duration_enums

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
        with pytest.raises(ScriptStructureValidationError):
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

    @pytest.mark.parametrize(
        "bad_filename",
        [
            "subdir/episode_1.json",  # 子目录
            "../etc/passwd",  # path traversal
            "/tmp/abs.json",  # 绝对路径
            "a\\b.json",  # Windows 分隔符
            "",  # 空字符串:Path("").name == "" 会过前两条校验,带空 filename 到写盘才崩
        ],
    )
    async def test_generate_rejects_non_basename_output_filename(self, tmp_path, bad_filename):
        """generate(output_filename=...) 的公开契约「只决定文件名,不接受目录」必须在入口兑现:
        save_script 咽喉的 _safe_subpath 能挡绝对路径与 path traversal,但子目录拼出的 realpath
        仍在 scripts/ 内,不挡;故公开 API 这层必须显式拒,让 docstring 不骗人。
        """
        project_path = tmp_path / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})

        fake = _FakeTextGenerator(json.dumps(_valid_narration_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        with pytest.raises(ValueError, match="只接受纯文件名"):
            await generator.generate(1, output_filename=bad_filename)


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


def _make_probe_generator(tmp_path: Path, project_extra: dict | None = None) -> ScriptGenerator:
    """构造一个只用于 _quality_probe 的 ScriptGenerator,跳过 backend 初始化."""
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    sg = ScriptGenerator.__new__(ScriptGenerator)
    sg.project_path = project_dir
    sg.project_json = {"content_mode": "narration", **(project_extra or {})}
    sg.content_mode = sg.project_json.get("content_mode", "narration")
    return sg


def _write_episode_source(sg: ScriptGenerator, episode: int, text: str) -> None:
    src = sg.project_path / "source" / f"episode_{episode}.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")


def _segment(novel_text: str) -> dict:
    return {
        "segment_id": "E1S01",
        "novel_text": novel_text,
        "image_prompt": {"scene": "x" * 60, "composition": {}},
        "video_prompt": {"action": "x" * 40},
    }


class TestQualityProbeNovelTextDrift:
    """narration 模式 novel_text 漂移 WARN — 不阻断/不重试/不推前端."""

    def test_drift_within_threshold_no_warning(self, tmp_path, caplog, monkeypatch):
        sg = _make_probe_generator(tmp_path, {"generation_mode": "storyboard"})
        monkeypatch.setattr(sg, "_effective_generation_mode", lambda _ep: "storyboard")
        _write_episode_source(sg, 1, "你好" * 50)  # 100 字
        script_data = {"segments": [_segment("你好" * 48)]}  # 96 字,偏差 4% < 10%
        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe(script_data, episode=1)
        assert not any("novel_text drift" in r.message for r in caplog.records)

    def test_drift_above_threshold_warns(self, tmp_path, caplog, monkeypatch):
        sg = _make_probe_generator(tmp_path, {"generation_mode": "storyboard"})
        monkeypatch.setattr(sg, "_effective_generation_mode", lambda _ep: "storyboard")
        _write_episode_source(sg, 2, "你好" * 50)  # 100 字
        script_data = {"segments": [_segment("你好" * 30)]}  # 60 字,偏差 40% > 10%
        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe(script_data, episode=2)
        drift_records = [r for r in caplog.records if "novel_text drift" in r.message]
        assert len(drift_records) == 1
        msg = drift_records[0].getMessage()
        assert "episode 2" in msg
        assert "expected=100" in msg
        assert "actual=60" in msg
        assert "40.0%" in msg

    def test_skipped_when_source_missing(self, tmp_path, caplog, monkeypatch):
        """老用户上传方式可能没切分,source/episode_N.txt 不存在 → 安静跳过."""
        sg = _make_probe_generator(tmp_path, {"generation_mode": "storyboard"})
        monkeypatch.setattr(sg, "_effective_generation_mode", lambda _ep: "storyboard")
        # 不写 source 文件
        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe({"segments": [_segment("a" * 5)]}, episode=1)
        assert not any("novel_text drift" in r.message for r in caplog.records)

    def test_skipped_for_drama_mode(self, tmp_path, caplog, monkeypatch):
        """drama 是改编不是回填,跳过 novel_text 漂移检测."""
        sg = _make_probe_generator(tmp_path, {"content_mode": "drama", "generation_mode": "storyboard"})
        monkeypatch.setattr(sg, "_effective_generation_mode", lambda _ep: "storyboard")
        _write_episode_source(sg, 1, "你好" * 100)
        script_data = {
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "image_prompt": {"scene": "x" * 60, "composition": {}},
                    "video_prompt": {"action": "x" * 40},
                }
            ]
        }
        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe(script_data, episode=1)
        assert not any("novel_text drift" in r.message for r in caplog.records)

    def test_skipped_for_reference_video_mode(self, tmp_path, caplog, monkeypatch):
        """reference_video 不走 narration 回填语义,跳过."""
        sg = _make_probe_generator(tmp_path, {"generation_mode": "reference_video"})
        monkeypatch.setattr(sg, "_effective_generation_mode", lambda _ep: "reference_video")
        _write_episode_source(sg, 1, "你好" * 100)
        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe({"video_units": []}, episode=1)
        assert not any("novel_text drift" in r.message for r in caplog.records)
