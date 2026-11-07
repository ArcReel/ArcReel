"""ScriptGenerator reference_video branch tests.

Prompt authoring reads formal script video_units only: rewrite pending bodies by default, scope with ``entry_ids``, explicitly rewrite with ``rewrite``. Read script plans only during content-confirmation ``materialize_script_plan``."""

import asyncio
import json as _json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.generation.video_request_facts import VideoRequestFactsError, VideoRequestFactsFailure
from lib.project import project_manager as project_manager_module
from lib.project.project_manager import ProjectManager, ScriptWriteConflict
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script import script_review
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    quarantine_path,
    write_quarantine,
)
from lib.script.reference_video.draft_validation import DraftViolation
from lib.script.reference_video.request_projection import configured_reference_request_facts
from lib.script.reference_video.text_parser import extract_mentions
from lib.script.reference_video.unit_capabilities import evaluate_reference_unit_capabilities
from lib.script.script_generator import (
    PlanningVideoFacts,
    PromptAuthoringUnitRejected,
    ScriptGenerator,
    UnitDurationOffTier,
)
from server.services.project.episode_drafts import EpisodeDraftService
from tests.factories import make_video_request_facts
from tests.fakes import use_text_generator

SCRIPT_PLAN_UNIT = {"unit_id": "E1U01", "text": "@[主角] 推开 @[酒馆] 的门", "duration_seconds": 4}
SCRIPT_PLAN_UNITS_JSON = _json.dumps({"units": [SCRIPT_PLAN_UNIT]}, ensure_ascii=False)


def _prompt_authoring_response(*texts: str, title: str = "t") -> str:
    """prompt_authoring LLM output is flat ``{title, units: [{text}]}``, without unit_id or duration."""
    return _json.dumps({"title": title, "units": [{"text": t} for t in texts]}, ensure_ascii=False)


def _fake_prompt_authoring_generator(*texts: str) -> MagicMock:
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock(return_value=MagicMock(text=_prompt_authoring_response(*texts)))
    return generator


def _idle_generator() -> MagicMock:
    """Text-generation substitute exposes any call, proving guards run before paid invocation."""
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock()
    return generator


#: Valid authoring paired with ``SCRIPT_PLAN_UNIT``, without editable dialogue.
PROMPT_AUTHORING_UNIT_TEXT = "镜头1：中景，平视。@[主角] 推开 @[酒馆] 的门，侧身跨过门槛。"


def _activate_project_artifacts(project_dir: Path, episode: int = 1) -> None:
    """Complete episode provenance inputs, then fully activate project artifacts.

    Manifests alone authorize generated reads: disk presence is not registration; unregistered script_plan cannot materialize. ``episode`` selects only which ``source/episode_{episode}.txt`` to fill; registration covers the whole project."""
    source = project_dir / "source" / f"episode_{episode}.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("原文", encoding="utf-8")
    activate_artifact_target_state(project_dir, bump_schema=False)


def _claim_character_sheet(project_dir: Path, name: str) -> None:
    """Persist/register a sheet for an existing character through activation so mentioning units select r2v."""
    sheet = project_dir / "characters" / f"{name}.png"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_bytes(b"png")
    project_file = project_dir / "project.json"
    project = _json.loads(project_file.read_text(encoding="utf-8"))
    project["characters"].setdefault(name, {"description": "d"})["character_sheet"] = f"characters/{name}.png"
    project_file.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _activate_project_artifacts(project_dir)


def _write_script_plan(project_dir: Path, payload: str, episode: int = 1) -> None:
    """Write formal script_plan and register in manifest."""
    path = project_dir / "drafts" / f"episode_{episode}" / "script_plan_reference_units.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    _activate_project_artifacts(project_dir, episode)


def _script_path(project: Path) -> Path:
    return project / "scripts" / "episode_1.json"


def _write_formal_units(project_dir: Path, units: list[dict[str, Any]], *, title: str = "第1集") -> Path:
    """Write formal scripts through the unified entry; units default to pending except explicit ``pending_authoring`` values."""
    project = _json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    script = {
        "title": title,
        "episode": 1,
        "content_mode": project["content_mode"],
        "novel": {"title": project["title"], "chapter": "第1集"},
        "video_units": [{"pending_authoring": True, **unit} for unit in units],
    }
    return ProjectManager.for_project_dir(project_dir).save_script(project_dir.name, script, "episode_1.json")


def _formal_units(project_dir: Path) -> dict[str, dict[str, Any]]:
    script = _json.loads(_script_path(project_dir).read_text(encoding="utf-8"))
    return {unit["unit_id"]: unit for unit in script["video_units"]}


@pytest.fixture(autouse=True)
def reference_request_facts(set_video_request_facts):
    """All tiers come from request facts: both buckets default to (4,6,8), with local overrides for different/failing buckets."""
    set_video_request_facts(make_video_request_facts(route="reference_video", generation_type="i2v"))


async def _materialize(generator: ScriptGenerator, episode: int = 1):
    """Materialize content confirmation against current planning and formal scripts; loaders first reject absent plans."""
    plan_path = generator.project_path / "drafts" / f"episode_{episode}" / "script_plan_reference_units.json"
    script_path = generator.project_path / "scripts" / f"episode_{episode}.json"
    return await generator.materialize_script_plan(
        episode,
        expected_plan_revision=script_review.content_fingerprint(plan_path) or "",
        expected_script_fingerprint=script_review.content_fingerprint(script_path),
        project_update=lambda _project: None,
    )


def _write_reference_project(
    tmp_path: Path, *, video_backend: str, content_mode: str = "narration", resolution: str | None = None
) -> Path:
    """Minimal reference-video project with planning. ``video_backend`` / ``resolution`` are self-reported identity only; case-supplied request facts determine planning tiers."""
    project_dir = tmp_path / "projects" / "proj"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        """{
          "schema_version": __SCHEMA__,
          "title": "t",
          "content_mode": "__MODE__",
          "generation_mode": "reference_video",
          "video_backend": "__BACKEND__",
          "overview": {"synopsis": "s", "genre": "g", "theme": "th", "world_setting": "w"},
          "style": "国漫",
          "style_description": "水墨",
          "characters": {"主角": {"description": "d"}},
          "scenes": {"酒馆": {"description": "d"}},
          "props": {},
          "episodes": [
            {"episode": 1, "title": "t1", "script_file": "scripts/episode_1.json",
             "generation_mode": "reference_video"}
          ]
        }""".replace("__SCHEMA__", str(CURRENT_PROJECT_SCHEMA_VERSION))
        .replace("__BACKEND__", video_backend)
        .replace("__MODE__", content_mode),
        encoding="utf-8",
    )
    if resolution is not None:
        project_file = project_dir / "project.json"
        project = _json.loads(project_file.read_text(encoding="utf-8"))
        project["model_settings"] = {video_backend: {"resolution": resolution}}
        project_file.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _write_script_plan(project_dir, SCRIPT_PLAN_UNITS_JSON)
    return project_dir


@pytest.fixture
def plan_only_reference_project(tmp_path: Path) -> Path:
    """Project with script planning but no formal script, for content-confirmation materialization."""
    return _write_reference_project(tmp_path, video_backend="vidu/vidu2.0", resolution="1080p")


@pytest.fixture
def reference_project(plan_only_reference_project: Path) -> Path:
    """Project with one pending formal unit, ``SCRIPT_PLAN_UNIT``."""
    _write_formal_units(plan_only_reference_project, [SCRIPT_PLAN_UNIT])
    return plan_only_reference_project


