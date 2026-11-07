"""Tests for promote_draft."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from contextlib import asynccontextmanager
from typing import Any

import pytest

from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.project.project_manager import ProjectManager
from lib.script import script_review
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    draft_revision,
    quarantine_path,
    read_quarantine,
    write_quarantine,
)
from lib.script.item_ids import format_item_id
from lib.script.reference_video.draft_validation import DraftViolation
from server.draft_workflow import DraftContext, DraftWorkflow
from server.text_generation import TextGenerationError, TextGenerationRequest, generate_reference_script_plan
from tests.fakes import FakeTextGenerator, use_text_generator
from tests.integration.server.agent_tool_support import (
    _RV_NOVEL,
    ToolHarness,
    draft_of,
    drama_project,
    drama_quarantine_path,
    drama_scene,
    drama_script_plan_path,
    nr_generator_returning,
    nr_quarantine_path,
    nr_script_plan_path,
    nr_segment,
    nr_source,
    open_drama_for_edit,
    open_for_edit,
    open_nr_for_edit,
    problem_of,
    promote_drama,
    promote_nr,
    promote_reference_draft,
    read_drama_quarantine,
    read_nr_quarantine,
    read_rv_quarantine,
    run_declared_tool,
    run_rv_split,
    rv_generator_returning,
    rv_project,
    rv_quarantine_path,
    rv_saved_unit,
    rv_script_plan_path,
    rv_source,
    rv_unit,
    said,
    use_fake_caps,
    write_drama_script_plan,
    write_nr_script_plan,
    write_rv_script_plan,
)

pytestmark = pytest.mark.usefixtures("video_request_facts")

# ---------------------------------------------------------------------------
# Draft/repair/promotion loop for script_plan.
# ---------------------------------------------------------------------------


#: Minimal cases for six blocking violation classes, seven total: unregistered ``@[X]`` splits by
#: description position (unregistered_asset) and speaker position (unregistered_speaker), using different entries;
#: combining them would miss one. Assert drafts, clean formal files, and class-located reports for every class,
#: not just one or two; shared traversal can otherwise regress to discard-and-regenerate on untested classes.
#: ``duration_off_tier`` needs different caps to place duration outside the unit reference state's effective tiers;
#: cover separately in ``test_split_reference_video_units_rejects_duration_off_reference_tier``.
_RV_VIOLATION_CASES = [
    ("unclosed_brace", rv_unit("@[张三] 起身，喊了一句 {我来了")),
    ("dialogue_line_syntax", rv_unit("门开了\n@[张三]：我来了。")),
    ("unregistered_asset", rv_unit("@[不存在的人] 出场")),
    ("unregistered_speaker", rv_unit("门开了\n@[无名氏]：{我来了。}")),
    ("braces_in_description", rv_unit("@[张三] 推门，音量 {}，转身离开")),
    ("source_text_not_verbatim", rv_unit("@[张三] 起身", source_text="张三在城里等人")),
    ("dialogue_overload", rv_unit("@[张三] 起身\n@[张三]：{" + "这是一段非常长的台词" * 6 + "}", duration=4)),
]


@pytest.mark.parametrize(("code", "unit"), _RV_VIOLATION_CASES, ids=[c for c, _ in _RV_VIOLATION_CASES])
async def test_split_reference_video_units_quarantines_each_violation_class(
    fake_ctx: ToolHarness, monkeypatch, code: str, unit: dict
) -> None:
    """Each of six blocking violations preserves draft output, writes no formal file, and reports class-specific locations."""
    rv_source(fake_ctx)
    out = await run_rv_split(fake_ctx, monkeypatch, [unit])

    assert out.problem is not None
    assert not rv_script_plan_path(fake_ctx).exists()

    envelope = read_rv_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_SCRIPT_PLAN
    assert [v["code"] for v in envelope["violations"]] == [code]
    assert envelope["violations"][0]["label"] == "unit E1U01"
    # Drafts hold flat editable body/source-anchor/duration values, not derived persisted shapes.
    assert envelope["content"]["units"][0]["text"] == unit["text"]
    assert "shots" not in envelope["content"]["units"][0]

    report = said(out)
    assert f"[{code}]" in report
    assert "unit E1U01" in report
    assert rv_quarantine_path(fake_ctx).relative_to(fake_ctx.project_path).as_posix() in report
    assert str(fake_ctx.project_path) not in report
    assert "promote_draft" in report


async def test_split_reference_video_units_reports_all_bad_units_in_one_round(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Reports include every invalid unit, not just the first; otherwise each Agent repair would require another paid split."""
    rv_source(fake_ctx)
    units = [
        rv_unit("@[张三] 起身"),
        rv_unit("@[不存在的人] 出场"),
        rv_unit("@[张三] 推门，音量 {}"),
    ]
    out = await run_rv_split(fake_ctx, monkeypatch, units)

    assert out.problem is not None
    envelope = read_rv_quarantine(fake_ctx)
    assert [v["label"] for v in envelope["violations"]] == ["unit E1U02", "unit E1U03"]
    assert [v["code"] for v in envelope["violations"]] == ["unregistered_asset", "braces_in_description"]
    # Preserve valid units unchanged too; Agents repair only invalid ones.
    assert len(envelope["content"]["units"]) == 3


