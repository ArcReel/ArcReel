import asyncio
import json
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.episode.episode_source_commands import set_episode_source_text
from lib.infra.json_io import atomic_write_json
from lib.project.project_manager import ProjectManager
from lib.script import script_review
from server.services.project.script_review import ScriptReviewError, ScriptReviewService
from tests.factories import make_video_request_facts
from tests.fakes import use_artifact_registration
from tests.integration.server.agent_tool_support import (
    _RV_NOVEL,
    ToolHarness,
    drama_scene,
    nr_segment,
    run_declared_tool,
    rv_script_plan_path,
)


def write_rv_script_plan(fake_ctx: ToolHarness, units: list[dict]) -> None:
    path = rv_script_plan_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": units}, ensure_ascii=False), encoding="utf-8")


def rv_saved_unit(text: str, *, unit_id: str = "E1U01", duration: int = 8) -> dict:
    return {
        "unit_id": unit_id,
        "text": text,
        "duration_seconds": duration,
        "source_text": _RV_NOVEL,
    }


def write_drama_script_plan(fake_ctx: ToolHarness, scenes: list[dict]) -> None:
    path = fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"title": "第一集", "scenes": scenes}, ensure_ascii=False), encoding="utf-8")


def write_nr_script_plan(fake_ctx: ToolHarness, segments: list[dict]) -> None:
    path = fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")


async def test_patch_plan_uses_revision_from_agent_read(tmp_path: Path, video_request_facts):
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    ctx = ToolHarness("demo", tmp_path, pm)
    write_drama_script_plan(ctx, [drama_scene()])
    read = await run_declared_tool("get_script_plan_content", ctx, {"episode_id": 1})
    assert read.value is not None

    edited = await run_declared_tool(
        "patch_script_plan",
        ctx,
        {"episode_id": 1, "base_revision": read.value.revision, "operations": [{"op": "set_title", "title": "山门"}]},
    )

    assert edited.value is not None
    assert edited.value.success
    updated = await run_declared_tool("get_script_plan_content", ctx, {"episode_id": 1})
    assert updated.value is not None
    assert updated.value.revision == edited.value.revision
    assert updated.value.content["title"] == "山门"


async def _read(ctx, episode=1):
    outcome = await run_declared_tool("get_script_plan_content", ctx, {"episode_id": episode})
    assert outcome.value is not None, outcome.problem
    return outcome.value


async def _patch(ctx, revision, *operations, episode=1):
    outcome = await run_declared_tool(
        "patch_script_plan", ctx, {"episode_id": episode, "base_revision": revision, "operations": list(operations)}
    )
    assert outcome.value is not None, outcome.problem
    return outcome.value


def _project(tmp_path, mode="drama", *, reference=False):
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", mode)
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    pm.add_character("demo", "阿离", "少女")
    if reference:
        pm.update_project("demo", lambda project: project.update(generation_mode="reference_video"))
    return ToolHarness("demo", tmp_path, pm)


async def test_failed_later_operation_preserves_plan_and_next_id(tmp_path, video_request_facts):
    ctx = _project(tmp_path)
    write_drama_script_plan(ctx, [drama_scene(scene_id="E1S09")])
    base = await _read(ctx)
    item = drama_scene()
    del item["scene_id"]
    insert = {"op": "insert", "item": item}

    rejected = await _patch(
        ctx,
        base.revision,
        insert,
        {
            "op": "update",
            "id": "E1S09",
            "fields": {"duration_seconds": "无效"},
        },
    )
    assert not rejected.success
    (problem,) = rejected.problems
    assert (problem.code, problem.operation_index, problem.next_action) == ("invalid_content", 1, "fix_operation")
    assert problem.locations[0].path == ("scenes", 1, "duration_seconds")
    assert (await _read(ctx)).revision == base.revision

    cleared = await _patch(ctx, base.revision.split(":")[1], {"op": "remove", "id": "E1S09"})
    assert cleared.success
    assert any(issue["code"] == "empty_script_plan" for issue in cleared.blocking_issues)
    inserted = await _patch(ctx, cleared.revision, insert)
    assert inserted.success
    assert inserted.affected_ids == ("E1S10",)


async def test_failed_artifact_registration_rolls_back_plan_and_item_ids(tmp_path, video_request_facts, monkeypatch):
    ctx = _project(tmp_path)
    write_drama_script_plan(ctx, [drama_scene()])
    base = await _read(ctx)
    item = drama_scene()
    del item["scene_id"]
    arguments = {
        "episode_id": 1,
        "base_revision": base.revision,
        "operations": [{"op": "insert", "item": item}],
    }

    def refuse_registration(*_args):
        raise RuntimeError("registration failed")

    with monkeypatch.context() as failure:
        use_artifact_registration(failure, refuse_registration)
        outcome = await run_declared_tool("patch_script_plan", ctx, arguments)
    assert outcome.problem is not None
    assert outcome.problem.code == "internal_error"
    assert (await _read(ctx)).revision == base.revision

    retried = await run_declared_tool("patch_script_plan", ctx, arguments)
    assert retried.value is not None
    assert retried.value.success
    assert retried.value.affected_ids == ("E1S02",)