@pytest.fixture
def wide_tier_reference_project(tmp_path: Path) -> Path:
    """Project for differing buckets: cases provide r2v3–16 and i2v1–16 facts. References only narrow, so image-backed tiers stricter than imageless is the meaningful direction. Cases write their own formal scripts."""
    return _write_reference_project(tmp_path, video_backend="vidu/viduq3-turbo")


@pytest.mark.asyncio
async def test_build_prompt_renders_pending_formal_units(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    prompt = await gen.build_prompt(episode=1)
    # Pending formal-unit bodies enter prompts verbatim with durations.
    assert "@[主角] 推开 @[酒馆] 的门" in prompt
    assert "(duration 4s)" in prompt
    # Never send unit_id to prompt_authoring: align output by position.
    assert "E1U01" not in prompt


@pytest.mark.asyncio
async def test_prompt_authoring_ignores_the_script_plan(reference_project: Path):
    """Authoring reads only formal scripts and proceeds with absent or inconsistent plans."""
    plan = reference_project / "drafts" / "episode_1" / "script_plan_reference_units.json"
    plan.write_text(
        _json.dumps({"units": [{"unit_id": "E1U09", "text": "规划里另一段正文", "duration_seconds": 8}]}),
        encoding="utf-8",
    )
    prompt = await ScriptGenerator(reference_project).build_prompt(episode=1)
    assert "@[主角] 推开 @[酒馆] 的门" in prompt
    assert "规划里另一段正文" not in prompt

    plan.unlink()
    out = await ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    ).generate(episode=1)
    assert _formal_units(reference_project)["E1U01"]["text"] == PROMPT_AUTHORING_UNIT_TEXT
    assert out == _script_path(reference_project)


@pytest.mark.asyncio
async def test_reference_prompt_authoring_ends_with_instructions_on_both_paths(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    assert "# Additional instructions" not in await gen.build_prompt(episode=1)
    preview = await gen.build_prompt(episode=1, instructions="多给人物面部特写")
    assert preview.endswith("Output units in script_plan_units order.\n\n# Additional instructions\n多给人物面部特写")

    fake_generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    await ScriptGenerator(reference_project, generator=fake_generator).generate(
        episode=1, instructions="多给人物面部特写"
    )
    assert fake_generator.generate.await_args.args[0].prompt == preview


@pytest.mark.asyncio
async def test_script_generator_uses_reference_schema_on_generate(reference_project: Path):
    """prompt_authoring produces flat-schema bodies, updates pending units and clears markers while retaining formal unit_id/durations."""
    from lib.script.script_models import ReferencePromptAuthoringFlatScript

    fake_generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    out = await ScriptGenerator(reference_project, generator=fake_generator).generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    # Reference-video scripts inherit project narration/drama content_mode; generation mode is project-level,
    # never persisted as a script marker.
    assert data["content_mode"] == "narration"
    assert "generation_mode" not in data
    # Episode total duration is not persisted: project summaries derive it by summing units on reads.
    assert "duration_seconds" not in data
    assert len(data["video_units"]) == 1
    unit = data["video_units"][0]
    assert unit["unit_id"] == "E1U01"
    assert unit["duration_seconds"] == 4
    assert unit["text"].startswith("镜头1：中景，平视。")
    assert "pending_authoring" not in unit
    assert extract_mentions(unit["text"]) == ["主角", "酒馆"]

    # response_schema is flat and excludes duration_seconds; LLM never authors durations.
    schema = fake_generator.generate.await_args.args[0].response_schema
    assert schema is ReferencePromptAuthoringFlatScript
    assert "duration_seconds" not in _json.dumps(schema.model_json_schema())


@pytest.mark.asyncio
async def test_prompt_authoring_rewrites_only_pending_units(reference_project: Path):
    """Authored unmarked units stay byte-identical; rewrite pending bodies only and preserve other fields."""
    authored = {
        "unit_id": "E1U01",
        "text": "镜头1：远景。@[主角] 站在 @[酒馆] 门口。",
        "duration_seconds": 4,
        "note": "用户备注",
        "pending_authoring": False,
    }
    pending = {
        "unit_id": "E1U02",
        "text": "@[主角] 走进 @[酒馆]",
        "duration_seconds": 4,
        "note": "新增单元",
        "source_text": "他推门走进酒馆。",
    }
    _write_formal_units(reference_project, [authored, pending])
    before = _formal_units(reference_project)

    rewritten: list[str] = []
    new_text = "镜头1：中景。@[主角] 走进 @[酒馆]，环顾四周。"
    fake_generator = _fake_prompt_authoring_generator(new_text)
    await ScriptGenerator(reference_project, generator=fake_generator).generate(
        episode=1, rewritten_entry_ids=rewritten
    )

    assert rewritten == ["E1U02"]
    prompt = fake_generator.generate.await_args.args[0].prompt
    assert "@[主角] 走进 @[酒馆]" in prompt
    after = _formal_units(reference_project)
    assert _json.dumps(after["E1U01"], sort_keys=True) == _json.dumps(before["E1U01"], sort_keys=True)
    assert after["E1U02"] == {
        "unit_id": "E1U02",
        "text": new_text,
        "duration_seconds": 4,
        "note": "新增单元",
        "source_text": "他推门走进酒馆。",
    }


@pytest.mark.asyncio
async def test_prompt_authoring_without_pending_units_skips_the_model(reference_project: Path):
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "pending_authoring": False}])
    before = _script_path(reference_project).read_bytes()
    generator = _idle_generator()

    rewritten: list[str] = ["stale"]
    out = await ScriptGenerator(reference_project, generator=generator).generate(
        episode=1, rewritten_entry_ids=rewritten
    )

    assert out == _script_path(reference_project)
    assert rewritten == []
    generator.generate.assert_not_awaited()
    assert _script_path(reference_project).read_bytes() == before


@pytest.mark.asyncio
async def test_entry_ids_rewrite_an_authored_unit_keeping_other_fields(reference_project: Path):
    authored = {**SCRIPT_PLAN_UNIT, "text": "镜头1：远景。@[主角] 推开 @[酒馆] 的门。", "note": "保留"}
    _write_formal_units(reference_project, [{**authored, "pending_authoring": False}])

    await ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    ).generate(
        episode=1,
        entry_ids=["E1U01"],
        rewrite=True,
        overwrite_revision=script_review.content_fingerprint(reference_project / "scripts" / "episode_1.json"),
    )

    unit = _formal_units(reference_project)["E1U01"]
    assert unit == {"unit_id": "E1U01", "text": PROMPT_AUTHORING_UNIT_TEXT, "duration_seconds": 4, "note": "保留"}


@pytest.mark.asyncio
async def test_prompt_authoring_rejects_formal_duration_outside_effective_tiers(
    reference_project: Path, set_video_request_facts
):
    """Resolution narrows full [4,6,8] tiers to [4], making existing eight-second units invalid. Never silently select/rewrite duration; fail loudly and direct to timeline.

    Block before TextBackend calls when neither image-backed nor imageless tiers admits duration. Waiting until output parsing would charge users for inevitably failing authoring."""
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video", generation_type="i2v", supported_durations=(4, 6, 8), allowed_durations=(4,)
        )
    )
    _write_formal_units(
        reference_project,
        [
            {**SCRIPT_PLAN_UNIT, "pending_authoring": False},
            {**SCRIPT_PLAN_UNIT, "unit_id": "E1U04", "duration_seconds": 8},
        ],
    )
    generator = _idle_generator()

    gen = ScriptGenerator(reference_project, generator=generator)
    with pytest.raises(PromptAuthoringUnitRejected, match=r"不在当前生效档位.*时间线") as exc_info:
        await gen.generate(episode=1)
    generator.generate.assert_not_awaited()
    # Classify as edit-input, naming UI unit position two with current duration and allowed tiers.
    assert (exc_info.value.code, exc_info.value.params) == (
        "prompt_authoring_duration_off_tier",
        {"unit_id": "E1U04", "position": 2, "duration_seconds": 8, "tiers": "4"},
    )


