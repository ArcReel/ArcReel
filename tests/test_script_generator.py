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


def _drama_step1_content() -> dict:
    """drama step1 结构化内容：含 utterances + source_text + scene_description（视觉改编）。"""
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["姜月茴"],
                "scenes": [],
                "props": [],
                "scene_description": "姜月茴立于庭院，目光沉静，晨光斜照。",
                "utterances": [{"kind": "dialogue", "speaker": "姜月茴", "text": "你来了。"}],
                "source_text": "姜月茴缓步走进庭院，抬眼望来。",
            }
        ],
    }


def _drama_visual_response() -> dict:
    """drama step2 视觉层响应：仅 scene_id + image_prompt + video_prompt（无 dialogue）。"""
    return {
        "scenes": [
            {
                "scene_id": "E1S01",
                "image_prompt": {
                    "scene": "场景",
                    "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                },
                "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
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
        """build_prompt 无需 client 即可使用（dry-run 模式）：narration 渲染结构化 step1。"""
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
        _write_step1_json(project_path, 1, [_step1_seg("E1S01", "第一段原文，逐字保留。", duration=4)])

        generator = ScriptGenerator(project_path)  # 无 client
        prompt = await generator.build_prompt(1)

        assert "E1S01" in prompt
        assert "第一段原文，逐字保留。" in prompt  # novel_text 作只读上下文渲染
        assert "姜月茴" in prompt
        # 透传式 prompt：不再要求 LLM 复制 novel_text，只产视觉层
        assert "只产视觉层" in prompt

    async def test_narration_step2_build_prompt_uses_project_source_language(self, tmp_path):
        """narration step2（视觉层）prompt 的输出语言须取项目 source_language（与 drama 同口径），非中文项目不得回落中文。"""
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {"synopsis": "概述"},
                "characters": {"姜月茴": {}},
                "style": "古风",
                "style_description": "cinematic",
                "source_language": "English",
                "_supported_durations": [4, 6, 8],
            },
        )
        _write_step1_json(project_path, 1, [_step1_seg("E1S01", "verbatim source line.", duration=4)])

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        # 输出语言锁定为项目 source_language，不回落默认中文
        assert "所有字符串值必须使用 English" in prompt
        assert "所有字符串值必须使用 中文" not in prompt

    async def test_load_step1_drama_missing_raises_without_fallback(self, tmp_path):
        """drama 集缺 step1_normalized_script.json 时显式报错；不得降级改读 narration 的拆分表。"""
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
        with pytest.raises(FileNotFoundError, match="step1_normalized_script.json"):
            generator._load_step1(1)

    async def test_load_drama_step1_content_rejects_non_dict_top_level(self, tmp_path):
        """drama step1 顶层非对象（如 JSON 数组）→ ValueError，不静默当空剧本。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write(project_path / "drafts" / "episode_1" / "step1_normalized_script.json", "[]")
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="顶层应为对象"):
            generator._load_drama_step1_content(1)

    async def test_load_drama_step1_content_rejects_non_list_scenes(self, tmp_path):
        """drama step1 scenes 非列表（如对象）→ ValueError fail-fast，不被当成空剧本继续。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write_json(
            project_path / "drafts" / "episode_1" / "step1_normalized_script.json",
            {"title": "第一集", "scenes": {}},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="scenes 必须是非空"):
            generator._load_drama_step1_content(1)

    async def test_load_drama_step1_content_rejects_empty_scenes(self, tmp_path):
        """drama step1 scenes 为空列表 → ValueError fail-fast（空剧本不是合法 step1 产物，避免落盘 scenes=[]）。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write_json(
            project_path / "drafts" / "episode_1" / "step1_normalized_script.json",
            {"title": "第一集", "scenes": []},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="scenes 必须是非空"):
            generator._load_drama_step1_content(1)

    async def test_load_drama_step1_content_rejects_non_dict_scene_item(self, tmp_path):
        """drama step1 scenes 列表含非对象项（数字 / 字符串）→ ValueError，不拖到 render/merge 阶段才炸。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write_json(
            project_path / "drafts" / "episode_1" / "step1_normalized_script.json",
            {"title": "第一集", "scenes": [{"scene_id": "E1S01"}, 42]},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="必须是场景对象"):
            generator._load_drama_step1_content(1)

    async def test_load_drama_step1_content_rejects_empty_scene_id(self, tmp_path):
        """drama step1 场景 scene_id 为空串 / 缺失 → ValueError fail-fast（空 scene_id 拖到合并阶段才暴露）。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write_json(
            project_path / "drafts" / "episode_1" / "step1_normalized_script.json",
            {"title": "第一集", "scenes": [{"scene_id": ""}]},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="scene_id 必须是非空字符串"):
            generator._load_drama_step1_content(1)

    async def test_load_drama_step1_content_rejects_rewritten_scene_id_collision(self, tmp_path):
        """原始 scene_id 互异但改写 episode 前缀后相撞（E1S02_1 与 E2S02_1 在 ep2 都成 E2S02_1）→ fail-loud，
        避免下游产物文件名 / 资产键撞车（与 _load_narration_step1 同口径）。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 2, "title": "第二集", "script_file": "scripts/episode_2.json"}],
        )
        _write_json(
            project_path / "drafts" / "episode_2" / "step1_normalized_script.json",
            {"title": "第二集", "scenes": [{"scene_id": "E1S02_1"}, {"scene_id": "E2S02_1"}]},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="改写到 episode=2 后重复"):
            generator._load_drama_step1_content(2)

    async def test_drama_step2_build_prompt_renders_step1_content(self, tmp_path):
        """drama step2（视觉层）build_prompt 须把 step1 已定稿内容渲染入 prompt，仅求视觉字段。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        _write_json(project_path / "drafts" / "episode_1" / "step1_normalized_script.json", _drama_step1_content())

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        # 已定稿内容透传进 prompt：scene_id + 视觉改编描述 + 口播（仅供理解）
        assert "E1S01" in prompt
        assert "姜月茴立于庭院" in prompt
        # step2 只补视觉层
        assert "image_prompt" in prompt
        assert "video_prompt" in prompt

    async def test_drama_step2_build_prompt_omits_outline(self, tmp_path):
        """分集大纲随内容抽取前移到 step1（normalize）；step2 视觉层 prompt 不再渲染大纲段。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [
                {
                    "episode": 1,
                    "title": "初入江湖",
                    "script_file": "scripts/episode_1.json",
                    "hook": "少年坠崖生死未卜",
                    "outline": {"story_beats": ["少年下山"], "next_episode_teaser": "崖底神秘人出手相救"},
                    "ledger_status": "planned",
                },
            ],
            characters={"姜月茴": {}},
        )
        _write_json(project_path / "drafts" / "episode_1" / "step1_normalized_script.json", _drama_step1_content())

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        # 大纲 / 钩子内容不在 step2 prompt（它们驱动 step1 内容生成，不影响 step2 视觉）
        assert "<episode_outline>" not in prompt
        assert "少年坠崖生死未卜" not in prompt

    async def test_drama_step2_build_prompt_uses_project_source_language(self, tmp_path):
        """step2 视觉层 prompt 的输出语言须取项目 source_language（与 step1 同源），非中文项目不得回落中文。"""
        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        # 注入非中文 source_language（生成内容语言真相源）
        project_json_path = project_path / "project.json"
        payload = json.loads(project_json_path.read_text(encoding="utf-8"))
        payload["source_language"] = "English"
        _write_json(project_json_path, payload)
        _write_json(project_path / "drafts" / "episode_1" / "step1_normalized_script.json", _drama_step1_content())

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        # 输出语言锁定为项目 source_language，不回落默认中文
        assert "所有字符串值必须使用 English" in prompt
        assert "所有字符串值必须使用 中文" not in prompt

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
        _write_step1_json(
            project_path,
            1,
            [{**_step1_seg("E1S01", "原样保留的小说原文。", duration=4), "characters_in_segment": ["姜月茴"]}],
        )

        fake = _FakeTextGenerator(json.dumps(_narration_visual_response(["E1S01"]), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        generator._fetch_video_capabilities = _fixed_caps_468
        output = await generator.generate(1)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert output == project_path / "scripts" / "episode_1.json"
        assert payload["episode"] == 1
        assert payload["duration_seconds"] == 4
        # 内容层（novel_text / 出场角色）由 step1 透传，视觉层由 step2 合并
        seg = payload["segments"][0]
        assert seg["novel_text"] == "原样保留的小说原文。"
        assert seg["characters_in_segment"] == ["姜月茴"]
        assert seg["image_prompt"]["scene"] == "画面"
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
        _write_json(project_path / "drafts" / "episode_1" / "step1_normalized_script.json", _drama_step1_content())

        # drama 两段式：step2 LLM 只出视觉层，后端按 scene_id 合并回 step1 内容
        fake = _FakeTextGenerator(json.dumps(_drama_visual_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        output = await generator.generate(1)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["hook"] == "少年坠崖生死未卜"
        assert payload["next_episode_teaser"] == "崖底神秘人出手相救"
        # step1 的逐字内容（utterances / source_text）经合并透传到最终剧本
        scene = payload["scenes"][0]
        assert scene["source_text"] == "姜月茴缓步走进庭院，抬眼望来。"
        assert scene["utterances"][0]["text"] == "你来了。"
        assert scene["image_prompt"]["scene"] == "场景"

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
        _write_step1_json(project_path, 1, [_step1_seg("E1S01", "原文", duration=4)])

        fake = _FakeTextGenerator(json.dumps(_narration_visual_response(["E1S01"]), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        generator._fetch_video_capabilities = _fixed_caps_468
        output = await generator.generate(1)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["hook"] is None
        assert payload["next_episode_teaser"] is None

    async def test_generate_narration_stamps_cli_episode_and_rewrites_prefix(self, tmp_path):
        """narration 两段式：CLI 集号是唯一真相（视觉 schema 无 episode 字段），且 _add_metadata
        兜底改写 segment_id 前缀——step1 误写 E1S01、生成第 10 集时应改为 E10S01。
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
        # step1 误写集号前缀 E1（应为 E10）
        _write_step1_json(project_path, 10, [_step1_seg("E1S01", "原文", duration=4)])

        fake = _FakeTextGenerator(json.dumps(_narration_visual_response(["E1S01"], title="第十集"), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        generator._fetch_video_capabilities = _fixed_caps_468

        output = await generator.generate(10)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert output == project_path / "scripts" / "episode_10.json"
        assert payload["episode"] == 10
        assert payload["segments"][0]["segment_id"] == "E10S01"

    async def test_generate_drama_step2_passes_visual_schema(self, tmp_path):
        """drama step2 LLM 输出 schema 是 DramaVisualScript（仅 scene_id + 视觉字段，无非视觉字段）。"""
        from lib.script_models import DramaVisualScript

        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        _write_json(project_path / "drafts" / "episode_1" / "step1_normalized_script.json", _drama_step1_content())

        fake = _FakeTextGenerator(json.dumps(_drama_visual_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        await generator.generate(1)

        schema = fake.backend.last_request.response_schema
        assert schema is DramaVisualScript
        props = DramaVisualScript.model_json_schema()["$defs"]["DramaSceneVisual"]["properties"]
        # 非视觉字段不进 LLM 输出 schema（工程透传，杜绝漂移）
        assert "utterances" not in props
        assert "source_text" not in props
        assert "duration_seconds" not in props

    async def test_generate_sets_script_max_output_tokens(self, tmp_path):
        """drama step2 generate 应在 TextGenerationRequest 上设置共享输出上限（DEFAULT_MAX_OUTPUT_TOKENS）。"""
        from lib.script_models import DramaVisualMergeError
        from lib.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS

        project_path = tmp_path / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        _write_json(project_path / "drafts" / "episode_1" / "step1_normalized_script.json", _drama_step1_content())

        # 空视觉响应 → 合并时 step1 场景缺视觉，fail-loud；但模型调用已发生，仍可断言请求参数
        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake)
        with pytest.raises(DramaVisualMergeError):
            await generator.generate(1)

        assert fake.backend.last_request.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
        assert DEFAULT_MAX_OUTPUT_TOKENS >= 16000

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


def _bare_generator(tmp_path: Path, project_extra: dict | None = None) -> ScriptGenerator:
    """构造跳过 backend 初始化的 narration ScriptGenerator（用于直接测内部方法）。"""
    project_dir = tmp_path / "demo"
    project_dir.mkdir(exist_ok=True)
    sg = ScriptGenerator.__new__(ScriptGenerator)
    sg.generator = None
    sg.project_path = project_dir
    sg.project_json = {"content_mode": "narration", **(project_extra or {})}
    sg.content_mode = sg.project_json.get("content_mode", "narration")
    return sg


# 骨架种类 → 触发该骨架的 (content_mode, generation_mode)，即 resolve_declared_kind 的逆。
_KIND_TO_MODES: dict[str, tuple[str, str | None]] = {
    "segments": ("narration", None),
    "scenes": ("drama", None),
    "shots": ("ad", None),
    "video_units": ("narration", "reference_video"),
}


class TestScriptGeneratorSkeletonExhaustiveness:
    """穷尽性断言：script_generator 的骨架分派覆盖 SKELETONS 全部键。

    第五种骨架加入 SKELETONS（+ 规范解析映射）时，未登记的本地映射与未处置的分派逐个报红。
    """

    def test_parse_schema_covers_every_skeleton_kind(self):
        from lib.script_generator import _KIND_PARSE_SCHEMA
        from lib.script_skeleton import SKELETONS

        assert set(_KIND_PARSE_SCHEMA) == set(SKELETONS)

    def test_metadata_count_key_covers_every_skeleton_kind(self):
        from lib.script_generator import _METADATA_COUNT_KEY
        from lib.script_skeleton import SKELETONS

        assert set(_METADATA_COUNT_KEY) == set(SKELETONS)

    def test_metadata_fallback_duration_covers_non_shots_kinds(self):
        # shots（ad）走 ad_script_total_duration、不进兜底时长表；其余骨架均须登记。
        from lib.script_generator import _METADATA_FALLBACK_DURATION
        from lib.script_skeleton import SKELETONS

        assert set(_METADATA_FALLBACK_DURATION) == set(SKELETONS) - {"shots"}

    @pytest.mark.parametrize("kind", list(_KIND_TO_MODES))
    def test_add_metadata_handles_every_skeleton_kind(self, kind: str, tmp_path: Path):
        from lib.script_generator import _METADATA_COUNT_KEY
        from lib.script_skeleton import SKELETONS

        # 参数化遍历 SKELETONS 全键：新增第五种骨架而 _KIND_TO_MODES 未登记即 KeyError 报红。
        assert set(_KIND_TO_MODES) == set(SKELETONS)

        content_mode, gen_mode = _KIND_TO_MODES[kind]
        extra: dict = {"content_mode": content_mode}
        if gen_mode:
            extra["generation_mode"] = gen_mode
        sg = _bare_generator(tmp_path, extra)

        id_field = SKELETONS[kind].id_field
        out = sg._add_metadata({kind: [{id_field: "E1S01"}]}, episode=2)

        # 数组键 + id 字段经查表：前缀改写为当前集号
        assert out[kind][0][id_field] == "E2S01"
        # 计数键随 kind 显式落位
        assert out["metadata"][_METADATA_COUNT_KEY[kind]] == 1

    def test_add_metadata_survives_dirty_degraded_items(self, tmp_path: Path):
        # 校验失败降级保存的原始 dict 里 segments 可能含非 dict / duration_seconds 非数字的脏条目；
        # 前缀改写与时长求和都不得崩溃，时长按稳健口径逐条兜底。
        sg = _bare_generator(tmp_path, {"content_mode": "narration"})

        out = sg._add_metadata(
            {
                "segments": [
                    {"segment_id": "E1S01", "duration_seconds": 5},
                    "junk_not_a_dict",
                    {"segment_id": "E1S02"},
                    {"segment_id": "E1S03", "duration_seconds": None},
                    {"segment_id": "E1S04", "duration_seconds": -5},
                ]
            },
            episode=2,
        )

        # dict 条目前缀改写；非 dict 条目原样保留、不参与改写
        assert out["segments"][0]["segment_id"] == "E2S01"
        assert out["segments"][1] == "junk_not_a_dict"
        assert out["segments"][2]["segment_id"] == "E2S02"
        assert out["segments"][4]["segment_id"] == "E2S04"
        # 计数取列表长度（含脏条目），与既有口径一致
        assert out["metadata"]["total_segments"] == 5
        # 时长：5(有效) + 0(非 dict) + 4(缺失→兜底) + 4(None→兜底) + 4(非正数→兜底) = 17
        assert out["duration_seconds"] == 17

    def test_add_metadata_survives_non_list_array(self, tmp_path: Path):
        # 数组键为真值标量（LLM 误写）时 `... or []` 挡不住，isinstance 守卫避免迭代/求和崩溃。
        sg = _bare_generator(tmp_path, {"content_mode": "narration"})

        out = sg._add_metadata({"segments": 123}, episode=1)

        assert out["metadata"]["total_segments"] == 0
        assert out["duration_seconds"] == 0

    def test_quality_probe_survives_non_list_array(self, tmp_path: Path, caplog):
        # 数组键为真值标量时,_quality_probe 应被 isinstance 守卫收敛为空;外层 try/except 虽会
        # 吞异常,但不得走 “quality probe skipped” 兜底(那意味着守卫失效、整段探针被误跳过)。
        sg = _bare_generator(tmp_path, {"content_mode": "narration"})

        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe({"segments": 123}, episode=1)

        assert not any("quality probe skipped" in r.message for r in caplog.records)


def _step1_seg(
    segment_id: str,
    novel_text: str,
    *,
    duration: int = 4,
    brk: bool = False,
    characters: list[str] | None = None,
    scenes: list[str] | None = None,
    props: list[str] | None = None,
) -> dict:
    return {
        "segment_id": segment_id,
        "novel_text": novel_text,
        "duration_seconds": duration,
        "segment_break": brk,
        "characters_in_segment": characters or [],
        "scenes": scenes or [],
        "props": props or [],
    }


def _visual_seg(segment_id: str, *, scene: str = "画面", action: str = "动作") -> dict:
    return {
        "segment_id": segment_id,
        "image_prompt": {
            "scene": scene,
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": action, "camera_motion": "Static", "ambiance_audio": "风声", "dialogue": []},
    }


def _write_step1_json(project_path: Path, episode: int, segments: list[dict]) -> None:
    """写 narration step1 结构化中间文件 step1_segments.json。"""
    path = project_path / "drafts" / f"episode_{episode}" / "step1_segments.json"
    _write(path, json.dumps({"episode": episode, "segments": segments}, ensure_ascii=False))


def _narration_visual_response(segment_ids: list[str], *, title: str = "第一集") -> dict:
    """step2 视觉层 LLM 响应（NarrationVisualEpisodeScript 形态）。"""
    return {"title": title, "segments": [_visual_seg(sid) for sid in segment_ids]}


async def _fixed_caps_468() -> dict:
    return {"supported_durations": [4, 6, 8]}


class TestMergeNarrationVisual:
    """step2 视觉层按 segment_id 合并回 step1 结构：novel_text 逐字透传、不经 LLM 重出。"""

    def test_novel_text_passthrough_verbatim(self, tmp_path):
        sg = _bare_generator(tmp_path)
        step1 = [_step1_seg("E1S01", "原文甲。", duration=6, brk=True), _step1_seg("E1S02", "原文乙！")]
        visual = {"title": "第一集", "segments": [_visual_seg("E1S01"), _visual_seg("E1S02")]}

        merged = sg._merge_narration_visual(step1, visual, episode=1)

        assert merged["title"] == "第一集"
        assert [s["segment_id"] for s in merged["segments"]] == ["E1S01", "E1S02"]
        # novel_text / 时长 / break 逐字取自 step1（LLM 不再重出）
        assert merged["segments"][0]["novel_text"] == "原文甲。"
        assert merged["segments"][0]["duration_seconds"] == 6
        assert merged["segments"][0]["segment_break"] is True
        assert merged["segments"][1]["novel_text"] == "原文乙！"
        # 视觉层取自 LLM
        assert merged["segments"][0]["image_prompt"]["scene"] == "画面"
        assert merged["segments"][0]["video_prompt"]["action"] == "动作"

    def test_merge_aligns_by_id_not_order(self, tmp_path):
        """LLM 视觉层乱序也按 segment_id 对齐，合并顺序随 step1。"""
        sg = _bare_generator(tmp_path)
        step1 = [_step1_seg("E1S01", "甲"), _step1_seg("E1S02", "乙")]
        visual = {
            "title": "t",
            "segments": [_visual_seg("E1S02", scene="乙画面"), _visual_seg("E1S01", scene="甲画面")],
        }

        merged = sg._merge_narration_visual(step1, visual, episode=1)

        assert [s["segment_id"] for s in merged["segments"]] == ["E1S01", "E1S02"]
        assert merged["segments"][0]["image_prompt"]["scene"] == "甲画面"
        assert merged["segments"][1]["image_prompt"]["scene"] == "乙画面"

    def test_missing_visual_segment_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        step1 = [_step1_seg("E1S01", "甲"), _step1_seg("E1S02", "乙")]
        visual = {"title": "t", "segments": [_visual_seg("E1S01")]}  # 缺 E1S02
        with pytest.raises(ValueError, match="E1S02"):
            sg._merge_narration_visual(step1, visual, episode=1)

    def test_extra_visual_segment_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        step1 = [_step1_seg("E1S01", "甲")]
        visual = {"title": "t", "segments": [_visual_seg("E1S01"), _visual_seg("E1S09")]}  # 多 E1S09
        with pytest.raises(ValueError, match="E1S09"):
            sg._merge_narration_visual(step1, visual, episode=1)

    def test_duplicate_visual_segment_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        step1 = [_step1_seg("E1S01", "甲")]
        visual = {"title": "t", "segments": [_visual_seg("E1S01"), _visual_seg("E1S01")]}  # 重复
        with pytest.raises(ValueError, match="E1S01"):
            sg._merge_narration_visual(step1, visual, episode=1)

    def test_title_fallback_when_missing(self, tmp_path):
        sg = _bare_generator(tmp_path)
        step1 = [_step1_seg("E1S01", "甲")]
        visual = {"segments": [_visual_seg("E1S01")]}  # 无 title
        merged = sg._merge_narration_visual(step1, visual, episode=3)
        assert merged["title"] == "第3集"


class TestLoadNarrationStep1:
    """step1 结构化中间文件 step1_segments.json 的读取与校验。"""

    @staticmethod
    def _step1_path(sg: ScriptGenerator, episode: int) -> Path:
        return sg.project_path / "drafts" / f"episode_{episode}" / "step1_segments.json"

    def _write(self, sg: ScriptGenerator, episode: int, payload: dict) -> None:
        path = self._step1_path(sg, episode)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_loads_structured_segments_verbatim(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(
            sg,
            1,
            {
                "episode": 1,
                "segments": [
                    _step1_seg("E1S01", "第一段原文，逐字保留。", duration=6, brk=True),
                    _step1_seg("E1S02", "第二段原文！"),
                ],
            },
        )
        segments = sg._load_narration_step1(1, [4, 6, 8])
        assert [s["segment_id"] for s in segments] == ["E1S01", "E1S02"]
        assert segments[0]["novel_text"] == "第一段原文，逐字保留。"
        assert segments[0]["duration_seconds"] == 6
        assert segments[0]["segment_break"] is True

    def test_missing_json_without_legacy_md_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        with pytest.raises(FileNotFoundError, match="step1_segments.json"):
            sg._load_narration_step1(1, [4, 6, 8])

    def test_legacy_md_only_raises_rerun_hint(self, tmp_path):
        """仅有结构化前的旧 step1_segments.md：明确要求重跑拆分，不读旧 md。"""
        sg = _bare_generator(tmp_path)
        legacy = sg.project_path / "drafts" / "episode_1" / "step1_segments.md"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("| 片段 | 原文 |\n| G01 | 旧表 |", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="split-narration-segments|重跑"):
            sg._load_narration_step1(1, [4, 6, 8])

    def test_malformed_json_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        path = self._step1_path(sg, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            sg._load_narration_step1(1, [4, 6, 8])

    def test_invalid_structure_missing_novel_text_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [{"segment_id": "E1S01", "duration_seconds": 4}]})
        with pytest.raises(ValueError):
            sg._load_narration_step1(1, [4, 6, 8])

    def test_duplicate_segment_id_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [_step1_seg("E1S01", "甲"), _step1_seg("E1S01", "乙")]})
        with pytest.raises(ValueError, match="重复|E1S01"):
            sg._load_narration_step1(1, [4, 6, 8])

    def test_post_rewrite_collision_raises(self, tmp_path):
        """原始 id 互异但改写 episode 前缀后相撞（E1S02_1 与 E2S02_1 在 ep2 都成 E2S02_1）→ fail-loud。"""
        sg = _bare_generator(tmp_path)
        self._write(sg, 2, {"segments": [_step1_seg("E1S02_1", "甲"), _step1_seg("E2S02_1", "乙")]})
        with pytest.raises(ValueError, match="改写|E2S02_1"):
            sg._load_narration_step1(2, [4, 6, 8])

    def test_duration_outside_supported_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [_step1_seg("E1S01", "甲", duration=5)]})  # 5 ∉ [4,6,8]
        with pytest.raises(ValueError, match="duration"):
            sg._load_narration_step1(1, [4, 6, 8])

    def test_empty_segments_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": []})
        with pytest.raises(ValueError):
            sg._load_narration_step1(1, [4, 6, 8])

    def test_missing_asset_arrays_raises(self, tmp_path):
        """step1 资产字段必填：漏写 characters_in_segment/scenes/props → fail-loud（不静默补 []）。"""
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [{"segment_id": "E1S01", "novel_text": "甲", "duration_seconds": 4}]})
        with pytest.raises(ValueError):
            sg._load_narration_step1(1, [4, 6, 8])

    def test_explicit_empty_asset_arrays_pass(self, tmp_path):
        """无资产时显式写 [] 合法，通过校验。"""
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [_step1_seg("E1S01", "甲", characters=[], scenes=[], props=[])]})
        segments = sg._load_narration_step1(1, [4, 6, 8])
        assert segments[0]["characters_in_segment"] == []


def _write_ad_project(project_path: Path, *, generation_mode: str = "storyboard", products: dict | None = None):
    payload = {
        "title": "速干杯",
        "content_mode": "ad",
        "generation_mode": generation_mode,
        "target_duration": 30,
        "brief": "突出速干卖点",
        "overview": {"synopsis": "带货短片"},
        "characters": {"小美": {"description": "白领"}},
        "scenes": {},
        "props": {},
        "products": products
        if products is not None
        else {"速干杯": {"description": "随行杯", "selling_points": ["30 秒速干"]}},
        "style": "实拍",
        "style_description": "真实质感",
        "aspect_ratio": "9:16",
        "_supported_durations": [4, 6, 8],
        "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
    }
    _write_json(project_path / "project.json", payload)


def _ad_shot(shot_id: str, *, duration: int = 4, section: str = "hook", voiceover: str = "口播") -> dict:
    return {
        "shot_id": shot_id,
        "section": section,
        "duration_seconds": duration,
        "voiceover_text": voiceover,
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": ["速干杯"],
        "image_prompt": {
            "scene": "速干杯特写" * 10,
            "composition": {"shot_type": "Close-up", "lighting": "柔和顶光", "ambiance": "清爽"},
        },
        "video_prompt": {
            "action": "水珠从杯壁滑落，杯身迅速恢复干爽" * 2,
            "camera_motion": "Static",
            "ambiance_audio": "水声",
            "dialogue": [],
        },
    }


class TestAdScriptGeneration:
    async def test_build_prompt_without_step1_uses_brief_and_products(self, tmp_path):
        """ad 一键生成不走 step1 中间文件：prompt 直接来自 brief + 产品信息 + 配比表。"""
        project_path = tmp_path / "demo"
        _write_ad_project(project_path)

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        assert "带货八段框架" in prompt
        assert "| cta | 3 | 27-30 | 1 |" in prompt
        assert "突出速干卖点" in prompt
        assert "### 速干杯" in prompt

    async def test_build_prompt_reference_path_uses_free_duration(self, tmp_path):
        """ad + reference_video：仍是 ad prompt（shots 骨架），时长约束为 1-15 自由整数。"""
        project_path = tmp_path / "demo"
        _write_ad_project(project_path, generation_mode="reference_video")

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        assert "带货八段框架" in prompt
        assert "1 到 15 秒间整数任选" in prompt
        # 不得落入参考视频 video_units prompt
        assert "video_units" not in prompt

    async def test_build_prompt_tolerates_null_project_fields(self, tmp_path):
        """project.json 手工编辑后字段显式为 null：prompt 构建按空值归一化，不抛 AttributeError。"""
        project_path = tmp_path / "demo"
        _write_json(
            project_path / "project.json",
            {
                "title": "速干杯",
                "content_mode": "ad",
                "generation_mode": "storyboard",
                "target_duration": 30,
                "brief": None,
                "overview": None,
                "characters": None,
                "scenes": None,
                "props": None,
                "products": None,
                "style": None,
                "style_description": None,
                "aspect_ratio": "9:16",
                "_supported_durations": [4, 6, 8],
                "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
            },
        )

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        # products 归一化为空 → 自动分流通用短片 prompt，不落带货框架
        assert "带货八段框架" not in prompt
        assert isinstance(prompt, str) and prompt

    async def test_generate_writes_ad_script_with_metadata(self, tmp_path):
        """generate 写盘 ad 剧本：shots 骨架、content_mode=ad、total_shots 与总时长统计。"""
        project_path = tmp_path / "demo"
        _write_ad_project(project_path)

        response = {
            "title": "速干杯短片",
            "shots": [
                _ad_shot("E1S01", duration=4, section="hook", voiceover="还在等杯子干？"),
                _ad_shot("E1S02", duration=6, section="demo", voiceover="30 秒，倒扣即干。"),
            ],
        }
        fake = _FakeTextGenerator(json.dumps(response, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        async def _fixed_caps():
            return {"supported_durations": [4, 6, 8]}

        generator._fetch_video_capabilities = _fixed_caps

        output_path = await generator.generate(1)

        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert saved["content_mode"] == "ad"
        assert saved["episode"] == 1
        assert [s["shot_id"] for s in saved["shots"]] == ["E1S01", "E1S02"]
        assert saved["shots"][0]["voiceover_text"] == "还在等杯子干？"
        assert saved["metadata"]["total_shots"] == 2
        assert saved["duration_seconds"] == 10

    async def test_generate_ad_storyboard_passes_enum_schema(self, tmp_path):
        """ad + storyboard：response_schema 是 AdEpisodeScript 的 duration 枚举子类。"""
        from lib.script_models import AdEpisodeScript

        project_path = tmp_path / "demo"
        _write_ad_project(project_path)
        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake)

        async def _fixed_caps():
            return {"supported_durations": [4, 6, 8]}

        generator._fetch_video_capabilities = _fixed_caps

        with pytest.raises(ScriptStructureValidationError):
            await generator.generate(1)

        schema = fake.backend.last_request.response_schema
        assert isinstance(schema, type) and issubclass(schema, AdEpisodeScript)
        duration_enums = [
            props["duration_seconds"].get("enum")
            for props in (d.get("properties", {}) for d in schema.model_json_schema().get("$defs", {}).values())
            if "duration_seconds" in props
        ]
        assert [4, 6, 8] in duration_enums

    async def test_generate_ad_reference_passes_free_range_schema(self, tmp_path):
        """ad + reference_video：response_schema 收紧为 1-15 区间而非枚举。"""
        from lib.script_models import AdEpisodeScript

        project_path = tmp_path / "demo"
        _write_ad_project(project_path, generation_mode="reference_video")
        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake)

        with pytest.raises(ScriptStructureValidationError):
            await generator.generate(1)

        schema = fake.backend.last_request.response_schema
        assert isinstance(schema, type) and issubclass(schema, AdEpisodeScript)
        field_schemas = [
            props["duration_seconds"]
            for props in (d.get("properties", {}) for d in schema.model_json_schema().get("$defs", {}).values())
            if "duration_seconds" in props
        ]
        assert any(fs.get("minimum") == 1 and fs.get("maximum") == 15 and "enum" not in fs for fs in field_schemas)

    async def test_generate_rewrites_wrong_episode_prefix_on_shot_ids(self, tmp_path):
        """LLM 写错集号前缀时兜底改写为 E1（ad 恒单集）。"""
        project_path = tmp_path / "demo"
        _write_ad_project(project_path)
        response = {
            "title": "速干杯短片",
            "shots": [_ad_shot("E3S01", duration=4)],
        }
        fake = _FakeTextGenerator(json.dumps(response, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        async def _fixed_caps():
            return {"supported_durations": [4, 6, 8]}

        generator._fetch_video_capabilities = _fixed_caps

        output_path = await generator.generate(1)
        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert saved["shots"][0]["shot_id"] == "E1S01"


class TestAdQualityProbe:
    """ad 总时长偏差探针：仅日志 WARN，不阻断、不推前端。"""

    def _sg(self, tmp_path, *, target_duration: int = 30) -> ScriptGenerator:
        project_path = tmp_path / "demo"
        _write_ad_project(project_path)
        sg = ScriptGenerator.__new__(ScriptGenerator)
        sg.generator = None
        sg.project_path = project_path
        sg.project_json = {
            "content_mode": "ad",
            "target_duration": target_duration,
            "generation_mode": "storyboard",
        }
        sg.content_mode = "ad"
        return sg

    def _script(self, durations: list[int]) -> dict:
        return {"shots": [_ad_shot(f"E1S{i:02d}", duration=d) for i, d in enumerate(durations, start=1)]}

    def test_drift_above_threshold_warns(self, tmp_path, caplog):
        sg = self._sg(tmp_path, target_duration=30)
        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe(self._script([4, 4]), episode=1)  # 8 秒 vs 30 秒
        assert any("target_duration drift" in r.message for r in caplog.records)

    def test_drift_within_threshold_silent(self, tmp_path, caplog):
        sg = self._sg(tmp_path, target_duration=30)
        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe(self._script([4, 6, 6, 6, 4, 6]), episode=1)  # 32 秒 vs 30 秒
        assert not any("target_duration drift" in r.message for r in caplog.records)

    def test_short_prompt_probe_covers_shots(self, tmp_path, caplog):
        sg = self._sg(tmp_path)
        script = self._script([4])
        script["shots"][0]["image_prompt"]["scene"] = "短"
        with caplog.at_level("WARNING", logger="lib.script_generator"):
            sg._quality_probe(script, episode=1)
        assert any("quality probe" in r.message and "E1S01" in r.message for r in caplog.records)

    async def test_save_not_blocked_by_drift(self, tmp_path, caplog):
        """偏差超阈值时保存照常成功（探针仅 WARN，不抛、不拒）。"""
        project_path = tmp_path / "demo"
        _write_ad_project(project_path)
        response = {"title": "短片", "shots": [_ad_shot("E1S01", duration=4)]}  # 4 秒 vs 30 秒
        fake = _FakeTextGenerator(json.dumps(response, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        async def _fixed_caps():
            return {"supported_durations": [4, 6, 8]}

        generator._fetch_video_capabilities = _fixed_caps

        with caplog.at_level("WARNING", logger="lib.script_generator"):
            output_path = await generator.generate(1)

        assert output_path.exists()
        assert any("target_duration drift" in r.message for r in caplog.records)


class TestAdAspectRatioFallback:
    def test_ad_without_aspect_ratio_falls_back_to_portrait(self, tmp_path):
        """ad 项目缺 aspect_ratio 时回退 9:16 竖屏（与创建向导默认一致）。"""
        sg = ScriptGenerator.__new__(ScriptGenerator)
        sg.generator = None
        sg.project_path = tmp_path
        sg.project_json = {"content_mode": "ad"}
        sg.content_mode = "ad"
        assert sg._resolve_aspect_ratio() == "9:16"


class TestAdReferenceSkeletonUnity:
    """ad + reference_video 生成的剧本不携带 generation_mode 戳（骨架唯一）。"""

    async def test_generate_ad_reference_script_carries_no_generation_mode(self, tmp_path):
        project_path = tmp_path / "demo"
        _write_ad_project(project_path, generation_mode="reference_video")
        response = {
            "title": "速干杯短片",
            "shots": [_ad_shot("E1S01", duration=7), _ad_shot("E1S02", duration=5, section="cta")],
        }
        fake = _FakeTextGenerator(json.dumps(response, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        output_path = await generator.generate(1)
        saved = json.loads(output_path.read_text(encoding="utf-8"))

        # 剧本级 generation_mode 戳会让按其分派的消费方（StatusCalculator 等）
        # 去找不存在的 video_units；ad 剧本只携带 content_mode
        assert "generation_mode" not in saved
        assert saved["content_mode"] == "ad"
        assert saved["metadata"]["total_shots"] == 2
        assert saved["duration_seconds"] == 12
