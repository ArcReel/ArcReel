"""Tests for split_narration_segments."""

from __future__ import annotations

import asyncio
import json
import unicodedata
from typing import Any

from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from tests.fakes import use_text_generator
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    nr_generator_returning,
    nr_segment,
    nr_source,
    run_declared_tool,
    said,
)


def rv_project(fake_ctx: ToolHarness, generation_mode: str = "reference_video") -> None:
    fake_ctx.pm.project_payload["content_mode"] = "narration"
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(fake_ctx.pm.project_payload, ensure_ascii=False),
        encoding="utf-8",
    )


async def test_split_narration_segments_dry_run(fake_ctx: ToolHarness, video_request_facts) -> None:
    nr_source(fake_ctx)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})
    assert out.problem is None, out
    prompt_text = said(out)
    assert "DRY RUN" in prompt_text
    assert "**segment_id**" not in prompt_text
    assert "张三" in prompt_text
    assert "4" in prompt_text
    assert "# 附加指令" not in prompt_text


async def test_split_narration_segments_injects_instructions(fake_ctx: ToolHarness, video_request_facts) -> None:
    """instructions 原样进 prompt 末尾的中性「附加指令」分节，不附加强度措辞。"""

    nr_source(fake_ctx)

    out = await run_declared_tool(
        "generate_script_plan",
        fake_ctx,
        {"episode_id": 1, "dry_run": True, "instructions": "单个分镜出场人物尽量不超过两人"},
    )
    assert out.problem is None, out
    prompt_text = said(out)
    assert "# 附加指令" in prompt_text
    assert "单个分镜出场人物尽量不超过两人" in prompt_text
    assert "必须全部落实" not in prompt_text


async def test_split_narration_segments_reports_bad_instructions(fake_ctx: ToolHarness, video_request_facts) -> None:

    nr_source(fake_ctx)

    out = await run_declared_tool(
        "generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True, "instructions": "长" * 4001}
    )
    assert out.problem is not None
    assert "4000" in said(out)

    out = await run_declared_tool(
        "generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True, "instructions": 42}
    )
    assert out.problem is not None

    out = await run_declared_tool(
        "generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True, "instructions": "   \n  "}
    )
    assert out.problem is None, out
    assert "# 附加指令" not in said(out)


async def test_split_narration_segments_happy(fake_ctx: ToolHarness, monkeypatch, video_request_facts) -> None:
    """happy path：结构化分镜 script_plan 落盘；模型经文本管道按 SCRIPT 任务解析并携带 project_name 入账。"""
    from server import text_generation as mod

    rv_project(fake_ctx, generation_mode="storyboard")
    fake_ctx.pm.mirror_to_disk()
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("张三走向村口。他停下脚步，久久凝望。", encoding="utf-8")
    captured: dict[str, Any] = {}
    segments = [
        nr_segment("E1S01", 4, "张三走向村口。", characters_in_segment=["张三"], scenes=["村口"]),
        nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
    ]
    use_text_generator(monkeypatch, nr_generator_returning(segments, captured))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is None, out
    assert "drafts/episode_1/script_plan_segments.json" in said(out)
    assert str(fake_ctx.project_path) not in said(out)

    script_plan_path = fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    assert script_plan_path.exists()
    saved = json.loads(script_plan_path.read_text(encoding="utf-8"))
    assert [s["segment_id"] for s in saved["segments"]] == ["E1S02", "E1S03"]
    assert saved["segments"][0]["novel_text"] == "张三走向村口。"
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


