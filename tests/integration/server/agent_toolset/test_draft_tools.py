"""Integration tests for the revisioned draft workflow tools."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lib.project import project_trash
from lib.project.project_activity import ACTIVITY_FILENAME, recorded_project_activity
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script import script_review
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    QUARANTINE_SCHEMA_VERSION,
    quarantine_path,
    write_quarantine,
)
from lib.workflow.workflow_state import WorkflowStateService
from server.draft_workflow import DraftContext, DraftWorkflow, DraftWorkflowError
from server.tool_runtime import ToolOutcome
from tests.fakes import FakeTextGenerator, use_text_generator
from tests.integration.server.agent_tool_support import (
    _RV_NOVEL,
    ToolHarness,
    derived_reference_names,
    draft_of,
    drama_project,
    drama_quarantine_path,
    drama_scene,
    drama_script_plan_path,
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


def _write_reference_prompt_authoring(fake_ctx: ToolHarness, script: dict) -> None:
    fake_ctx.pm.script_payload = script
    scripts = fake_ctx.project_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# open_draft
# ---------------------------------------------------------------------------


async def test_open_draft_returns_flat_draft_structure(fake_ctx: ToolHarness) -> None:
    """Retrieve flat drafts without derived values: Agents edit reference-syntax bodies / source anchors / duration.
    Promotion derives unit_id from array positions; storing it in drafts would permit drift."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身\n@[张三] 走向 @[村口]")])

    out = await open_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.problem is None, out
    envelope = read_rv_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_SCRIPT_PLAN
    assert envelope["violations"] == []
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    unit = envelope["content"]["units"][0]
    assert set(unit) == {"duration_seconds", "source_text", "text"}
    assert unit["duration_seconds"] == 8
    assert unit["source_text"] == _RV_NOVEL
    assert unit["text"] == "@[张三] 起身\n@[张三] 走向 @[村口]"