@pytest.mark.asyncio
async def test_no_image_five_second_unit_uses_i2v_facts_through_conversion_and_authoring(
    tmp_path: Path, set_video_request_facts
):
    project = _write_reference_project(tmp_path, video_backend="gemini-aistudio/veo-3.1-generate-preview")
    _write_script_plan(
        project,
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "text": "镜头1：空镜，街道渐亮。",
                        "duration_seconds": 5,
                        "source_text": "原文",
                    }
                ]
            },
            ensure_ascii=False,
        ),
    )
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            provider_id="ark",
            model_id="doubao-seedance-2-0-260128",
            supported_durations=tuple(range(4, 16)),
            allowed_durations=tuple(range(4, 16)),
        )
    )

    generator = ScriptGenerator(
        project,
        generator=_fake_prompt_authoring_generator("镜头1：远景，晨光照亮空旷街道。"),
    )

    await _materialize(generator)
    await generator.generate(episode=1)

    assert _formal_units(project)["E1U1"]["duration_seconds"] == 5


@pytest.mark.asyncio
async def test_no_image_conversion_rejects_unavailable_i2v_even_when_r2v_accepts_duration(
    tmp_path: Path, set_video_request_facts
):
    project = _write_reference_project(tmp_path, video_backend="gemini-aistudio/veo-3.1-generate-preview")
    _write_script_plan(
        project,
        _json.dumps({"units": [{"unit_id": "E1U01", "text": "镜头1：空镜，街道渐亮。", "duration_seconds": 8}]}),
    )
    set_video_request_facts(
        {
            "r2v": make_video_request_facts(route="reference_video", generation_type="r2v"),
            "i2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)),
        }
    )

    with pytest.raises(VideoRequestFactsError) as exc:
        await _materialize(ScriptGenerator(project))

    assert exc.value.code == "reference_capability_unavailable"
    assert not _script_path(project).exists()


@pytest.mark.asyncio
async def test_script_generator_narrows_duration_tiers_per_unit_not_episode_wide(
    wide_tier_reference_project: Path, set_video_request_facts
):
    """One image-backed unit narrows to3–16 while another remains1–16; the latter's valid two seconds must not become three. Select effective tiers per unit reference state, never episode-level any(...) coarse narrowing."""
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            supported_durations=tuple(range(1, 17)),
            allowed_durations=tuple(range(1, 17)),
        )
    )
    project = wide_tier_reference_project
    _write_formal_units(
        project,
        [
            {"unit_id": "E1U01", "text": "@[主角] 推门", "duration_seconds": 3},
            {"unit_id": "E1U02", "text": "空镜", "duration_seconds": 2},
        ],
    )

    fake_generator = _fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门", "镜头1：空镜，风吹过门廊")
    gen = ScriptGenerator(project, generator=fake_generator)
    await gen.generate(episode=1)

    units_by_id = _formal_units(project)
    assert units_by_id["E1U01"]["duration_seconds"] == 3
    assert (
        units_by_id["E1U02"]["duration_seconds"] == 2
    )  # Another image-backed unit's narrowing must not change this one.


@pytest.mark.asyncio
async def test_script_generator_takes_duration_tier_from_final_output_references(
    wide_tier_reference_project: Path, set_video_request_facts
):
    """An existing referenced body excludes two seconds under image3–16, but authoring removes references and two becomes valid under text1–16. Recompute using final persisted references, not pre-rewrite state."""
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            supported_durations=tuple(range(1, 17)),
            allowed_durations=tuple(range(1, 17)),
        )
    )
    project = wide_tier_reference_project
    _write_formal_units(project, [{"unit_id": "E1U01", "text": "@[主角] 推门", "duration_seconds": 2}])

    fake_generator = _fake_prompt_authoring_generator("镜头1：空镜，门廊在风里轻响")
    gen = ScriptGenerator(project, generator=fake_generator)
    await gen.generate(episode=1)

    unit = _formal_units(project)["E1U01"]
    assert extract_mentions(unit["text"]) == []
    assert unit["duration_seconds"] == 2


@pytest.mark.asyncio
async def test_prompt_authoring_takes_the_i2v_tier_for_a_registered_reference_without_image(
    wide_tier_reference_project: Path, set_video_request_facts, db_factory
):
    """Select authoring buckets by usable images: registered but missing references select i2v like confirmation/execution. Two seconds is valid in i2v1–16; a body `@` alone must not trigger r2v3–16 rejection into drafts."""
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            supported_durations=tuple(range(1, 17)),
            allowed_durations=tuple(range(1, 17)),
        )
    )
    project = wide_tier_reference_project
    _write_formal_units(project, [{"unit_id": "E1U01", "text": "@[主角] 推门", "duration_seconds": 2}])
    resolver = ConfigResolver(db_factory)
    gen = ScriptGenerator(
        project, generator=_fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门"), config_resolver=resolver
    )

    await gen.generate(episode=1)

    unit = _formal_units(project)["E1U01"]
    assert "@[主角]" in unit["text"]
    assert unit["duration_seconds"] == 2
    (capability,) = await evaluate_reference_unit_capabilities(
        gen.project_json, project, [unit], request_facts=configured_reference_request_facts(gen.project_json, resolver)
    )
    assert capability.generation_type == "i2v"


@pytest.mark.asyncio
async def test_conversion_requires_i2v_facts_for_a_registered_reference_without_image(
    plan_only_reference_project: Path, set_video_request_facts
):
    """Confirmation materialization uses i2v for registered but missing images. Unresolvable i2v facts reject, never borrow r2v tiers for unexecutable plans."""
    set_video_request_facts(VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)))

    with pytest.raises(VideoRequestFactsError) as exc:
        await _materialize(ScriptGenerator(plan_only_reference_project))

    assert exc.value.code == "reference_capability_unavailable"
    assert not _script_path(plan_only_reference_project).exists()


@pytest.mark.asyncio
async def test_prompt_authoring_reads_the_r2v_tier_for_a_unit_with_a_usable_reference(
    reference_project: Path, set_video_request_facts
):
    """Usable references select r2v facts. With r2v[4] and i2v[4,8], eight seconds is not preflight-blocked because removing references could validate it. If output keeps references, reject r2v duration into a repair draft, never admit using i2v."""
    set_video_request_facts(
        {
            "r2v": make_video_request_facts(
                route="reference_video", generation_type="r2v", supported_durations=(4, 8), allowed_durations=(4,)
            ),
            "i2v": make_video_request_facts(
                route="reference_video", generation_type="i2v", supported_durations=(4, 8), allowed_durations=(4, 8)
            ),
        }
    )
    _claim_character_sheet(reference_project, "主角")
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "duration_seconds": 8}])
    generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    gen = ScriptGenerator(reference_project, generator=generator)
    with pytest.raises(DraftViolation, match="不在当前生效档位"):
        await gen.generate(episode=1)

    generator.generate.assert_awaited_once()
    assert _formal_units(reference_project)["E1U01"]["duration_seconds"] == 8
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert [v["code"] for v in envelope["violations"]] == ["duration_off_tier"]


