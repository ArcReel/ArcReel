"""End-to-end script/project JSON editing tools (patch_episode_script / patch_project and peers).

Real ProjectManager follows shared declaration entries through handlers, editing core, and unified persistence. Assert handler
``ToolOutcome`` and disk results, including real no-worse structure/upsert validation, without mocking private methods.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import types

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.project.data_validator import DataValidator
from lib.project.project_manager import ProjectManager
from lib.script.reference_video.request_projection import unit_reference_declarations
from lib.script.script_batch_edit import script_revision
from server.agent_runtime.arcreel_mcp import build_arcreel_mcp_server
from server.agent_toolset.repair_channel import MERGE_ASSET, PATCH_EPISODE_META, PATCH_PROJECT, RENAME_ASSET
from server.agent_toolset.script_editing import PATCH_EPISODE_SCRIPT
from server.tool_runtime import ScriptPatchResult, ToolMessage, ToolOutcome
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


def _segment(segment_id: str, duration: int = 4) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "duration_seconds": duration,
        "novel_text": "原文",
        "characters_in_segment": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": [_segment("E1S01"), _segment("E1S02")],
    }


def _scene(scene_id: str, duration: int = 8) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "duration_seconds": duration,
        "scene_type": "剧情",
        "characters_in_scene": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _drama_script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "drama",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": [_scene("E1S01"), _scene("E1S02")],
    }


_UNAUTHORED = {"image_prompt": None, "video_prompt": None}


def _unit(unit_id: str) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "text": "推门进屋\n环视四周",
        "duration_seconds": 8,
    }


def _reference_script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": [_unit("E1U1"), _unit("E1U2")],
    }


def _ad_shot(shot_id: str, duration: int = 5) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": "口播文案",
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _ad_script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "ad",
        "shots": [_ad_shot("E1S01"), _ad_shot("E1S02")],
    }


def _register_default_character(pm: ProjectManager) -> None:
    pm.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})


@pytest.fixture
def ctx(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    _register_default_character(pm)
    pm.save_script("demo", _script(), "episode_1.json")
    return ToolHarness(project_name="demo", data_root=tmp_path, pm=pm)


@pytest.fixture
def drama_ctx(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="drama")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    _register_default_character(pm)
    pm.save_script("demo", _drama_script(), "episode_1.json")
    return ToolHarness(project_name="demo", data_root=tmp_path, pm=pm)


@pytest.fixture
def ref_ctx(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    _register_default_character(pm)
    pm.update_project("demo", lambda project: project.update({"generation_mode": "reference_video"}))
    pm.save_script("demo", _reference_script(), "episode_1.json")
    return ToolHarness(project_name="demo", data_root=tmp_path, pm=pm)


@pytest.fixture
def ad_ctx(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="ad")
    pm.create_project_metadata("demo", "Demo", "Anime", "ad")
    _register_default_character(pm)
    pm.save_script("demo", _ad_script(), "episode_1.json")
    return ToolHarness(project_name="demo", data_root=tmp_path, pm=pm)


def _derived_references(tool_ctx: ToolHarness, index: int) -> list[tuple[str, str]]:
    """Current body-derived unit reference images; references do not persist and derive on read."""
    project = tool_ctx.pm.load_project("demo")
    unit = _load(tool_ctx)["video_units"][index]
    return [(ref.type, ref.name) for ref in unit_reference_declarations(project, unit)]


def _said(outcome: ToolOutcome[ToolMessage]) -> str:
    """Human-readable outcome: success message or failure problem detail."""
    if outcome.problem is not None:
        return outcome.problem.detail
    assert outcome.value is not None
    return outcome.value.message


def _load(ctx: ToolHarness) -> dict[str, Any]:
    return ctx.pm.load_script("demo", "episode_1.json")


def _make_project_invalid_outside_assets(ctx: ToolHarness) -> None:
    """Give the pre-edit project an asset-unrelated validation error, an invalid style type."""
    ctx.pm.update_project("demo", lambda p: p.update({"style": 123}))
    assert not DataValidator(projects_dir=ctx.pm.projects_dir).validate_project("demo").valid


async def _patch(
    ctx: ToolHarness, operations: list[dict[str, Any]], *, script: str = "episode_1.json"
) -> ToolOutcome[ScriptPatchResult]:
    return await run_declared_tool(
        PATCH_EPISODE_SCRIPT,
        ctx,
        {"script": script, "base_revision": script_revision(_load(ctx)), "operations": operations},
    )


def _updates(edits: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand per-item-ID field changes into individual update operations."""
    return [{"op": "update", "id": item_id, "fields": fields} for item_id, fields in edits.items()]


def _committed(outcome: ToolOutcome[ScriptPatchResult]) -> ScriptPatchResult:
    assert outcome.problem is None, outcome.problem
    assert outcome.value is not None
    assert outcome.value.success is True, outcome.value.problems
    return outcome.value


def _rejected(outcome: ToolOutcome[ScriptPatchResult]) -> ScriptPatchResult:
    assert outcome.problem is None, outcome.problem
    assert outcome.value is not None
    assert outcome.value.success is False
    return outcome.value


def _problem_code(outcome: ToolOutcome[ScriptPatchResult]) -> str:
    assert outcome.value is None
    assert outcome.problem is not None
    return outcome.problem.code