async def test_open_draft_leaves_official_file_untouched(fake_ctx: ToolHarness) -> None:
    """Retrieval only opens editing workspaces, leaving formal files untouched; only locked promotion writes edits back."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    before = rv_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    await open_for_edit(fake_ctx)

    assert rv_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_open_draft_round_trips_through_promote(fake_ctx: ToolHarness) -> None:
    """Full case-B loop: retrieve, edit draft, promote; locked promotion persists edits into formal files."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])

    await open_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 在 @[村口] 出场"
    rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_reference_draft(fake_ctx)

    assert out.problem is None, out
    assert not rv_quarantine_path(fake_ctx).exists()
    saved = json.loads(rv_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["text"] == "@[张三] 在 @[村口] 出场"
    assert derived_reference_names(fake_ctx, saved["units"][0]["text"]) == ["张三", "村口"]


async def test_open_draft_returns_existing_draft_without_clobbering(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Do not overwrite existing drafts, which may contain unpromoted Agent edits or pending repairs;
    covering them with formal files would erase ongoing work."""
    rv_source(fake_ctx)
    await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])
    before = rv_quarantine_path(fake_ctx).read_text(encoding="utf-8")
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])

    out = await open_for_edit(fake_ctx)

    assert out.problem is None
    assert rv_quarantine_path(fake_ctx).read_text(encoding="utf-8") == before
    assert "reference_script_plan" in said(out)


async def test_open_draft_without_official_file(fake_ctx: ToolHarness) -> None:
    """Without formal script_plan, direct users to initial splitting instead of opening empty drafts for Agents to handwrite whole episodes."""
    rv_source(fake_ctx)

    out = await open_for_edit(fake_ctx)

    assert out.problem is not None
    assert "generate_script_plan" in said(out)
    assert not rv_quarantine_path(fake_ctx).exists()


async def test_open_draft_keeps_malformed_duration_verbatim(fake_ctx: ToolHarness) -> None:
    """Carry invalid disk unit-field types into drafts unchanged; normalizing ``8.0`` into ``0``
    would show Agents an unwritten duration and a promotion tier error inconsistent with original disk values.
    Preserve values so promotion schemas report each issue visibly."""
    rv_source(fake_ctx)
    unit = rv_saved_unit("@[张三] 起身")
    unit["duration_seconds"] = 8.0
    write_rv_script_plan(fake_ctx, [unit])

    out = await open_for_edit(fake_ctx)

    assert out.problem is None, out
    assert read_rv_quarantine(fake_ctx)["content"]["units"][0]["duration_seconds"] == 8.0


async def test_open_draft_keeps_malformed_non_dict_unit_slot(fake_ctx: ToolHarness) -> None:
    """Do not discard nondict disk units; shortening draft arrays could let remaining valid units promote
    and silently overwrite formal files, losing one unit unnoticed. Keep empty placeholders at original
    positions for promotion schemas to reject and identify individually."""
    rv_source(fake_ctx)
    good_unit = rv_saved_unit("@[张三] 起身")
    path = rv_script_plan_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": [good_unit, "不是对象"]}, ensure_ascii=False), encoding="utf-8")

    out = await open_for_edit(fake_ctx)

    assert out.problem is None, out
    units = read_rv_quarantine(fake_ctx)["content"]["units"]
    assert len(units) == 2
    assert units[1] == {"duration_seconds": None, "source_text": "", "text": ""}


async def test_open_draft_rejects_missing_source_without_side_effect(
    fake_ctx: ToolHarness,
) -> None:
    """Nonexistent `source` paths must not persist drafts. Creating one records the invalid meta.source,
    causing repeated promotion `load_novel_source` errors while its presence prevents refetching with a corrected source,
    trapping Agents in uneditable state. Validation failure has no persistent effects; corrected arguments can retry."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    workflow = DraftWorkflow(
        DraftContext(
            project_name=fake_ctx.project_name,
            data_root=fake_ctx.data_root,
            pm=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )
    )

    with pytest.raises(DraftWorkflowError) as excinfo:
        await workflow.open(
            1,
            "reference_script_plan",
            source="source/episode_不存在.txt",
        )

    assert excinfo.value.code == "draft_open_failed"
    assert not rv_quarantine_path(fake_ctx).exists()


async def test_script_plan_write_cannot_race_a_prompt_authoring_draft_patch(fake_ctx: ToolHarness) -> None:
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    _write_reference_prompt_authoring(
        fake_ctx,
        {
            "title": "第一集",
            "content_mode": "narration",
            "episode": 1,
            "video_units": [{"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 4}],
        },
    )
    opened = draft_of(await open_for_edit(fake_ctx, doc_type="reference_prompt_authoring"))
    changed = copy.deepcopy(opened["content"])
    changed["units"][0]["text"] = "并发编辑"
    target = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    patch_reached_write = threading.Event()
    allow_patch_write = threading.Event()
    writer_attempted_draft_lock = threading.Event()
    workflow = DraftWorkflow(
        DraftContext(
            project_name=fake_ctx.project_name,
            data_root=fake_ctx.data_root,
            pm=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )
    )

    def pause_before_patch_commit() -> None:
        patch_reached_write.set()
        _ = allow_patch_write.wait()

    def patch() -> dict:
        return asyncio.run(
            workflow.patch(
                1,
                "reference_prompt_authoring",
                changed,
                opened["revision"],
                before_commit=pause_before_patch_commit,
            )
        )

    def rewrite_script_plan() -> None:
        script_review.write_script_plan(
            fake_ctx.project_path,
            1,
            {"units": [rv_saved_unit("@[张三] 修改 script_plan")]},
            before_lock=writer_attempted_draft_lock.set,
        )

    patch_task = asyncio.create_task(asyncio.to_thread(patch))
    writer_task: asyncio.Task[None] | None = None
    patch_result: dict | None = None
    try:
        assert await asyncio.to_thread(patch_reached_write.wait, 1)
        writer_task = asyncio.create_task(asyncio.to_thread(rewrite_script_plan))
        attempted = await asyncio.to_thread(writer_attempted_draft_lock.wait, 1)
        assert attempted
    finally:
        allow_patch_write.set()
        if writer_task is not None:
            patch_result, _ = await asyncio.wait_for(asyncio.gather(patch_task, writer_task), timeout=2)

    assert patch_result is not None
    assert not target.exists()


async def test_open_draft_rejects_non_reference_episode(fake_ctx: ToolHarness) -> None:
    """Episodes switched away from reference generation cannot edit unrelated on-disk script_plan, matching promotion criteria."""
    rv_source(fake_ctx)
    rv_project(fake_ctx, generation_mode="image_to_video")
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])

    out = await open_for_edit(fake_ctx)

    assert out.problem is not None
    assert not rv_quarantine_path(fake_ctx).exists()


async def test_open_draft_records_base_fingerprint(fake_ctx: ToolHarness) -> None:
    """Retrieval records current formal-file content fingerprints in meta.base_fingerprint for prepromotion baseline checks."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])

    out = await open_for_edit(fake_ctx)

    assert out.problem is None, out
    meta = read_rv_quarantine(fake_ctx)["meta"]
    assert meta["base_fingerprint"] == script_review.content_fingerprint(rv_script_plan_path(fake_ctx))


async def test_open_draft_returns_drama_scenes(fake_ctx: ToolHarness) -> None:
    """Drama retrieval holds storyboard-content tables and leaves formal files untouched; only locked promotion persists."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene(needs_replan=True)])
    before = drama_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    out = await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.problem is None, out
    envelope = read_drama_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_DRAMA_SCRIPT_PLAN
    assert envelope["violations"] == []
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    assert envelope["meta"]["base_fingerprint"]
    scene = envelope["content"]["scenes"][0]
    # Derive needs_replan through speech admission and strip on retrieval; otherwise Agents may treat it as editable,
    # although promotion always derives it again, making disagreement misleading.
    assert "needs_replan" not in scene
    assert scene["scene_description"] == "阿离站在山门前。"
    assert drama_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_open_draft_drama_round_trips_through_promote(fake_ctx: ToolHarness) -> None:
    """Full retrieve/edit/promote loop persists under lock and recomputes derived fields from new content."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])

    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["scene_description"] = "阿离推开山门。"
    drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_drama(fake_ctx)

    assert out.problem is None, out
    assert not drama_quarantine_path(fake_ctx).exists()
    saved = json.loads(drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["scene_description"] == "阿离推开山门。"


async def test_open_draft_returns_existing_drama_draft(fake_ctx: ToolHarness) -> None:
    """Preserve existing unpromoted drafts; continue editing and promoting them instead of overwriting."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["scene_description"] = "未晋升的修改。"
    drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.problem is None
    assert read_drama_quarantine(fake_ctx)["content"]["scenes"][0]["scene_description"] == "未晋升的修改。"


def _mark_script_plan_confirmed(fake_ctx: ToolHarness) -> None:
    """An episode with formal script output whose confirmation fingerprint matches its current formal script plan."""
    scripts = fake_ctx.project_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "episode_1.json").write_text(json.dumps({"title": "第一集", "scenes": []}), encoding="utf-8")
    _confirm_script_plan(fake_ctx, drama_script_plan_path(fake_ctx))


