"""Tests for split_narration_segments."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    nr_generator_returning,
    nr_project,
    nr_segment,
    nr_source,
    run_declared_tool,
    said,
)

# ---------------------------------------------------------------------------
# split_narration_segments
# ---------------------------------------------------------------------------


async def test_split_narration_segments_dry_run(fake_ctx: ToolHarness, video_request_facts) -> None:
    nr_source(fake_ctx)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1, "dry_run": True})
    assert out.problem is None, out
    prompt_text = said(out)
    assert "DRY RUN" in prompt_text
    # episode 注入 segment_id 前缀、资产候选与能力档位进 prompt
    assert "E1S" in prompt_text
    assert "张三" in prompt_text
    assert "4" in prompt_text
    # 未传 instructions 时无附加指令分节
    assert "# 附加指令" not in prompt_text


async def test_split_narration_segments_injects_instructions(fake_ctx: ToolHarness, video_request_facts) -> None:
    """instructions 原样进 prompt 末尾的中性「附加指令」分节，不附加强度措辞。"""

    nr_source(fake_ctx)

    out = await run_declared_tool(
        "generate_script_plan",
        fake_ctx,
        {"episode": 1, "dry_run": True, "instructions": "单个分镜出场人物尽量不超过两人"},
    )
    assert out.problem is None, out
    prompt_text = said(out)
    assert "# 附加指令" in prompt_text
    assert "单个分镜出场人物尽量不超过两人" in prompt_text
    assert "必须全部落实" not in prompt_text


async def test_split_narration_segments_rejects_bad_instructions(fake_ctx: ToolHarness, video_request_facts) -> None:
    """instructions 超长 / 非字符串按参数错误拒绝；空白 strip 后视同未传（校验为四个生成工具共享）。"""

    nr_source(fake_ctx)

    out = await run_declared_tool(
        "generate_script_plan", fake_ctx, {"episode": 1, "dry_run": True, "instructions": "长" * 4001}
    )
    assert out.problem is not None
    assert "4000" in said(out)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1, "dry_run": True, "instructions": 42})
    assert out.problem is not None

    out = await run_declared_tool(
        "generate_script_plan", fake_ctx, {"episode": 1, "dry_run": True, "instructions": "   \n  "}
    )
    assert out.problem is None, out
    assert "# 附加指令" not in said(out)


async def test_split_narration_segments_happy(fake_ctx: ToolHarness, monkeypatch, video_request_facts) -> None:
    """happy path：结构化分镜 script_plan 落盘；模型经文本管道按 SCRIPT 任务解析并携带 project_name 入账。"""
    from server import text_generation as mod

    nr_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("张三走向村口。他停下脚步，久久凝望。", encoding="utf-8")
    captured: dict[str, Any] = {}
    segments = [
        nr_segment("E1S01", 4, "张三走向村口。", characters_in_segment=["张三"], scenes=["村口"]),
        nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
    ]
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning(segments, captured))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})
    assert out.problem is None, out

    script_plan_path = fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    assert script_plan_path.exists()
    saved = json.loads(script_plan_path.read_text(encoding="utf-8"))
    assert [s["segment_id"] for s in saved["segments"]] == ["E1S01", "E1S02"]
    # novel_text 逐字保留
    assert saved["segments"][0]["novel_text"] == "张三走向村口。"
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


async def test_split_narration_segments_registers_the_frozen_default_source_basis(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifacts.artifact_provenance import build_script_plan_basis
    from server import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
    }
    fake_ctx.pm.project_payload = project
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    episode_source = source_dir / "episode_1.txt"
    episode_source.write_text("第一段原文。", encoding="utf-8")
    frozen_source = "第一段原文。"
    expected = build_script_plan_basis(frozen_source, episode=1, project=project)

    class _Generator:
        async def generate(self, _request, project_name=None):
            episode_source.write_text("等待供应商期间改过的原文。", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest
            project_file.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {"episode": 1, "segments": [nr_segment(novel_text=frozen_source)]}, ensure_ascii=False
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return _Generator()

    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})

    assert result.problem is None, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_script_plan(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_split_narration_segments_rejects_out_of_enum_duration(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """静态分镜 schema 的 duration 是开区间，超出 supported_durations 的时长由工具后校验拦截，不落盘。"""
    from server import text_generation as mod

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 5)]
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})
    assert out.problem is not None
    assert "不在模型档位" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_duplicate_segment_ids(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    from server import text_generation as mod

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 4), nr_segment("E1S01", 6)]
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})
    assert out.problem is not None
    assert "segment_id 重复" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_blank_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """novel_text 为纯空白（如单个空格）满足 schema min_length=1 却无实际旁白内容，须被后校验拦截，不落盘。"""
    from server import text_generation as mod

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 4, "张三在村口等人"), nr_segment("E1S02", 4, novel_text=" ")]
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})
    assert out.problem is not None
    assert "novel_text 为空白" in said(out)
    assert "E1S02" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_empty_segments(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    from server import text_generation as mod

    nr_source(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning([]))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})
    assert out.problem is not None
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_missing_field(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """缺资产字段（characters_in_segment 等）由既有分镜 schema（NarrationScriptPlanSegment strict）拦截。"""
    from server import text_generation as mod

    nr_source(fake_ctx)
    bad = {"segment_id": "E1S01", "novel_text": "缺字段", "duration_seconds": 4, "segment_break": False}
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning([bad]))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})
    assert out.problem is not None
    assert "script_plan 拆分内容结构校验失败" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_unregistered_asset_reference(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """characters_in_segment / scenes / props 引用了 project.json 未登记的名称须被拦截，不落盘。"""
    from server import text_generation as mod

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 4, "张三在村口等人", characters_in_segment=["王五"])]
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})
    assert out.problem is not None
    assert "未登记的资产名" in said(out)
    assert "王五" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_accepts_asset_name_in_other_unicode_form(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """资产表记 NFC、模型写回 NFD（或反之）指的是同一个已登记资产，不该判成未登记。

    与 rv 侧 ``validate_unit_text`` 同一比对坐标系：两侧都归一到 ``asset_name_comparison_key``
    再判等，否则一个登记过的越南语角色名会被拦在拆分之外，且 Agent 从报告上看不出差别在哪。
    """
    from server import text_generation as mod

    nr_source(fake_ctx)
    nfc_name = unicodedata.normalize("NFC", "Hiếu")
    fake_ctx.pm.project_payload["characters"][nfc_name] = {"description": "配角"}
    segments = [
        nr_segment("E1S01", 4, "张三在村口等人", characters_in_segment=[unicodedata.normalize("NFD", nfc_name)])
    ]
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})

    assert out.problem is None, out
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def _nr_source_and_call(fake_ctx: ToolHarness, monkeypatch, source_text: str, segments: list[dict]):
    from server import text_generation as mod

    nr_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(source_text, encoding="utf-8")
    monkeypatch.setattr(mod.TextGenerator, "create", nr_generator_returning(segments))

    return await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})


async def test_split_narration_segments_rejects_truncated_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """分镜合并后比源文短（模型删减）：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [nr_segment("E1S01", 4, "张三走向村口。")],
    )
    assert out.problem is not None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_rewritten_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """分镜文字被模型改写（非逐字）：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [
            nr_segment("E1S01", 4, "张三缓缓走向村口。"),
            nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
        ],
    )
    assert out.problem is not None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_reordered_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """分镜顺序被模型打乱：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [
            nr_segment("E1S01", 6, "他停下脚步，久久凝望。", segment_break=True),
            nr_segment("E1S02", 4, "张三走向村口。"),
        ],
    )
    assert out.problem is not None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_dropped_word_space(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """空格分词语言里模型丢失词间空格（"Hello world" -> "Helloworld"）属实质内容损坏，须拦截。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "Hello world, this is fine.",
        [nr_segment("E1S01", 4, "Helloworld, this is fine.")],
    )
    assert out.problem is not None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_accepts_unicode_form_difference(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """源文以 NFD 落盘、模型回写 NFC：纯编码形式差异不是删字改字，覆盖校验不该误判。

    带组合附加符的语种（如 vi）两种形式都在真实语料里出现，误判会把一份逐字正确的分镜表
    挡在正式文件外、连带堵住内容确认与 prompt_authoring 生成。
    """
    text = "Ngu\u1eddi \u0111\u00e0n \u00f4ng \u0111i v\u1ec1 ph\u00eda c\u1ed5ng l\u00e0ng."
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        unicodedata.normalize("NFD", text),
        [nr_segment("E1S01", 4, unicodedata.normalize("NFC", text))],
    )
    assert out.problem is None, out
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_accepts_split_at_paragraph_break(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """分镜边界恰好落在源文的段落换行处：边界处允许可选空格，不应误报删减。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。\n他停下脚步，久久凝望。",
        [
            nr_segment("E1S01", 4, "张三走向村口。"),
            nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
        ],
    )
    assert out.problem is None, out
    script_plan_path = fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    assert script_plan_path.exists()


async def test_split_narration_segments_accepts_split_at_halfwidth_punctuation(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """分镜边界落在半角标点后（源文无空白分隔）：边界处允许可选空格，不应误报删减。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口.他停下脚步.",
        [
            nr_segment("E1S01", 4, "张三走向村口."),
            nr_segment("E1S02", 6, "他停下脚步.", segment_break=True),
        ],
    )
    assert out.problem is None, out
    script_plan_path = fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    assert script_plan_path.exists()


async def test_split_narration_segments_rejects_dropped_space_after_punctuation(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """标点后的词间空格在分镜内部（非边界）丢失："Hello, world." -> "Hello,world."，属实质内容损坏，须拦截。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "Hello, world. This is fine.",
        [nr_segment("E1S01", 4, "Hello,world. This is fine.")],
    )
    assert out.problem is not None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_no_source(fake_ctx: ToolHarness) -> None:
    nr_project(fake_ctx)
    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode": 1})
    assert out.problem is not None