class TestPatchEpisodeScript:
    @pytest.mark.parametrize("spelling", ["prefixed", "bare"])
    async def test_four_operation_union_commits_as_one_batch(self, ctx: ToolHarness, spelling: str) -> None:
        """Embedded envelopes contain summaries then structured JSON; regeneration notices/mention warnings appear in both."""
        revision = script_revision(_load(ctx))
        session_server = build_arcreel_mcp_server(project_name="demo", data_root=ctx.data_root)["instance"]
        request = types.CallToolRequest(
            params=types.CallToolRequestParams(
                name="patch_episode_script",
                arguments={
                    "script": "episode_1.json",
                    "base_revision": revision.removeprefix("sha256-v1:") if spelling == "bare" else revision,
                    "operations": [
                        {"op": "update", "id": "E1S01", "fields": {"image_prompt.scene": "@[无名路人]回头"}},
                        {"op": "insert", "after_id": "E1S01", "item": _segment("ignored")},
                        {"op": "split", "id": "E1S02", "parts": [_segment("a"), _segment("b")]},
                        {"op": "remove", "id": "E1S03"},
                    ],
                },
            )
        )

        result = (await session_server.request_handlers[types.CallToolRequest](request)).root

        assert isinstance(result, types.CallToolResult)
        assert result.isError is False
        summary, encoded = (block.text for block in result.content if isinstance(block, types.TextContent))
        patched = json.loads(encoded)["script_patch"]
        assert patched["before_revision"] == revision
        assert patched["revision"] != revision
        assert patched["regeneration_required_ids"] == ["E1S01"]
        assert patched["warnings"][0]["params"] == {"unit_id": "E1S01", "name": "无名路人"}
        assert "无名路人" in summary
        assert "immediate regeneration of their corresponding images/videos" in summary
        saved = _load(ctx)["segments"]
        assert [segment["segment_id"] for segment in saved] == ["E1S01", "E1S02", "E1S04"]
        assert saved[0]["image_prompt"]["scene"] == "@[无名路人]回头"

    async def test_batch_multi_segment_multi_field(self, ctx: ToolHarness) -> None:
        """Persist multiple storyboard × field changes in one call."""
        _committed(
            await _patch(
                ctx,
                _updates(
                    {
                        "E1S01": {"image_prompt.scene": "新场景一", "duration_seconds": 6},
                        "E1S02": {"video_prompt.action": "抬头"},
                    }
                ),
            )
        )
        saved = _load(ctx)["segments"]
        assert saved[0]["image_prompt"]["scene"] == "新场景一"
        assert saved[0]["duration_seconds"] == 6
        assert saved[1]["video_prompt"]["action"] == "抬头"

    async def test_unbound_scene_mentions_are_reported_as_warnings(self, ctx: ToolHarness) -> None:
        """Visual @[名称] not matching shot references or registration still commits, with warnings."""
        from lib.script.storyboard_mentions import WARN_STORYBOARD_MENTION_UNBOUND

        result = _committed(await _patch(ctx, _updates({"E1S02": {"image_prompt.scene": "@[角色A]与@[无名路人]对视"}})))

        assert result.warnings == (
            {"key": WARN_STORYBOARD_MENTION_UNBOUND, "params": {"unit_id": "E1S02", "name": "无名路人"}},
        )
        assert _load(ctx)["segments"][1]["image_prompt"]["scene"] == "@[角色A]与@[无名路人]对视"

    async def test_unknown_id_rolls_back_whole_batch(self, ctx: ToolHarness) -> None:
        """Unmatched IDs make the whole batch write nothing, rolling back valid siblings and locating errors at that ID."""
        result = _rejected(
            await _patch(ctx, _updates({"E1S01": {"image_prompt.scene": "本应回滚"}, "E9": {"duration_seconds": 5}}))
        )

        assert (result.problems[0].operation_index, result.problems[0].unit_id) == (1, "E9")
        # Valid sibling edits do not persist: all-or-nothing.
        assert _load(ctx)["segments"][0]["image_prompt"]["scene"] == "场景描述"

    async def test_invalid_value_rolls_back_whole_batch(self, ctx: ToolHarness) -> None:
        """An out-of-range duration turning valid scripts invalid is blocked at unified persistence; write none of the batch."""
        _rejected(
            await _patch(
                ctx, _updates({"E1S01": {"duration_seconds": 999}, "E1S02": {"image_prompt.scene": "本应回滚"}})
            )
        )
        saved = _load(ctx)["segments"]
        assert saved[0]["duration_seconds"] == 4  # Nothing persisted.
        assert saved[1]["image_prompt"]["scene"] == "场景描述"  # Whole batch rolls back.

    async def test_error_localizes_scene_id_and_field(self, ctx: ToolHarness) -> None:
        """Missing paths identify triggering scene_id + field precisely."""
        result = _rejected(await _patch(ctx, _updates({"E1S01": {"image_prompt.nope.deep": "x"}})))

        problem = result.problems[0]
        assert problem.unit_id == "E1S01"
        assert problem.locations[0].path == ("operations", 0, "fields", "image_prompt", "nope", "deep")

    async def test_empty_operations_rejected(self, ctx: ToolHarness) -> None:
        """Reject empty operations under no-zero-signal-success rules."""
        assert _problem_code(await _patch(ctx, [])) == "invalid_request"

    async def test_empty_field_map_rejected(self, ctx: ToolHarness) -> None:
        """Reject empty storyboard field maps under no-zero-signal-success rules."""
        assert _problem_code(await _patch(ctx, _updates({"E1S01": {}}))) == "invalid_request"
        assert _load(ctx)["segments"][0]["image_prompt"]["scene"] == "场景描述"

    async def test_reject_generated_assets(self, ctx: ToolHarness) -> None:
        """Forbid generated_assets edits exactly as single edits do; persist none of the batch."""
        before = _load(ctx)
        _rejected(await _patch(ctx, _updates({"E1S01": {"generated_assets.status": "completed"}})))
        assert _load(ctx) == before

    async def test_reject_id_field(self, ctx: ToolHarness) -> None:
        """Forbid storyboard ID edits exactly as single edits do."""
        _rejected(await _patch(ctx, _updates({"E1S01": {"segment_id": "E1S99"}})))
        assert [s["segment_id"] for s in _load(ctx)["segments"]] == ["E1S01", "E1S02"]

    async def test_unchanged_legacy_mixed_speech_allows_metadata_patch(self, ctx: ToolHarness) -> None:
        script = _script()
        prompt = {**script["segments"][0]["video_prompt"], "dialogue": [{"speaker": "甲", "line": "台词"}]}
        script["segments"][0]["video_prompt"] = prompt
        script["segments"][0]["needs_replan"] = True
        ctx.pm.save_script("demo", script, "episode_1.json")

        _committed(await _patch(ctx, _updates({"E1S01": {"video_prompt": prompt, "note": "保留历史媒体"}})))

        saved = _load(ctx)["segments"][0]
        assert saved["note"] == "保留历史媒体"
        assert saved["needs_replan"] is True

    async def test_legacy_mixed_speech_allows_visual_prompt_patch(self, ctx: ToolHarness) -> None:
        script = _script()
        script["segments"][0]["video_prompt"]["dialogue"] = [{"speaker": "甲", "line": "台词"}]
        script["segments"][0]["needs_replan"] = True
        ctx.pm.save_script("demo", script, "episode_1.json")

        _committed(await _patch(ctx, _updates({"E1S01": {"video_prompt.action": "慢慢转身"}})))

        saved = _load(ctx)["segments"][0]
        assert saved["video_prompt"]["action"] == "慢慢转身"
        assert saved["needs_replan"] is True

    async def test_repairing_machine_candidate_clears_replan_marker(self, ctx: ToolHarness) -> None:
        script = _script()
        script["segments"][0]["video_prompt"]["dialogue"] = [{"speaker": "甲", "line": "台词"}]
        script["segments"][0]["needs_replan"] = True
        ctx.pm.save_script("demo", script, "episode_1.json")

        _committed(
            await _patch(
                ctx,
                _updates(
                    {"E1S01": {"video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"}}}
                ),
            )
        )

        assert _load(ctx)["segments"][0].get("needs_replan") is not True

    async def test_rejects_path_in_script_arg(self, ctx: ToolHarness) -> None:
        assert (
            _problem_code(await _patch(ctx, _updates({"E1S01": {"duration_seconds": 5}}), script="../x.json"))
            == "invalid_request"
        )

    async def test_hallucinated_leaf_blocked_by_funnel(self, ctx: ToolHarness) -> None:
        """Existing intermediate paths do not permit invented leaves such as video_prompt.hallucinated_key;
        unified persistence extra='forbid' rejects instead of silently saving."""
        _rejected(await _patch(ctx, _updates({"E1S01": {"video_prompt.hallucinated_key": "stray"}})))
        assert "hallucinated_key" not in _load(ctx)["segments"][0]["video_prompt"]

    async def test_middle_path_typo_fail_loud(self, ctx: ToolHarness) -> None:
        """Misspelled image_prompt.scen instead of .scene fails loudly with ID/field location."""
        result = _rejected(await _patch(ctx, _updates({"E1S01": {"image_prompt.scen.x": "y"}})))

        problem = result.problems[0]
        assert problem.unit_id == "E1S01"
        assert problem.locations[0].path == ("operations", 0, "fields", "image_prompt", "scen", "x")

    async def test_prompt_change_requires_regeneration(self, ctx: ToolHarness) -> None:
        """Changed image_prompt / video_prompt items enter the regeneration list."""
        result = _committed(await _patch(ctx, _updates({"E1S01": {"image_prompt.scene": "新场景"}})))
        assert result.regeneration_required_ids == ("E1S01",)

    async def test_non_prompt_change_requires_no_regeneration(self, ctx: ToolHarness) -> None:
        """Nonprompt-only edits such as duration_seconds leave that list empty."""
        result = _committed(await _patch(ctx, _updates({"E1S01": {"duration_seconds": 5}})))
        assert result.regeneration_required_ids == ()

    async def test_drama_mode_by_scene_id(self, drama_ctx: ToolHarness) -> None:
        """Drama locates scene_id and persists batch edits."""
        _committed(await _patch(drama_ctx, _updates({"E1S02": {"image_prompt.scene": "剧集新场景"}})))
        assert _load(drama_ctx)["scenes"][1]["image_prompt"]["scene"] == "剧集新场景"

    async def test_reference_mode_by_unit_id(self, ref_ctx: ToolHarness) -> None:
        """Reference mode locates unit_id and persists batch edits."""
        _committed(await _patch(ref_ctx, _updates({"E1U1": {"note": "单元备注"}})))
        assert _load(ref_ctx)["video_units"][0]["note"] == "单元备注"

    async def test_reference_replan_marker_requires_planning_edit(self, ref_ctx: ToolHarness) -> None:
        script = _reference_script()
        script["video_units"][0]["needs_replan"] = True
        ref_ctx.pm.save_script("demo", script, "episode_1.json")

        _committed(await _patch(ref_ctx, _updates({"E1U1": {"note": "待复核"}})))
        assert _load(ref_ctx)["video_units"][0]["needs_replan"] is True

        _committed(await _patch(ref_ctx, _updates({"E1U1": {"text": "修复后的无声镜头"}})))
        assert _load(ref_ctx)["video_units"][0].get("needs_replan") is not True

    async def test_reference_duration_repair_clears_non_content_marker(self, ref_ctx: ToolHarness) -> None:
        script = _reference_script()
        script["video_units"][0].update({"duration_seconds": 1, "needs_replan": True})
        ref_ctx.pm.save_script("demo", script, "episode_1.json")

        _committed(await _patch(ref_ctx, _updates({"E1U1": {"duration_seconds": 1}})))

        assert _load(ref_ctx)["video_units"][0].get("needs_replan") is not True

    async def test_reference_text_edit_moves_the_derived_references(self, ref_ctx: ToolHarness) -> None:
        project = ref_ctx.pm.load_project("demo")
        project["products"] = {"商品A": {"description": ""}, "商品B": {"description": ""}}
        ref_ctx.pm.save_project("demo", project)
        script = _reference_script()
        script["video_units"][0]["text"] = "@[商品A] 正面展示"
        ref_ctx.pm.save_script("demo", script, "episode_1.json")

        _committed(await _patch(ref_ctx, _updates({"E1U1": {"text": "@[商品B] 侧面展示"}})))

        assert _derived_references(ref_ctx, 0) == [("product", "商品B")]

    async def test_reference_text_edit_admits_non_character_mentions(self, ref_ctx: ToolHarness) -> None:
        project = ref_ctx.pm.load_project("demo")
        project["scenes"] = {"酒馆": {"description": ""}}
        ref_ctx.pm.save_project("demo", project)

        _committed(await _patch(ref_ctx, _updates({"E1U1": {"text": "@[酒馆]：木门被风吹开"}})))

        assert _derived_references(ref_ctx, 0) == [("scene", "酒馆")]

    async def test_reference_replan_marker_cannot_be_patched_directly(self, ref_ctx: ToolHarness) -> None:
        script = _reference_script()
        script["video_units"][0]["needs_replan"] = True
        ref_ctx.pm.save_script("demo", script, "episode_1.json")

        _rejected(await _patch(ref_ctx, _updates({"E1U1": {"needs_replan": False}})))

        assert _load(ref_ctx)["video_units"][0]["needs_replan"] is True

    async def test_ad_mode_by_shot_id(self, ad_ctx: ToolHarness) -> None:
        """Ad/short-film locates shot_id and persists batch edits."""
        _committed(await _patch(ad_ctx, _updates({"E1S02": {"voiceover_text": "新口播"}})))
        assert _load(ad_ctx)["shots"][1]["voiceover_text"] == "新口播"

    @pytest.mark.parametrize(
        ("fixture", "items_key", "item_id", "fields"),
        [
            ("ctx", "segments", "E1S01", {"novel_text": "改后的旁白"}),
            (
                "drama_ctx",
                "scenes",
                "E1S01",
                {"utterances": [{"kind": "dialogue", "speaker": "角色A", "text": "走吧。"}], "source_text": "原文锚"},
            ),
            ("ref_ctx", "video_units", "E1U1", {"text": "新正文", "source_text": "原文锚"}),
        ],
    )
    async def test_content_fields_are_patchable(
        self,
        request: pytest.FixtureRequest,
        fixture: str,
        items_key: str,
        item_id: str,
        fields: dict[str, Any],
    ) -> None:
        """Postauthoring content edits target formal scripts: narration, dialogue, unit bodies, and corresponding source text remain editable."""
        tool_ctx: ToolHarness = request.getfixturevalue(fixture)

        out = await _patch(tool_ctx, [{"op": "update", "id": item_id, "fields": fields}])

        _committed(out)
        saved = _load(tool_ctx)[items_key][0]
        assert {key: saved[key] for key in fields} == fields