async def test_reference_script_plan_write_transaction_does_not_block_event_loop(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    rv_source(fake_ctx)
    resolver = use_fake_caps(fake_ctx)

    use_text_generator(monkeypatch, rv_generator_returning([rv_unit("@[张三] 起身")]))
    started = threading.Event()
    release = threading.Event()
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def before_commit() -> None:
        worker_threads.append(threading.get_ident())
        started.set()
        release.wait()

    generation = asyncio.create_task(
        generate_reference_script_plan(
            TextGenerationRequest(episode=1),
            project_name=fake_ctx.project_name,
            projects=fake_ctx.pm,
            config_resolver=resolver,
            before_commit=before_commit,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 10)
        ticked = asyncio.Event()
        asyncio.get_running_loop().call_soon(ticked.set)
        await asyncio.wait_for(ticked.wait(), timeout=10)
    finally:
        release.set()

    result = await generation

    assert result.message.startswith("✅")
    assert worker_threads
    assert all(thread != caller_thread for thread in worker_threads)


async def test_promote_draft_promotes_after_repair(fake_ctx: ToolHarness, monkeypatch) -> None:
    """After Agent repair, promotion writes formal script_plan, clears drafts, and derives structure mechanically from bodies."""
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])

    envelope = read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 在 @[村口] 出场"
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is None, out
    assert not rv_quarantine_path(fake_ctx).exists()
    saved = json.loads(rv_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    # Server assigns IDs at persistence; fixtures already used ID 1.
    assert saved["units"][0]["unit_id"] == "E1U2"
    assert saved["units"][0]["text"] == "@[张三] 在 @[村口] 出场"


async def test_promote_draft_reports_again_without_round_limit(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Further violations refresh reports and retain drafts for repeated promotion, without a convergence-round limit."""
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])

    for _round in range(3):
        out = await promote_reference_draft(fake_ctx)
        assert out.problem is not None
        assert "unregistered_asset" in said(out)
        assert rv_quarantine_path(fake_ctx).exists()
        assert not rv_script_plan_path(fake_ctx).exists()

    # Another violation class refreshes reports instead of retaining previous snapshots.
    envelope = read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 推门，音量 {}"
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    await promote_reference_draft(fake_ctx)
    assert [v["code"] for v in read_rv_quarantine(fake_ctx)["violations"]] == ["braces_in_description"]


async def test_promote_draft_rejects_stale_draft_revision(fake_ctx: ToolHarness, monkeypatch) -> None:
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])
    args = {"episode_id": 1, "doc_type": "reference_script_plan"}
    opened = draft_of(await run_declared_tool("open_draft", fake_ctx, args))
    updated = copy.deepcopy(opened["content"])
    updated["units"][0]["text"] = "@[张三] 在 @[村口] 出场"
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {**args, "content": updated, "base_revision": opened["revision"]},
    )
    assert patched.problem is None, patched

    out = await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": opened["revision"]})

    assert out.problem is not None
    assert problem_of(out).code == "revision_conflict"
    assert rv_quarantine_path(fake_ctx).exists()
    assert not rv_script_plan_path(fake_ctx).exists()


# ---------------------------------------------------------------------------
# script_plan optimistic concurrency: retrieval baseline fingerprints, compared under lock before promotion.
# ---------------------------------------------------------------------------


async def test_promote_conflicts_when_official_changed_after_open(fake_ctx: ToolHarness) -> None:
    """Concurrent Web confirmation edits and Agent draft promotion: another writer changing formal files after retrieval
    aborts promotion with current content/merge guidance, preserving their edits and retaining drafts.
    Reopen or patch drafts to explicitly accept formal_revision from the report before retrying promotion."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    await open_for_edit(fake_ctx, source="source/episode_1.txt")

    # Simulate Web saves changing formal files after retrieval.
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 在 @[村口] 等候")])
    web_version = rv_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is not None
    report = said(out)
    assert "并发冲突" in report
    assert "accept_formal_revision" in report
    assert "patch_draft" in report
    assert "base_revision" in report
    assert '"doc_type": "reference_script_plan"' in report
    # Conflict reports include current formal flat units for Agent comparison/merge.
    assert "在 @[村口] 等候" in report
    # Formal files remain unchanged; drafts remain present.
    assert rv_script_plan_path(fake_ctx).read_text(encoding="utf-8") == web_version
    assert rv_quarantine_path(fake_ctx).exists()

    # Merge through tools and explicitly accept formal revisions per guidance, then promotion passes.
    refreshed = draft_of(await open_for_edit(fake_ctx))
    refreshed["content"]["units"][0]["text"] = "@[张三] 在 @[村口] 等候"
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": refreshed["content"],
            "base_revision": refreshed["revision"],
            "accept_formal_revision": refreshed["formal_revision"],
        },
    )
    assert patched.problem is None, patched
    out = await promote_reference_draft(fake_ctx)
    assert out.problem is None, out
    assert not rv_quarantine_path(fake_ctx).exists()


async def test_promote_conflict_report_renders_missing_fingerprint_as_json_null(fake_ctx: ToolHarness) -> None:
    """Deleted formal files have null fingerprints; report JSON null, not string "None".
    Explicitly accepting null through patch_draft then permits promotion; strings would never compare equal."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    await open_for_edit(fake_ctx, source="source/episode_1.txt")
    rv_script_plan_path(fake_ctx).unlink()

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is not None
    report = said(out)
    assert "null" in report
    assert "None" not in report
    assert "显式传入 null" in report

    refreshed = draft_of(await open_for_edit(fake_ctx))
    assert refreshed["formal_revision"] is None
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": refreshed["content"],
            "base_revision": refreshed["revision"],
            "accept_formal_revision": None,
        },
    )
    assert patched.problem is None, patched
    out = await promote_reference_draft(fake_ctx)
    assert out.problem is None, out
    assert not rv_quarantine_path(fake_ctx).exists()


async def test_promote_without_base_fingerprint_meta_promotes_unchecked(fake_ctx: ToolHarness) -> None:
    """Legacy drafts predating meta.base_fingerprint promote without baselines, unaffected by new checks."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    await open_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = read_rv_quarantine(fake_ctx)
    del envelope["meta"]["base_fingerprint"]
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    # Formal files changed after retrieval, but baseline-less legacy drafts retain old overwrite semantics.
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 在 @[村口] 等候")])

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is None, out
    assert not rv_quarantine_path(fake_ctx).exists()