def _confirm_script_plan(fake_ctx: ToolHarness, plan_path: Path) -> None:
    """Record the current formal-plan fingerprint as confirmation."""
    fingerprint = script_review.content_fingerprint(plan_path)
    assert fingerprint is not None
    script_review.apply_confirmation(fake_ctx.pm.project_payload, 1, fingerprint, "2026-01-01T00:00:00+00:00")


def _problem(out: ToolOutcome[Any]) -> dict[str, Any]:
    return problem_of(out).model_dump()


async def test_open_draft_refuses_to_copy_a_confirmed_script_plan(fake_ctx: ToolHarness) -> None:
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    _mark_script_plan_confirmed(fake_ctx)

    problem = _problem(await open_drama_for_edit(fake_ctx, source="source/episode_1.txt"))

    assert problem["code"] == "script_plan_confirmed"
    assert "patch_episode_script" in problem["detail"]
    assert "generate_script_plan" in problem["detail"]
    assert not drama_quarantine_path(fake_ctx).exists()


async def test_patch_and_promote_refuse_an_edit_copy_once_the_script_plan_is_confirmed(fake_ctx: ToolHarness) -> None:
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    opened = draft_of(await open_drama_for_edit(fake_ctx, source="source/episode_1.txt"))
    _mark_script_plan_confirmed(fake_ctx)
    formal_before = drama_script_plan_path(fake_ctx).read_bytes()
    draft_before = drama_quarantine_path(fake_ctx).read_bytes()
    content = opened["content"]
    content["scenes"][0]["scene_description"] = "阿离推开山门。"
    args = {"episode_id": 1, "doc_type": "drama_script_plan"}

    patched = await run_declared_tool(
        "patch_draft", fake_ctx, {**args, "content": content, "base_revision": opened["revision"]}
    )
    use_fake_caps(fake_ctx, supported_durations=(4, 6, 8), default_duration=4)
    promoted = await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": opened["revision"]})

    assert _problem(patched)["code"] == "script_plan_confirmed"
    assert _problem(promoted)["code"] == "script_plan_confirmed"
    assert drama_quarantine_path(fake_ctx).read_bytes() == draft_before
    assert drama_script_plan_path(fake_ctx).read_bytes() == formal_before