class TestPatchEpisodeScriptStructuralOperations:
    @pytest.mark.parametrize(
        ("fixture", "items_key"), [("ctx", "segments"), ("drama_ctx", "scenes"), ("ad_ctx", "shots")]
    )
    async def test_removing_every_item_commits_an_empty_script(
        self, request: pytest.FixtureRequest, fixture: str, items_key: str
    ) -> None:
        tool_ctx: ToolHarness = request.getfixturevalue(fixture)

        out = await _patch(tool_ctx, [{"op": "remove", "id": "E1S01"}, {"op": "remove", "id": "E1S02"}])

        _committed(out)
        assert _load(tool_ctx)[items_key] == []

    async def test_removing_one_of_several_items_still_commits(self, ad_ctx: ToolHarness) -> None:
        out = await _patch(ad_ctx, [{"op": "remove", "id": "E1S01"}])

        _committed(out)
        assert [shot["shot_id"] for shot in _load(ad_ctx)["shots"]] == ["E1S02"]

    async def test_insert_adds_at_position(self, ctx: ToolHarness) -> None:
        out = await _patch(ctx, [{"op": "insert", "after_id": "E1S01", "item": _segment("IGN")}])
        _committed(out)
        ids = [s["segment_id"] for s in _load(ctx)["segments"]]
        assert ids == ["E1S01", "E1S03", "E1S02"]

    @pytest.mark.parametrize(("prompts", "expected"), [(_UNAUTHORED, True), ({}, False)])
    async def test_insert_marks_new_entry_pending_authoring_unless_prompts_are_supplied(
        self, ctx: ToolHarness, prompts: dict[str, Any], expected: bool
    ) -> None:
        out = await _patch(ctx, [{"op": "insert", "after_id": "E1S01", "item": _segment("IGN") | prompts}])

        _committed(out)
        segments = {s["segment_id"]: s for s in _load(ctx)["segments"]}
        assert segments["E1S03"].get("pending_authoring", False) is expected
        assert "pending_authoring" not in segments["E1S01"]

    async def test_split_marks_parts_without_prompts_pending_authoring(self, ctx: ToolHarness) -> None:
        parts = [_segment("a") | _UNAUTHORED, _segment("b") | _UNAUTHORED, _segment("c")]
        out = await _patch(ctx, [{"op": "split", "id": "E1S01", "parts": parts}])

        _committed(out)
        segments = _load(ctx)["segments"]
        assert segments[0]["segment_id"] == "E1S01"
        assert [segment.get("pending_authoring", False) for segment in segments[:3]] == [True, True, False]

    async def test_writing_unit_text_clears_pending_authoring(self, ref_ctx: ToolHarness) -> None:
        with ref_ctx.pm.locked_script("demo", "episode_1.json", validate=False) as script:
            for unit in script["video_units"]:
                unit["pending_authoring"] = True

        out = await _patch(
            ref_ctx,
            [
                {"op": "update", "id": "E1U1", "fields": {"text": "推门进屋\n抬头望向窗外"}},
                {"op": "update", "id": "E1U2", "fields": {"duration_seconds": 8}},
            ],
        )

        _committed(out)
        units = {u["unit_id"]: u for u in _load(ref_ctx)["video_units"]}
        assert "pending_authoring" not in units["E1U1"]
        assert units["E1U2"]["pending_authoring"] is True

    async def test_reference_insert_admits_non_character_mentions(self, ref_ctx: ToolHarness) -> None:
        project = ref_ctx.pm.load_project("demo")
        project["scenes"] = {"酒馆": {"description": ""}}
        ref_ctx.pm.save_project("demo", project)
        inserted = _unit("ignored")
        inserted["text"] = "@[酒馆]：木门被风吹开"

        out = await _patch(ref_ctx, [{"op": "insert", "after_id": "E1U1", "item": inserted}])

        _committed(out)
        assert _derived_references(ref_ctx, 1) == [("scene", "酒馆")]

    @pytest.mark.parametrize(
        ("fixture", "items_key", "id_key", "move", "expected"),
        [
            ("ctx", "segments", "segment_id", {"id": "E1S02"}, ["E1S02", "E1S01"]),
            ("drama_ctx", "scenes", "scene_id", {"id": "E1S01", "after_id": "E1S02"}, ["E1S02", "E1S01"]),
            ("ad_ctx", "shots", "shot_id", {"id": "E1S02", "after_id": None}, ["E1S02", "E1S01"]),
            ("ref_ctx", "video_units", "unit_id", {"id": "E1U1", "after_id": "E1U2"}, ["E1U2", "E1U1"]),
        ],
    )
    async def test_move_reorders_items_and_keeps_their_media(
        self,
        request: pytest.FixtureRequest,
        fixture: str,
        items_key: str,
        id_key: str,
        move: dict[str, Any],
        expected: list[str],
    ) -> None:
        tool_ctx: ToolHarness = request.getfixturevalue(fixture)
        with tool_ctx.pm.locked_script("demo", "episode_1.json", validate=False) as script:
            script[items_key][0]["generated_assets"] = {"storyboard_image": "storyboards/first.png"}

        out = await _patch(tool_ctx, [{"op": "move", **move}])

        result = _committed(out)
        saved = _load(tool_ctx)[items_key]
        assert [item[id_key] for item in saved] == expected
        moved_first = next(item for item in saved if item[id_key] == expected[1])
        assert moved_first["generated_assets"] == {"storyboard_image": "storyboards/first.png"}
        assert result.regeneration_required_ids == ()

    async def test_move_after_itself_is_rejected_without_writing(self, ctx: ToolHarness) -> None:
        before = _load(ctx)

        out = await _patch(ctx, [{"op": "move", "id": "E1S01", "after_id": "E1S01"}])

        problem = _rejected(out).problems[0]
        assert problem.operation_index == 0
        assert _load(ctx) == before

    async def test_move_combines_with_insert_in_one_batch(self, ctx: ToolHarness) -> None:
        out = await _patch(
            ctx,
            [
                {"op": "insert", "after_id": "E1S02", "item": _segment("IGN")},
                {"op": "move", "id": "E1S03", "after_id": None},
            ],
        )

        _committed(out)
        assert [s["segment_id"] for s in _load(ctx)["segments"]] == ["E1S03", "E1S01", "E1S02"]

    async def test_remove_by_id(self, ctx: ToolHarness) -> None:
        out = await _patch(ctx, [{"op": "remove", "id": "E1S01"}])
        _committed(out)
        assert [s["segment_id"] for s in _load(ctx)["segments"]] == ["E1S02"]

    @pytest.mark.parametrize(
        ("removed_id", "structural"),
        [
            # Delete the last item then add; allocation never reuses removed IDs.
            ("E1S02", {"op": "insert", "after_id": "E1S01", "item": _segment("ignored")}),
            ("E1S01_1", {"op": "split", "id": "E1S01", "parts": [_segment("a"), _segment("b")]}),
        ],
    )
    async def test_removed_id_is_not_handed_out_again(
        self,
        ctx: ToolHarness,
        removed_id: str,
        structural: dict[str, Any],
    ) -> None:
        script = _script()
        script["segments"] = [s for s in script["segments"] if s["segment_id"] != removed_id]
        removed = _segment(removed_id)
        removed["generated_assets"] = {"video_clip": "old-paid.mp4", "status": "completed"}
        script["segments"].insert(1, removed)
        ctx.pm.save_script("demo", script, "episode_1.json")
        adapter = ProjectArtifactManifestAdapter(ctx.project_path)
        old_video = ArtifactKey.episode_video(1, removed_id)
        adapter.put_entry(
            old_video,
            ArtifactManifestEntry(artifact_path="videos/old-paid.mp4", basis_digest=f"sha256-v1:{'a' * 64}"),
        )

        out = await _patch(ctx, [{"op": "remove", "id": removed_id}, structural])

        _committed(out)
        saved = _load(ctx)["segments"]
        assert removed_id not in [segment["segment_id"] for segment in saved]
        assert all((segment.get("generated_assets") or {}).get("video_clip") != "old-paid.mp4" for segment in saved)
        assert adapter.get_entry(old_video) is None

    async def test_split_keeps_first_id_and_clears_new_identity_assets(self, ctx: ToolHarness) -> None:
        # Untrusted parts assets: same-ID anchors retain actual assets; new identities clear all assets.
        part_a = _segment("a")
        part_a["generated_assets"] = {"storyboard_image": "stale.png", "status": "completed"}
        out = await _patch(ctx, [{"op": "split", "id": "E1S01", "parts": [part_a, _segment("b")]}])
        _committed(out)
        saved = _load(ctx)["segments"]
        ids = [s["segment_id"] for s in saved]
        # Later parts take new main IDs above episode high-water marks, without _k suffixes.
        assert ids == ["E1S01", "E1S03", "E1S02"]
        assert not saved[0].get("generated_assets")
        assert not saved[1].get("generated_assets")

    async def test_reference_split_validates_contiguous_replacement_after_reordered_derived_id(
        self, ref_ctx: ToolHarness
    ) -> None:
        script = _reference_script()
        script["video_units"] = [_unit("E1U1_1"), _unit("E1U1"), _unit("E1U2")]
        ref_ctx.pm.save_script("demo", script, "episode_1.json")
        before = _load(ref_ctx)
        mixed = _unit("ignored")
        mixed["text"] = "@[角色A]：{快走。}\n{风吹过旷野。}"

        out = await _patch(
            ref_ctx,
            [{"op": "split", "id": "E1U1", "parts": [_unit("ignored"), mixed]}],
        )

        admission = _rejected(out).speech_admission
        assert admission is not None
        assert admission.unit_id == "E1U3"
        assert admission.problems[0].code == "mixed_speech"
        assert _load(ref_ctx) == before

    async def test_reference_split_admits_non_character_mentions(self, ref_ctx: ToolHarness) -> None:
        project = ref_ctx.pm.load_project("demo")
        project["scenes"] = {"酒馆": {"description": ""}}
        ref_ctx.pm.save_project("demo", project)
        parts = [_unit("ignored"), _unit("ignored")]
        for part in parts:
            part["text"] = "@[酒馆]：木门被风吹开"

        out = await _patch(ref_ctx, [{"op": "split", "id": "E1U1", "parts": parts}])

        _committed(out)
        assert [_derived_references(ref_ctx, index) for index in (0, 1)] == [
            [("scene", "酒馆")],
            [("scene", "酒馆")],
        ]

    async def test_split_too_few_parts_errors(self, ctx: ToolHarness) -> None:
        assert (
            _problem_code(await _patch(ctx, [{"op": "split", "id": "E1S01", "parts": [_segment("a")]}]))
            == "invalid_request"
        )


