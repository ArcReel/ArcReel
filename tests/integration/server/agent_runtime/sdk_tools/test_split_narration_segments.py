"""Tests for split_narration_segments (split from test_sdk_tools.py)."""

from __future__ import annotations

import json
import unicodedata
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib import script_review
from lib.artifact_manifest import ArtifactStatus
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.enqueue_videos import (
    generate_video_all_tool,
    generate_video_episode_tool,
)
from server.agent_runtime.sdk_tools.text_generation import (
    generate_episode_script_tool,
    normalize_drama_script_tool,
    split_narration_segments_tool,
    split_reference_video_units_tool,
)
from tests.fakes import fake_reference_caps_fetcher
from tests.integration.server.agent_runtime.sdk_tools.conftest import (
    _call,
    _generation_result,
    _nr_caps,
    _nr_generator_returning,
    _nr_project,
    _nr_segment,
    _nr_source,
    _reference_video_script,
    _rv_source,
    _use_reference_route,
)

# ---------------------------------------------------------------------------
# split_narration_segments
# ---------------------------------------------------------------------------


async def test_split_narration_segments_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "DRY RUN" in prompt_text
    # episode 注入 segment_id 前缀、资产候选与能力档位进 prompt
    assert "E1S" in prompt_text
    assert "张三" in prompt_text
    assert "4" in prompt_text
    # 未传 instructions 时无用户意见分节
    assert "# 用户意见" not in prompt_text


async def test_split_narration_segments_injects_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 原样进 prompt 末尾的中性「用户意见」分节，不附加强度措辞。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "单个分镜出场人物尽量不超过两人"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "# 用户意见" in prompt_text
    assert "单个分镜出场人物尽量不超过两人" in prompt_text
    assert "必须全部落实" not in prompt_text


async def test_split_narration_segments_rejects_bad_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 超长 / 非字符串按参数错误拒绝；空白 strip 后视同未传（校验为四个生成工具共享）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    tool_obj = split_narration_segments_tool(fake_ctx)

    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "长" * 4001})
    assert out.get("is_error") is True
    assert "4000" in out["content"][0]["text"]

    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": 42})
    assert out.get("is_error") is True

    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "   \n  "})
    assert out.get("is_error") is not True, out
    assert "# 用户意见" not in out["content"][0]["text"]


async def test_normalize_drama_script_injects_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "打斗场面多拆几个短镜头"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "# 用户意见" in prompt_text
    assert "打斗场面多拆几个短镜头" in prompt_text


async def test_split_reference_video_units_injects_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher())

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "单 unit 出场人物尽量不超过两人"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "# 用户意见" in prompt_text
    assert "单 unit 出场人物尽量不超过两人" in prompt_text


async def test_generate_episode_script_forwards_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """handler 把 instructions 原样转交 ScriptGenerator（dry_run 与生成路径同口径）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    step1 = drafts / "step1_segments.json"
    step1.write_text("step1", encoding="utf-8")
    fingerprint = script_review.content_fingerprint(step1)
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "episodes": [{"episode": 1, "step1_review": {"fingerprint": fingerprint, "confirmed_at": "t"}}],
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    class _FakeGenerator:
        def __init__(self, _path):
            pass

        @classmethod
        async def create(cls, _path):
            return cls(_path)

        async def build_prompt(self, _episode, *, instructions=None):
            captured["build_prompt"] = instructions
            return "fake prompt"

        async def generate(self, *, episode, instructions=None):
            captured["generate"] = instructions
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)

    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "偏好特写镜头"})
    assert out.get("is_error") is not True, out
    assert captured["build_prompt"] == "偏好特写镜头"

    out = await _call(tool_obj, {"episode": 1, "instructions": "偏好特写镜头"})
    assert out.get("is_error") is not True, out
    assert captured["generate"] == "偏好特写镜头"


async def test_split_narration_segments_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    """happy path：结构化分镜 step1 落盘；模型经文本管道按 SCRIPT 任务解析并携带 project_name 入账。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("张三走向村口。他停下脚步，久久凝望。", encoding="utf-8")
    captured: dict[str, Any] = {}
    segments = [
        _nr_segment("E1S01", 4, "张三走向村口。", characters_in_segment=["张三"], scenes=["村口"]),
        _nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
    ]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments, captured))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True, out

    step1_path = fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json"
    assert step1_path.exists()
    saved = json.loads(step1_path.read_text(encoding="utf-8"))
    assert [s["segment_id"] for s in saved["segments"]] == ["E1S01", "E1S02"]
    # novel_text 逐字保留
    assert saved["segments"][0]["novel_text"] == "张三走向村口。"
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