@pytest.mark.asyncio
async def test_script_generator_rejects_prompt_authoring_unit_count_change(reference_project: Path):
    """prompt_authoring may not merge/split/add/remove units: output count must equal pending-unit count or fail loudly."""
    gen = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT, "镜头1：多出来的一段")
    )
    with pytest.raises(ValueError, match="unit 数"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_generator_rejects_prompt_authoring_dialogue_rewrite(reference_project: Path):
    """Preserve normalized dialogue lines verbatim; altered speech fails, never silently accepting wording changed to fit visuals."""
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "@[主角] 推门\n@[主角]：{我来了。}"}])
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门跨入\n@[主角]：{我到了。}"),
    )
    with pytest.raises(ValueError, match="台词"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_generator_accepts_prompt_authoring_expansion_keeping_dialogue(reference_project: Path):
    """Freely expanded visuals with verbatim dialogue pass; exclude dialogue-speaker positions from reference images."""
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "@[主角] 推门\n@[主角]：{我来了。}"}])
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator(
            "镜头1：中景，平视。@[主角] 推开 @[酒馆] 的门，跨过门槛\n@[主角]：{我来了。}"
        ),
    )
    await gen.generate(episode=1)
    assert extract_mentions(_formal_units(reference_project)["E1U01"]["text"]) == ["主角", "酒馆"]


@pytest.mark.asyncio
async def test_script_generator_rejects_prompt_authoring_unregistered_mention(reference_project: Path):
    """Validate added authoring mentions too; unregistered assets never enter bodies."""
    gen = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator("镜头1：@[主角] 与 @[路人乙] 对视")
    )
    with pytest.raises(ValueError, match="未登记"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_prompt_authoring_keeps_an_unregistered_speaker_from_the_formal_unit(reference_project: Path):
    """Speakers explicitly marked do-not-register during confirmation stay in formal bodies; authoring preserves them verbatim without unregistered-speaker rejection."""
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "路人 拦住 @[主角]\n@[路人]{借过。}"}])
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator("镜头1：中景。路人 拦住 @[主角] 的去路\n@[路人]{借过。}"),
    )

    await gen.generate(episode=1)

    assert _formal_units(reference_project)["E1U01"]["text"].endswith("@[路人]{借过。}")


@pytest.mark.asyncio
async def test_prompt_authoring_refuses_a_speaker_the_formal_unit_did_not_have(reference_project: Path):
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "@[主角] 推门\n@[主角]{我来了。}"}])
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator("镜头1：@[主角] 推门\n@[主角]{我来了。}\n@[路人乙]{谁？}"),
    )

    with pytest.raises(ValueError, match="说话人"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_plan_conversion_inherits_drama_content_mode(tmp_path: Path):
    """Reference-video scripts materialized in drama projects must have content_mode=drama.

    Pydantic ReferenceVideoScript defaults narration, and model_dump persists it. _add_metadata explicitly overrides rather than setdefault, avoiding drama scripts mislabeled narration."""
    project_dir = _write_reference_project(tmp_path, video_backend="vidu/vidu2.0", content_mode="drama")

    await _materialize(ScriptGenerator(project_dir))

    data = _json.loads(_script_path(project_dir).read_text(encoding="utf-8"))
    assert data["content_mode"] == "drama"
    assert "generation_mode" not in data


@pytest.mark.parametrize(
    ("with_references", "expected"),
    [
        (make_video_request_facts(route="reference_video", generation_type="r2v", max_reference_images=3), 3),
        (make_video_request_facts(route="reference_video", generation_type="r2v", max_reference_images=1), 1),
        # Zero is an explicit no-reference endpoint limit; forward unchanged to clip to zero images.
        (make_video_request_facts(route="reference_video", generation_type="r2v", max_reference_images=0), 0),
        # Undeclared fact limit means None.
        (make_video_request_facts(route="reference_video", generation_type="r2v", max_reference_images=None), None),
        # Unresolvable r2v facts mean undeclared limit; image-backed units separately block missing facts at checkpoints.
        (VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "r2v"),)), None),
    ],
)
def test_resolve_max_refs_reads_the_r2v_request_facts(with_references, expected):
    """Reference-image limits come only from r2v request facts; never fall back to self-reported project identity registry lookup."""
    facts = PlanningVideoFacts(
        by_bucket={
            "r2v": with_references,
            "i2v": make_video_request_facts(route="reference_video", generation_type="i2v", max_reference_images=9),
        },
    )

    assert ScriptGenerator._resolve_max_refs(facts) == expected


def _write_minimal_reference_project(tmp_path: Path, **overrides: Any) -> Path:
    project_dir = tmp_path / "projects" / "proj"
    project_dir.mkdir(parents=True)
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "title": "t",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        "style": "s",
        "style_description": "d",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        **overrides,
    }
    (project_dir / "project.json").write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _write_script_plan(project_dir, SCRIPT_PLAN_UNITS_JSON)
    _write_formal_units(project_dir, [SCRIPT_PLAN_UNIT])
    return project_dir


@pytest.mark.asyncio
async def test_generate_without_r2v_facts_fails_with_the_facts_code_before_the_paid_call(
    tmp_path: Path, set_video_request_facts
):
    """Image-backed units select r2v; unresolvable facts fail authoring before model calls with same-family codes. Tiers come only from request facts, never project-reported registry identity or borrowing resolvable i2v facts."""
    set_video_request_facts(
        {
            "r2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "r2v"),)),
            "i2v": make_video_request_facts(route="reference_video", generation_type="i2v"),
        }
    )
    project_dir = _write_minimal_reference_project(tmp_path)
    _claim_character_sheet(project_dir, "主角")
    generator = _idle_generator()

    gen = ScriptGenerator(project_dir, generator=generator)
    with pytest.raises(VideoRequestFactsError) as exc:
        await gen.generate(episode=1)

    assert exc.value.code == "reference_capability_unavailable"
    generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_prompt_follows_project_reference_route(tmp_path: Path):
    """For reference_video projects, build_prompt takes the reference branch. Read project-level ``generation_mode`` from ``project.json``, constant across episodes."""
    project_dir = _write_minimal_reference_project(
        tmp_path, video_backend="vidu/vidu2.0", characters={"主角": {"description": "d"}}
    )

    prompt = await ScriptGenerator(project_dir).build_prompt(episode=1)
    assert "@[主角] 推开 @[酒馆] 的门" in prompt
    assert "(duration 4s)" in prompt


# ---------------------------------------------------------------------------
# Content-confirmation materialization reads reference_video planning.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversion_reads_legacy_script_plan_draft_without_source_text(plan_only_reference_project: Path):
    """Legacy script_plan drafts lacking source_text and migrated per-shot durations remain readable/materializable.

    ``source_text`` is the validated production source anchor. Treat missing fields as legacy; empty-string defaults permit reads without forcing users to rerun splitting."""
    project = plan_only_reference_project
    saved = _json.loads((project / "drafts" / "episode_1" / "script_plan_reference_units.json").read_text("utf-8"))
    assert "source_text" not in saved["units"][0]

    receipt = await _materialize(ScriptGenerator(project))

    assert receipt.entry_ids == ("E1U1",)
    assert list(_formal_units(project)) == ["E1U1"]


@pytest.mark.asyncio
async def test_reference_script_plan_legacy_md_prompts_resplit(plan_only_reference_project: Path):
    """If only pre-structured legacy .md splitting tables exist, explicitly advise rerunning splitting rather than generic missing-file errors."""
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").unlink()
    (drafts / "script_plan_reference_units.md").write_text("| E1U1 | Shot1(4s) |", encoding="utf-8")

    gen = ScriptGenerator(plan_only_reference_project)
    with pytest.raises(FileNotFoundError, match="generate_script_plan"):
        await _materialize(gen)


@pytest.mark.asyncio
async def test_reference_script_plan_missing_raises(plan_only_reference_project: Path):
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").unlink()

    gen = ScriptGenerator(plan_only_reference_project)
    with pytest.raises(FileNotFoundError, match="video_unit 拆分"):
        await _materialize(gen)