class TestPatchEpisodeMeta:
    """patch_episode_meta edits allowlisted top-level title, automatically mirroring persistence into project.json."""

    async def test_set_title(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_EPISODE_META,
            ctx,
            {"script": "episode_1.json", "field": "title", "value": "新标题"},
        )
        assert out.problem is None
        assert _load(ctx)["title"] == "新标题"
        # project.json mirror synchronization on locked_script exit through sync_episode_from_script.
        episodes = ctx.pm.load_project("demo")["episodes"]
        entry = next(e for e in episodes if e["episode"] == 1)
        assert entry["title"] == "新标题"

    async def test_title_trimmed(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_EPISODE_META,
            ctx,
            {"script": "episode_1.json", "field": "title", "value": "  去空白  "},
        )
        assert out.problem is None
        assert _load(ctx)["title"] == "去空白"

    async def test_empty_title_rejected(self, ctx: ToolHarness) -> None:
        for blank in ("", "   ", "\t\n"):
            out = await run_declared_tool(
                PATCH_EPISODE_META,
                ctx,
                {"script": "episode_1.json", "field": "title", "value": blank},
            )
            assert out.problem is not None
        assert _load(ctx)["title"] == "标题"  # Original unchanged.

    async def test_non_whitelist_field_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_EPISODE_META,
            ctx,
            {"script": "episode_1.json", "field": "episode", "value": 9},
        )
        assert out.problem is not None
        assert _load(ctx)["episode"] == 1  # Not rewritten.

    async def test_non_string_value_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_EPISODE_META,
            ctx,
            {"script": "episode_1.json", "field": "title", "value": 123},
        )
        assert out.problem is not None
        assert _load(ctx)["title"] == "标题"

    async def test_rejects_path_in_script_arg(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_EPISODE_META,
            ctx,
            {"script": "../x.json", "field": "title", "value": "x"},
        )
        assert out.problem is not None