async def test_split_narration_segments_registers_the_frozen_combined_source_basis(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifact_provenance import build_step1_basis
    from server.agent_runtime.sdk_tools import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,  # type: ignore[attr-defined]
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
    }
    fake_ctx.pm.project_payload = project  # type: ignore[attr-defined]
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    first_source = source_dir / "episode_1.txt"
    second_source = source_dir / "episode_2.txt"
    first_source.write_text("第一段原文。", encoding="utf-8")
    second_source.write_text("第二段原文。", encoding="utf-8")
    frozen_source = "第一段原文。\n\n第二段原文。"
    expected = build_step1_basis(frozen_source, episode=1, project=project)

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    class _Generator:
        async def generate(self, _request, project_name=None):
            second_source.write_text("等待供应商期间改过的第二段。", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest  # type: ignore[attr-defined]
            project_file.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {"episode": 1, "segments": [_nr_segment(novel_text=frozen_source)]}, ensure_ascii=False
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await _call(split_narration_segments_tool(fake_ctx), {"episode": 1})

    assert result.get("is_error") is not True, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_step1(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_split_narration_segments_rejects_out_of_enum_duration(fake_ctx: ToolContext, monkeypatch) -> None:
    """静态分镜 schema 的 duration 是开区间，超出 supported_durations 的时长由工具后校验拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 5)]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "不在模型档位" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_duplicate_segment_ids(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 4), _nr_segment("E1S01", 6)]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "segment_id 重复" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_blank_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """novel_text 为纯空白（如单个空格）满足 schema min_length=1 却无实际旁白内容，须被后校验拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 4, "张三在村口等人"), _nr_segment("E1S02", 4, novel_text=" ")]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "novel_text 为空白" in out["content"][0]["text"]
    assert "E1S02" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_empty_segments(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning([]))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_missing_field(fake_ctx: ToolContext, monkeypatch) -> None:
    """缺资产字段（characters_in_segment 等）由既有分镜 schema（NarrationStep1Segment strict）拦截。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    bad = {"segment_id": "E1S01", "novel_text": "缺字段", "duration_seconds": 4, "segment_break": False}
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning([bad]))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "step1 拆分内容结构校验失败" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_unregistered_asset_reference(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """characters_in_segment / scenes / props 引用了 project.json 未登记的名称须被拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 4, "张三在村口等人", characters_in_segment=["王五"])]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "未登记的资产名" in out["content"][0]["text"]
    assert "王五" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_accepts_asset_name_in_other_unicode_form(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """资产表记 NFC、模型写回 NFD（或反之）指的是同一个已登记资产，不该判成未登记。

    与 rv 侧 ``validate_unit_text`` 同一比对坐标系：两侧都归一到 ``asset_name_comparison_key``
    再判等，否则一个登记过的越南语角色名会被拦在拆分之外，且 Agent 从报告上看不出差别在哪。
    """
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    nfc_name = unicodedata.normalize("NFC", "Hiếu")
    fake_ctx.pm.project_payload["characters"][nfc_name] = {"description": "配角"}  # type: ignore[attr-defined]
    segments = [
        _nr_segment("E1S01", 4, "张三在村口等人", characters_in_segment=[unicodedata.normalize("NFD", nfc_name)])
    ]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    out = await _call(split_narration_segments_tool(fake_ctx), {"episode": 1})

    assert out.get("is_error") is not True, out
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def _nr_source_and_call(fake_ctx: ToolContext, monkeypatch, source_text: str, segments: list[dict]):
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(source_text, encoding="utf-8")
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    return await _call(tool_obj, {"episode": 1})


async def test_split_narration_segments_rejects_truncated_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """分镜合并后比源文短（模型删减）：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [_nr_segment("E1S01", 4, "张三走向村口。")],
    )
    assert out.get("is_error") is True
    assert "未按序、逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_rewritten_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """分镜文字被模型改写（非逐字）：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [
            _nr_segment("E1S01", 4, "张三缓缓走向村口。"),
            _nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
        ],
    )
    assert out.get("is_error") is True
    assert "未按序、逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_reordered_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """分镜顺序被模型打乱：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [
            _nr_segment("E1S01", 6, "他停下脚步，久久凝望。", segment_break=True),
            _nr_segment("E1S02", 4, "张三走向村口。"),
        ],
    )
    assert out.get("is_error") is True
    assert "未按序、逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_dropped_word_space(fake_ctx: ToolContext, monkeypatch) -> None:
    """空格分词语言里模型丢失词间空格（"Hello world" -> "Helloworld"）属实质内容损坏，须拦截。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "Hello world, this is fine.",
        [_nr_segment("E1S01", 4, "Helloworld, this is fine.")],
    )
    assert out.get("is_error") is True
    assert "未按序、逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_accepts_unicode_form_difference(fake_ctx: ToolContext, monkeypatch) -> None:
    """源文以 NFD 落盘、模型回写 NFC：纯编码形式差异不是删字改字，覆盖校验不该误判。

    带组合附加符的语种（如 vi）两种形式都在真实语料里出现，误判会把一份逐字正确的分镜表
    挡在正式文件外、连带堵住内容确认与 step2 生成。
    """
    text = "Ngu\u1eddi \u0111\u00e0n \u00f4ng \u0111i v\u1ec1 ph\u00eda c\u1ed5ng l\u00e0ng."
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        unicodedata.normalize("NFD", text),
        [_nr_segment("E1S01", 4, unicodedata.normalize("NFC", text))],
    )
    assert out.get("is_error") is not True, out
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_accepts_split_at_paragraph_break(fake_ctx: ToolContext, monkeypatch) -> None:
    """分镜边界恰好落在源文的段落换行处：边界处允许可选空格，不应误报删减。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。\n他停下脚步，久久凝望。",
        [
            _nr_segment("E1S01", 4, "张三走向村口。"),
            _nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
        ],
    )
    assert out.get("is_error") is not True, out
    step1_path = fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json"
    assert step1_path.exists()