@pytest.mark.asyncio
async def test_reference_script_plan_rejects_out_of_enum_duration(
    plan_only_reference_project: Path, set_video_request_facts
):
    """Materialization rechecks durations against both effective buckets, rejecting full-set members removed by narrowing to prevent manual-edit drift."""
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video", generation_type="r2v", supported_durations=(4, 6, 8), allowed_durations=(4, 8)
        )
    )
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps({"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 6}]}),
        encoding="utf-8",
    )
    _claim_character_sheet(plan_only_reference_project, "主角")

    gen = ScriptGenerator(plan_only_reference_project)
    with pytest.raises(UnitDurationOffTier, match="视频单元 1 已确认时长 6s 不在当前生效档位") as exc_info:
        await _materialize(gen)
    assert (exc_info.value.position, exc_info.value.duration_seconds, exc_info.value.tiers) == (1, 6, [4, 8])
    assert "E1U01" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_reference_script_plan_rejects_duplicate_unit_ids(plan_only_reference_project: Path):
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    unit = {"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4}
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps({"units": [unit, dict(unit)]}), encoding="utf-8"
    )

    gen = ScriptGenerator(plan_only_reference_project)
    with pytest.raises(ValueError, match="unit_id 重复"):
        await _materialize(gen)


def test_reference_script_plan_migration_carries_confirmation_forward(plan_only_reference_project: Path):
    """Migration writeback changes content fingerprints. If an episode was confirmed against exact pre-migration content, shift confirmation fingerprints to migrated values; merely loading must not reopen confirmation."""
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    # duration_override retired with per-shot durations and is removed by load-time coercion migrations.
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    project_path = plan_only_reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")

    gen = ScriptGenerator(plan_only_reference_project)
    gen._load_reference_script_plan(episode=1, supported_durations=[4, 8])

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    after = script_review.content_fingerprint(script_plan_path)
    assert review["fingerprint"] == after
    assert review["fingerprint"] != before


@pytest.mark.asyncio
async def test_reference_script_plan_migration_waits_for_prompt_authoring_draft_lock(
    plan_only_reference_project: Path, monkeypatch
) -> None:
    script_plan_path = plan_only_reference_project / "drafts" / "episode_1" / "script_plan_reference_units.json"
    script_plan_path.write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "text": "@[主角] 转身",
                        "duration_seconds": 4,
                        "duration_override": True,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prompt_authoring_path = quarantine_path(plan_only_reference_project, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    pm = ProjectManager.for_project_dir(plan_only_reference_project)
    held = threading.Event()
    release = threading.Event()

    def hold_prompt_authoring_lock() -> None:
        with pm.file_lock(prompt_authoring_path):
            held.set()
            _ = release.wait()

    holder = asyncio.create_task(asyncio.to_thread(hold_prompt_authoring_lock))
    try:
        assert await asyncio.to_thread(held.wait, 1)
        attempted = threading.Event()
        target_lock = prompt_authoring_path.parent / f".{prompt_authoring_path.name}.lock"
        original_acquire = project_manager_module.portalocker.Lock.acquire

        def tracked_acquire(lock, *args, **kwargs):
            if Path(lock.filename) == target_lock:
                attempted.set()
            return original_acquire(lock, *args, **kwargs)

        monkeypatch.setattr(project_manager_module.portalocker.Lock, "acquire", tracked_acquire)
        loading = asyncio.create_task(
            ScriptGenerator(plan_only_reference_project)._load_plan_entries_for_materialization(1)
        )
        attempted_before_release = await asyncio.to_thread(attempted.wait, 1)
        if attempted_before_release:
            assert not loading.done()
            assert "duration_override" in script_plan_path.read_text(encoding="utf-8")
            ticked = asyncio.Event()
            asyncio.get_running_loop().call_soon(ticked.set)
            await asyncio.wait_for(ticked.wait(), timeout=1)
    finally:
        release.set()
        await asyncio.wait_for(holder, timeout=1)

    await asyncio.wait_for(loading, timeout=1)
    assert attempted_before_release
    assert "duration_override" not in script_plan_path.read_text(encoding="utf-8")


def test_reference_script_plan_migration_carries_confirmation_confirmed_after_construction(
    plan_only_reference_project: Path,
):
    """Confirmation may occur after ScriptGenerator construction, such as during generate() await _fetch_video_request_facts() via concurrent ScriptReviewService.confirm(). Stale self.project_json cannot see it, but migration still shifts it correctly; never short-circuit using that snapshot."""
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    # duration_override retired with per-shot durations and is removed by load-time coercion migrations.
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    # No confirmation records in project.json at construction.
    gen = ScriptGenerator(plan_only_reference_project)

    # Confirmation occurs after construction, simulating concurrent ScriptReviewService.confirm(); self.project_json stays stale.
    project_path = plan_only_reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert "script_plan_review" not in gen.project_json["episodes"][0]  # Constructor snapshot indeed lacks it.

    gen._load_reference_script_plan(episode=1, supported_durations=[4, 8])

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    after = script_review.content_fingerprint(script_plan_path)
    assert review["fingerprint"] == after
    assert review["confirmed_at"] == "2026-01-01T00:00:00Z"
    assert review["confirmed_at"] == "2026-01-01T00:00:00Z"


def test_reference_script_plan_migration_does_not_carry_confirmation_when_duration_is_clamped(
    plan_only_reference_project: Path,
):
    """Migration warnings from summed durations requiring tier changes are not format-only coercion. Confirmed episodes become pending again; never shift acceptance to seconds users did not approve."""
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    # duration_override retired with per-shot durations and is removed by load-time coercion migrations.
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    project_path = plan_only_reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")

    gen = ScriptGenerator(plan_only_reference_project)
    # Summed four seconds is outside model tiers; selecting eight emits a warning.
    gen._load_reference_script_plan(episode=1, supported_durations=[8])

    assert _json.loads(script_plan_path.read_text(encoding="utf-8"))["units"][0]["duration_seconds"] == 8

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    assert (
        review["fingerprint"] == before
    )  # Fingerprint was not shifted and remains pre-migration; confirmation stays pending normally.


@pytest.mark.asyncio
async def test_formal_text_violation_is_caught_before_the_paid_prompt_authoring_call(reference_project: Path):
    """Block pending-body syntax violations before text-model calls, naming formal scripts/timeline. prompt_authoring preserves those bodies verbatim, so violations inevitably recur; later checks would charge then falsely blame authoring for corruption."""
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "@[查无此人} 推门"}])
    generator = _idle_generator()

    with pytest.raises(PromptAuthoringUnitRejected, match=r"来自正式脚本.*时间线") as exc_info:
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1)
    generator.generate.assert_not_awaited()
    assert (exc_info.value.code, exc_info.value.params) == (
        "prompt_authoring_unit_invalid",
        {"unit_id": "E1U01", "position": 1},
    )