class TestPatchProject:
    async def test_add_new_character(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "voice_style": "豪放"}}},
        )
        assert out.problem is None
        chars = ctx.pm.load_project("demo")["characters"]
        assert chars["李白"]["description"] == "白衣剑客"
        assert chars["李白"]["voice_style"] == "豪放"

    async def test_modify_existing_character_merges_fields(self, ctx: ToolHarness) -> None:
        await run_declared_tool(
            PATCH_PROJECT, ctx, {"table": "characters", "entries": {"李白": {"description": "剑客"}}}
        )
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "改后描述"}}},
        )
        assert out.problem is None
        assert ctx.pm.load_project("demo")["characters"]["李白"]["description"] == "改后描述"

    @pytest.mark.parametrize(
        ("table", "entry"),
        [
            ("characters", {"description": ""}),
            ("scenes", {"description": ""}),
            ("props", {}),
            ("products", {"description": ""}),
        ],
    )
    async def test_new_asset_without_description_is_written(self, ctx: ToolHarness, table: str, entry: dict) -> None:
        """Descriptions are required only for asset-image generation; new assets persist empty-string descriptions when empty/absent."""
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"table": table, "entries": {"无描述资产": entry}})

        assert out.problem is None
        assert out.value.changes["added"] == ["无描述资产"]
        assert ctx.pm.load_project("demo")[table]["无描述资产"]["description"] == ""

    async def test_unknown_table_errors(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"table": "weapons", "entries": {"剑": {"description": "x"}}})
        assert out.problem is not None

    async def test_invalid_entry_rejected_even_when_project_already_invalid(self, ctx: ToolHarness) -> None:
        """No-worse error-set differences reject new upsert errors, such as nonstring descriptions,
        even on already-corrupt projects. Simple `before_valid AND after.valid`
        lets new errors piggyback; error-set differences close that bypass."""
        _make_project_invalid_outside_assets(ctx)
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            # Invalid description types are newly introduced errors.
            {"table": "scenes", "entries": {"空场景": {"description": 1}}},
        )
        assert out.problem is not None
        # No persistence: the empty scene was not written.
        assert "空场景" not in ctx.pm.load_project("demo").get("scenes", {})

    async def test_upsert_allowed_when_project_already_invalid(self, ctx: ToolHarness) -> None:
        """No-worse patch_project succeeds despite unrelated historical invalid style types;
        otherwise legacy-corrupt projects lose the whole editing path."""
        _make_project_invalid_outside_assets(ctx)
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        assert out.problem is None
        assert "李白" in ctx.pm.load_project("demo").get("characters", {})

    async def test_entry_name_whitespace_normalized(self, ctx: ToolHarness) -> None:
        """Strip surrounding name spaces before storing to avoid whitespace differences breaking name lookup."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"  李白  ": {"description": "白衣剑客"}}},
        )
        assert out.problem is None
        chars = ctx.pm.load_project("demo")["characters"]
        assert "李白" in chars  # Store normalized names.
        assert "  李白  " not in chars

    async def test_blank_entry_name_rejected(self, ctx: ToolHarness) -> None:
        """Empty/whitespace names fail loudly instead of writing \"\" / \"   \" as valid entry keys."""
        for blank_name in ("", "   ", "\t\n"):
            out = await run_declared_tool(
                PATCH_PROJECT,
                ctx,
                {"table": "characters", "entries": {blank_name: {"description": "x"}}},
            )
            assert out.problem is not None

    async def test_non_string_extra_field_rejected(self, ctx: ToolHarness) -> None:
        """voice_style and other extra_string_fields require strings; guards reject int/dict/list input
        instead of later reference_image path construction crashing."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "voice_style": 1}}},
        )
        assert out.problem is not None
        assert "李白" not in ctx.pm.load_project("demo").get("characters", {})

    async def test_upsert_strips_sheet_and_unknown_fields(self, ctx: ToolHarness) -> None:
        """Least privilege: Agents edit description + spec.extra_string_fields only.
        Silently discard system-generated sheet paths and undeclared keys, preventing
        overwrites outside Agent authority."""
        # Upsert clean entries, then attempt ignored sheet changes plus unknown keys.
        await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "voice_style": "豪放"}}},
        )
        # Simulate system _update_asset_sheet writing paths.
        ctx.pm.update_project(
            "demo", lambda p: p["characters"]["李白"].update({"character_sheet": "characters/li_bai.png"})
        )

        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "改后描述",
                        "voice_style": "沉稳",
                        "character_sheet": "fake/agent_overwrite.png",  # Discard.
                        "random_extra_field": "noise",  # Discard.
                    }
                },
            },
        )
        assert out.problem is None
        char = ctx.pm.load_project("demo")["characters"]["李白"]
        assert char["description"] == "改后描述"
        assert char["voice_style"] == "沉稳"
        assert char["character_sheet"] == "characters/li_bai.png"  # Agents do not overwrite system fields.
        assert "random_extra_field" not in char  # Fields outside spec never persist.

    async def test_upsert_strips_reference_audio(self, ctx: ToolHarness) -> None:
        """reference_audio, like reference_image, is an uploaded path outside
        agent_editable_extra_fields; silently discard Agent writes."""
        ctx.pm.update_project(
            "demo",
            lambda p: p["characters"].update(
                {
                    "李白": {
                        "description": "白衣剑客",
                        "voice_style": "豪放",
                        "reference_audio": "characters/refs_audio/李白.wav",
                    }
                }
            ),
        )

        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "改后描述",
                        "reference_audio": "fake/agent_overwrite.wav",  # Discard.
                    }
                },
            },
        )
        assert out.problem is None
        char = ctx.pm.load_project("demo")["characters"]["李白"]
        assert char["reference_audio"] == "characters/refs_audio/李白.wav"  # Not overwritten by Agents.

    async def test_non_string_description_rejected(self, ctx: ToolHarness) -> None:
        """description requires strings; numeric Agent input, such as LLM "1" emitted as int,
        fails loudly instead of persisting as valid assets."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"阿青": {"description": 1}}},
        )
        assert out.problem is not None
        assert "阿青" not in ctx.pm.load_project("demo").get("characters", {})

    async def test_upsert_fails_loud_when_bucket_not_dict(self, ctx: ToolHarness) -> None:
        """Existing nondict bucket_key, such as legacy lists, fails loudly
        instead of vague AttributeError at bucket.get."""
        ctx.pm.update_project("demo", lambda p: p.update({"characters": []}))
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        assert out.problem is not None

    async def test_normalized_name_collision_fails_loud(self, ctx: ToolHarness) -> None:
        """Raw keys equal after strip, such as "李白" / "  李白  ", fail loudly instead of later keys
        silently overwriting earlier attributes; Agents must see collisions and deduplicate."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {"description": "白衣剑客"},
                    "  李白  ": {"description": "白衣剑客v2"},
                },
            },
        )
        assert out.problem is not None
        # Neither version persists; validation raises before writes.
        assert "李白" not in ctx.pm.load_project("demo").get("characters", {})

    async def test_upsert_strips_reference_image_field(self, ctx: ToolHarness) -> None:
        """reference_image is an uploaded/system-generated path like sheet_field, excluded from
        agent_editable_extra_fields. patch_project silently discards it so Agents cannot overwrite
        uploaded character references; use update_character_reference_image.
        Validator extra_string_fields still retains reference_image for type checks."""
        # Upsert clean entries first.
        await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "voice_style": "豪放"}}},
        )
        # Simulate WebUI reference upload.
        ctx.pm.update_character_reference_image("demo", "李白", "characters/refs/li_bai.jpg")
        assert ctx.pm.load_project("demo")["characters"]["李白"]["reference_image"] == "characters/refs/li_bai.jpg"

        # Agent description edits also attempt reference_image overwrite; discard it.
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "改后描述",
                        "voice_style": "沉稳",
                        "reference_image": "",  # Filter through the allowlist.
                    }
                },
            },
        )
        assert out.problem is None
        char = ctx.pm.load_project("demo")["characters"]["李白"]
        assert char["description"] == "改后描述"
        assert char["voice_style"] == "沉稳"
        # Agents never overwrite uploaded reference_image.
        assert char["reference_image"] == "characters/refs/li_bai.jpg"

    async def test_product_upsert_selling_points_editable(self, ctx: ToolHarness) -> None:
        """Agents can edit products and allowlisted selling_points, drafted by Agents and editable by users;
        initialize new-entry lists according to spec."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "products",
                "entries": {"保温杯": {"description": "不锈钢保温杯", "selling_points": ["12 小时保温", "一键开盖"]}},
            },
        )
        assert out.problem is None
        product = ctx.pm.load_project("demo")["products"]["保温杯"]
        assert product["description"] == "不锈钢保温杯"
        assert product["selling_points"] == ["12 小时保温", "一键开盖"]
        assert product["reference_images"] == []
        assert product["product_sheet"] == ""
        assert product["brand"] == ""

    async def test_product_upsert_strips_reference_images(self, ctx: ToolHarness) -> None:
        """reference_images is a list of user-uploaded originals and fidelity acceptance anchors, outside Agent allowlists;
        upsert silently discards it without overwriting existing values; use dedicated upload APIs."""
        await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "products", "entries": {"保温杯": {"description": "不锈钢保温杯"}}},
        )
        ctx.pm.add_product_reference_image("demo", "保温杯", "products/refs/保温杯_1.jpg")

        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "products",
                "entries": {
                    "保温杯": {
                        "description": "改后描述",
                        "selling_points": ["双层真空"],
                        "reference_images": [],
                    }
                },
            },
        )
        assert out.problem is None
        product = ctx.pm.load_project("demo")["products"]["保温杯"]
        assert product["description"] == "改后描述"
        assert product["selling_points"] == ["双层真空"]
        assert product["reference_images"] == ["products/refs/保温杯_1.jpg"]

    async def test_product_upsert_invalid_selling_points_blocked(self, ctx: ToolHarness) -> None:
        """selling_points requires string lists; invalid types fail structure checks without persistence."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "products", "entries": {"保温杯": {"description": "杯", "selling_points": "不是列表"}}},
        )
        assert out.problem is not None
        assert "保温杯" not in ctx.pm.load_project("demo").get("products", {})

    async def test_response_distinguishes_added_and_merged(self, ctx: ToolHarness) -> None:
        """Return text distinguishes new counts from merged-field-edit counts, letting Agents verify intended strategies."""
        out1 = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        text1 = _said(out1)
        assert "新增" in text1
        assert "李白" in text1
        assert "合并" not in text1

        out2 = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "改后描述"}}},
        )
        text2 = _said(out2)
        assert "合并改字段" in text2
        assert "李白" in text2
        assert "新增" not in text2

    async def test_response_lists_dropped_non_allowed_fields(self, ctx: ToolHarness) -> None:
        """Return text explicitly lists discarded fields such as reference_image / sheet_field,
        explaining ineffectual edits so LLMs stop retrying."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "白衣剑客",
                        "reference_image": "x.jpg",  # System-managed; ignore.
                        "character_sheet": "y.jpg",  # Pipeline writeback; ignore.
                    }
                },
            },
        )
        text = _said(out)
        assert "reference_image" in text
        assert "character_sheet" in text
        assert "Agent 可编辑范围" in text or "已忽略" in text

    async def test_existing_entry_with_only_dropped_fields_reports_noop(self, ctx: ToolHarness) -> None:
        """Existing entries with all submitted fields removed by allowlists/legacy stripping produce
        cleaned[name]={} and bucket.update({}), a no-op. Explicitly report skipped without writable fields,
        not one merged edit implying actual changes."""
        # Create clean entries first.
        await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        # Submit only discarded fields: system reference_image / historical type.
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {"李白": {"reference_image": "x.jpg", "type": "主角"}},
            },
        )
        assert out.problem is None
        text = _said(out)
        # Report noop / no writable fields, not merged.
        assert "合并改字段" not in text
        assert "无可写字段已跳过" in text or "无变更" in text
        # Description unchanged.
        assert ctx.pm.load_project("demo")["characters"]["李白"]["description"] == "白衣剑客"

    async def test_response_lists_dropped_legacy_fields(self, ctx: ToolHarness) -> None:
        """Return text lists stripped historical type / importance fields so Agents avoid sending them."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "白衣剑客",
                        "type": "主角",  # Historical field; strip.
                        "importance": "high",  # Historical field; strip.
                    }
                },
            },
        )
        text = _said(out)
        assert "type" in text
        assert "importance" in text
        assert "历史字段" in text or "已废弃" in text

    async def test_new_character_registers_derivatives(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "白衣剑客",
                        "derivatives": {"战袍": {"description": "换上玄色战袍，其余外观保持不变"}},
                    }
                },
            },
        )
        assert out.problem is None
        entry = ctx.pm.load_project("demo")["characters"]["李白"]
        assert entry["derivatives"] == {
            "战袍": {"description": "换上玄色战袍，其余外观保持不变", "character_sheet": ""}
        }

    async def test_derivative_upsert_merges_by_name_and_keeps_the_generated_sheet(self, ctx: ToolHarness) -> None:
        """Merge derivatives by name: edit same-named descriptions, retain pipeline images, and preserve unmentioned derivatives."""
        await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {"李白": {"description": "白衣剑客", "derivatives": {"战袍": {"description": "玄色战袍"}}}},
            },
        )
        ctx.pm.update_project(
            "demo",
            lambda p: p["characters"]["李白"]["derivatives"]["战袍"].update(
                {"character_sheet": "characters/derivatives/李白/战袍.png"}
            ),
        )

        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "derivatives": {"战袍": {"description": "改后变化"}, "夜行衣": {"description": "玄黑夜行衣"}}
                    }
                },
            },
        )
        assert out.problem is None
        derivatives = ctx.pm.load_project("demo")["characters"]["李白"]["derivatives"]
        assert derivatives["战袍"] == {
            "description": "改后变化",
            "character_sheet": "characters/derivatives/李白/战袍.png",
        }
        assert derivatives["夜行衣"] == {"description": "玄黑夜行衣", "character_sheet": ""}

    async def test_derivative_upsert_leaves_unmentioned_derivatives_untouched(self, ctx: ToolHarness) -> None:
        await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "白衣剑客",
                        "derivatives": {"战袍": {"description": "玄色战袍"}, "夜行衣": {"description": "玄黑夜行衣"}},
                    }
                },
            },
        )

        await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"derivatives": {"战袍": {"description": "改后变化"}}}}},
        )
        derivatives = ctx.pm.load_project("demo")["characters"]["李白"]["derivatives"]
        assert sorted(derivatives) == ["夜行衣", "战袍"]
        assert derivatives["夜行衣"]["description"] == "玄黑夜行衣"

    async def test_empty_derivative_table_reports_noop(self, ctx: ToolHarness) -> None:
        await run_declared_tool(
            PATCH_PROJECT, ctx, {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}}
        )

        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"derivatives": {}}}},
        )
        assert out.problem is None
        text = _said(out)
        assert "合并改字段" not in text
        assert "无可写字段已跳过" in text or "无变更" in text

    @pytest.mark.parametrize(
        "derivatives",
        [
            {"战/袍": {"description": "变化"}},
            {"战袍": {"description": 1}},
            {"战袍": {"description": "变化", "character_sheet": "characters/x.png"}},
            {"战袍": "变化"},
            "不是表",
        ],
        ids=["illegal-name", "non-string-description", "system-field", "non-object-entry", "non-object-table"],
    )
    async def test_malformed_derivatives_rejected_and_not_written(self, ctx: ToolHarness, derivatives: object) -> None:
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "derivatives": derivatives}}},
        )
        assert out.problem is not None
        assert "李白" not in ctx.pm.load_project("demo").get("characters", {})

    async def test_derivatives_dropped_for_a_type_without_the_capability(self, ctx: ToolHarness) -> None:
        """Scenes lack derivatives; discard/report that field like other outside-spec fields."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {
                "table": "scenes",
                "entries": {"竹林": {"description": "雨后竹林", "derivatives": {"雪夜": {"description": "覆雪"}}}},
            },
        )
        assert out.problem is None
        scene = ctx.pm.load_project("demo")["scenes"]["竹林"]
        assert "derivatives" not in scene
        assert "derivatives" in _said(out)