async def test_split_violation_quarantine_records_base_fingerprint(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Violation-created drafts also record baselines, aborting promotion after concurrent formal changes.
    Initial absent formal files mean null baselines; another successful split creating files also conflicts."""
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])

    meta = read_rv_quarantine(fake_ctx)["meta"]
    assert "base_fingerprint" in meta
    assert meta["base_fingerprint"] is None

    # Another path writes formal files while drafts exist; repaired promotion must conflict instead of overwrite.
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    envelope = read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 出场"
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    formal_fingerprint = script_review.content_fingerprint(rv_script_plan_path(fake_ctx))

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is not None
    assert "并发冲突" in said(out)
    assert "base_revision" in said(out)
    assert script_review.content_fingerprint(rv_script_plan_path(fake_ctx)) == formal_fingerprint


async def test_split_violation_keeps_pre_generation_formal_baseline(fake_ctx: ToolHarness, monkeypatch) -> None:

    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    expected = script_review.content_fingerprint(rv_script_plan_path(fake_ctx))
    started = asyncio.Event()
    release = asyncio.Event()

    class _Generator:
        async def generate(self, _request, project_name=None):
            started.set()
            await release.wait()

            class _Result:
                text = json.dumps({"units": [rv_unit("@[不存在的人] 出场")]}, ensure_ascii=False)

            return _Result()

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return _Generator()

    resolver = use_fake_caps(fake_ctx)
    use_text_generator(monkeypatch, fake_create)
    generation = asyncio.create_task(
        generate_reference_script_plan(
            TextGenerationRequest(episode=1),
            project_name=fake_ctx.project_name,
            projects=fake_ctx.pm,
            config_resolver=resolver,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
    except TimeoutError:
        generation.cancel()
        await asyncio.gather(generation, return_exceptions=True)
        raise
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 在 @[村口] 等候")])
    release.set()

    with pytest.raises(TextGenerationError):
        await asyncio.wait_for(generation, timeout=10)

    current = script_review.content_fingerprint(rv_script_plan_path(fake_ctx))
    assert current != expected
    assert read_rv_quarantine(fake_ctx)["meta"]["base_fingerprint"] == expected


@pytest.mark.parametrize(
    ("mutate", "hint"),
    [
        (lambda u: u.update(duration_seconds=7), "当前值：7"),
        (lambda u: u.pop("duration_seconds"), "duration_seconds"),
        (lambda u: u.update(source_text=""), "source_text"),
    ],
    ids=["off_slot_duration", "duration_removed", "blank_source_text"],
)
async def test_promote_draft_rejects_schema_breach(fake_ctx: ToolHarness, monkeypatch, mutate, hint: str) -> None:
    """Malformed schema fields refresh reports only; generation/promotion share schemas, preserving formal files.

    response_schema hard-constrains generated duration enums. Content-only promotion would allow Agent edits to
    unsupported duration_seconds or deletion to 0 seconds to enter formal script_plan.
    """
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])

    envelope = read_rv_quarantine(fake_ctx)
    mutate(envelope["content"]["units"][0])
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is not None
    assert hint in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()
    assert [v["code"] for v in read_rv_quarantine(fake_ctx)["violations"]] == ["schema_invalid"]


@pytest.mark.parametrize(
    "mutate_content",
    [
        lambda c: c.pop("units"),
        lambda c: c.update(units={}),
        lambda c: c.update(units=[]),
    ],
    ids=["units_removed", "units_not_a_list", "units_emptied"],
)
async def test_promote_draft_reports_broken_outer_shape(fake_ctx: ToolHarness, monkeypatch, mutate_content) -> None:
    """Malformed outer shapes also refresh reports instead of returning bare errors.

    Agents may delete units, replace arrays, or empty them. Refreshing only per-unit violations would exclude
    these mistakes from the report-guided repair/promotion loop.
    """
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])

    envelope = read_rv_quarantine(fake_ctx)
    mutate_content(envelope["content"])
    edited_content = copy.deepcopy(envelope["content"])
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is not None
    assert "content.units" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()
    refreshed = read_rv_quarantine(fake_ctx)
    assert [v["code"] for v in refreshed["violations"]] == ["schema_invalid"]
    # Retain exact Agent-written drafts; reshaping them would make report-based review disagree with their originals,
    # breaking edit-then-promote recovery.
    assert rv_quarantine_path(fake_ctx).exists()
    assert refreshed["content"] == edited_content


async def test_promote_draft_requires_source_provenance(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Changed meta.source blocks promotion; reparsing all source/ is looser than generation, allowing other episodes' anchors to match."""
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])

    envelope = read_rv_quarantine(fake_ctx)
    assert "source" in envelope["meta"], "拆分侧须一律写出 source 键（未指定源文时为 null）"
    envelope["meta"] = {}
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_reference_draft(fake_ctx)
    assert out.problem is not None
    assert problem_of(out).code == "draft_invalid"
    assert "meta.source 缺失" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_promote_draft_reports_promotion_not_split(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Successful summaries say promotion, not splitting, which would imply regeneration overwrote Agent edits."""
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])
    envelope = read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is None, out
    assert "晋升" in said(out)


def _rv_scenes(fake_ctx: ToolHarness, scenes: dict[str, Any]) -> None:
    """Rewrite registered scene assets in both in-memory views and disk project.json."""
    fake_ctx.pm.project_payload["scenes"] = scenes
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(fake_ctx.pm.project_payload, ensure_ascii=False),
        encoding="utf-8",
    )


async def _rv_draft_with(fake_ctx: ToolHarness, monkeypatch, texts: list[str]) -> None:
    """Create repair-pending drafts, overriding bodies with ``texts``; initially force drafting with guaranteed-invalid text."""
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场") for _ in texts])
    envelope = read_rv_quarantine(fake_ctx)
    for unit, text in zip(envelope["content"]["units"], texts, strict=True):
        unit["text"] = text
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")


async def test_promote_draft_report_names_units_without_scene_reference(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Hard-blocked promotion reports also include soft violations so Agents see degradation notices every repair round."""
    rv_source(fake_ctx)
    _rv_scenes(fake_ctx, {"村口": {"description": "黄昏的村口"}})
    await _rv_draft_with(fake_ctx, monkeypatch, ["@[张三] 起身", "@[不存在的人] 出场"])

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is not None
    text = said(out)
    assert "unregistered_asset" in text
    assert "未引用场景" in text
    assert "unit E1U01：" in text
    # Soft violations stay outside blocking draft entries; combining them would make nonempty-means-block turn notices into hard violations.
    assert [v["code"] for v in read_rv_quarantine(fake_ctx)["violations"]] == ["unregistered_asset"]


async def test_promote_draft_report_silent_when_every_unit_references_a_scene(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Reports omit soft-violation sections when every unit references a scene, leaving no degradation to report."""
    rv_source(fake_ctx)
    _rv_scenes(fake_ctx, {"村口": {"description": "黄昏的村口"}})
    await _rv_draft_with(fake_ctx, monkeypatch, ["@[村口] 黄昏，@[不存在的人] 出场"])

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is not None
    assert "未引用场景" not in said(out)


async def test_promote_receipt_names_units_without_scene_reference(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Promotion receipts include soft violations like splitting receipts; they do not block promotion or formal persistence."""
    rv_source(fake_ctx)
    _rv_scenes(fake_ctx, {"村口": {"description": "黄昏的村口"}})
    await _rv_draft_with(fake_ctx, monkeypatch, ["@[村口] 黄昏，@[张三] 推门", "@[张三] 起身"])

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is None, out
    assert not rv_quarantine_path(fake_ctx).exists()
    text = draft_of(out)["message"]
    assert "降级提示" in text
    assert "未引用场景" in text
    # Locate receipts by server-issued IDs; fixtures already used ID 1.
    assert "unit E1U3：" in text
    assert "unit E1U2：" not in text


async def test_promote_receipt_silent_when_every_unit_references_a_scene(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Receipts omit soft violations when every unit references a scene."""
    rv_source(fake_ctx)
    _rv_scenes(fake_ctx, {"村口": {"description": "黄昏的村口"}})
    await _rv_draft_with(fake_ctx, monkeypatch, ["@[村口] 黄昏，@[张三] 推门"])

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is None, out
    assert "未引用场景" not in draft_of(out)["message"]


async def test_cancelled_reference_script_plan_promotion_finishes_commit_and_cleanup(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])
    envelope = read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    started = threading.Event()
    release = threading.Event()

    def before_commit() -> None:
        started.set()
        release.wait()

    draft = read_quarantine(fake_ctx.project_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
    assert draft is not None
    use_fake_caps(fake_ctx)
    workflow = DraftWorkflow(
        DraftContext(
            project_name=fake_ctx.project_name,
            data_root=fake_ctx.data_root,
            pm=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )
    )
    promotion = asyncio.create_task(
        workflow.promote(
            1,
            "reference_script_plan",
            draft_revision(draft),
            before_commit=before_commit,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 10)
        promotion.cancel()
        await asyncio.sleep(0)
        assert not promotion.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(promotion, timeout=10)
    assert rv_script_plan_path(fake_ctx).exists()
    assert not rv_quarantine_path(fake_ctx).exists()


async def test_writing_reference_script_plan_clears_stale_prompt_authoring_quarantine(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Changed script_plan clears existing prompt_authoring drafts whose old-plan diff baseline would otherwise prevent any promotion."""
    rv_source(fake_ctx)
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )
    prompt_authoring_path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    assert prompt_authoring_path.exists()

    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[张三] 起身")])

    assert out.problem is None, out
    assert not prompt_authoring_path.exists()


async def test_promote_reference_script_plan_preserves_prompt_authoring_draft_when_content_unchanged(
    fake_ctx: ToolHarness,
) -> None:
    """Abandon case-B edits and promote unchanged: persisted script_plan exactly matches disk originals,
    so retain existing prompt_authoring drafts whose structural diffs still match. Abandoning plan edits
    must not destroy still-valid prompt-authoring repair drafts."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )
    prompt_authoring_path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    assert prompt_authoring_path.exists()

    await open_for_edit(fake_ctx, source="source/episode_1.txt")
    out = await promote_reference_draft(fake_ctx)

    assert out.problem is None, out
    assert prompt_authoring_path.exists()


def _write_rv_formal_script(fake_ctx: ToolHarness, text: str) -> str | None:
    """One pending-authoring formal unit E1U01; return its written content fingerprint for draft generation baselines."""
    path = fake_ctx.project_path / "scripts" / "episode_1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    script = {
        "episode": 1,
        "content_mode": "narration",
        "title": "第1集",
        "video_units": [{"unit_id": "E1U01", "text": text, "duration_seconds": 8, "pending_authoring": True}],
    }
    ProjectManager.for_project_dir(fake_ctx.project_path).save_script(
        fake_ctx.project_path.name, script, "episode_1.json", validate=False
    )
    return script_review.content_fingerprint(path)


async def test_promote_draft_prompt_authoring_uses_async_factory(fake_ctx: ToolHarness, monkeypatch) -> None:
    """prompt_authoring promotion uses ``ScriptGenerator.create`` and _add_metadata, like generation; direct construction would record
    metadata.generator="unknown", diverging from the same directly generated output."""

    rv_project(fake_ctx)
    baseline = _write_rv_formal_script(fake_ctx, "@[张三] 在 @[村口] 等候")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "镜头1：中景，平视。@[张三] 在 @[村口] 等候。"}]},
        violations=[],
        meta={"base_fingerprint": baseline, "unit_ids": ["E1U01"]},
    )
    seen: dict[str, object] = {}

    class _TextBoundary:
        model = "review-factory"

    async def create_text_generator(task_type, project_name=None, **_kwargs):
        seen["task_type"] = task_type
        seen["project_name"] = project_name
        return _TextBoundary()

    use_text_generator(monkeypatch, create_text_generator)
    out = await promote_reference_draft(fake_ctx)
    assert out.problem is None, out
    assert "episode_1.json" in said(out)
    saved = json.loads((fake_ctx.project_path / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    assert saved["metadata"]["generator"] == "review-factory"
    assert seen["project_name"] == fake_ctx.project_name


async def test_promote_draft_surfaces_model_not_in_catalog_with_reselect_guidance(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """If promotion's text model disappeared from provider lists, return reselection guidance instead of wrapping draft_promote_failed."""
    from lib.catalog.errors import ModelNotInCatalogError

    rv_project(fake_ctx)
    baseline = _write_rv_formal_script(fake_ctx, "@[张三] 在 @[村口] 等候")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "镜头1：中景，平视。@[张三] 在 @[村口] 等候。"}]},
        violations=[],
        meta={"base_fingerprint": baseline, "unit_ids": ["E1U01"]},
    )

    async def retired(*_args, **_kwargs):
        raise ModelNotInCatalogError(provider_id="ark", model_id="old-text", media_type="text")

    use_text_generator(monkeypatch, retired)
    out = await promote_reference_draft(fake_ctx)
    assert out.problem is not None
    assert out.problem.code == "model_not_in_provider_catalog"
    assert out.problem.action == "configure_provider"