async def test_split_narration_segments_registers_the_frozen_default_source_basis(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifacts.artifact_provenance import build_script_plan_basis
    from tests.factories import make_video_request_facts

    set_video_request_facts(make_video_request_facts())

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
            await asyncio.to_thread(episode_source.write_text, "等待供应商期间改过的原文。", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest
            await asyncio.to_thread(project_file.write_text, json.dumps(latest, ensure_ascii=False), encoding="utf-8")
            set_video_request_facts(make_video_request_facts(allowed_durations=(6, 8)))
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

    use_text_generator(monkeypatch, fake_create)

    result = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})

    assert result.problem is None, result
    assert {issue["code"] for issue in result.value.blocking_issues} == {
        "duration_off_tier",
        "novel_text_coverage",
    }
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_script_plan(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_split_narration_segments_rejects_out_of_enum_duration(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """模型输出在结构化解析时即拒绝越档时长。"""

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 5)]
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "literal_error" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_uses_effective_tiers_and_issues_ids(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    from tests.factories import make_video_request_facts

    nr_source(fake_ctx)
    fake_ctx.pm.mirror_to_disk()
    set_video_request_facts(
        make_video_request_facts(generation_type="i2v", supported_durations=(4, 6, 8), allowed_durations=(6, 8))
    )
    segments = [
        nr_segment("duplicate", 6, "张三"),
        nr_segment("duplicate", 8, "在村口"),
        nr_segment("ignored", "6", "等人"),
    ]
    segments[-1].pop("segment_id")
    captured: dict[str, Any] = {}
    use_text_generator(monkeypatch, nr_generator_returning(segments, captured))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})

    assert out.problem is None, out
    schema = captured["response_schema"].model_json_schema()
    array = schema["properties"]["segments"]
    assert array["minItems"] == 1
    fields = schema["$defs"][array["items"]["$ref"].split("/")[-1]]["properties"]
    assert fields["duration_seconds"]["enum"] == [6, 8]
    assert "segment_id" not in fields
    path = fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [segment["segment_id"] for segment in saved["segments"]] == ["E1S02", "E1S03", "E1S04"]
    assert [segment["duration_seconds"] for segment in saved["segments"]] == [6, 8, 6]


async def test_split_narration_segments_reports_blank_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:

    nr_source(fake_ctx)
    fake_ctx.pm.mirror_to_disk()
    segments = [nr_segment("E1S01", 4, "张三在村口等人"), nr_segment("E1S02", 4, novel_text=" ")]
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is None
    assert "novel_text 为空白" in said(out)
    assert "E1S03" in said(out)
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_empty_segments(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:

    nr_source(fake_ctx)
    use_text_generator(monkeypatch, nr_generator_returning([]))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_missing_field(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """缺资产字段（characters_in_segment 等）由既有分镜 schema（NarrationScriptPlanSegment strict）拦截。"""

    nr_source(fake_ctx)
    bad = {"segment_id": "E1S01", "novel_text": "缺字段", "duration_seconds": 4, "segment_break": False}
    use_text_generator(monkeypatch, nr_generator_returning([bad]))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "script_plan 拆分内容结构校验失败" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_reports_unregistered_asset_reference(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 4, "张三在村口等人", characters_in_segment=["王五"])]
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is None
    assert "未登记的资产名" in said(out)
    assert "王五" in said(out)
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_accepts_asset_name_in_other_unicode_form(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """资产表记 NFC、模型写回 NFD（或反之）指的是同一个已登记资产，不该判成未登记。

    与 rv 侧 ``validate_unit_text`` 同一比对坐标系：两侧都归一到 ``asset_name_comparison_key``
    再判等，否则一个登记过的越南语角色名会被拦在拆分之外，且 Agent 从报告上看不出差别在哪。
    """

    nr_source(fake_ctx)
    nfc_name = unicodedata.normalize("NFC", "Hiếu")
    fake_ctx.pm.project_payload["characters"][nfc_name] = {"description": "配角"}
    segments = [
        nr_segment("E1S01", 4, "张三在村口等人", characters_in_segment=[unicodedata.normalize("NFD", nfc_name)])
    ]
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})

    assert out.problem is None, out
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def _nr_source_and_call(fake_ctx: ToolHarness, monkeypatch, source_text: str, segments: list[dict]):

    rv_project(fake_ctx, generation_mode="storyboard")
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(source_text, encoding="utf-8")
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    return await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})


async def test_split_narration_segments_reports_truncated_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [nr_segment("E1S01", 4, "张三走向村口。")],
    )
    assert out.problem is None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_reports_rewritten_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [
            nr_segment("E1S01", 4, "张三缓缓走向村口。"),
            nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
        ],
    )
    assert out.problem is None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_reports_reordered_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [
            nr_segment("E1S01", 6, "他停下脚步，久久凝望。", segment_break=True),
            nr_segment("E1S02", 4, "张三走向村口。"),
        ],
    )
    assert out.problem is None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_reports_dropped_word_space(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "Hello world, this is fine.",
        [nr_segment("E1S01", 4, "Helloworld, this is fine.")],
    )
    assert out.problem is None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


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


async def test_split_narration_segments_reports_dropped_space_after_punctuation(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "Hello, world. This is fine.",
        [nr_segment("E1S01", 4, "Hello,world. This is fine.")],
    )
    assert out.problem is None
    assert "未按序、逐字、完整覆盖小说原文" in said(out)
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_no_source(fake_ctx: ToolHarness) -> None:
    rv_project(fake_ctx, generation_mode="storyboard")
    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