class TestPatchProjectSettings:
    """patch_project settings: initial episode_target_units write/clear/validation support."""

    async def test_set_episode_target_units(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_units": 1000}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["episode_target_units"] == 1000
        assert "已更新" in _said(out)

    async def test_clear_episode_target_units(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_units": 1000}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_units": None}})
        assert out.problem is None
        assert "episode_target_units" not in ctx.pm.load_project("demo")
        assert "已清除" in _said(out)

    async def test_noop_when_same_value(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_units": 800}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_units": 800}})
        assert out.problem is None
        assert "无变更" in _said(out)

    async def test_non_whitelist_field_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"arbitrary_field": 1}})
        assert out.problem is not None
        assert "arbitrary_field" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize(("field", "value"), [("generation_mode", "reference_video"), ("grid_storyboard", True)])
    async def test_route_fields_not_patchable_via_settings(self, ctx: ToolHarness, field: str, value: Any) -> None:
        """generation_mode is immutable after creation and grid_storyboard changes only in settings UI; neither enters settings allowlists."""
        before = ctx.pm.load_project("demo").get(field)
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {field: value}})
        assert out.problem is not None
        assert ctx.pm.load_project("demo").get(field) == before

    @pytest.mark.parametrize("lang", ["zh", "en", "vi"])
    async def test_set_source_language_allowed_values(self, ctx: ToolHarness, lang: str) -> None:
        """source_language enum validation supports user-confirmed recovery when overview fails/is skipped."""
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"source_language": lang}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["source_language"] == lang

    async def test_clear_source_language(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"source_language": "en"}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"source_language": None}})
        assert out.problem is None
        assert "source_language" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("bad", ["english", "ja", "ZH", "", 1, True, ["en"]])
    async def test_invalid_source_language_rejected(self, ctx: ToolHarness, bad: Any) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"source_language": bad}})
        assert out.problem is not None
        assert "source_language" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("binding", ["prompt", "reference_audio"])
    async def test_set_character_voice_binding_allowed_values(self, ctx: ToolHarness, binding: str) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"character_voice_binding": binding}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["character_voice_binding"] == binding

    async def test_clear_character_voice_binding(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"character_voice_binding": "reference_audio"}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"character_voice_binding": None}})
        assert out.problem is None
        assert "character_voice_binding" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("bad", ["native", "soft", "PROMPT", "", 1, True, ["prompt"]])
    async def test_invalid_character_voice_binding_rejected(self, ctx: ToolHarness, bad: Any) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"character_voice_binding": bad}})
        assert out.problem is not None
        assert "character_voice_binding" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("bad_value", [0, -5, 1.5, True, "10.5", "10.0", "abc", ""])
    async def test_invalid_value_rejected(self, ctx: ToolHarness, bad_value: Any) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_units": bad_value}})
        assert out.problem is not None
        assert "episode_target_units" not in ctx.pm.load_project("demo")

    async def test_positive_int_setting_accepts_digit_string(self, ctx: ToolHarness) -> None:
        """MCP objects lack per-field types, so models often quote numbers; tolerate numeric strings as persisted ints."""
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_units": "10"}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["episode_target_units"] == 10

    @pytest.mark.parametrize("key", ["planning_window_chars", "planning_max_episodes"])
    async def test_planning_window_and_batch_size_are_not_settings(self, ctx: ToolHarness, key: str) -> None:
        """Planning-window characters and batch episode counts are not creator settings and are rejected."""
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {key: 12}})
        assert out.problem is not None
        assert key not in ctx.pm.load_project("demo")

    async def test_table_and_settings_together_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"x": {"description": "y"}}, "settings": {"episode_target_units": 1}},
        )
        assert out.problem is not None

    async def test_neither_table_nor_settings_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {})
        assert out.problem is not None

    async def test_empty_settings_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {}})
        assert out.problem is not None

    async def test_legacy_upsert_path_still_works(self, ctx: ToolHarness) -> None:
        """Legacy schema: table/entries alone selects upsert, preserving 8 existing call sites."""
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        assert out.problem is None
        assert ctx.pm.load_project("demo")["characters"]["李白"]["description"] == "白衣剑客"