async def test_promote_draft_waits_for_file_lock_without_blocking_event_loop(
    fake_ctx: ToolHarness, monkeypatch
) -> None:

    rv_project(fake_ctx)
    baseline = _write_rv_formal_script(fake_ctx, "@[张三] 起身")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "镜头1：中景，平视。@[张三] 起身。"}]},
        violations=[],
        meta={"base_fingerprint": baseline, "unit_ids": ["E1U01"]},
    )
    path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    draft = read_quarantine(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    assert draft is not None
    use_fake_caps(fake_ctx)
    workflow = DraftWorkflow(
        DraftContext(
            project_name=fake_ctx.project_name,
            data_root=fake_ctx.data_root,
            pm=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )
    )

    class _TextBoundary:
        model = "async-lock"

    async def create_text_generator(_task_type, _project_name=None, **_kwargs):
        return _TextBoundary()

    use_text_generator(monkeypatch, create_text_generator)
    pm = ProjectManager(fake_ctx.data_root)
    held = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with pm.file_lock(path):
            held.set()
            release.wait()

    holder = asyncio.create_task(asyncio.to_thread(hold_lock))
    promotion: asyncio.Task[dict[str, Any]] | None = None
    try:
        assert await asyncio.to_thread(held.wait, 10)
        attempted = asyncio.Event()
        promotion = asyncio.create_task(
            workflow.promote(
                1,
                "reference_prompt_authoring",
                draft_revision(draft),
                before_lock=attempted.set,
            )
        )
        await asyncio.wait_for(attempted.wait(), timeout=10)
        assert not promotion.done()
    finally:
        release.set()
        assert await asyncio.wait_for(holder, timeout=10) is None

    assert promotion is not None
    out = await asyncio.wait_for(promotion, timeout=10)
    assert out["promoted"] is True
    assert (fake_ctx.project_path / "scripts" / "episode_1.json").exists()


async def test_open_script_plan_draft_waits_for_quarantine_lock(fake_ctx: ToolHarness, monkeypatch) -> None:
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    target = drama_quarantine_path(fake_ctx)
    pm = ProjectManager(fake_ctx.data_root)
    attempted = asyncio.Event()
    original_async_lock = ProjectManager.async_file_lock

    async with pm.async_file_lock(target):

        @asynccontextmanager
        async def observed_async_lock(self, path, **kwargs):
            if path == target:
                attempted.set()
            async with original_async_lock(self, path, **kwargs):
                yield

        monkeypatch.setattr(ProjectManager, "async_file_lock", observed_async_lock)
        opening = asyncio.create_task(open_drama_for_edit(fake_ctx, source="source/episode_1.txt"))
        await asyncio.wait_for(attempted.wait(), timeout=10)
        assert not opening.done()

    out = await opening
    assert out.problem is None, out
    assert target.exists()


async def test_promote_draft_refuses_after_mode_switch(fake_ctx: ToolHarness) -> None:
    """After switching away from reference generation, never promote leftover drafts that would overwrite formal scripts with reference shapes."""
    rv_project(fake_ctx, generation_mode="storyboard")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )

    out = await promote_reference_draft(fake_ctx)
    assert out.problem is not None
    assert problem_of(out).code == "doc_type_not_applicable"
    assert quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).exists(), (
        "残留草稿应原样留在盘上，不被这条路径消费"
    )