async def test_rerun_draft_on_a_confirmed_script_plan_can_still_be_repaired_and_promoted(
    fake_ctx: ToolHarness,
) -> None:
    """Repair-pending drafts from replanning are not editing copies of confirmed content; repair/promotion replaces formal planning with new content."""
    drama_project(fake_ctx)
    write_drama_script_plan(fake_ctx, [drama_scene()])
    _mark_script_plan_confirmed(fake_ctx)
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
        content={"title": "第一集", "scenes": [drama_scene(scene_description="旧产出。")]},
        violations=[],
        meta={
            "source": "source/episode_1.txt",
            "base_fingerprint": script_review.content_fingerprint(drama_script_plan_path(fake_ctx)),
        },
    )
    args = {"episode_id": 1, "doc_type": "drama_script_plan"}
    opened = draft_of(await run_declared_tool("open_draft", fake_ctx, args))
    content = opened["content"]
    content["scenes"][0]["scene_description"] = "阿离推开山门。"

    patched = draft_of(
        await run_declared_tool(
            "patch_draft", fake_ctx, {**args, "content": content, "base_revision": opened["revision"]}
        )
    )
    use_fake_caps(fake_ctx, supported_durations=(4, 6, 8), default_duration=4)
    promoted = await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": patched["revision"]})

    assert promoted.problem is None, promoted
    saved = json.loads(drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["scene_description"] == "阿离推开山门。"


async def test_prompt_authoring_draft_is_not_affected_by_a_confirmed_script_plan(fake_ctx: ToolHarness) -> None:
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    _write_reference_prompt_authoring(
        fake_ctx,
        {
            "title": "第一集",
            "content_mode": "narration",
            "episode": 1,
            "video_units": [{"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 4}],
        },
    )
    _confirm_script_plan(fake_ctx, rv_script_plan_path(fake_ctx))
    project = fake_ctx.pm.load_project(fake_ctx.project_name)
    assert script_review.formal_script_plan_confirmed(fake_ctx.project_path, project, 1)
    args = {"episode_id": 1, "doc_type": "reference_prompt_authoring"}

    opened = draft_of(await run_declared_tool("open_draft", fake_ctx, args))
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            **args,
            "content": {"title": "第一集", "units": [{"text": "@[张三] 坐下"}]},
            "base_revision": opened["revision"],
        },
    )

    assert draft_of(patched)["content"]["units"][0]["text"] == "@[张三] 坐下"


async def test_open_draft_rejects_variant_without_draft_channel(fake_ctx: ToolHarness) -> None:
    """Ad has no structured script_plan or draft channel; errors must name that boundary so Agents do not repeatedly retry an apparently broken tool."""
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(
            {"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "ad", "generation_mode": "storyboard"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_ctx.pm.project_payload["content_mode"] = "ad"
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"

    out = await open_for_edit(fake_ctx)

    assert out.problem is not None
    assert problem_of(out).code == "doc_type_not_applicable"


async def test_open_draft_returns_narration_segments(fake_ctx: ToolHarness) -> None:
    """Narration retrieval holds storyboard tables, leaving formal files untouched until locked promotion."""
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    before = nr_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    out = await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.problem is None, out
    envelope = read_nr_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_NARRATION_SCRIPT_PLAN
    assert envelope["content"]["segments"][0]["novel_text"] == _RV_NOVEL
    assert envelope["violations"] == [], "取回是编辑工位，不是待修复草稿"
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    assert envelope["meta"]["base_fingerprint"] is not None
    assert nr_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_open_draft_narration_round_trips_through_promote(fake_ctx: ToolHarness) -> None:
    """Retrieve/edit/promote writes formal files and clears drafts, using the same promotion channel as drama/reference-to-video."""
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    envelope = read_nr_quarantine(fake_ctx)
    envelope["content"]["segments"][0]["duration_seconds"] = 8
    nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await promote_nr(fake_ctx)

    assert out.problem is None, out
    assert not nr_quarantine_path(fake_ctx).exists()
    saved = json.loads(nr_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["segments"][0]["duration_seconds"] == 8
    assert saved["segments"][0]["novel_text"] == _RV_NOVEL


async def test_open_draft_returns_existing_narration_draft(fake_ctx: ToolHarness) -> None:
    """Existing drafts may hold unpromoted edits; formal-file overwrite would erase ongoing work, so preserve them."""
    nr_source(fake_ctx)
    write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [nr_segment("E1S01", 8, "改到一半的正文")]},
        violations=[],
    )

    out = await open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.problem is None
    assert read_nr_quarantine(fake_ctx)["content"]["segments"][0]["novel_text"] == "改到一半的正文"


async def test_patch_draft_stamps_the_current_schema_version_on_a_legacy_envelope(fake_ctx: ToolHarness) -> None:
    """patch is another draft-write entry; stamp current envelope versions instead of retaining versions read from disk."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))

    path = rv_quarantine_path(fake_ctx)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    del envelope["meta"]["schema_version"]
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    content = opened["content"]
    content["units"][0]["text"] = "@[张三] 走向 @[村口]"
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": content,
            "base_revision": opened["revision"],
        },
    )

    assert patched.problem is None, patched
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["meta"]["schema_version"] == QUARANTINE_SCHEMA_VERSION
    assert saved["meta"]["source"] == "source/episode_1.txt"


async def test_patch_draft_moves_project_activity_forward(fake_ctx: ToolHarness) -> None:
    # Drafts are JSON; mtime does not count as activity, recorded by write exits.
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))
    stale = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
    (fake_ctx.project_path / ACTIVITY_FILENAME).write_text(stale.isoformat(), encoding="utf-8")

    content = opened["content"]
    content["units"][0]["text"] = "@[张三] 走向 @[村口]"
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": content,
            "base_revision": opened["revision"],
        },
    )

    assert patched.problem is None, patched
    recorded = recorded_project_activity(fake_ctx.project_path)
    assert recorded is not None
    assert recorded > stale


async def test_discard_draft_moves_project_activity_forward(fake_ctx: ToolHarness) -> None:
    # Discard deletes draft files and their mtime; deletion exits record activity.
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx))
    stale = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
    (fake_ctx.project_path / ACTIVITY_FILENAME).write_text(stale.isoformat(), encoding="utf-8")

    discarded = draft_of(
        await run_declared_tool(
            "discard_draft",
            fake_ctx,
            {"episode_id": 1, "doc_type": "reference_script_plan", "base_revision": opened["revision"]},
        )
    )

    assert discarded["discarded"] is True
    recorded = recorded_project_activity(fake_ctx.project_path)
    assert recorded is not None
    assert recorded > stale


async def test_patch_draft_supports_multiple_rounds_and_rejects_stale_revision(fake_ctx: ToolHarness) -> None:
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))

    first_content = opened["content"]
    first_content["units"][0]["text"] = "@[张三] 走向 @[村口]"
    first = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": first_content,
            "base_revision": opened["revision"],
        },
    )
    first_result = draft_of(first)
    assert first_result["revision"] != opened["revision"]

    stale = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": {"units": []},
            "base_revision": opened["revision"],
        },
    )
    assert stale.problem is not None
    assert problem_of(stale).code == "revision_conflict"

    second_content = first_result["content"]
    second_content["units"][0]["text"] = "@[张三] 在 @[村口] 停下"
    second = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": second_content,
            "base_revision": first_result["revision"],
        },
    )
    assert draft_of(second)["content"]["units"][0]["text"] == "@[张三] 在 @[村口] 停下"


@pytest.mark.parametrize(
    "doc_type",
    ["drama_script_plan", "narration_script_plan", "reference_script_plan", "reference_prompt_authoring"],
)
@pytest.mark.parametrize("spelling", ["prefixed", "bare"])
async def test_each_doc_type_completes_multi_patch_then_promote_or_discard(
    fake_ctx: ToolHarness, doc_type: str, spelling: str
) -> None:
    if doc_type == "drama_script_plan":
        drama_project(fake_ctx)
        write_drama_script_plan(fake_ctx, [drama_scene()])
    elif doc_type == "narration_script_plan":
        nr_source(fake_ctx)
        write_nr_script_plan(fake_ctx, [nr_segment("E1S01", 4, _RV_NOVEL)])
    elif doc_type == "reference_script_plan":
        rv_source(fake_ctx)
        write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    else:
        rv_project(fake_ctx)
        _write_reference_prompt_authoring(
            fake_ctx,
            {
                "title": "第一集",
                "content_mode": "narration",
                "episode": 1,
                "video_units": [{"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 4}],
            },
        )

    def revision(value: str) -> str:
        return value.removeprefix("sha256-v1:") if spelling == "bare" else value

    args = {"episode_id": 1, "doc_type": doc_type}
    opened = draft_of(await run_declared_tool("open_draft", fake_ctx, args))

    def edit(content: dict, marker: str) -> None:
        if doc_type == "drama_script_plan":
            content["scenes"][0]["scene_description"] = marker
        elif doc_type == "narration_script_plan":
            content["segments"][0]["duration_seconds"] = 8 if marker == "second" else 6
        elif doc_type == "reference_script_plan":
            content["units"][0]["text"] = "@[张三] 在 @[村口] 等候" if marker == "second" else "@[张三] 走向 @[村口]"
        else:
            content["units"][0]["text"] = marker

    first_content = opened["content"]
    edit(first_content, "first")
    first = draft_of(
        await run_declared_tool(
            "patch_draft",
            fake_ctx,
            {**args, "content": first_content, "base_revision": revision(opened["revision"])},
        )
    )
    second_content = first["content"]
    edit(second_content, "second")
    second = draft_of(
        await run_declared_tool(
            "patch_draft",
            fake_ctx,
            {**args, "content": second_content, "base_revision": revision(first["revision"])},
        )
    )
    assert second["revision"] != first["revision"]

    if doc_type == "reference_prompt_authoring":
        discarded = draft_of(
            await run_declared_tool("discard_draft", fake_ctx, {**args, "base_revision": revision(second["revision"])})
        )
        assert discarded["discarded"] is True
    else:
        result = await run_declared_tool(
            "promote_draft", fake_ctx, {**args, "base_revision": revision(second["revision"])}
        )
        assert result.problem is None, result


@pytest.mark.parametrize("spelling", ["prefixed", "bare"])
async def test_patch_draft_can_accept_a_merged_formal_revision(fake_ctx: ToolHarness, spelling: str) -> None:
    """Expose formal_revision with sha256-v1: prefix, still accepting earlier bare hex values."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))

    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 走向 @[村口]")])
    refreshed = draft_of(await open_for_edit(fake_ctx))
    merged = refreshed["content"]
    merged["units"][0]["text"] = "@[张三] 在 @[村口] 停下"
    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": merged,
            "base_revision": opened["revision"],
            "accept_formal_revision": (
                refreshed["formal_revision"]
                if spelling == "prefixed"
                else refreshed["formal_revision"].removeprefix("sha256-v1:")
            ),
        },
    )

    assert patched.problem is None, patched
    assert f"sha256-v1:{read_rv_quarantine(fake_ctx)['meta']['base_fingerprint']}" == refreshed["formal_revision"]
    promoted = await promote_reference_draft(fake_ctx)
    assert promoted.problem is None, promoted