class TestPatchProjectNarrationSettings:
    """narration_voice / narration_speed write/clear/validation through settings, forming project TTS snapshots."""

    async def test_set_narration_voice(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_voice": "Ethan"}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["narration_voice"] == "Ethan"
        assert "已更新" in _said(out)

    async def test_modify_narration_voice(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_voice": "Ethan"}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_voice": "Cherry"}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["narration_voice"] == "Cherry"

    async def test_clear_narration_voice(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_voice": "Ethan"}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_voice": None}})
        assert out.problem is None
        assert "narration_voice" not in ctx.pm.load_project("demo")
        assert "已清除" in _said(out)

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n", 1, 1.5, True, ["Ethan"], {"id": "Ethan"}])
    async def test_invalid_narration_voice_rejected(self, ctx: ToolHarness, bad: Any) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_voice": bad}})
        assert out.problem is not None
        assert "narration_voice" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("speed", [1.2, 0.5, 2, 1])
    async def test_set_narration_speed(self, ctx: ToolHarness, speed: Any) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_speed": speed}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["narration_speed"] == speed

    async def test_narration_speed_accepts_numeric_string(self, ctx: ToolHarness) -> None:
        """MCP objects lack per-field types, so models often quote numbers; tolerate finite numeric strings as persisted floats."""
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_speed": "1.5"}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["narration_speed"] == 1.5

    async def test_clear_narration_speed(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_speed": 1.2}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_speed": None}})
        assert out.problem is None
        assert "narration_speed" not in ctx.pm.load_project("demo")
        assert "已清除" in _said(out)

    @pytest.mark.parametrize("bad", [0, -1.5, float("inf"), float("nan"), True, False, "fast", "", [1.2], 10**400])
    async def test_invalid_narration_speed_rejected(self, ctx: ToolHarness, bad: Any) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_speed": bad}})
        assert out.problem is not None
        # Oversized integers beyond float range get clear validation copy, not low-level overflow.
        assert "narration_speed 必须是正的有限数值" in _said(out)
        assert "narration_speed" not in ctx.pm.load_project("demo")

    async def test_tts_project_keeps_complete_snapshot(self, ctx: ToolHarness) -> None:
        ctx.pm.update_project(
            "demo",
            lambda project: project.update(
                narration_delivery="use_tts", audio_backend="dashscope/qwen3-tts-flash", narration_voice="Cherry"
            ),
        )
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"narration_voice": None}})
        assert out.problem is not None
        assert ctx.pm.load_project("demo")["narration_voice"] == "Cherry"

    async def test_one_invalid_field_rejects_whole_batch(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"settings": {"narration_voice": "Ethan", "narration_speed": -1}},
        )
        assert out.problem is not None
        project = ctx.pm.load_project("demo")
        assert "narration_voice" not in project
        assert "narration_speed" not in project

    async def test_tts_snapshot_reads_values_written_by_tool(self, ctx: ToolHarness) -> None:
        """Tool writes and TTS snapshots read the same top-level fields."""
        from lib.speech.narration_config import project_tts_settings

        ctx.pm.update_project("demo", lambda project: project.update(audio_backend="dashscope/qwen3-tts-flash"))
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"settings": {"narration_voice": "Ethan", "narration_speed": 1.2}},
        )
        assert out.problem is None
        settings = project_tts_settings(ctx.pm.load_project("demo"))
        assert settings is not None
        assert (settings.voice, settings.speed) == ("Ethan", 1.2)


class TestPatchProjectOverview:
    """patch_project overview merge-edits four allowlisted fields, creates absent overviews, and enforces three-way exclusivity."""

    async def test_set_overview_fields(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"overview": {"synopsis": "一句话", "genre": "悬疑", "theme": "复仇", "world_setting": "近未来"}},
        )
        assert out.problem is None
        ov = ctx.pm.load_project("demo")["overview"]
        assert ov["synopsis"] == "一句话"
        assert ov["genre"] == "悬疑"
        assert ov["theme"] == "复仇"
        assert ov["world_setting"] == "近未来"
        assert "已更新" in _said(out)

    async def test_merge_preserves_untouched_fields(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"overview": {"synopsis": "原始梗概", "genre": "原题材"}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"overview": {"genre": "悬疑"}})
        assert out.problem is None
        ov = ctx.pm.load_project("demo")["overview"]
        assert ov["genre"] == "悬疑"
        assert ov["synopsis"] == "原始梗概"  # Preserve omitted fields.

    async def test_creates_overview_when_absent(self, ctx: ToolHarness) -> None:
        assert "overview" not in ctx.pm.load_project("demo")
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"overview": {"synopsis": "新建概述"}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["overview"]["synopsis"] == "新建概述"

    async def test_non_whitelist_key_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"overview": {"title": "x"}})
        assert out.problem is not None
        assert "title" not in ctx.pm.load_project("demo").get("overview", {})

    async def test_non_string_value_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"overview": {"synopsis": 1}})
        assert out.problem is not None
        assert "overview" not in ctx.pm.load_project("demo")

    async def test_empty_overview_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"overview": {}})
        assert out.problem is not None

    async def test_noop_when_same_value(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"overview": {"synopsis": "同值"}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"overview": {"synopsis": "同值"}})
        assert out.problem is None
        assert "无变更" in _said(out)

    async def test_overview_with_settings_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"overview": {"synopsis": "x"}, "settings": {"episode_target_units": 1}},
        )
        assert out.problem is not None

    async def test_overview_with_table_rejected(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            PATCH_PROJECT,
            ctx,
            {"overview": {"synopsis": "x"}, "table": "characters", "entries": {"a": {"description": "b"}}},
        )
        assert out.problem is not None


