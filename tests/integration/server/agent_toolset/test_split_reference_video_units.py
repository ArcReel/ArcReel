"""Tests for split_reference_video_units."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.generation.video_request_facts import VideoRequestFactsError, VideoRequestFactsFailure
from lib.script.reference_video.unit_capabilities import evaluate_reference_unit_capabilities
from server.services.tasks.video_caps import reference_request_facts_lookup
from tests.factories import make_video_request_facts, seed_endpoint_fixed_video_model
from tests.fakes import use_text_generator
from tests.integration.server.agent_tool_support import (
    _RV_NOVEL,
    ToolHarness,
    derived_reference_names,
    read_rv_quarantine,
    run_declared_tool,
    run_rv_split,
    rv_generator_returning,
    rv_script_plan_path,
    rv_source,
    rv_unit,
    said,
)


def rv_character_sheet(fake_ctx: ToolHarness, name: str, *, claimed: bool) -> None:
    """Write registered character images; ``claimed`` activates manifest claims before units can select r2v."""
    from lib.artifacts.artifact_activation import activate_artifact_target_state

    sheet = fake_ctx.project_path / "characters" / f"{name}.png"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"][name]["character_sheet"] = f"characters/{name}.png"
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(fake_ctx.pm.project_payload, ensure_ascii=False), encoding="utf-8"
    )
    if claimed:
        activate_artifact_target_state(fake_ctx.project_path, bump_schema=False)


def _reference_facts(generation_type: str, **overrides: Any):
    """Reference-route bucket video-request facts with optional field overrides."""
    return make_video_request_facts(route="reference_video", generation_type=generation_type, **overrides)


def _veo_720p_facts(set_video_request_facts) -> None:
    """Veo 720p reference images require 8 seconds: r2v narrows to [8], i2v keeps [4, 6, 8], yielding different per-unit tiers."""
    set_video_request_facts(
        {
            "r2v": _reference_facts("r2v", supported_durations=(4, 6, 8), allowed_durations=(8,)),
            "i2v": _reference_facts("i2v", supported_durations=(4, 6, 8), allowed_durations=(4, 6, 8)),
        }
    )


# ---------------------------------------------------------------------------
# split_reference_video_units
# ---------------------------------------------------------------------------


async def test_fetch_reference_split_caps_returns_declared_slots(set_video_request_facts) -> None:
    """Unit durations are actual provider values; use bucket fact tiers unchanged, without intersecting static bounds."""
    from server import text_generation as mod

    set_video_request_facts(
        _reference_facts(
            "r2v", supported_durations=(1, 8, 16, 18), allowed_durations=(1, 8, 16, 18), max_reference_images=None
        )
    )

    caps = await mod.fetch_reference_split_caps({"default_duration": 16})

    assert caps.durations == [1, 8, 16, 18]
    assert caps.reference_durations == [1, 8, 16, 18]
    assert caps.text_durations == [1, 8, 16, 18]
    assert caps.max_duration == 18
    assert caps.default_duration == 16  # Valid tier members are accepted normally.
    assert caps.max_refs is None
    assert caps.text_problem is None


async def test_fetch_reference_split_caps_splits_tiers_by_bucket(set_video_request_facts) -> None:
    """Reference-image/duration constraints apply per unit: Veo 720p references allow only 8 seconds, while no references allow 4/6/8.

    Enum/prompt candidates use the union; universal reference narrowing would remove valid short nonreference tiers.
    """
    from server import text_generation as mod

    _veo_720p_facts(set_video_request_facts)

    caps = await mod.fetch_reference_split_caps({})
    assert caps.reference_durations == [8]
    assert caps.text_durations == [4, 6, 8]
    assert caps.durations == [4, 6, 8]
    assert caps.max_duration == 8
    assert caps.tiers_for(has_references=True) == [8]
    assert caps.tiers_for(has_references=False) == [4, 6, 8]


async def test_fetch_reference_split_caps_narrows_unit_duration_cap(set_video_request_facts) -> None:
    """Upper bounds follow narrowed tiers: Hailuo 1080p accepts only 6 seconds despite full set [6, 10].

    Without narrowing, script_plan could split 10-second units that prompt_authoring enums later reject.
    """
    from server import text_generation as mod

    set_video_request_facts(_reference_facts("r2v", supported_durations=(6, 10), allowed_durations=(6,)))

    caps = await mod.fetch_reference_split_caps({})
    assert caps.durations == [6]
    assert caps.max_duration == 6


async def test_fetch_reference_split_caps_drops_out_of_range_default(set_video_request_facts) -> None:
    """Saved default_duration outside the narrowed union becomes None, avoiding contradictory prompts."""
    from server import text_generation as mod

    set_video_request_facts(_reference_facts("r2v", supported_durations=(4, 6, 8), allowed_durations=(8,)))

    assert (await mod.fetch_reference_split_caps({"default_duration": 4})).default_duration is None
    assert (await mod.fetch_reference_split_caps({"default_duration": 8})).default_duration == 8


async def test_fetch_reference_split_caps_reports_unavailable_i2v_as_text_problem(set_video_request_facts) -> None:
    """Unresolved i2v gives empty no-image tiers and unchanged failures; image tiers remain normal, without substituting r2v for no-image units."""
    from server import text_generation as mod

    set_video_request_facts(
        {
            "r2v": _reference_facts("r2v", supported_durations=(6, 10), allowed_durations=(6, 10)),
            "i2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)),
        }
    )

    caps = await mod.fetch_reference_split_caps({})
    assert caps.reference_durations == [6, 10]
    assert caps.text_durations == []
    assert caps.durations == [6, 10]
    assert caps.text_problem is not None
    assert caps.text_problem.code == "reference_capability_unavailable"
    assert caps.text_problem.parameters() == {"capability": "i2v"}


async def test_fetch_reference_split_caps_raises_typed_code_when_r2v_facts_fail(set_video_request_facts) -> None:
    """r2v is the main bucket: failed resolution raises its problem code instead of falling back to any tier set."""
    from server import text_generation as mod

    set_video_request_facts(
        {
            "r2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "r2v"),)),
            "i2v": _reference_facts("i2v"),
        }
    )

    with pytest.raises(VideoRequestFactsError) as exc:
        await mod.fetch_reference_split_caps({})

    assert exc.value.code == "reference_capability_unavailable"
    assert exc.value.params == {"capability": "r2v"}


async def test_fetch_reference_split_caps_takes_voice_settings_from_r2v_facts(set_video_request_facts) -> None:
    """Voice settings and tiers share this r2v evaluation; silence intent follows facts without audible fallback."""
    from server import text_generation as mod

    set_video_request_facts(
        _reference_facts(
            "r2v",
            voice_consistency="native",
            requested_generate_audio=False,
            max_reference_audio_count=2,
            model_id="m",
            reference_audio_per_image=True,
        )
    )

    voice = (await mod.fetch_reference_split_caps({"video_generate_audio": False})).voice
    assert voice.voice_consistency == "native"
    assert voice.requested_generate_audio is False
    assert voice.max_reference_audio == 2
    assert voice.model_id == "m"
    assert voice.requires_reference_image is True
    assert voice.is_silent


async def test_reference_split_planning_reports_empty_intersection_as_incompatible(monkeypatch, db_factory) -> None:
    """Empty intersections of reference/resolution constraints raise r2v incompatible codes. Replace model data with contradictory declarations,
    retaining real narrowing rules."""
    from arcreel_catalog_core import DurationConstraints
    from lib.catalog.query import factory_snapshot
    from lib.config.resolver import ConfigResolver
    from server.text_generation import fetch_reference_split_caps
    from tests.catalog_support import install_catalog, with_entry_facts

    provider_id, model_id = "gemini-vertex", "veo-3.1-generate-001"
    constraints = DurationConstraints(by_resolution={"1080p": (6,)}, with_reference_images=(8,))
    install_catalog(
        monkeypatch, with_entry_facts(factory_snapshot(), provider_id, model_id, duration_constraints=constraints)
    )
    project = {
        "generation_mode": "reference_video",
        "video_provider_r2v": f"{provider_id}/{model_id}",
        "video_provider_i2v": f"{provider_id}/{model_id}",
        "model_settings": {f"{provider_id}/{model_id}": {"resolution": "1080p"}},
    }

    with pytest.raises(VideoRequestFactsError) as exc:
        await fetch_reference_split_caps(project, config_resolver=ConfigResolver(db_factory))

    assert exc.value.code == "reference_supported_durations_incompatible"
    assert exc.value.params["capability"] == "r2v"


async def test_reference_split_planning_reports_unavailable_i2v_instead_of_r2v_fallback(db_factory) -> None:
    """Projects with only r2v models still execute no-image units through i2v; planning reports i2v unavailable without borrowing r2v tiers."""
    from lib.config.resolver import ConfigResolver
    from server.text_generation import fetch_reference_split_caps

    project = {"generation_mode": "reference_video", "video_provider_r2v": "dashscope/happyhorse-1.1-r2v"}
    caps = await fetch_reference_split_caps(project, config_resolver=ConfigResolver(db_factory))

    assert caps.reference_durations == list(range(3, 16))
    assert caps.text_durations == []
    assert caps.text_problem is not None
    assert caps.text_problem.code == "reference_capability_unavailable"
    assert caps.text_problem.parameters() == {"capability": "i2v"}
    assert caps.text_problem.action == "configure_video_model"


async def test_reference_split_planning_accepts_five_seconds_from_i2v_facts(db_factory) -> None:
    """No-image tiers evaluate i2v models; planning durations must match the execution bucket's
    declarations, avoiding acceptance of r2v-only tiers or omission of i2v-only tiers."""
    from lib.config.resolver import ConfigResolver
    from server.text_generation import fetch_reference_split_caps

    project = {
        "generation_mode": "reference_video",
        "video_provider_r2v": "gemini-vertex/veo-3.1-generate-001",
        "video_provider_i2v": "ark/doubao-seedance-2-0-260128",
    }
    caps = await fetch_reference_split_caps(project, config_resolver=ConfigResolver(db_factory))

    assert caps.reference_durations == [8]
    assert 5 in caps.text_durations
    assert 5 in caps.durations
    assert caps.text_problem is None


@pytest.mark.parametrize("fixed_buckets", [("r2v",), ("i2v",), ("r2v", "i2v")])
async def test_reference_split_planning_borrows_planning_tiers_for_endpoint_fixed_buckets(
    db_factory, fixed_buckets: tuple[str, ...]
) -> None:
    """Endpoint-fixed buckets have no borrowable tiers; splitting still uses shared planning tiers, while other buckets narrow by their own facts."""
    from lib.config.resolver import ConfigResolver
    from lib.generation.video_request_facts import ENDPOINT_FIXED_PLANNING_DURATIONS
    from server.text_generation import fetch_reference_split_caps

    fixed_model = await seed_endpoint_fixed_video_model(db_factory, reference_images=True)
    veo = "gemini-vertex/veo-3.1-generate-001"
    project = {
        "generation_mode": "reference_video",
        "video_provider_r2v": fixed_model if "r2v" in fixed_buckets else veo,
        "video_provider_i2v": fixed_model if "i2v" in fixed_buckets else veo,
        "model_settings": {veo: {"resolution": "720p"}},
    }

    caps = await fetch_reference_split_caps(project, config_resolver=ConfigResolver(db_factory))

    assert caps.reference_durations == (ENDPOINT_FIXED_PLANNING_DURATIONS if "r2v" in fixed_buckets else [8])
    assert caps.text_durations == (ENDPOINT_FIXED_PLANNING_DURATIONS if "i2v" in fixed_buckets else [4, 6, 8])
    assert caps.durations == sorted(set(caps.reference_durations) | set(caps.text_durations))
    assert caps.max_duration == max(caps.durations)
    assert caps.text_problem is None


async def test_split_reference_video_units_dry_run(fake_ctx: ToolHarness, video_request_facts) -> None:
    rv_source(fake_ctx)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})
    assert out.problem is None, out
    prompt_text = said(out)
    assert "DRY RUN" in prompt_text
    # Inject episode IDs, asset candidates, capabilities, and reference-syntax rules into prompts.
    assert "episode 1" in prompt_text
    assert "张三" in prompt_text
    # Maximum duration is derived from the union of per-unit tier sets.
    assert "8 seconds" in prompt_text
    assert "Place dialogue tokens immediately after the action they correspond to" in prompt_text


async def test_split_reference_video_units_happy_derives_structure(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Happy path: LLMs write flat bodies persisted verbatim; only unit_id derives mechanically in tools."""
    from server import text_generation as mod

    rv_source(fake_ctx)
    captured: dict[str, Any] = {}
    text = "@[张三] 走向 @[村口]\n@[张三] 停下脚步"
    units = [rv_unit(text)]
    use_text_generator(monkeypatch, rv_generator_returning(units, captured))

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is None, out
    assert "drafts/episode_1/script_plan_reference_units.json" in said(out)
    assert str(fake_ctx.project_path) not in said(out)

    saved = json.loads(rv_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    unit = saved["units"][0]
    # Fixtures used E1S01; never reuse IDs, so units start at 2.
    assert unit["unit_id"] == "E1U2"
    assert unit["text"] == text
    # References never persist; reads derive them from body @[名称].
    assert "references" not in unit
    assert derived_reference_names(fake_ctx, unit["text"]) == ["张三", "村口"]
    assert unit["source_text"] == _RV_NOVEL
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


async def test_split_reference_video_units_issues_unit_ids_after_the_highest_used_number(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Servers assign unit_id in array order above episode high-water marks; LLMs write no IDs, preventing writable duplicates/wrong episodes/reuse."""
    rv_source(fake_ctx)
    units = [rv_unit("@[张三] 起身"), rv_unit("@[张三] 出门")]
    out = await run_rv_split(fake_ctx, monkeypatch, units)
    assert out.problem is None, out
    saved = json.loads(rv_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert [u["unit_id"] for u in saved["units"]] == ["E1U2", "E1U3"]
    assert fake_ctx.pm.project_payload["episodes"][0]["item_id_high_water"] == 3


async def test_split_reference_video_units_derives_dialogue_without_reference_image(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Dialogue speakers do not derive references; attaching offscreen-character images would encourage drawing them onscreen."""
    rv_source(fake_ctx)
    units = [rv_unit("门开了\n@[张三]：{我来了。}")]
    out = await run_rv_split(fake_ctx, monkeypatch, units)
    assert out.problem is None, out
    saved = json.loads(rv_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert derived_reference_names(fake_ctx, saved["units"][0]["text"]) == []


async def test_split_reference_video_units_rejects_unregistered_asset(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Unregistered body asset references fail loudly without persistence, preserving reference integrity."""
    rv_source(fake_ctx)
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[不存在的人] 出场")])
    assert out.problem is not None
    assert "未登记" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_unregistered_speaker(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Unregistered speakers block too, because speakers determine dialogue reference-audio binding."""
    rv_source(fake_ctx)
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("门开了\n@[无名氏]：{我来了。}")])
    assert out.problem is not None
    assert "说话人未登记" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_over_max_refs(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    """Per-unit `@` mention limits come from r2v reference-image limits."""
    rv_source(fake_ctx)
    set_video_request_facts(_reference_facts("r2v", max_reference_images=2))
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[张三] 与 @[李四] 在 @[村口]")])
    assert out.problem is not None
    assert "参考图数" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_duration_off_reference_tier(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    """Image-bearing units using durations legal only without images violate contracts and write no formal files.

    Schemas constrain the union, so these pass schema validation; without this guard, execution would fail to request them.
    """
    rv_source(fake_ctx)
    _veo_720p_facts(set_video_request_facts)
    rv_character_sheet(fake_ctx, "张三", claimed=True)
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[张三] 起身", duration=4)])
    assert out.problem is not None
    text = said(out)
    assert "生效档位" in text
    assert "[8]" in text
    # Off-tier content also becomes repairable drafts, like other Agent-fixable violations.
    assert not rv_script_plan_path(fake_ctx).exists()
    assert [v["code"] for v in read_rv_quarantine(fake_ctx)["violations"]] == ["duration_off_tier"]


async def test_split_reference_video_units_locates_the_unit_when_its_i2v_tiers_are_unknown(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    """Unresolved no-image i2v facts report the specific unit location, not episode-level aggregation."""
    rv_source(fake_ctx)
    set_video_request_facts(
        {
            "r2v": _reference_facts("r2v", supported_durations=(6, 10), allowed_durations=(6, 10)),
            "i2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)),
        }
    )
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("门开了", duration=6)])
    assert out.problem is not None
    [violation] = read_rv_quarantine(fake_ctx)["violations"]
    assert violation["code"] == "reference_capability_unavailable"
    assert violation["label"]


@pytest.mark.parametrize("sheet", ["absent", "unclaimed"])
async def test_split_reference_video_units_buckets_a_reference_without_usable_image_as_i2v(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts, sheet: str
) -> None:
    """`@` characters without usable images, ungenerated or unclaimed on disk, select i2v like confirmation
    panels/execution. Four seconds is valid in i2v, not outside reference-image [8] tiers.
    """
    rv_source(fake_ctx)
    _veo_720p_facts(set_video_request_facts)
    if sheet == "unclaimed":
        rv_character_sheet(fake_ctx, "张三", claimed=False)
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[张三] 起身", duration=4)])
    assert out.problem is None, out
    saved = json.loads(rv_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["duration_seconds"] == 4
    project = json.loads((fake_ctx.project_path / "project.json").read_text(encoding="utf-8"))
    (capability,) = await evaluate_reference_unit_capabilities(
        project, fake_ctx.project_path, saved["units"], request_facts=reference_request_facts_lookup(project)
    )
    assert capability.generation_type == "i2v"


async def test_split_reference_video_units_accepts_wide_tier_without_references(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    """Units without `@` references avoid reference-image/duration constraints and may choose shorter tiers."""
    rv_source(fake_ctx)
    _veo_720p_facts(set_video_request_facts)
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("门被风吹开", duration=4)])
    assert out.problem is None, out
    saved = json.loads(rv_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["duration_seconds"] == 4
    assert derived_reference_names(fake_ctx, saved["units"][0]["text"]) == []


async def test_split_reference_video_units_rejects_out_of_enum_duration(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Local validation reuses dynamic schemas to reject unsupported unit durations without writing."""
    rv_source(fake_ctx)
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[张三] 起身", duration=5)])
    assert out.problem is not None
    assert "script_plan 拆分内容结构校验失败" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_empty_units(fake_ctx: ToolHarness, monkeypatch) -> None:
    rv_source(fake_ctx)
    out = await run_rv_split(fake_ctx, monkeypatch, [])
    assert out.problem is not None
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_non_verbatim_source_text(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """source_text outside verbatim source substrings fails loudly, catching model paraphrase/fabrication."""
    rv_source(fake_ctx)
    units = [rv_unit("@[张三] 起身", source_text="张三在城里等人")]
    out = await run_rv_split(fake_ctx, monkeypatch, units)
    assert out.problem is not None
    assert "不是小说原文的逐字片段" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_accepts_source_text_substring(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Anchors need only be source substrings; visual units need not cover every source character."""
    rv_source(fake_ctx)
    units = [rv_unit("@[张三] 起身", source_text="张三在村口")]
    out = await run_rv_split(fake_ctx, monkeypatch, units)
    assert out.problem is None, out


async def test_split_reference_video_units_rejects_dialogue_overload(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Estimated dialogue beyond unit duration and tolerance blocks."""
    rv_source(fake_ctx)
    long_line = "这是一段非常长的台词" * 6  # 60 characters at zh 5/second ≈ 12 seconds.
    units = [rv_unit(f"@[张三] 起身\n@[张三]：{{{long_line}}}", duration=4)]
    out = await run_rv_split(fake_ctx, monkeypatch, units)
    assert out.problem is not None
    assert "超过该 unit" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_braces_in_description(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Misused reserved braces in visuals block; braces unrecognized as speech markers fail loudly."""
    rv_source(fake_ctx)
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[张三] 推门，音量 {}，转身离开")])
    assert out.problem is not None
    assert "花括号" in said(out)
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_no_source(fake_ctx: ToolHarness) -> None:
    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None


async def test_split_reference_video_units_injects_instructions(fake_ctx: ToolHarness, video_request_facts) -> None:
    rv_source(fake_ctx)

    out = await run_declared_tool(
        "generate_script_plan",
        fake_ctx,
        {"episode_id": 1, "dry_run": True, "instructions": "单 unit 出场人物尽量不超过两人"},
    )
    assert out.problem is None, out
    prompt_text = said(out)
    assert "# Additional instructions" in prompt_text
    assert "单 unit 出场人物尽量不超过两人" in prompt_text


async def test_split_reference_video_units_surfaces_tolerated_voice_warnings(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    """Three voice-degradation warnings do not block persistence but accompany outputs, exposing compromises before final generation."""
    rv_source(fake_ctx)
    set_video_request_facts(
        _reference_facts("r2v", voice_consistency="native", max_reference_audio_count=2, model_id="m")
    )
    out = await run_rv_split(fake_ctx, monkeypatch, [rv_unit("@[张三] 起身\n@[张三]：{我来了。}")])

    assert out.problem is None, out
    assert rv_script_plan_path(fake_ctx).exists()
    text = said(out)
    assert "降级提示" in text
    assert "未设置参考音频" in text


async def test_split_reference_video_units_names_units_without_scene_reference(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Name units lacking scenes individually; model-chosen locations may mismatch adjacent alternating indoor/outdoor units."""
    rv_source(fake_ctx)
    fake_ctx.pm.project_payload["scenes"] = {"酒馆": {"description": "木质吧台"}}
    out = await run_rv_split(
        fake_ctx,
        monkeypatch,
        [rv_unit("@[酒馆] 内景，@[张三] 推门。"), rv_unit("@[张三] 起身。")],
    )

    assert out.problem is None, out
    text = said(out)
    # Receipts locate server-issued IDs; fixtures already used 1.
    assert "unit E1U3：" in text
    assert "unit E1U2：" not in text
    assert "未引用场景" in text


async def test_split_reference_video_units_counts_this_episodes_new_scenes_as_scene_references(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Units referencing only newly declared scenes count as scene-referenced, matching confirmation overlays on asset tables."""
    rv_source(fake_ctx)
    fake_ctx.pm.project_payload["scenes"] = {"酒馆": {"description": "木质吧台"}}
    new_assets = [
        {"type": "scene", "name": "码头", "decision": "register", "reason": "原文第一次出现", "description": "夜雾"}
    ]
    out = await run_rv_split(
        fake_ctx,
        monkeypatch,
        [rv_unit("@[码头] 夜雾，@[张三] 上岸。"), rv_unit("@[张三] 起身。")],
        new_assets=new_assets,
    )

    assert out.problem is None, out
    text = said(out)
    # Receipts locate server-issued IDs; fixtures already used 1.
    assert "unit E1U3：" in text
    assert "unit E1U2：" not in text


async def test_split_reference_video_units_reports_soft_violations_alongside_the_violation_report(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Invalid-output reports also include soft violations so Agents see degradation during draft repair, before promotion."""
    rv_source(fake_ctx)
    fake_ctx.pm.project_payload["scenes"] = {"酒馆": {"description": "木质吧台"}}
    out = await run_rv_split(
        fake_ctx,
        monkeypatch,
        [rv_unit("@[酒馆] 内景，@[张三] 推门。"), rv_unit("@[不存在的人] 出场")],
    )

    assert out.problem is not None
    text = said(out)
    assert "未登记" in text
    assert "降级提示" in text
    assert "未引用场景" in text
    assert "unit E1U02：" in text
    assert not rv_script_plan_path(fake_ctx).exists()


async def test_split_reference_video_units_keeps_voice_warnings_on_per_image_backend(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    """Per-image-audio backends still show warnings; splitting has no references yet, so that flag must not affect detection.

    Enabling ``reference_audio_per_image`` without reference sets marks every speaker as lacking an image attachment;
    that non-tolerated warning gets discarded, hiding even clip-count-limit notices.
    """
    rv_source(fake_ctx)
    fake_ctx.pm.project_payload["characters"] = {
        "张三": {"description": "主角", "reference_audio": "characters/refs_audio/张三.wav"},
        "李四": {"description": "", "reference_audio": "characters/refs_audio/李四.wav"},
    }
    set_video_request_facts(
        _reference_facts(
            "r2v",
            voice_consistency="native",
            max_reference_audio_count=1,
            model_id="m",
            reference_audio_per_image=True,
        )
    )
    out = await run_rv_split(
        fake_ctx, monkeypatch, [rv_unit("@[张三] 起身\n@[张三]：{我来了。}\n@[李四]：{你终于来了。}")]
    )

    assert out.problem is None, out
    assert "参考音频最多 1 段" in said(out)