async def test_split_narration_segments_accepts_split_at_halfwidth_punctuation(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """分镜边界落在半角标点后（源文无空白分隔）：边界处允许可选空格，不应误报删减。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口.他停下脚步.",
        [
            _nr_segment("E1S01", 4, "张三走向村口."),
            _nr_segment("E1S02", 6, "他停下脚步.", segment_break=True),
        ],
    )
    assert out.get("is_error") is not True, out
    step1_path = fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json"
    assert step1_path.exists()


async def test_split_narration_segments_rejects_dropped_space_after_punctuation(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """标点后的词间空格在分镜内部（非边界）丢失："Hello, world." -> "Hello,world."，属实质内容损坏，须拦截。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "Hello, world. This is fine.",
        [_nr_segment("E1S01", 4, "Hello,world. This is fine.")],
    )
    assert out.get("is_error") is True
    assert "未按序、逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_no_source(fake_ctx: ToolContext) -> None:
    _nr_project(fake_ctx)
    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


async def test_generate_episode_script_reference_legacy_md_hints_resplit(fake_ctx: ToolContext) -> None:
    """reference_video 集仅存旧 .md 拆分表时，generate_episode_script 给出重跑拆分提示。"""
    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "episodes": [{"episode": 1, "generation_mode": "reference_video"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.md").write_text("| E1U1 |", encoding="utf-8")

    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "重跑 split-reference-video-units" in text
    assert "step1_reference_units.json" in text


async def test_generate_video_episode_reports_an_interrupted_batch_enqueue_per_id(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """入队中断逐 ID 报告：建成的算 succeeded，没轮到的带「入队中断」问题码且未计费。"""
    from lib.generation_queue_client import BatchTaskResult
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": "E1S01", "novel_text": "第一段旁白。", "video_prompt": "第一镜"},
        {"segment_id": "E1S02", "novel_text": "第二段旁白。", "video_prompt": "第二镜"},
    ]
    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        image = project_dir / "storyboards" / f"scene_{segment_id}.png"
        image.write_bytes(b"png")
        for item in fake_ctx.pm.script_payload["segments"]:  # type: ignore[attr-defined]
            if item["segment_id"] == segment_id:
                item["generated_assets"] = {"storyboard_image": f"storyboards/scene_{segment_id}.png"}

    async def _interrupted(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        success = BatchTaskResult(
            resource_id="E1S01",
            task_id="t1",
            status="succeeded",
            result={"file_path": "videos/E1S01.mp4"},
        )
        if on_success is not None:
            on_success(success)
        return (
            [success],
            [
                BatchTaskResult(
                    resource_id="E1S02",
                    task_id="",
                    status="failed",
                    error="queue unavailable",
                    enqueue_interrupted=True,
                )
            ],
        )

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _interrupted)

    out = await _call(generate_video_episode_tool(fake_ctx), {"script": "episode_1.json"})

    payload = out["generation_result"]
    assert payload["succeeded"] == ["E1S01"]
    assert payload["failed"] == ["E1S02"]
    failed_item = next(item for item in payload["items"] if item["unit_id"] == "E1S02")
    assert failed_item["problem"]["code"] == "generation_enqueue_interrupted"
    assert failed_item["task_state"] == "not_queued"
    assert failed_item["task_id"] is None


async def test_generate_video_episode_batch_is_all_or_nothing_when_a_unit_is_occupied(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """在途任务冲突拦下整批：一个都不入队，其余 unit 报告自己是被谁扣下的。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": "E1S01", "novel_text": "第一段旁白。", "video_prompt": "第一镜"},
        {"segment_id": "E1S02", "novel_text": "第二段旁白。", "video_prompt": "第二镜"},
    ]
    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        image = project_dir / "storyboards" / f"scene_{segment_id}.png"
        image.write_bytes(b"png")
        for item in fake_ctx.pm.script_payload["segments"]:  # type: ignore[attr-defined]
            if item["segment_id"] == segment_id:
                item["generated_assets"] = {"storyboard_image": f"storyboards/scene_{segment_id}.png"}

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(specs)
        return [], []

    async def _active(**_kwargs):
        return [{"resource_id": "E1S02", "id": "task-running", "status": "running"}]

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr("server.services.video_batch_admission.get_active_tasks_for_resources", _active)

    out = await _call(generate_video_episode_tool(fake_ctx), {"script": "episode_1.json"})

    assert out["is_error"] is True
    assert enqueued == []
    result = _generation_result(out)
    assert sorted(result.blocked) == ["E1S01", "E1S02"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1S02"] == "generation_active_task_conflict"
    assert codes["E1S01"] == "generation_batch_admission_withheld"


async def test_generate_video_all_creates_zero_tasks_when_one_artifact_state_is_unreadable(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """产物状态读不出的场景属于这次请求：它带着自己的问题进准入，整批停下，健康的场景不入队计费。"""
    from dataclasses import replace as dc_replace

    from lib.artifact_manifest import ArtifactBlocker
    from lib.generation_result import GenerationCandidate, GenerationTargetState
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload["segments"].append(  # type: ignore[attr-defined]
        {
            "segment_id": "E1S02",
            "image_prompt": "山道清晨",
            "novel_text": "清晨的山道上落着薄雾。",
            "video_prompt": {"action": "镜头推近", "camera_motion": "Push", "ambiance_audio": "鸟鸣"},
            "duration_seconds": 4,
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        }
    )
    (fake_ctx.project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (fake_ctx.project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"\x89PNG")

    select_targets = mod.select_generation_targets

    def _one_unavailable(**kwargs: Any):
        selection = select_targets(**kwargs)
        blocked = GenerationTargetState(
            candidate=GenerationCandidate(unit_id="E1S02"),
            status=ArtifactStatus.BLOCKED,
            blocker=ArtifactBlocker(code="artifact_manifest_unreadable", path="", detail="侧车读不出"),
        )
        return dc_replace(
            selection,
            targets=tuple(state for state in selection.targets if state.unit_id != "E1S02"),
            unavailable=(blocked,),
        )

    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "select_generation_targets", _one_unavailable)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(generate_video_all_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("batch_admission") is not None, out
    assert out["batch_admission"]["decision"] == "blocked"
    enqueue.assert_not_awaited()
    codes = {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]] for unit in out["batch_admission"]["units"]
    }
    assert codes["E1S02"] == ["generation_artifact_state_unavailable"]
    assert codes["E1S01"] == ["generation_batch_admission_withheld"]


async def test_generate_video_all_admits_legacy_narration_stored_under_scenes(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """narration 数据落在 scenes 键的历史剧本按实际骨架做发声准入，不被整批判成解析失败。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "video_prompt": {
                    "action": "阿离转身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [{"speaker": "张三", "line": "跟紧我。"}],
                },
                "voiceover": [],
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }
    (fake_ctx.project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (fake_ctx.project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"\x89PNG")

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        return [
            BatchTaskResult(
                resource_id=spec.resource_id,
                task_id=f"t-{spec.resource_id}",
                status="succeeded",
                result={"file_path": f"videos/{spec.resource_id}.mp4"},
            )
            for spec in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(generate_video_all_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert list(result.succeeded) == ["E1S01"]


async def test_generate_video_all_reports_an_all_unreadable_selection_as_blocked(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全部目标的产物状态都读不出时不能报成空的成功：那会把每一条状态问题都藏起来。"""
    from dataclasses import replace as dc_replace

    from lib.artifact_manifest import ArtifactBlocker
    from lib.generation_result import GenerationCandidate, GenerationTargetState
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    select_targets = mod.select_generation_targets

    def _all_unavailable(**kwargs: Any):
        selection = select_targets(**kwargs)
        blocked = GenerationTargetState(
            candidate=GenerationCandidate(unit_id="E1S01"),
            status=ArtifactStatus.BLOCKED,
            blocker=ArtifactBlocker(code="artifact_manifest_unreadable", path="", detail="侧车读不出"),
        )
        return dc_replace(selection, targets=(), unavailable=(blocked,))

    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "select_generation_targets", _all_unavailable)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(generate_video_all_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("batch_admission") is not None, out
    assert out["batch_admission"]["decision"] == "blocked"
    enqueue.assert_not_awaited()
    codes = {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]] for unit in out["batch_admission"]["units"]
    }
    assert codes == {"E1S01": ["generation_artifact_state_unavailable"]}


async def test_generate_reference_episode_refuses_a_non_scalar_unit_id(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整集参考生成遇到非标量 unit_id：它按位置记名拒收，健康的兄弟条目不会独自入队计费。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    script = _reference_video_script()
    healthy = script["video_units"][0]
    script["video_units"] = [{**healthy, "unit_id": ["U9"]}, healthy]
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(mod.generate_video_episode_tool(fake_ctx), {"script": "episode_1.json"})

    enqueue.assert_not_awaited()
    codes = {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]] for unit in out["batch_admission"]["units"]
    }
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert healthy["unit_id"] in codes


async def test_generate_reference_units_refuses_a_duplicated_named_unit(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点名的 unit 在剧本里有两份：无从判定要做哪一条，整批停在建任务之前。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    script = _reference_video_script()
    script["video_units"] = [*script["video_units"], {**script["video_units"][0]}]
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    duplicated_id = script["video_units"][0]["unit_id"]
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(
        mod.generate_video_selected_tool(fake_ctx),
        {"script": "episode_1.json", "scene_ids": [duplicated_id]},
    )

    enqueue.assert_not_awaited()
    codes = {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]] for unit in out["batch_admission"]["units"]
    }
    assert codes == {duplicated_id: ["generation_unit_request_invalid"]}