async def test_formal_revision_matches_the_script_plan_content_and_workflow_plan_revisions(
    fake_ctx: ToolHarness,
) -> None:
    """Remote read surfaces use one revision spelling for identical formal script_plan so clients can compare strings directly."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])

    content = await run_declared_tool("get_script_plan_content", fake_ctx, {"episode_id": 1})
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))
    # Production-plan gates/artifacts come from production status, whose service requires real project managers.
    status = WorkflowStateService(ProjectManager(fake_ctx.data_root)).get_status(fake_ctx.project_name, 1)

    assert content.problem is None, content
    revision = content.value.revision
    assert revision.startswith("sha256-v1:")
    assert opened["formal_revision"] == revision
    assert status.artifacts["script_plan"]["revision"] == revision
    assert status.gates["script_plan_review"]["revision"] == revision


async def test_patch_draft_receipt_omits_the_stale_violation_report(fake_ctx: ToolHarness) -> None:
    """Draft violations judge old bodies; patch receipts omit them rather than misrepresent old reports as current edit results."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))
    # Simulate reports left by failed prior promotion.
    envelope = read_rv_quarantine(fake_ctx)
    stale = [{"code": "novel_text_coverage", "label": "", "message": "旧正文的违约", "line": None}]
    rv_quarantine_path(fake_ctx).write_text(
        json.dumps({**envelope, "violations": stale}, ensure_ascii=False), encoding="utf-8"
    )
    reopened = draft_of(await open_for_edit(fake_ctx))
    assert reopened["violations"] == stale

    patched = draft_of(
        await run_declared_tool(
            "patch_draft",
            fake_ctx,
            {
                "episode_id": 1,
                "doc_type": "reference_script_plan",
                "content": opened["content"],
                "base_revision": reopened["revision"],
            },
        )
    )

    assert "violations" not in patched
    assert patched["revision"].startswith("sha256-v1:")
    assert patched["content"] == opened["content"]