async def test_invalid_split_request_reports_operation_location(tmp_path, video_request_facts):
    ctx = _project(tmp_path)
    write_drama_script_plan(ctx, [drama_scene()])
    base = await _read(ctx)
    outcome = await run_declared_tool(
        "patch_script_plan",
        ctx,
        {
            "episode_id": 1,
            "base_revision": base.revision,
            "operations": [{"op": "set_title", "title": "山门"}, {"op": "split", "id": "E1S01", "parts": [{}]}],
        },
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "invalid_request"
    params = outcome.problem.params
    assert params is not None
    assert params["operation_index"] == 1
    assert params["location"] == ["operations", 1, "split", "parts"]
    assert params["next_action"] == "fix_operation"
    assert (await _read(ctx)).revision == base.revision


@pytest.mark.parametrize(
    ("mode", "reference", "field"),
    [
        ("drama", False, "needs_replan"),
        ("drama", False, "unit_id"),
        ("narration", False, "source_text"),
        ("reference_video", True, "segment_break"),
    ],
)
async def test_fields_outside_each_route_whitelist_are_rejected(tmp_path, video_request_facts, mode, reference, field):
    ctx = _project(tmp_path, "drama" if reference else mode, reference=reference)
    if reference:
        write_rv_script_plan(ctx, [rv_saved_unit("@[阿离] 收伞。")])
        item_id = "E1U01"
    elif mode == "narration":
        write_nr_script_plan(ctx, [nr_segment()])
        item_id = "E1S01"
    else:
        write_drama_script_plan(ctx, [drama_scene()])
        item_id = "E1S01"
    base = await _read(ctx)
    rejected = await _patch(ctx, base.revision, {"op": "update", "id": item_id, "fields": {field: ""}})
    assert rejected.problems[0].code == "field_not_editable"
    assert rejected.problems[0].locations[0].path == ("operations", 0, "fields", field)
    assert (await _read(ctx)).revision == base.revision


async def test_narration_coverage_uses_final_order_after_move(tmp_path, video_request_facts):
    ctx = _project(tmp_path, "narration")
    set_episode_source_text(ctx.pm, "demo", 1, "风停了。雨也停了。")
    write_nr_script_plan(ctx, [nr_segment(novel_text="风停了。"), nr_segment("E1S02", novel_text="雨也停了。")])
    base = await _read(ctx)
    assert not base.blocking_issues

    moved = await _patch(ctx, base.revision, {"op": "move", "id": "E1S02"})
    assert moved.success
    assert {issue["code"] for issue in moved.blocking_issues} == {"novel_text_coverage"}
    repaired = await _patch(
        ctx,
        moved.revision,
        {"op": "update", "id": "E1S02", "fields": {"novel_text": "风停了。"}},
        {"op": "update", "id": "E1S01", "fields": {"novel_text": "雨也停了。"}},
    )
    assert repaired.success
    assert not repaired.blocking_issues


async def test_legacy_mixed_speech_allows_unchanged_utterances_only(tmp_path, video_request_facts):
    ctx = _project(tmp_path)
    mixed = [{"kind": "dialogue", "speaker": "阿离", "text": "回来了。"}, {"kind": "voiceover", "text": "是夜。"}]
    write_drama_script_plan(ctx, [drama_scene(utterances=mixed, needs_replan=True)])
    base = await _read(ctx)
    saved = await _patch(
        ctx,
        base.revision,
        {
            "op": "update",
            "id": "E1S01",
            "fields": {"scene_description": "阿离收伞。"},
        },
    )
    assert saved.success
    assert (await _read(ctx)).content["scenes"][0]["needs_replan"]
    rejected = await _patch(
        ctx,
        saved.revision,
        {
            "op": "update",
            "id": "E1S01",
            "fields": {"utterances": [mixed[0], {**mixed[1], "text": "早晨。"}]},
        },
    )
    assert rejected.problems[0].code == "mixed_speech"
    assert rejected.problems[0].operation_index == 0
    assert (await _read(ctx)).revision == saved.revision


async def test_web_save_and_agent_patch_share_revision_guard(tmp_path, video_request_facts):
    ctx = _project(tmp_path)
    write_drama_script_plan(ctx, [drama_scene()])
    base = await _read(ctx)
    service = ScriptReviewService(ctx.pm, config_resolver=ctx.services.capabilities)

    web, agent = await asyncio.gather(
        service.save_content("demo", 1, {**base.content, "title": "归来"}, base.revision),
        _patch(ctx, base.revision, {"op": "set_title", "title": "雨夜"}),
        return_exceptions=True,
    )

    assert not isinstance(agent, BaseException)
    current = await _read(ctx)
    assert agent.revision == current.revision
    if agent.success:
        assert isinstance(web, ScriptReviewError)
        assert web.code == "conflict"
        assert current.content["title"] == "雨夜"
    else:
        assert isinstance(web, dict)
        assert agent.problems[0].code == "revision_conflict"
        assert current.content["title"] == "归来"


@pytest.mark.parametrize("legacy_duration", [8, 90])
async def test_reference_plan_migration_patch_confirm_and_rename(
    tmp_path,
    set_video_request_facts,
    legacy_duration,
):
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            supported_durations=(4, 8, 12),
            allowed_durations=(4, 8, 12),
        )
    )
    ctx = _project(tmp_path, reference=True)
    ctx.pm.add_episode("demo", 2, "第二集", "scripts/episode_2.json")
    write_rv_script_plan(
        ctx, [{**rv_saved_unit("@[阿离] 收伞。", duration=legacy_duration), "duration_override": False}]
    )
    anchor = rv_saved_unit("@[阿离] 收伞。")["source_text"]
    set_episode_source_text(ctx.pm, "demo", 1, anchor)
    activate_artifact_target_state(ctx.project_path, bump_schema=False)
    old = script_review.content_fingerprint(rv_script_plan_path(ctx))
    assert old is not None
    ctx.pm.update_project(
        "demo",
        lambda project: script_review.apply_confirmation(
            project,
            1,
            old,
            "2026-01-01T00:00:00Z",
        ),
    )
    service = ScriptReviewService(ctx.pm, config_resolver=ctx.services.capabilities)

    migrated = await _read(ctx)
    assert migrated.content["units"][0]["duration_seconds"] == (8 if legacy_duration == 8 else 12)
    assert "duration_override" not in migrated.content["units"][0]
    assert migrated.revision != script_review.published_revision(old)
    first_edit = await _patch(
        ctx,
        migrated.revision,
        {
            "op": "update",
            "id": "E1U01",
            "fields": {"text": "@[阿离] 撑伞。", "duration_seconds": 8},
        },
    )
    if legacy_duration == 8:
        assert first_edit.problems[0].code == "script_plan_confirmed"
        assert (await service.get_state("demo", 1))["fingerprint"] == script_review.fingerprint_of_revision(
            migrated.revision
        )
        return

    assert first_edit.success
    candidate = {"text": "@[阿离] 收伞。", "duration_seconds": 8, "source_text": anchor}
    edited = await _patch(
        ctx,
        first_edit.revision,
        {"op": "split", "id": "E1U01", "parts": [candidate, {**candidate, "text": "@[阿离] 转身。"}]},
        {"op": "move", "id": "E1U2"},
        {"op": "insert", "after_id": "E1U01", "item": {**candidate, "text": "@[旅人] 远去。"}},
        {
            "op": "set_new_assets",
            "new_assets": [
                {
                    "type": "character",
                    "name": "旅人",
                    "decision": "register",
                    "reason": "本集出场",
                    "description": "背着行囊",
                },
            ],
        },
    )
    assert edited.success, edited.problems
    assert edited.affected_ids == ("E1U01", "E1U2", "E1U3")
    assert not edited.blocking_issues
    assert [row["unit_id"] for row in (await _read(ctx)).content["units"]] == ["E1U2", "E1U01", "E1U3"]
    rejected = await _patch(
        ctx,
        edited.revision,
        {
            "op": "update",
            "id": "E1U2",
            "fields": {"text": "@[阿离]{回来了。} {天黑了。}"},
        },
    )
    assert rejected.problems[0].code == "mixed_speech"
    assert (await _read(ctx)).revision == edited.revision

    cleared = await _patch(ctx, edited.revision, {"op": "set_new_assets", "new_assets": []})
    assert cleared.success
    assert any(issue["code"] == "unregistered_asset" for issue in cleared.blocking_issues)
    repaired = await _patch(ctx, cleared.revision, {"op": "remove", "id": "E1U3"})
    assert repaired.success
    assert not repaired.blocking_issues
    assert (await service.confirm("demo", 1))["status"] == "confirmed"
    readonly = await _patch(ctx, repaired.revision, {"op": "remove", "id": "E1U01"})
    assert readonly.problems[0].code == "script_plan_confirmed"

    pending_path = ctx.project_path / "drafts" / "episode_2" / rv_script_plan_path(ctx).name
    pending_path.parent.mkdir()
    atomic_write_json(
        pending_path,
        {
            "units": [
                rv_saved_unit("@[阿离] 望向 @[小离]。", unit_id="E2U1"),
            ]
        },
    )
    pending = await _read(ctx, 2)
    assert any(issue["code"] == "unregistered_asset" for issue in pending.blocking_issues)
    preview = ctx.pm.rename_asset("demo", "characters", "阿离", "小离", dry_run=True)
    renamed = ctx.pm.rename_asset("demo", "characters", "阿离", "小离")
    assert (preview.episodes, preview.references) == (renamed.episodes, renamed.references) == (2, 5)
    assert not (await _read(ctx, 2)).blocking_issues
    confirmed = await service.get_state("demo", 1)
    assert confirmed["status"] == "confirmed"
    assert confirmed["revision"] != repaired.revision
    assert all("@[小离]" in row["text"] for row in confirmed["content"]["units"])