def test_speech_violation_preserves_canonical_unit_and_locations(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    units = [
        {
            "unit_id": "E1U01",
            "duration_seconds": 4,
            "text": "门被推开\n@[主角]：{快走。}\n{风吹过旷野。}",
        }
    ]

    with pytest.raises(DraftViolation) as exc_info:
        gen._assert_reference_unit_text_valid(units, max_refs=None)

    problem = exc_info.value
    assert problem.code == "mixed_speech"
    assert "unit E1U01 发声准入未通过" in str(problem)
    assert "unit 正式脚本 的 unit" not in str(problem)
    assert problem.locations == (
        {"path": ["text"], "line": 1},
        {"path": ["text"], "line": 2},
    )
    assert problem.reason == "character_and_narrator_mixed"
    assert problem.action == "replan_unit"


async def test_dialogue_overload_is_caught_before_the_paid_prompt_authoring_call(reference_project: Path):
    """Timeline duration shortening or added dialogue bypasses original speech-volume validation; reassess before authoring. Dialogue is preserved verbatim and no later volume check exists, so otherwise unspeakable units would persist into output."""
    long_line = "他站在门口足足看了半晌才缓缓开口说出这句迟到了整整十年的道歉与告别" * 2
    _write_formal_units(
        reference_project,
        [
            {**SCRIPT_PLAN_UNIT, "pending_authoring": False},
            {**SCRIPT_PLAN_UNIT, "unit_id": "E1U04", "text": f"@[主角] 推开 @[酒馆] 的门\n@[主角]：{{{long_line}}}"},
        ],
    )
    generator = _idle_generator()

    with pytest.raises(PromptAuthoringUnitRejected, match="台词念完约需") as exc_info:
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1)
    generator.generate.assert_not_awaited()
    # Input-problem codes name UI position two rather than allocate-only, never-reused unit IDs.
    rejection = exc_info.value
    assert rejection.code == "prompt_authoring_dialogue_overload"
    assert rejection.params["position"] == 2
    assert rejection.params["unit_id"] == "E1U04"
    assert rejection.params["duration_seconds"] == 4
    assert float(rejection.params["spoken_seconds"]) > 4


async def test_prompt_authoring_missing_title_does_not_fail_the_paid_call(reference_project: Path):
    """Nonconstrained channels omitting title must not fail paid expansions: title is unused in writeback, preserving formal titles."""
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock(
        return_value=MagicMock(text=_json.dumps({"units": [{"text": PROMPT_AUTHORING_UNIT_TEXT}]}, ensure_ascii=False))
    )

    out = await ScriptGenerator(reference_project, generator=generator).generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    assert data["title"] == "第1集"
    assert data["video_units"][0]["text"] == PROMPT_AUTHORING_UNIT_TEXT


@pytest.mark.asyncio
async def test_prompt_authoring_runs_while_script_plan_quarantined(reference_project: Path):
    """Authoring reads formal scripts only; repair drafts for script planning do not block it."""
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_SCRIPT_PLAN,
        content={"units": []},
        violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
    )
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT))

    await gen.generate(episode=1)

    assert _formal_units(reference_project)["E1U01"]["text"] == PROMPT_AUTHORING_UNIT_TEXT


# ---------------------------------------------------------------------------
# Violating prompt_authoring repair drafts and repair/promotion loop.
# ---------------------------------------------------------------------------

#: Violating expansion mentions unregistered @[路人甲], absent from original unit bodies.
BAD_PROMPT_AUTHORING_UNIT_TEXT = "镜头1：中景。@[路人甲] 推开 @[酒馆] 的门。"


def _prompt_authoring_quarantine(project: Path):
    return quarantine_path(project, 1, QUARANTINE_KIND_PROMPT_AUTHORING)


@pytest.mark.asyncio
async def test_prompt_authoring_violation_quarantines_instead_of_discarding(reference_project: Path):
    """Do not discard paid authoring expansions on violations: store repair drafts, preserve formal scripts and include recovery guidance."""
    formal_before = _script_path(reference_project).read_bytes()
    baseline = script_review.content_fingerprint(_script_path(reference_project))
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))

    with pytest.raises(DraftViolation) as excinfo:
        await gen.generate(episode=1)

    report = str(excinfo.value)
    assert "unregistered_asset" in report
    assert "promote_draft" in report
    assert _script_path(reference_project).read_bytes() == formal_before

    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["kind"] == QUARANTINE_KIND_PROMPT_AUTHORING
    assert envelope["meta"]["base_fingerprint"] == baseline
    assert envelope["meta"]["unit_ids"] == ["E1U01"]
    assert [v["code"] for v in envelope["violations"]] == ["unregistered_asset"]
    # Draft uses flat editable shape whose body the Agent repairs.
    assert envelope["content"]["units"][0]["text"] == BAD_PROMPT_AUTHORING_UNIT_TEXT


@pytest.mark.asyncio
async def test_cancelled_prompt_authoring_quarantine_finishes_without_blocking_event_loop(reference_project: Path):
    generator = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT)
    )
    started = threading.Event()
    release = threading.Event()
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def before_quarantine_commit() -> None:
        worker_threads.append(threading.get_ident())
        started.set()
        release.wait()

    generation = asyncio.create_task(generator.generate(episode=1, before_quarantine_commit=before_quarantine_commit))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        ticked = asyncio.Event()
        asyncio.get_running_loop().call_soon(ticked.set)
        await asyncio.wait_for(ticked.wait(), timeout=1)
        generation.cancel()
        await asyncio.sleep(0)
        assert not generation.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(generation, timeout=1)
    assert _prompt_authoring_quarantine(reference_project).exists()
    assert worker_threads
    assert all(thread != caller_thread for thread in worker_threads)


@pytest.mark.asyncio
async def test_prompt_authoring_generation_preserves_draft_edited_during_model_call(reference_project: Path):
    started = asyncio.Event()
    release = asyncio.Event()
    generator = MagicMock()
    generator.model = "mock"

    async def generate(_request, **_kwargs):
        started.set()
        await release.wait()
        return MagicMock(text=_prompt_authoring_response(BAD_PROMPT_AUTHORING_UNIT_TEXT))

    generator.generate = AsyncMock(side_effect=generate)
    generation = asyncio.create_task(ScriptGenerator(reference_project, generator=generator).generate(episode=1))
    await started.wait()
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "并发草稿", "units": [{"text": "并发编辑内容"}]},
        violations=[],
    )
    release.set()

    with pytest.raises(DraftViolation) as excinfo:
        await asyncio.wait_for(generation, timeout=1)
    assert excinfo.value.code == "draft_revision_conflict"
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["content"]["title"] == "并发草稿"
    assert envelope["content"]["units"][0]["text"] == "并发编辑内容"


@pytest.mark.asyncio
async def test_successful_prompt_authoring_generation_rejects_draft_created_during_model_call(reference_project: Path):
    formal_before = _script_path(reference_project).read_bytes()
    started = asyncio.Event()
    release = asyncio.Event()
    generator = MagicMock()
    generator.model = "mock"

    async def generate(_request, **_kwargs):
        started.set()
        await release.wait()
        return MagicMock(text=_prompt_authoring_response(PROMPT_AUTHORING_UNIT_TEXT))

    generator.generate = AsyncMock(side_effect=generate)
    generation = asyncio.create_task(ScriptGenerator(reference_project, generator=generator).generate(episode=1))
    await asyncio.wait_for(started.wait(), timeout=1)
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "并发草稿", "units": [{"text": "并发编辑内容"}]},
        violations=[],
        meta={"base_fingerprint": None},
    )
    release.set()

    with pytest.raises(DraftViolation) as excinfo:
        await asyncio.wait_for(generation, timeout=1)
    assert excinfo.value.code == "draft_revision_conflict"
    assert _script_path(reference_project).read_bytes() == formal_before
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["content"]["title"] == "并发草稿"