async def test_patch_draft_without_accept_formal_revision_keeps_the_old_baseline(fake_ctx: ToolHarness) -> None:
    """Omitting accept_formal_revision does not accept concurrent formal edits; baseline remains unchanged and promotion still conflicts."""
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))
    baseline = read_rv_quarantine(fake_ctx)["meta"]["base_fingerprint"]
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 走向 @[村口]")])

    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": opened["content"],
            "base_revision": opened["revision"],
        },
    )

    assert patched.problem is None, patched
    assert read_rv_quarantine(fake_ctx)["meta"]["base_fingerprint"] == baseline
    assert problem_of(await promote_reference_draft(fake_ctx)).code == "formal_revision_conflict"


@pytest.mark.parametrize(
    ("given", "expected_source"),
    [
        pytest.param({}, "source/episode_1.txt", id="omitted"),
        pytest.param({"source": None}, None, id="explicit-null"),
        pytest.param({"source": "source/episode_2.txt"}, "source/episode_2.txt", id="explicit-path"),
    ],
)
async def test_patch_draft_changes_the_source_scope_only_when_source_is_given(
    fake_ctx: ToolHarness, given: dict, expected_source: str | None
) -> None:
    rv_source(fake_ctx)
    (fake_ctx.project_path / "source" / "episode_2.txt").write_text(_RV_NOVEL, encoding="utf-8")
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))

    patched = await run_declared_tool(
        "patch_draft",
        fake_ctx,
        {
            "episode_id": 1,
            "doc_type": "reference_script_plan",
            "content": opened["content"],
            "base_revision": opened["revision"],
            **given,
        },
    )

    assert patched.problem is None, patched
    assert read_rv_quarantine(fake_ctx)["meta"]["source"] == expected_source