async def test_promote_draft_prompt_authoring_ignores_unconfirmed_script_plan(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """prompt_authoring promotes against formal scripts; unconfirmed replanned script_plan does not block it, matching authoring entry semantics."""

    rv_project(fake_ctx)
    baseline = _write_rv_formal_script(fake_ctx, "@[张三] 起身")
    script_plan = rv_script_plan_path(fake_ctx)
    script_plan.parent.mkdir(parents=True, exist_ok=True)
    script_plan.write_text(json.dumps({"units": []}, ensure_ascii=False), encoding="utf-8")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "镜头1：中景，平视。@[张三] 起身。"}]},
        violations=[],
        meta={"base_fingerprint": baseline, "unit_ids": ["E1U01"]},
    )
    project = fake_ctx.pm.project_payload
    project["episodes"][0]["script_plan_review"] = {
        "fingerprint": "sha256-v1:" + "0" * 64,
        "confirmed_at": "2026-08-24T00:00:00Z",
    }
    (fake_ctx.project_path / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert script_review.review_status(fake_ctx.project_path, project, 1) == "pending_review"

    class _TextBoundary:
        model = "unconfirmed-plan"

    async def create_text_generator(_task_type, _project_name=None, **_kwargs):
        return _TextBoundary()

    use_text_generator(monkeypatch, create_text_generator)
    out = await promote_reference_draft(fake_ctx)

    assert out.problem is None, out
    saved = json.loads((fake_ctx.project_path / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    assert saved["video_units"][0]["text"] == "镜头1：中景，平视。@[张三] 起身。"
    assert "pending_authoring" not in saved["video_units"][0]
    assert not quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).exists()


async def test_promote_draft_without_draft(fake_ctx: ToolHarness) -> None:
    out = await promote_reference_draft(fake_ctx)
    assert out.problem is not None
    assert problem_of(out).code == "draft_not_found"


async def test_split_reference_video_units_clears_stale_quarantine_on_success(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Successful resplitting clears previous drafts; otherwise confirmation/generation would stay blocked on superseded outputs."""
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])
    assert rv_quarantine_path(fake_ctx).exists()

    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[张三] 起身")])
    assert out.problem is None, out
    assert not rv_quarantine_path(fake_ctx).exists()


def _write_prompt_authoring_draft(fake_ctx: ToolHarness, violations: list[DraftViolation]) -> None:
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=violations,
        meta={"base_fingerprint": None, "unit_ids": ["E1U01"]},
    )


async def _dry_run_authoring(fake_ctx: ToolHarness, content_mode: str, items_key: str, entry: dict) -> str:
    """Add a formal pending-authoring item and dry-run authoring; successful prechecks let real generators render prompts from formal scripts."""
    scripts = fake_ctx.project_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    script = {
        "episode": 1,
        "content_mode": content_mode,
        "title": "第1集",
        items_key: [entry | {"pending_authoring": True}],
    }
    ProjectManager.for_project_dir(fake_ctx.project_path).save_script(
        fake_ctx.project_path.name, script, "episode_1.json", validate=False
    )
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "dry_run": True})
    assert out.problem is None, out
    return said(out)


_UNAUTHORED_PROMPTS = {"image_prompt": None, "video_prompt": None}


def _write_rv_quarantine(fake_ctx: ToolHarness) -> None:
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_SCRIPT_PLAN,
        content={"units": []},
        violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
    )


async def test_generate_episode_script_blocked_by_prompt_authoring_draft(fake_ctx: ToolHarness) -> None:
    """Existing authoring repair drafts block entry with edit-draft-then-promote recovery guidance."""
    rv_project(fake_ctx)
    _write_rv_formal_script(fake_ctx, "@[张三] 起身")
    _write_prompt_authoring_draft(fake_ctx, [DraftViolation("坏", code="empty_text", label="unit E1U01")])

    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "待处理的提示词编写草稿" in said(out)
    assert "promote_draft" in said(out)


async def test_generate_episode_script_not_blocked_by_reference_script_plan_draft(fake_ctx: ToolHarness) -> None:
    """Authoring reads formal scripts only; reference planning repair drafts do not block and remain unchanged."""
    rv_project(fake_ctx)
    _write_rv_quarantine(fake_ctx)

    prompt = await _dry_run_authoring(
        fake_ctx, "narration", "video_units", {"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 8}
    )

    assert "DRY RUN" in prompt
    assert "张三] 起身" in prompt
    assert rv_quarantine_path(fake_ctx).exists()


async def test_generate_episode_script_preserves_editable_draft_without_violations(fake_ctx: ToolHarness) -> None:
    """Editable drafts have no violation reports; authoring guides validation/promotion instead of demanding invented repairs."""
    rv_project(fake_ctx)
    _write_rv_formal_script(fake_ctx, "@[张三] 起身")
    _write_prompt_authoring_draft(fake_ctx, [])

    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    text = said(out)
    assert "这是可编辑草稿" in text
    assert "保留已有修改" in text
    assert "按草稿内 violations" not in text


async def test_generate_episode_script_quarantine_precedes_missing_formal_script(fake_ctx: ToolHarness) -> None:
    """Existing authoring drafts take priority over missing formal scripts; report pending drafts instead of directing plan reruns."""
    rv_project(fake_ctx)
    _write_prompt_authoring_draft(fake_ctx, [DraftViolation("坏", code="empty_text", label="unit E1U01")])
    assert not (fake_ctx.project_path / "scripts" / "episode_1.json").exists()

    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    text = said(out)
    assert "待处理的提示词编写草稿" in text
    assert "尚无正式脚本" not in text


async def test_generate_episode_script_ignores_quarantine_after_mode_switch(fake_ctx: ToolHarness) -> None:
    """Reference drafts left after route changes are unrelated to new paths; nonreference paths ignore/preserve them or episodes would block forever."""
    rv_project(fake_ctx, generation_mode="storyboard")
    _write_rv_quarantine(fake_ctx)

    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    # Ordinary missing-formal-script checks block, not reference-route drafts.
    assert "待处理的提示词编写草稿" not in said(out)


# ---------------------------------------------------------------------------
# Drama script_plan retrieve/edit/promote loop.
# ---------------------------------------------------------------------------


async def test_promote_drama_script_plan_rederives_needs_replan(fake_ctx: ToolHarness) -> None:
    """Promotion rederives needs_replan from speech admission, ignoring draft values. This machine criterion
    must not trust handwritten values that would hide genuinely needed replanning."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])

    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = read_drama_quarantine(fake_ctx)
    scene = envelope["content"]["scenes"][0]
    scene["utterances"] = [
        {"kind": "dialogue", "speaker": "阿离", "text": "我回来了。"},
        {"kind": "voiceover", "speaker": None, "text": "三年后。"},
    ]
    scene["needs_replan"] = False
    drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_drama(fake_ctx)

    assert out.problem is None, out
    saved = json.loads(drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["needs_replan"] is True


async def test_promote_drama_script_plan_returns_a_receipt_with_statistics(fake_ctx: ToolHarness) -> None:
    """Drama promotion/output receipts share statistics formatting so Agents read both consistently."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])

    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    out = await promote_drama(fake_ctx)

    assert out.problem is None, out
    message = draft_of(out)["message"]
    assert "规范化剧本晋升" in message
    assert "1 个分镜" in message


async def test_promote_drama_script_plan_reports_schema_breach_without_writing(fake_ctx: ToolHarness) -> None:
    """Malformed drafts write no formal content; retain them for violations-guided repair/promotion without loss or contamination."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    before = drama_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["duration_seconds"] = 7  # Off-tier.
    drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_drama(fake_ctx)

    assert out.problem is not None
    assert drama_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before
    assert drama_quarantine_path(fake_ctx).exists()
    assert "content.scenes[i]" in said(out)


async def test_promote_drama_script_plan_reports_video_request_facts_problem(
    fake_ctx: ToolHarness, set_video_request_facts
) -> None:
    """Unresolvable video-request facts at promotion reevaluation reject with problem codes/parameters and retain drafts."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    set_video_request_facts(VideoRequestFactsFailure("video_capability_unavailable", (("capability", "i2v"),)))

    out = await promote_drama(fake_ctx)

    assert out.problem is not None
    problem = problem_of(out)
    assert (problem.code, problem.params) == ("video_capability_unavailable", {"capability": "i2v"})
    assert "video_capability_unavailable（capability=i2v）" in said(out)
    assert drama_quarantine_path(fake_ctx).exists()


async def test_promote_drama_script_plan_aborts_on_concurrent_write(fake_ctx: ToolHarness) -> None:
    """Other writers changing formal files between retrieval/promotion abort with conflicts, never silently overwriting saved changes."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])

    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    write_drama_script_plan(fake_ctx, [drama_scene(scene_description="别处改过的描述。")])
    concurrent = drama_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    out = await promote_drama(fake_ctx)

    assert out.problem is not None
    assert drama_script_plan_path(fake_ctx).read_text(encoding="utf-8") == concurrent
    assert drama_quarantine_path(fake_ctx).exists()


async def test_generate_episode_script_not_blocked_by_drama_script_plan_draft(fake_ctx: ToolHarness) -> None:
    """Drama planning repair drafts do not block authoring, which reads formal scripts and preserves drafts."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    ProjectManager.for_project_dir(fake_ctx.project_path).update_project(
        fake_ctx.project_path.name, lambda project: project.update(characters={"阿离": {"description": "主角"}})
    )

    prompt = await _dry_run_authoring(fake_ctx, "drama", "scenes", drama_scene() | _UNAUTHORED_PROMPTS)

    assert "DRY RUN" in prompt
    assert "E1S01" in prompt
    assert drama_quarantine_path(fake_ctx).exists()


async def test_normalize_drama_script_clears_quarantine_on_regeneration(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Renormalization deliberately rebuilds whole outputs like reference resplitting; clear old drafts in the same critical section
    replacing formal files. Retaining superseded drafts would block confirmation/authoring, while mismatched baseline fingerprints
    make promotion repeatedly conflict, leaving Agents no other recovery path."""

    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    assert drama_quarantine_path(fake_ctx).exists()

    regenerated = {"title": "第一集", "scenes": [drama_scene(scene_description="重新规范化后的描述。")]}

    use_fake_caps(fake_ctx)
    use_text_generator(monkeypatch, FakeTextGenerator(json.dumps(regenerated, ensure_ascii=False)).create)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "source": "source/episode_1.txt"})

    assert out.problem is None, out
    assert not drama_quarantine_path(fake_ctx).exists()
    saved = json.loads(drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["scene_description"] == "重新规范化后的描述。"


async def test_normalize_drama_script_serializes_commit_with_draft_edits(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Regenerated formal commits share draft locks with patch/promotion, preventing interleaved overwrites."""

    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    regenerated = {"title": "第一集", "scenes": [drama_scene(scene_description="重新规范化后的描述。")]}

    use_fake_caps(fake_ctx)
    use_text_generator(monkeypatch, FakeTextGenerator(json.dumps(regenerated, ensure_ascii=False)).create)
    pm = ProjectManager(fake_ctx.data_root)
    target = drama_quarantine_path(fake_ctx)
    attempted = asyncio.Event()
    original_async_file_lock = ProjectManager.async_file_lock
    async with pm.async_file_lock(target):

        @asynccontextmanager
        async def observed_async_file_lock(self, path, **kwargs):
            if path == target:
                attempted.set()
            async with original_async_file_lock(self, path, **kwargs):
                yield

        monkeypatch.setattr(ProjectManager, "async_file_lock", observed_async_file_lock)
        task = asyncio.create_task(
            run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "source": "source/episode_1.txt"})
        )
        await asyncio.wait_for(attempted.wait(), timeout=10)
        assert not task.done(), "generation commit must wait for the draft lock"

    out = await task
    assert out.problem is None, out


async def test_normalize_drama_script_preserves_draft_edited_during_model_call(
    fake_ctx: ToolHarness, monkeypatch
) -> None:

    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    opened_out = await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    opened = draft_of(opened_out)
    started = asyncio.Event()
    release = asyncio.Event()
    regenerated = {"title": "第一集", "scenes": [drama_scene(scene_description="重生成内容")]}

    class _Generator:
        async def generate(self, _request, project_name=None):
            started.set()
            await release.wait()

            class _R:
                text = json.dumps(regenerated, ensure_ascii=False)

            return _R()

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return _Generator()

    use_fake_caps(fake_ctx)
    use_text_generator(monkeypatch, fake_create)
    generation = asyncio.create_task(
        run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "source": "source/episode_1.txt"})
    )
    await started.wait()
    edited = copy.deepcopy(opened["content"])
    edited["scenes"][0]["scene_description"] = "并发编辑内容"
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "drama_script_plan",
            "content": edited,
            "base_revision": opened["revision"],
        },
    )
    assert patched.problem is None, patched
    release.set()

    out = await generation

    assert out.problem is not None
    assert "draft_revision_conflict" in said(out)
    assert read_drama_quarantine(fake_ctx)["content"]["scenes"][0]["scene_description"] == "并发编辑内容"
    formal = json.loads(drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert formal["scenes"][0]["scene_description"] != "重生成内容"


async def test_normalize_drama_script_preserves_output_when_formal_changes_during_model_call(
    fake_ctx: ToolHarness, monkeypatch
) -> None:

    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene(scene_description="生成前内容")])
    started = asyncio.Event()
    release = asyncio.Event()
    regenerated = {"title": "第一集", "scenes": [drama_scene(scene_description="本次生成内容")]}

    class _Generator:
        async def generate(self, _request, project_name=None):
            started.set()
            await release.wait()

            class _R:
                text = json.dumps(regenerated, ensure_ascii=False)

            return _R()

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return _Generator()

    use_fake_caps(fake_ctx)
    use_text_generator(monkeypatch, fake_create)
    generation = asyncio.create_task(
        run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "source": "source/episode_1.txt"})
    )
    await started.wait()
    write_drama_script_plan(fake_ctx, [drama_scene(scene_description="并发正式内容")])
    release.set()

    out = await generation

    assert out.problem is not None
    assert "formal_revision_conflict" in said(out)
    formal = json.loads(drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert formal["scenes"][0]["scene_description"] == "并发正式内容"
    assert read_drama_quarantine(fake_ctx)["content"]["scenes"][0]["scene_description"] == "本次生成内容"


# ---------------------------------------------------------------------------
# Narration script_plan retrieve/edit/promote loop.
# ---------------------------------------------------------------------------


async def test_split_narration_segments_quarantines_violation_instead_of_discarding(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Invalid outputs become drafts instead of being discarded; write no formal files, report violation classes/storyboard locations, and retain this output.

    Discard-and-regenerate wastes money without convergence because the same model/source likely repeats the same error.
    The mechanism lets Agents repair already-paid output in place.
    """

    nr_source(fake_ctx)
    segments = [nr_segment("E1S01", 5, _RV_NOVEL, characters_in_segment=["王五"])]
    use_fake_caps(fake_ctx)
    use_text_generator(monkeypatch, nr_generator_returning(segments))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "source": "source/episode_1.txt"})

    assert out.problem is not None
    report = said(out)
    assert nr_quarantine_path(fake_ctx).relative_to(fake_ctx.project_path).as_posix() in report
    assert str(fake_ctx.project_path) not in report
    assert "[duration_off_tier]" in report
    assert "[unregistered_asset]" in report
    assert "segment E1S01" in report
    assert not nr_script_plan_path(fake_ctx).exists(), "正式文件一步不动"
    envelope = read_nr_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_NARRATION_SCRIPT_PLAN
    assert envelope["content"]["segments"][0]["segment_id"] == "E1S01"
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    # Absent formal files mean null baselines; another writer creating them before repaired promotion must conflict.
    assert envelope["meta"]["base_fingerprint"] is None


async def test_split_narration_segments_clears_quarantine_on_regeneration(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Successful resplitting clears prior drafts; formal files now hold new outputs, so old drafts must not keep confirmation/authoring
    blocked on superseded content."""

    nr_source(fake_ctx)
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [nr_segment("E1S01", 5)]},
        violations=[],
    )
    use_fake_caps(fake_ctx)
    use_text_generator(monkeypatch, nr_generator_returning([nr_segment("E1S01", 4, _RV_NOVEL)]))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "source": "source/episode_1.txt"})

    assert out.problem is None, out
    assert not nr_quarantine_path(fake_ctx).exists()
    assert json.loads(nr_script_plan_path(fake_ctx).read_text(encoding="utf-8"))["segments"][0]["duration_seconds"] == 4


@pytest.mark.parametrize("segment_id", [" ", "items[0]", "E1S1", "E2S01"])
async def test_split_narration_segments_rejects_malformed_segment_id(
    fake_ctx: ToolHarness, monkeypatch, segment_id: str
) -> None:

    nr_source(fake_ctx)
    use_fake_caps(fake_ctx)
    use_text_generator(monkeypatch, nr_generator_returning([nr_segment(segment_id, 4, _RV_NOVEL)]))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "source": "source/episode_1.txt"})

    assert out.problem is not None
    assert not nr_script_plan_path(fake_ctx).exists()


async def test_promote_narration_script_plan_rejects_segment_id_from_another_episode(fake_ctx: ToolHarness) -> None:
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = read_nr_quarantine(fake_ctx)
    envelope["content"]["segments"][0]["segment_id"] = "E2S01"
    nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_nr(fake_ctx)

    assert out.problem is not None
    assert "[invalid_segment_id]" in said(out)
    assert nr_quarantine_path(fake_ctx).exists()


async def test_promote_narration_script_plan_reports_schema_breach_without_writing(fake_ctx: ToolHarness) -> None:
    """Drafts failing the original generation schema refresh reports without formal writes, retaining exact Agent content."""
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    before = nr_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    envelope = read_nr_quarantine(fake_ctx)
    del envelope["content"]["segments"][0]["novel_text"]
    nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_nr(fake_ctx)

    assert out.problem is not None
    assert "[schema_invalid]" in said(out)
    assert nr_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before
    assert "novel_text" not in read_nr_quarantine(fake_ctx)["content"]["segments"][0]


async def test_promote_narration_script_plan_aborts_on_concurrent_write(fake_ctx: ToolHarness) -> None:
    """Formal changes after retrieval abort promotion with merge conflicts instead of silent overwrite."""
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 6, _RV_NOVEL)])

    out = await promote_nr(fake_ctx)

    assert out.problem is not None
    assert "并发冲突" in said(out)
    assert "content.segments" in said(out)
    assert nr_quarantine_path(fake_ctx).exists(), "冲突时草稿仍在场，合并后可重试"
    assert json.loads(nr_script_plan_path(fake_ctx).read_text(encoding="utf-8"))["segments"][0]["duration_seconds"] == 6


async def test_promote_narration_script_plan_revalidates_against_current_source(fake_ctx: ToolHarness) -> None:
    """Promotion rechecks current source coverage; structurally valid rewritten draft bodies still reject without contaminating formal files."""
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    before = nr_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    envelope = read_nr_quarantine(fake_ctx)
    envelope["content"]["segments"][0]["novel_text"] = "张三在村口等候"
    nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_nr(fake_ctx)

    assert out.problem is not None
    assert "[novel_text_coverage]" in said(out)
    assert nr_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_promote_narration_script_plan_names_source_scope_on_coverage_violation(fake_ctx: ToolHarness) -> None:
    """Retrieval using another episode's source rejects even unchanged drafts; reports identify ranges and recovery.

    Existing drafts prevent refetch, so direct Agents to update source ranges through patch_draft.
    """
    nr_source(fake_ctx)
    (fake_ctx.project_path / "source" / "episode_2.txt").write_text("李四走进院子", encoding="utf-8")
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx, source="source/episode_2.txt")
    assert read_nr_quarantine(fake_ctx)["meta"]["source"] == "source/episode_2.txt"

    out = await promote_nr(fake_ctx)

    text = said(out)
    assert out.problem is not None
    assert "[novel_text_coverage]" in text
    assert "源文件 source/episode_2.txt" in text
    assert "patch_draft" in text

    refreshed = draft_of(await open_nr_for_edit(fake_ctx))
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "narration_script_plan",
            "content": refreshed["content"],
            "base_revision": refreshed["revision"],
            "source": "source/episode_1.txt",
        },
    )
    assert patched.problem is None, patched
    promoted = await promote_nr(fake_ctx)
    assert promoted.problem is None, promoted


async def test_promote_narration_script_plan_revalidates_a_default_source_draft_against_the_episode_file(
    fake_ctx: ToolHarness,
) -> None:
    """Without retrieval source (meta.source=null), reevaluate using this episode's derived source, unaffected by other source/ files."""
    nr_source(fake_ctx)
    (fake_ctx.project_path / "source" / "novel.txt").write_text("整本小说原文", encoding="utf-8")
    (fake_ctx.project_path / "source" / "episode_2.txt").write_text("李四走进院子", encoding="utf-8")
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx)
    assert read_nr_quarantine(fake_ctx)["meta"]["source"] is None

    out = await promote_nr(fake_ctx)

    assert out.problem is None, out


async def test_promote_narration_script_plan_returns_a_receipt_with_statistics(fake_ctx: ToolHarness) -> None:
    """Narration promotion/splitting receipts share statistics formatting so Agents read both consistently."""
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    out = await promote_nr(fake_ctx)

    assert out.problem is None, out
    message = draft_of(out)["message"]
    assert "旁白/解说分镜晋升" in message
    assert "1 个分镜" in message
    assert "segment_break 标记" in message


async def test_promote_narration_script_plan_keeps_segment_ids_past_99(fake_ctx: ToolHarness) -> None:
    """IDs only increase; server-issued E1S100 remains valid after retrieval/promotion and is preserved rather than treated as malformed."""
    segment_id = format_item_id(1, "segments", 100)
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment(segment_id, 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    out = await promote_nr(fake_ctx)

    assert out.problem is None, out
    promoted = json.loads(nr_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert [segment["segment_id"] for segment in promoted["segments"]] == [segment_id]


async def test_generate_episode_script_not_blocked_by_narration_script_plan_draft(fake_ctx: ToolHarness) -> None:
    """Narration planning repair drafts do not block authoring, which reads formal scripts and preserves drafts."""
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [nr_segment("E1S01", 4, _RV_NOVEL)]},
        violations=[],
    )

    prompt = await _dry_run_authoring(
        fake_ctx, "narration", "segments", nr_segment("E1S01", 4, _RV_NOVEL) | _UNAUTHORED_PROMPTS
    )

    assert "DRY RUN" in prompt
    assert "E1S01" in prompt
    assert nr_quarantine_path(fake_ctx).exists()