@pytest.mark.asyncio
async def test_prompt_authoring_generation_rejects_existing_draft_before_model_call(reference_project: Path):
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "现有草稿", "units": [{"text": PROMPT_AUTHORING_UNIT_TEXT}]},
        violations=[],
        meta={"base_fingerprint": None},
    )
    generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    with pytest.raises(DraftViolation) as excinfo:
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1)

    assert excinfo.value.code == "draft_revision_conflict"
    generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_authoring_preserves_output_when_formal_script_changes_during_generation(reference_project: Path):
    formal = _script_path(reference_project)
    concurrent = _json.loads(formal.read_text(encoding="utf-8"))
    concurrent["title"] = "并发正式标题"
    generator = MagicMock()
    generator.model = "mock"

    async def generate(_request, **_kwargs):
        # Concurrent writer runs off generation's event loop; perform writes in a worker thread.
        await asyncio.to_thread(formal.write_text, _json.dumps(concurrent, ensure_ascii=False), encoding="utf-8")
        return MagicMock(text=_prompt_authoring_response(PROMPT_AUTHORING_UNIT_TEXT, title="本次生成标题"))

    generator.generate = AsyncMock(side_effect=generate)

    with pytest.raises(DraftViolation) as excinfo:
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1)

    assert "formal_revision_conflict" in str(excinfo.value)
    assert _json.loads(formal.read_text(encoding="utf-8"))["title"] == "并发正式标题"
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["content"]["title"] == "本次生成标题"
    assert envelope["meta"]["base_fingerprint"] is not None


@pytest.mark.asyncio
async def test_prompt_authoring_violation_keeps_generation_start_formal_baseline(reference_project: Path):
    formal = _script_path(reference_project)
    baseline = script_review.content_fingerprint(formal)
    assert baseline is not None
    concurrent = _json.loads(formal.read_text(encoding="utf-8"))
    concurrent["title"] = "并发正式标题"
    generator = MagicMock()
    generator.model = "mock"

    async def generate(_request, **_kwargs):
        # Concurrent writer runs off generation's event loop; perform writes in a worker thread.
        await asyncio.to_thread(formal.write_text, _json.dumps(concurrent, ensure_ascii=False), encoding="utf-8")
        return MagicMock(text=_prompt_authoring_response(BAD_PROMPT_AUTHORING_UNIT_TEXT, title="本次违约输出"))

    generator.generate = AsyncMock(side_effect=generate)

    with pytest.raises(DraftViolation):
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1)

    assert _json.loads(formal.read_text(encoding="utf-8"))["title"] == "并发正式标题"
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["content"]["title"] == "本次违约输出"
    assert envelope["meta"]["base_fingerprint"] == baseline
    assert envelope["meta"]["base_fingerprint"] != script_review.content_fingerprint(formal)


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_after_repair(reference_project: Path, monkeypatch):
    """Promote repaired drafts by writing formal bodies and clearing pending markers/drafts."""
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0]["text"] = PROMPT_AUTHORING_UNIT_TEXT
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []
    original_save = ProjectManager.save_script

    def tracked_save(self, *args, **kwargs):
        worker_threads.append(threading.get_ident())
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(ProjectManager, "save_script", tracked_save)

    out = await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    assert out.exists()
    assert worker_threads
    assert all(thread != caller_thread for thread in worker_threads)
    assert not path.exists()
    data = _json.loads(out.read_text(encoding="utf-8"))
    unit = data["video_units"][0]
    assert unit["unit_id"] == "E1U01"
    assert unit["duration_seconds"] == 4
    assert "pending_authoring" not in unit
    assert extract_mentions(unit["text"]) == ["主角", "酒馆"]


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_merges_only_the_recorded_units(reference_project: Path):
    """Use meta.unit_ids to identify units pending at production time; promotion updates only them, leaving others byte-identical."""
    authored = {
        "unit_id": "E1U01",
        "text": "镜头1：远景。@[主角] 站在 @[酒馆] 门口。",
        "duration_seconds": 4,
        "pending_authoring": False,
    }
    pending = {"unit_id": "E1U02", "text": "@[主角] 走进 @[酒馆]", "duration_seconds": 4}
    _write_formal_units(reference_project, [authored, pending])
    before_first = _json.dumps(_formal_units(reference_project)["E1U01"], sort_keys=True)
    gen = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator("镜头1：@[路人甲] 走进 @[酒馆]")
    )
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    envelope = _json.loads(path.read_text(encoding="utf-8"))
    assert envelope["meta"]["unit_ids"] == ["E1U02"]
    envelope["content"]["units"][0]["text"] = "镜头1：中景。@[主角] 走进 @[酒馆]。"
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    after = _formal_units(reference_project)
    assert _json.dumps(after["E1U01"], sort_keys=True) == before_first
    assert after["E1U02"] == {"unit_id": "E1U02", "text": "镜头1：中景。@[主角] 走进 @[酒馆]。", "duration_seconds": 4}


@pytest.mark.asyncio
async def test_promote_whole_script_draft_clears_pending_only_for_units_whose_text_changed(reference_project: Path):
    """Whole drafts without meta.unit_ids clear pending markers only on changed bodies; unchanged units remain byte-identical including markers."""
    authored = {
        "unit_id": "E1U01",
        "text": "镜头1：远景。@[主角] 站在 @[酒馆] 门口。",
        "duration_seconds": 4,
        "pending_authoring": False,
    }
    rewritten = {"unit_id": "E1U02", "text": "@[主角] 走进 @[酒馆]", "duration_seconds": 4}
    untouched = {"unit_id": "E1U03", "text": "@[主角] 坐在 @[酒馆] 角落", "duration_seconds": 4}
    _write_formal_units(reference_project, [authored, rewritten, untouched])
    before = _formal_units(reference_project)
    formal = _script_path(reference_project)
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={
            "title": "第1集",
            "units": [
                {"text": authored["text"]},
                {"text": "镜头1：中景。@[主角] 走进 @[酒馆]。"},
                {"text": untouched["text"]},
            ],
        },
        violations=[],
        meta={"base_fingerprint": script_review.content_fingerprint(formal)},
    )

    await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    after = _formal_units(reference_project)
    assert after["E1U01"] == before["E1U01"]
    assert after["E1U02"] == {"unit_id": "E1U02", "text": "镜头1：中景。@[主角] 走进 @[酒馆]。", "duration_seconds": 4}
    assert after["E1U03"] == before["E1U03"]
    assert after["E1U03"]["pending_authoring"] is True
    assert not _prompt_authoring_quarantine(reference_project).exists()


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_rejects_stale_formal_baseline(reference_project: Path):
    formal = _script_path(reference_project)
    baseline = script_review.content_fingerprint(formal)
    assert baseline is not None
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "修复稿", "units": [{"text": PROMPT_AUTHORING_UNIT_TEXT}]},
        violations=[],
        meta={"base_fingerprint": baseline, "unit_ids": ["E1U01"]},
    )

    pm = ProjectManager.for_project_dir(reference_project)
    concurrent = pm.load_script(reference_project.name, formal.name)
    concurrent["title"] = "并发修改"
    pm.save_script(reference_project.name, concurrent, formal.name)

    with pytest.raises(ScriptWriteConflict):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(
            episode=1,
            expected_fingerprint=baseline,
        )

    assert pm.load_script(reference_project.name, formal.name)["title"] == "并发修改"
    assert _prompt_authoring_quarantine(reference_project).exists()


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_requires_formal_baseline(reference_project: Path):
    formal_before = _script_path(reference_project).read_bytes()
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "修复稿", "units": [{"text": PROMPT_AUTHORING_UNIT_TEXT}]},
        violations=[],
    )

    with pytest.raises(DraftViolation) as excinfo:
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    assert excinfo.value.code == "formal_revision_missing"
    assert _prompt_authoring_quarantine(reference_project).exists()
    assert _script_path(reference_project).read_bytes() == formal_before


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_reports_again_without_round_limit(reference_project: Path):
    """Repeated violations refresh reports in-place; promotion retries have no convergence-round limit."""
    formal_before = _script_path(reference_project).read_bytes()
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    for _round in range(3):
        with pytest.raises(DraftViolation, match="unregistered_asset"):
            await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)
        assert path.exists()
        assert _script_path(reference_project).read_bytes() == formal_before

    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0]["text"] = "门开了\n@[主角]：{我来了"
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DraftViolation):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)
    refreshed = _json.loads(path.read_text(encoding="utf-8"))
    assert [v["code"] for v in refreshed["violations"]] == ["dialogue_line_syntax"]


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_rejects_schema_breach_with_report(reference_project: Path):
    """Schema-corrupt draft content also returns refreshed reports, matching script_plan promotion without contaminating formal scripts.

    No backend can retry Agent-authored content here. Raising ValueError directly would leave stale violation snapshots, exposing only tool-text Pydantic errors to the Agent."""
    formal_before = _script_path(reference_project).read_bytes()
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0].pop("text")
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DraftViolation, match="schema_invalid"):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    assert path.exists()
    assert _script_path(reference_project).read_bytes() == formal_before
    refreshed = _json.loads(path.read_text(encoding="utf-8"))
    assert [(v["code"], v.get("item_index"), v.get("item_id")) for v in refreshed["violations"]] == [
        ("schema_invalid", 0, "E1U01")
    ]