@pytest.mark.skipif(os.name != "posix", reason="FIFO-backed filesystem snapshot requires POSIX")
async def test_patch_draft_revision_covers_source_metadata_without_blocking_event_loop(
    fake_ctx: ToolHarness,
) -> None:
    rv_source(fake_ctx)
    (fake_ctx.project_path / "source" / "episode_2.txt").write_text(_RV_NOVEL, encoding="utf-8")
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx, source="source/episode_1.txt"))
    args = {
        "episode_id": 1,
        "doc_type": "reference_script_plan",
        "content": opened["content"],
        "base_revision": opened["revision"],
    }
    path = rv_quarantine_path(fake_ctx)
    snapshot = path.read_text(encoding="utf-8")
    path.unlink()
    os.mkfifo(path)
    snapshot_writer = subprocess.Popen(  # noqa: ASYNC220 -- Deliberately use blocking subprocesses and FIFO to detect event-loop blockage; async subprocesses would remove that criterion.
        [
            sys.executable,
            "-c",
            """
import select
import sys

with open(sys.argv[1], "w", encoding="utf-8") as fifo:
    print("snapshot_started", flush=True)
    if not select.select([sys.stdin], [], [], 10)[0]:
        print("event_loop_blocked", flush=True)
    fifo.write(sys.argv[2])
""",
            os.fspath(path),
            snapshot,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert snapshot_writer.stdin is not None
    assert snapshot_writer.stdout is not None

    def release_fifo() -> None:
        try:
            snapshot_writer.stdin.write("\n")
            snapshot_writer.stdin.flush()
        except BrokenPipeError:
            # The child may close stdin after its FIFO write completes.
            pass

    patching = asyncio.create_task(
        run_declared_tool("patch_draft", fake_ctx, {**args, "source": "source/episode_2.txt"})
    )
    try:
        started = await asyncio.wait_for(asyncio.to_thread(snapshot_writer.stdout.readline), timeout=20)
        assert started == "snapshot_started\n"
        asyncio.get_running_loop().call_soon(release_fifo)
        first = draft_of(await patching)
        writer_output = await asyncio.to_thread(snapshot_writer.stdout.read)
    finally:
        release_fifo()
        if snapshot_writer.poll() is None:
            snapshot_writer.terminate()
        await asyncio.to_thread(snapshot_writer.wait, 10)
        # The child may close stdin after its FIFO write completes.
        with contextlib.suppress(BrokenPipeError):
            snapshot_writer.stdin.close()
        snapshot_writer.stdout.close()
        if not patching.done():
            patching.cancel()
        await asyncio.gather(patching, return_exceptions=True)

    stale = await run_declared_tool("patch_draft", fake_ctx, {**args, "source": "source/episode_1.txt"})

    assert "event_loop_blocked" not in writer_output
    assert first["revision"] != opened["revision"]
    assert read_rv_quarantine(fake_ctx)["meta"]["source"] == "source/episode_2.txt"
    assert stale.problem is not None
    assert problem_of(stale).code == "revision_conflict"


async def test_discard_draft_rejects_stale_revision(fake_ctx: ToolHarness) -> None:
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    opened = draft_of(await open_for_edit(fake_ctx))
    changed = copy.deepcopy(opened["content"])
    changed["units"][0]["text"] = "@[张三] 走向 @[村口]"
    patched = draft_of(
        await run_declared_tool(
            "patch_draft",
            fake_ctx,
            {
                "episode_id": 1,
                "doc_type": "reference_script_plan",
                "content": changed,
                "base_revision": opened["revision"],
            },
        )
    )

    stale = await run_declared_tool(
        "discard_draft",
        fake_ctx,
        {"episode_id": 1, "doc_type": "reference_script_plan", "base_revision": opened["revision"]},
    )

    assert stale.problem is not None
    assert problem_of(stale).code == "revision_conflict"
    assert rv_quarantine_path(fake_ctx).exists()
    discarded = draft_of(
        await run_declared_tool(
            "discard_draft",
            fake_ctx,
            {"episode_id": 1, "doc_type": "reference_script_plan", "base_revision": patched["revision"]},
        )
    )
    assert discarded["discarded"] is True


async def test_discard_draft_keeps_formal_content_and_is_idempotent(fake_ctx: ToolHarness) -> None:
    rv_source(fake_ctx)
    write_rv_script_plan(fake_ctx, [rv_saved_unit("@[张三] 起身")])
    formal_before = rv_script_plan_path(fake_ctx).read_text(encoding="utf-8")
    opened = draft_of(await open_for_edit(fake_ctx))

    args = {"episode_id": 1, "doc_type": "reference_script_plan", "base_revision": opened["revision"]}
    first = draft_of(await run_declared_tool("discard_draft", fake_ctx, args))
    second = draft_of(await run_declared_tool("discard_draft", fake_ctx, args))

    assert first["discarded"] is True
    assert second["discarded"] is False
    assert not rv_quarantine_path(fake_ctx).exists()
    assert rv_script_plan_path(fake_ctx).read_text(encoding="utf-8") == formal_before
    # Discarded drafts enter project trash and restore to their original locations.
    project_dir = rv_quarantine_path(fake_ctx).parents[2]
    (record,) = project_trash.list_records(project_dir)
    assert record.origin == "agent"
    project_trash.restore(project_dir, record.id)
    assert rv_quarantine_path(fake_ctx).is_file()


async def test_open_reference_prompt_authoring_keeps_an_empty_title_empty(fake_ctx: ToolHarness) -> None:
    rv_project(fake_ctx)
    _write_reference_prompt_authoring(
        fake_ctx,
        {
            "title": "",
            "content_mode": "narration",
            "episode": 1,
            "video_units": [{"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 4}],
        },
    )

    out = await run_declared_tool("open_draft", fake_ctx, {"episode_id": 1, "doc_type": "reference_prompt_authoring"})

    assert draft_of(out)["content"]["title"] == ""


async def test_open_reference_prompt_authoring_returns_flat_editable_content(fake_ctx: ToolHarness) -> None:
    rv_project(fake_ctx)
    _write_reference_prompt_authoring(
        fake_ctx,
        {
            "title": "第一集",
            "content_mode": "narration",
            "episode": 1,
            "video_units": [{"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 4}],
        },
    )

    out = await run_declared_tool("open_draft", fake_ctx, {"episode_id": 1, "doc_type": "reference_prompt_authoring"})

    draft = draft_of(out)
    assert draft["content"] == {"title": "第一集", "units": [{"text": "@[张三] 起身"}]}
    assert draft["revision"].startswith("sha256-v1:")
    envelope = json.loads(
        quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).read_text(encoding="utf-8")
    )
    assert draft["formal_revision"] == f"sha256-v1:{envelope['meta']['base_fingerprint']}"


@pytest.mark.parametrize(
    ("unit", "phrase"),
    [
        (
            {"text": "@[张三] 起身\n@[张三]：{" + "这是一段非常长的台词" * 6 + "}", "duration_seconds": 4},
            "视频单元 1 的台词念完约需",
        ),
        ({"text": "@[张三] 起身", "duration_seconds": 5}, "视频单元 1 的时长 5 秒"),
    ],
    ids=["dialogue_overload", "duration_off_tier"],
)
async def test_promote_prompt_authoring_draft_names_rejected_formal_unit_by_position(
    fake_ctx: ToolHarness, monkeypatch, unit: dict[str, Any], phrase: str
) -> None:
    """Failed prepromotion formal-unit checks still return draft_invalid; explanations use 视频单元 N rather than internal IDs."""
    rv_source(fake_ctx)
    # Prechecks precede text calls; the substitute only assembles ScriptGenerator for promotion and must not be called.
    use_text_generator(monkeypatch, FakeTextGenerator("").create)
    _write_reference_prompt_authoring(
        fake_ctx,
        {
            "title": "第一集",
            "content_mode": "narration",
            "episode": 1,
            "video_units": [{"unit_id": "E1U01", **unit}],
        },
    )
    # Formal scripts count as present only after manifest registration, allowing promotion to read them.
    fake_ctx.pm.mirror_to_disk("episode_1.json")
    args = {"episode_id": 1, "doc_type": "reference_prompt_authoring"}
    opened = draft_of(await run_declared_tool("open_draft", fake_ctx, args))

    out = await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": opened["revision"]})

    problem = problem_of(out)
    assert problem.code == "draft_invalid", problem.detail
    assert phrase in problem.detail
    assert "E1U01" not in problem.detail
    assert quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).exists()