class TestPatchProjectEpisodeTargetDurationSetting:
    """Target episode duration writes/clears through settings; bounds/ad exclusion match REST paths."""

    @pytest.fixture
    def ad_ctx(self, tmp_path: Path) -> ToolHarness:
        pm = ProjectManager(str(tmp_path))
        pm.create_project("ad-demo", content_mode="ad")
        pm.create_project_metadata("ad-demo", "Ad Demo", "Realistic", "ad", target_duration=60)
        return ToolHarness(project_name="ad-demo", data_root=tmp_path, pm=pm)

    async def test_set_episode_target_duration(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_duration": 120}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["episode_target_duration"] == 120

    async def test_clear_episode_target_duration(self, ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_duration": 120}})
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_duration": None}})
        assert out.problem is None
        assert "episode_target_duration" not in ctx.pm.load_project("demo")

    async def test_accepts_digit_string(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_duration": "120"}})
        assert out.problem is None
        assert ctx.pm.load_project("demo")["episode_target_duration"] == 120

    @pytest.mark.parametrize("bad", [5, 900, 0, -30, 120.0, True, "abc", ""])
    async def test_out_of_range_or_dirty_value_rejected(self, ctx: ToolHarness, bad: Any) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"episode_target_duration": bad}})
        assert out.problem is not None
        assert "episode_target_duration" not in ctx.pm.load_project("demo")

    async def test_rejected_on_ad_project(self, ad_ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ad_ctx, {"settings": {"episode_target_duration": 120}})
        assert out.problem is not None
        assert "episode_target_duration" not in ad_ctx.pm.load_project("ad-demo")


class TestPatchProjectBriefSetting:
    """brief is ad creative-request short text, writable/clearable through settings; reject non-ad projects."""

    @pytest.fixture
    def ad_ctx(self, tmp_path: Path) -> ToolHarness:
        pm = ProjectManager(str(tmp_path))
        pm.create_project("ad-demo", content_mode="ad")
        pm.create_project_metadata("ad-demo", "Ad Demo", "Realistic", "ad", target_duration=60)
        return ToolHarness(project_name="ad-demo", data_root=tmp_path, pm=pm)

    async def test_set_brief_on_ad_project(self, ad_ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ad_ctx, {"settings": {"brief": "突出 3 秒速干卖点"}})
        assert out.problem is None
        assert ad_ctx.pm.load_project("ad-demo")["brief"] == "突出 3 秒速干卖点"

    async def test_clear_brief_on_ad_project(self, ad_ctx: ToolHarness) -> None:
        await run_declared_tool(PATCH_PROJECT, ad_ctx, {"settings": {"brief": "x"}})
        out = await run_declared_tool(PATCH_PROJECT, ad_ctx, {"settings": {"brief": None}})
        assert out.problem is None
        assert "brief" not in ad_ctx.pm.load_project("ad-demo")

    async def test_brief_rejected_on_non_ad_project(self, ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ctx, {"settings": {"brief": "x"}})
        assert out.problem is not None
        assert "brief" not in ctx.pm.load_project("demo")

    async def test_non_string_brief_rejected(self, ad_ctx: ToolHarness) -> None:
        out = await run_declared_tool(PATCH_PROJECT, ad_ctx, {"settings": {"brief": 42}})
        assert out.problem is not None
        # Invalid writes preserve creation-time valid empty brief="".
        assert ad_ctx.pm.load_project("ad-demo")["brief"] == ""


class TestRenameAssetTool:
    """rename_asset is separate from patch_project's project.json-field-writing charter;
    use real ProjectManager.rename_asset cascading with recovery-oriented errors."""

    @pytest.fixture
    def rename_ctx(self, ctx: ToolHarness) -> ToolHarness:
        ctx.pm.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})
        return ctx

    async def test_rename_cascades_script_references(self, rename_ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            RENAME_ASSET, rename_ctx, {"table": "characters", "old_name": "角色A", "new_name": "主角甲"}
        )
        assert out.problem is None
        assert "主角甲" in _said(out)
        project = rename_ctx.pm.load_project("demo")
        assert "主角甲" in project["characters"]
        assert "角色A" not in project["characters"]
        assert _load(rename_ctx)["segments"][0]["characters_in_segment"] == ["主角甲"]

    async def test_asset_without_description_can_be_renamed(self, ctx: ToolHarness) -> None:
        ctx.pm.upsert_assets("demo", "scenes", {"无描述场景": {"description": ""}})

        out = await run_declared_tool(
            RENAME_ASSET, ctx, {"table": "scenes", "old_name": "无描述场景", "new_name": "村口"}
        )

        assert out.problem is None
        assert ctx.pm.load_project("demo")["scenes"] == {
            "村口": {"description": "", "scene_sheet": "", "aliases": ["无描述场景"]}
        }

    async def test_missing_old_name_error_hints_idempotency(self, rename_ctx: ToolHarness) -> None:
        await run_declared_tool(
            RENAME_ASSET, rename_ctx, {"table": "characters", "old_name": "角色A", "new_name": "主角甲"}
        )
        out = await run_declared_tool(
            RENAME_ASSET, rename_ctx, {"table": "characters", "old_name": "角色A", "new_name": "主角甲"}
        )
        assert out.problem is not None
        assert "可能上次重命名已成功" in _said(out)

    async def test_conflict_rejected(self, rename_ctx: ToolHarness) -> None:
        rename_ctx.pm.upsert_assets("demo", "characters", {"主角甲": {"description": "另一个"}})
        out = await run_declared_tool(
            RENAME_ASSET, rename_ctx, {"table": "characters", "old_name": "角色A", "new_name": "主角甲"}
        )
        assert out.problem is not None
        assert "冲突" in _said(out)
        assert "角色A" in rename_ctx.pm.load_project("demo")["characters"]


class TestMergeAssetTool:
    """merge_asset uses real ProjectManager.merge_asset cascading; dry_run reports episode impacts without writing, execution retargets references to retained assets."""

    @pytest.fixture
    def merge_ctx(self, ctx: ToolHarness) -> ToolHarness:
        ctx.pm.upsert_assets(
            "demo",
            "characters",
            {"角色A": {"description": "主角"}, "主角甲": {"description": "同一个人"}},
        )
        return ctx

    async def test_dry_run_reports_per_episode_without_writing(self, merge_ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            MERGE_ASSET, merge_ctx, {"table": "characters", "source": "角色A", "target": "主角甲", "dry_run": True}
        )

        assert out.problem is None
        assert "预览" in _said(out)
        assert "《标题》（第 1 个，id=1）：正式脚本" in _said(out)
        assert "角色A" in merge_ctx.pm.load_project("demo")["characters"]
        assert _load(merge_ctx)["segments"][0]["characters_in_segment"] == ["角色A"]

    async def test_merge_points_references_at_the_kept_asset(self, merge_ctx: ToolHarness) -> None:
        out = await run_declared_tool(
            MERGE_ASSET, merge_ctx, {"table": "characters", "source": "角色A", "target": "主角甲"}
        )

        assert out.problem is None
        characters = merge_ctx.pm.load_project("demo")["characters"]
        assert "角色A" not in characters
        assert characters["主角甲"]["aliases"] == ["角色A"]
        assert _load(merge_ctx)["segments"][0]["characters_in_segment"] == ["主角甲"]

    async def test_products_are_not_offered(self, merge_ctx: ToolHarness) -> None:
        out = await run_declared_tool(MERGE_ASSET, merge_ctx, {"table": "products", "source": "甲", "target": "乙"})

        assert out.problem is not None