@pytest.mark.asyncio
async def test_prompt_authoring_duration_off_tier_after_merge_quarantines(
    wide_tier_reference_project: Path, set_video_request_facts
):
    """Tier failures discovered after merging also retain paid expansions in repair drafts.

    prompt_authoring may add/remove unit `@` references, changing effective tiers. Adding usable images to a two-second imageless unit narrows1–16 to3–16; references only narrow, so new invalidity occurs only from adding usable images. This check runs in `_add_metadata` after structure-preserving diff; otherwise paid output stays in memory while errors invite regeneration."""
    set_video_request_facts(
        {
            "r2v": make_video_request_facts(
                route="reference_video",
                generation_type="r2v",
                supported_durations=tuple(range(1, 17)),
                allowed_durations=tuple(range(3, 17)),
            ),
            "i2v": make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                supported_durations=tuple(range(1, 17)),
                allowed_durations=tuple(range(1, 17)),
            ),
        }
    )
    project = wide_tier_reference_project
    _claim_character_sheet(project, "主角")
    _write_formal_units(project, [{"unit_id": "E1U01", "text": "他推门", "duration_seconds": 2}])
    formal_before = _script_path(project).read_bytes()
    with_reference_text = "镜头1：中景，平视。@[主角] 推开门，侧身跨过门槛。"
    gen = ScriptGenerator(project, generator=_fake_prompt_authoring_generator(with_reference_text))

    with pytest.raises(DraftViolation) as excinfo:
        await gen.generate(episode=1)

    assert "生效档位" in str(excinfo.value)
    assert _script_path(project).read_bytes() == formal_before
    envelope = _json.loads(_prompt_authoring_quarantine(project).read_text(encoding="utf-8"))
    assert [v["code"] for v in envelope["violations"]] == ["duration_off_tier"]
    # Draft retains Agent-editable body; remove `@` references to promote again.
    assert envelope["content"]["units"][0]["text"] == with_reference_text


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_revalidates_edited_formal_unit(reference_project: Path):
    """Before promotion, reassess current formal units with production's preflight. Timeline edits while drafts exist must not bypass it: human editors only warn for unregistered @[名称], while structure-preserving diff compares dialogue structure without revalidating current body legality."""
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0]["text"] = PROMPT_AUTHORING_UNIT_TEXT
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    formal = _script_path(reference_project)
    script = _json.loads(formal.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@[路人甲] 推开 @[酒馆] 的门"
    formal.write_text(_json.dumps(script, ensure_ascii=False), encoding="utf-8")
    formal_before = formal.read_bytes()

    with pytest.raises(DraftViolation, match="正式脚本") as excinfo:
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    assert path.exists()
    assert formal.read_bytes() == formal_before
    # Body violations use UI positions with specific reasons, never internal unit IDs.
    assert (excinfo.value.code, excinfo.value.params) == ("draft_formal_unit_invalid", {"position": 1})
    assert "视频单元 1 引用了未登记的资产名" in str(excinfo.value)
    assert "E1U01" not in str(excinfo.value)


async def _quarantine_then_edit_formal_unit(project: Path, **changes: Any) -> tuple[Path, bytes]:
    """Persist a promotable authoring draft, then edit formal unit bodies on timeline; return draft path and edited formal bytes."""
    gen = ScriptGenerator(project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)
    path = _prompt_authoring_quarantine(project)
    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0]["text"] = PROMPT_AUTHORING_UNIT_TEXT
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    formal = _script_path(project)
    script = _json.loads(formal.read_text(encoding="utf-8"))
    script["video_units"][0].update(changes)
    formal.write_text(_json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return path, formal.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "code", "params", "phrase"),
    [
        (
            {"text": "@[主角] 推开 @[酒馆] 的门\n@[主角]：{" + "这是一段非常长的台词" * 6 + "}"},
            "draft_formal_unit_dialogue_overload",
            {"position": 1, "duration_seconds": 4},
            "视频单元 1 的台词念完约需",
        ),
        (
            {"duration_seconds": 5},
            "draft_formal_unit_duration_off_tier",
            {"position": 1, "duration_seconds": 5, "tiers": "4 / 6 / 8"},
            "视频单元 1 的时长 5 秒",
        ),
    ],
    ids=["dialogue_overload", "duration_off_tier"],
)
async def test_promote_prompt_authoring_draft_names_rejected_formal_unit_by_position(
    reference_project: Path, changes: dict[str, Any], code: str, params: dict[str, Any], phrase: str
):
    """Formal-unit preflight failure before promotion still reports a draft violation; codes/params localize as video unit N, without internal IDs."""
    path, formal_before = await _quarantine_then_edit_formal_unit(reference_project, **changes)

    with pytest.raises(DraftViolation) as excinfo:
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    violation = excinfo.value
    assert violation.code == code
    assert {key: value for key, value in (violation.params or {}).items() if key != "spoken_seconds"} == params
    assert phrase in str(violation)
    assert "E1U01" not in str(violation)
    assert path.exists()
    assert _script_path(reference_project).read_bytes() == formal_before


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_without_draft(reference_project: Path):
    with pytest.raises(FileNotFoundError, match="没有可晋升的 prompt_authoring 待修复草稿"):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)


@pytest.mark.asyncio
async def test_rewrite_completed_unit_draft_can_be_hand_fixed_and_adopted(reference_project: Path, monkeypatch):
    original = "镜头1：远景。@[主角] 站在 @[酒馆] 门口。"
    _write_formal_units(
        reference_project,
        [{"unit_id": "E1U01", "text": original, "duration_seconds": 4, "pending_authoring": False}],
    )
    formal = _script_path(reference_project)
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator("镜头1：@[路人甲] 走进 @[酒馆]"),
    )
    with pytest.raises(DraftViolation):
        await gen.generate(
            episode=1,
            entry_ids=["E1U01"],
            rewrite=True,
            overwrite_revision=script_review.content_fingerprint(formal),
        )
    service = EpisodeDraftService(ProjectManager.for_project_dir(reference_project))
    view = await service.get_draft(reference_project.name, 1, "reference_prompt_authoring")
    assert view["item_ids"] == ["E1U01"]
    use_text_generator(monkeypatch, AsyncMock(return_value=_idle_generator()))
    fixed = "镜头1：中景。@[主角] 走进 @[酒馆]。"
    result = await service.save_draft(
        reference_project.name,
        1,
        "reference_prompt_authoring",
        {"title": "修复稿", "units": [{"text": fixed}]},
        view["revision"],
    )
    assert result["adopted"] is True
    assert _formal_units(reference_project)["E1U01"]["text"] == fixed
    assert not _prompt_authoring_quarantine(reference_project).exists()
