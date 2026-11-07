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
    rv_project,
    said,
)

# ---------------------------------------------------------------------------
# split_narration_segments
# ---------------------------------------------------------------------------


async def test_split_narration_segments_dry_run(fake_ctx: ToolHarness, video_request_facts) -> None:
    nr_source(fake_ctx)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})
    assert out.problem is None, out
    prompt_text = said(out)
    assert "DRY RUN" in prompt_text
    # Inject episode segment_id prefixes, asset candidates, and capability tiers into prompts.
    assert "E1S" in prompt_text
    assert "张三" in prompt_text
    assert "4" in prompt_text
    # Without instructions, omit the additional-instructions section.
    assert "# Additional instructions" not in prompt_text


async def test_split_narration_segments_injects_instructions(fake_ctx: ToolHarness, video_request_facts) -> None:
    """Append instructions unchanged in neutral additional-instructions sections, without strength qualifiers."""

    nr_source(fake_ctx)

    out = await run_declared_tool(
        "generate_script_plan",
        fake_ctx,
        {"episode_id": 1, "dry_run": True, "instructions": "单个分镜出场人物尽量不超过两人"},
    )
    assert out.problem is None, out
    prompt_text = said(out)
    assert "# Additional instructions" in prompt_text
    assert "单个分镜出场人物尽量不超过两人" in prompt_text
    assert "必须全部落实" not in prompt_text


async def test_split_narration_segments_rejects_bad_instructions(fake_ctx: ToolHarness, video_request_facts) -> None:
    """Oversized/nonstring instructions reject as bad arguments; stripped whitespace equals absence, sharing validation across four generation tools."""

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
    assert "# Additional instructions" not in said(out)


async def test_split_narration_segments_happy(fake_ctx: ToolHarness, monkeypatch, video_request_facts) -> None:
    """Happy path persists structured storyboard script_plan; the text pipeline resolves SCRIPT models and accounts with project_name."""
    from server import text_generation as mod

    rv_project(fake_ctx, generation_mode="storyboard")
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
    # Model IDs are placeholders; servers assign persisted IDs above fixture-used E1S01.
    assert [s["segment_id"] for s in saved["segments"]] == ["E1S02", "E1S03"]
    # Preserve novel_text exactly.
    assert saved["segments"][0]["novel_text"] == "张三走向村口。"
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


async def test_split_narration_segments_registers_the_frozen_default_source_basis(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifacts.artifact_provenance import build_script_plan_basis

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
            # Other writers change files during provider waits outside the tested call's event loop.
            await asyncio.to_thread(episode_source.write_text, "等待供应商期间改过的原文。", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest
            await asyncio.to_thread(project_file.write_text, json.dumps(latest, ensure_ascii=False), encoding="utf-8")
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
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_script_plan(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_split_narration_segments_rejects_out_of_enum_duration(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Static storyboard duration schemas use open intervals; tool postvalidation rejects unsupported durations without writing."""

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 5)]
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "不在模型档位" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_duplicate_segment_ids(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 4), nr_segment("E1S01", 6)]
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "segment_id 重复" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_blank_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Whitespace-only novel_text meets min_length=1 but has no narration content; postvalidation rejects without writing."""

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 4, "张三在村口等人"), nr_segment("E1S02", 4, novel_text=" ")]
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "novel_text 为空白" in said(out)
    assert "E1S02" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


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
    """Existing strict NarrationScriptPlanSegment schemas reject missing asset fields such as characters_in_segment."""

    nr_source(fake_ctx)
    bad = {"segment_id": "E1S01", "novel_text": "缺字段", "duration_seconds": 4, "segment_break": False}
    use_text_generator(monkeypatch, nr_generator_returning([bad]))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "script_plan 拆分内容结构校验失败" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_rejects_unregistered_asset_reference(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Reject characters_in_segment / scenes / props names absent from project.json without persistence."""

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 4, "张三在村口等人", characters_in_segment=["王五"])]
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "未登记的资产名" in said(out)
    assert "王五" in said(out)
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").exists()


async def test_split_narration_segments_accepts_asset_name_in_other_unicode_form(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """NFC asset-table names and NFD model names, or vice versa, identify the same registered asset.

    Match rv ``validate_unit_text`` coordinates by normalizing both with ``asset_name_comparison_key``;
    otherwise registered Vietnamese characters would fail splitting with differences invisible to Agents reading reports.
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


async def test_split_narration_segments_rejects_truncated_novel_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Merged storyboard text shorter than source means model deletion; novel_text integrity checks reject without writing."""
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
    """Nonverbatim model rewrites fail novel_text integrity checks without persistence."""
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
    """Reordered storyboard text fails novel_text integrity checks without persistence."""
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
    """Lost word spaces in space-delimited languages ("Hello world" -> "Helloworld") are substantive corruption and must reject."""
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
    """NFD sources with NFC model writeback differ only in encoding, not wording; coverage checks must not misclassify.

    Both forms occur in real combining-character languages such as vi. False rejection keeps correct verbatim storyboards
    outside formal files and blocks confirmation/prompt_authoring generation.
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
    """Storyboard boundaries at paragraph newlines allow optional spaces, without false deletion reports."""
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
    """Boundaries after halfwidth punctuation without source whitespace allow optional spaces, without false deletion reports."""
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
    """Lost postpunctuation word spaces inside storyboards, not boundaries ("Hello, world." -> "Hello,world."), are substantive corruption and reject."""
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
    rv_project(fake_ctx, generation_mode="storyboard")
    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
