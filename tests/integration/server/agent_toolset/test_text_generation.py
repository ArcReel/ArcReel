"""Tests for text_generation."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest

from lib.artifacts.artifact_activation import register_current_artifact
from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import TextOutputTruncatedError
from lib.generation.generation_result import GenerationAction
from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script import script_review
from server.agent_toolset.envelope import json_value
from server.agent_toolset.orientation import GET_VIDEO_CAPABILITIES
from server.text_generation import TextGenerationRequest, _parse_normalized_content
from server.tool_runtime import (
    GenerateScriptPlanRequest,
    TextGenerationResult,
    ToolRequest,
    generate_script_plan,
)
from tests.factories import make_video_request_facts, seed_endpoint_fixed_video_model
from tests.fakes import FakeTextGenerator, use_text_generator
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    nr_source,
    problem_of,
    run_declared_tool,
    rv_source,
    said,
    use_fake_caps,
)

# ---------------------------------------------------------------------------
# get_video_capabilities
# ---------------------------------------------------------------------------


def _as_json(value: Any) -> Any:
    """Agent-visible JSON shape, such as integer keys converted to strings."""
    return json.loads(json.dumps(value, ensure_ascii=False))


async def test_get_video_capabilities_happy(fake_ctx: ToolHarness, video_request_facts) -> None:
    use_fake_caps(fake_ctx, provider_id="fake", supported_durations=[4, 6, 8])
    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    assert out.problem is None
    assert out.value is not None
    assert out.value["provider_id"] == "fake"


async def test_get_video_capabilities_resolves_by_project(fake_ctx: ToolHarness, video_request_facts) -> None:
    """Resolve capabilities by project generation mode; tools take no episode ID, rejecting it as extra before resolution."""
    resolver = use_fake_caps(fake_ctx, provider_id="fake", supported_durations=[4, 6, 8])
    assert (await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})).problem is None
    rejected = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {"episode_id": 3})
    assert rejected.problem is not None
    assert rejected.problem.code == "invalid_request"
    assert resolver.generation_type_calls == [None]


async def test_get_video_capabilities_annotates_reference_unit_tiers(
    fake_ctx: ToolHarness, set_video_request_facts
) -> None:
    """Image tiers use narrowed r2v facts; Agents receive no-image tiers/failures in one place only."""
    set_video_request_facts(
        {
            "r2v": make_video_request_facts(
                route="reference_video", generation_type="r2v", supported_durations=(4, 6, 8), allowed_durations=(8,)
            ),
            "i2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)),
        }
    )
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"
    use_fake_caps(
        fake_ctx,
        provider_id="gemini-aistudio",
        model="veo-3.1-generate-preview",
        supported_durations=[4, 6, 8],
        generation_mode="reference_video",
    )
    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    assert out.problem is None, out
    payload = _as_json(out.value)
    assert payload["reference_unit_durations"] == {
        "with_references": [8],
        "with_references_endpoint_fixed": False,
        "with_references_endpoint_fixed_reason": None,
        "without_references": None,
        "without_references_endpoint_fixed": False,
        "without_references_endpoint_fixed_reason": None,
        "excluded": None,
        "problem": {
            "code": "reference_capability_unavailable",
            "params": {"capability": "i2v"},
            "action": "configure_video_model",
        },
        "units": {},
    }
    # Preserve the full model-declared set, not effective tiers.
    assert payload["supported_durations"] == [4, 6, 8]


async def test_get_video_capabilities_reports_both_successful_buckets(
    fake_ctx: ToolHarness, set_video_request_facts
) -> None:
    """When both buckets succeed, ``reference_unit_durations`` contains both tier sets."""
    set_video_request_facts(
        {
            "r2v": make_video_request_facts(
                route="reference_video", generation_type="r2v", supported_durations=(4, 6, 8), allowed_durations=(8,)
            ),
            "i2v": make_video_request_facts(
                route="reference_video", generation_type="i2v", supported_durations=(5, 10), allowed_durations=(5, 10)
            ),
        }
    )
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"
    use_fake_caps(
        fake_ctx,
        provider_id="gemini-aistudio",
        model="veo-3.1-generate-preview",
        supported_durations=[4, 6, 8],
        generation_mode="reference_video",
    )

    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    payload = _as_json(out.value)

    assert payload["reference_unit_durations"] == {
        "with_references": [8],
        "with_references_endpoint_fixed": False,
        "with_references_endpoint_fixed_reason": None,
        "without_references": [5, 10],
        "without_references_endpoint_fixed": False,
        "without_references_endpoint_fixed_reason": None,
        "excluded": {},
        "problem": None,
        "units": {},
    }


@pytest.mark.parametrize("fixed_bucket", ["i2v", "r2v"])
async def test_get_video_capabilities_reports_endpoint_fixed_per_bucket(
    fake_ctx: ToolHarness, db_factory, fixed_bucket: str
) -> None:
    from lib.config.resolver import ConfigResolver

    fixed_model = await seed_endpoint_fixed_video_model(db_factory, reference_images=True)
    veo = "gemini-vertex/veo-3.1-generate-001"
    fake_ctx.pm.project_payload.update(
        {
            "generation_mode": "reference_video",
            "video_provider_r2v": fixed_model if fixed_bucket == "r2v" else veo,
            "video_provider_i2v": fixed_model if fixed_bucket == "i2v" else veo,
        }
    )
    fake_ctx.config_resolver = ConfigResolver(db_factory)
    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    assert out.problem is None, out
    payload = _as_json(out.value)
    tiers = payload["reference_unit_durations"]
    assert tiers["with_references_endpoint_fixed"] is (fixed_bucket == "r2v")
    assert tiers["without_references_endpoint_fixed"] is (fixed_bucket == "i2v")
    assert tiers["with_references_endpoint_fixed_reason"] == ("endpoint" if fixed_bucket == "r2v" else None)
    assert tiers["without_references_endpoint_fixed_reason"] == ("endpoint" if fixed_bucket == "i2v" else None)
    assert tiers["with_references"] == ([] if fixed_bucket == "r2v" else [8])
    assert tiers["without_references"] == ([] if fixed_bucket == "i2v" else [4, 6, 8])


@pytest.mark.parametrize(
    ("generation_mode", "content_mode"),
    [("storyboard", "drama"), ("reference_video", "ad")],
)
async def test_get_video_capabilities_skips_tiers_off_episode_reference_path(
    fake_ctx: ToolHarness, video_request_facts, generation_mode: str, content_mode: str
) -> None:
    """Non-episode-reference paths omit it: other paths lack per-unit reference states, and ad storyboard durations have no tier enum."""
    use_fake_caps(
        fake_ctx,
        provider_id="gemini-aistudio",
        model="veo-3.1-generate-preview",
        supported_durations=[4, 6, 8],
        generation_mode=generation_mode,
        content_mode=content_mode,
    )
    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    payload = _as_json(out.value)
    assert "reference_unit_durations" not in payload


async def test_get_video_capabilities_shares_rest_resolution_entry(fake_ctx: ToolHarness, video_request_facts) -> None:
    """Agent tools pass closure projects to ``ConfigResolver.video_capabilities_for_project``.

    Resolvers never reload global project directories by name, preserving nondefault projects_root sessions.
    """
    resolver = use_fake_caps(fake_ctx, provider_id="kling", model="kling-v3-omni", supported_durations=[5])
    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    assert out.problem is None, out
    assert _as_json(out.value)["model"] == "kling-v3-omni"
    assert resolver.project_payloads == [fake_ctx.pm.project_payload]


async def test_get_video_capabilities_duration_constraints_come_from_request_facts(
    fake_ctx: ToolHarness, set_video_request_facts
) -> None:
    """Agent ``duration_constraints`` comes from project main-bucket video-request facts."""
    set_video_request_facts(
        make_video_request_facts(
            resolution="1080p", allowed_durations=(8,), excluded_durations=((4, "resolution"), (6, "resolution"))
        )
    )
    use_fake_caps(
        fake_ctx, provider_id="gemini-aistudio", model="veo-3.1-generate-preview", supported_durations=[4, 6, 8]
    )
    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    assert out.problem is None, out
    payload = _as_json(out.value)
    assert payload["duration_constraints"] == {
        "resolution": "1080p",
        "uses_reference_images": False,
        "allowed": [8],
        "excluded": {"4": "resolution", "6": "resolution"},
    }


async def test_get_video_capabilities_reports_request_facts_failure(
    fake_ctx: ToolHarness, set_video_request_facts
) -> None:
    set_video_request_facts(
        VideoRequestFactsFailure(
            "video_supported_durations_incompatible",
            (("provider", "gemini-aistudio"), ("model", "veo-3.1-generate-preview"), ("resolution", "4k")),
        )
    )
    use_fake_caps(
        fake_ctx, provider_id="gemini-aistudio", model="veo-3.1-generate-preview", supported_durations=[4, 6, 8]
    )
    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    assert out.problem is not None
    assert out.problem is not None
    problem = out.problem.model_dump()
    assert problem["code"] == "video_supported_durations_incompatible"
    assert problem["params"] == {"provider": "gemini-aistudio", "model": "veo-3.1-generate-preview", "resolution": "4k"}
    assert problem["action"] == "configure_video_model"
    assert "video_supported_durations_incompatible（provider=gemini-aistudio" in problem["detail"]


async def test_get_video_capabilities_error(fake_ctx: ToolHarness) -> None:
    use_fake_caps(fake_ctx, error=FileNotFoundError("missing project.json"))
    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})
    assert out.problem is not None


@pytest.mark.parametrize(
    ("content_mode", "code"), [("ad", "operation_not_admitted"), ("unsupported", "generation_refused")]
)
async def test_generate_script_plan_rejects_inapplicable_content_modes(
    fake_ctx: ToolHarness, content_mode: str, code: str
) -> None:
    fake_ctx.pm.project_payload["content_mode"] = content_mode
    resolver = use_fake_caps(fake_ctx)
    caller_thread = threading.get_ident()

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})

    assert out.problem is not None
    assert problem_of(out).code == code
    assert resolver.generation_type_calls == []
    assert fake_ctx.pm.project_load_threads
    assert all(thread != caller_thread for thread in fake_ctx.pm.project_load_threads)


@pytest.mark.parametrize("name", ["generate_episode_script", "generate_script_plan", "confirm_script_review"])
@pytest.mark.parametrize("bad", [0, True, "1"])
async def test_text_tools_reject_a_non_positive_or_non_integer_episode(fake_ctx: ToolHarness, name: str, bad) -> None:
    out = await run_declared_tool(name, fake_ctx, {"episode_id": bad})
    assert problem_of(out).code == "invalid_request"


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "1"])
def test_text_generation_request_rejects_non_positive_or_non_integer_episode(bad: Any) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        TextGenerationRequest(episode=bad)


def _write_formal_script(project_path: Path, episode: int = 1, *, pending: bool = True) -> None:
    """Formal scripts default to one pending-authoring item, establishing authoring admission."""
    scripts = project_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    segments = (
        [{"segment_id": f"E{episode}S01", "novel_text": "原文", "characters_in_segment": [], "pending_authoring": True}]
        if pending
        else []
    )
    (scripts / f"episode_{episode}.json").write_text(
        json.dumps({"episode": episode, "title": "番外", "segments": segments}), encoding="utf-8"
    )


def _prepare_script_admission(project_path: Path) -> None:
    """Tool cases supply admission through real projects and artifact manifests."""
    project = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    project.setdefault("generation_mode", "storyboard")
    project.setdefault("episodes", [{"episode": 1, "script_file": "scripts/episode_1.json"}])
    for entry in project["episodes"]:
        entry.setdefault("script_file", f"scripts/episode_{entry['episode']}.json")
    (project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    ProjectArtifactManifestAdapter(project_path).replace_entries_atomically({})
    script_path = project_path / "scripts" / "episode_1.json"
    if script_path.exists():
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script.setdefault("title", "番外")
        script_path.write_text(json.dumps(script), encoding="utf-8")
        register_current_artifact(project_path, ArtifactKey.episode_script(1))


async def test_generate_episode_script_dry_run(fake_ctx: ToolHarness, monkeypatch) -> None:
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    _write_formal_script(project_path)
    (project_path / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "narration"}), encoding="utf-8"
    )

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}

        def __init__(self, _path, **_kwargs):
            pass

        async def build_prompt(self, _episode, *, instructions=None, **_kwargs):
            return "fake prompt"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "dry_run": True})
    assert out.problem is None
    assert "fake prompt" in said(out)


async def test_generate_episode_script_without_formal_script_is_refused(fake_ctx: ToolHarness) -> None:
    """Non-ad projects without formal scripts reject authoring and direct content confirmation."""
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "narration"}), encoding="utf-8"
    )
    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    assert "尚无正式脚本" in said(out)
    assert "内容确认" in said(out)


async def test_generate_episode_script_writes_to_default_project_scripts(fake_ctx: ToolHarness, monkeypatch) -> None:
    """output is retired; ScriptGenerator determines output paths internally, never handler-controlled by Agents."""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    _write_formal_script(project_path)
    (project_path / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "narration"}), encoding="utf-8"
    )

    captured: dict[str, dict[str, Any]] = {"calls": {}}

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}
        content_mode = "narration"

        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **kwargs) -> Path:
            captured["calls"] = kwargs
            kwargs["rewritten_entry_ids"].append("E1S01")
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)

    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})
    assert out.problem is None
    # Handlers omit output_path; ScriptGenerator decides destinations.
    assert "output_path" not in captured["calls"]
    assert "scripts/episode_1.json" in said(out)
    assert str(project_path) not in said(out)


async def test_generate_episode_script_ad_skips_script_plan(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Ad one-click generation requires neither intermediate script_plan nor formal scripts; both may be absent without errors."""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "content_mode": "ad",
                "target_duration": 30,
                "brief": "夏季新品",
            }
        ),
        encoding="utf-8",
    )

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}
        content_mode = "ad"

        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **_kwargs) -> Path:
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})
    assert out.problem is None


async def test_generate_episode_script_entry_ids_reach_the_generator(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Pass entry_ids unchanged to ScriptGenerator.generate; receipts list authored entries."""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "content_mode": "ad",
                "target_duration": 30,
                "brief": "夏季新品",
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}
        content_mode = "ad"

        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **kwargs) -> Path:
            captured.update(kwargs)
            kwargs["rewritten_entry_ids"].append("E1S02")
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)

    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "entry_ids": ["E1S02"]})
    assert out.problem is None
    assert captured["entry_ids"] == ("E1S02",)
    assert "E1S02" in said(out)


async def test_generate_episode_script_rewrite_over_existing_prompts_returns_the_loss_list(
    fake_ctx: ToolHarness,
) -> None:
    """Explicit Agent rewrites reject before submission through the same service, returning server loss manifests/acceptance tokens without submitting tasks."""
    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "narration"}), encoding="utf-8"
    )
    scripts = project_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    segment = {
        "segment_id": "E1S01",
        "novel_text": "原文。",
        "characters_in_segment": [],
        "image_prompt": "手写图片",
        "video_prompt": None,
    }
    (scripts / "episode_1.json").write_text(json.dumps({"episode": 1, "segments": [segment]}), encoding="utf-8")

    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool(
        "generate_episode_script", fake_ctx, {"episode_id": 1, "entry_ids": ["E1S01"], "rewrite": True}
    )

    assert out.problem is not None
    assert out.problem.code == "prompt_overwrite_required"
    overwrite = (out.problem.params or {})["prompt_overwrite"]
    assert overwrite["revision"] == script_review.published_revision(
        script_review.content_fingerprint(scripts / "episode_1.json")
    )
    assert overwrite["entries"] == [{"id": "E1S01", "fields": ["image_prompt"]}]
    assert overwrite["text"] in said(out)


@pytest.mark.parametrize(
    ("content_mode", "redo_hint"),
    [("narration", "重跑脚本规划"), ("ad", "regenerate=true")],
)
async def test_generate_episode_script_without_pending_entries_says_how_to_rewrite(
    fake_ctx: ToolHarness, monkeypatch, content_mode: str, redo_hint: str
) -> None:
    """No pending-authoring items means no admission/model calls; shared production-status reason codes guide targeted rewrites or whole rebuilds."""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    items_key = "shots" if content_mode == "ad" else "segments"
    (project_path / "scripts").mkdir(parents=True, exist_ok=True)
    (project_path / "scripts" / "episode_1.json").write_text(
        json.dumps({"episode": 1, "content_mode": content_mode, items_key: []}), encoding="utf-8"
    )
    (project_path / "project.json").write_text(
        json.dumps(
            {"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": content_mode, "target_duration": 30}
        ),
        encoding="utf-8",
    )

    class _UnreachableGenerator:
        @classmethod
        async def create(cls, _path, **_kwargs):
            raise AssertionError("准入不成立时不应构造生成器")

    monkeypatch.setattr(mod, "ScriptGenerator", _UnreachableGenerator)
    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})

    problem = problem_of(out)
    assert problem.code == "operation_not_admitted"
    assert problem.params == {"operation": "author_prompts", "reason": "no_pending_authoring"}
    message = said(out)
    assert "没有待编写的条目" in message
    assert "entry_ids" in message
    assert redo_hint in message


async def test_generate_episode_script_reports_unbound_scene_mentions(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Rewritten visual @[名称] mismatching references yields warnings in receipts for written scripts."""
    from lib.script.storyboard_mentions import WARN_STORYBOARD_MENTION_UNBOUND
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "content_mode": "ad",
                "target_duration": 30,
                "brief": "夏季新品",
                "characters": {"主播": {"description": "出镜"}},
            }
        ),
        encoding="utf-8",
    )
    fake_ctx.pm.project_payload["characters"] = {"主播": {"description": "出镜"}}
    fake_ctx.pm.script_payload = {
        "episode": 1,
        "content_mode": "ad",
        "shots": [
            {"shot_id": "E1S01", "characters_in_shot": ["主播"], "image_prompt": "@[主播]举起@[神秘商品]"},
            {"shot_id": "E1S02", "characters_in_shot": [], "image_prompt": "@[主播]微笑"},
        ],
    }

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}

        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **kwargs) -> Path:
            kwargs["rewritten_entry_ids"].append("E1S02")
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)

    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "entry_ids": ["E1S02"]})

    assert out.problem is None
    assert isinstance(out.value, TextGenerationResult)
    payload = json_value(out.value)
    assert payload["warnings"] == [
        {"key": WARN_STORYBOARD_MENTION_UNBOUND, "params": {"unit_id": "E1S02", "name": "主播"}}
    ]
    assert "主播" in payload["message"]


async def test_generate_episode_script_unknown_entry_id_is_refused_not_internal(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Requested entries absent from formal scripts report generation rejection, not internal_error encouraging unchanged retries."""
    from lib.script.script_generator import PromptAuthoringTargetError
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "content_mode": "ad",
                "target_duration": 30,
                "brief": "夏季新品",
            }
        ),
        encoding="utf-8",
    )

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}

        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **_kwargs) -> Path:
            raise PromptAuthoringTargetError("entry_ids 不在第 1 集正式脚本内: ['E9U99']")

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "entry_ids": ["E9U99"]})

    assert out.problem is not None
    text = said(out)
    assert "编写范围无效" in text
    assert "generate_episode_script 失败" not in text


def _narration_formal_script(project_path: Path) -> None:
    _write_formal_script(project_path)
    (project_path / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "narration"}), encoding="utf-8"
    )
    _prepare_script_admission(project_path)


async def test_generate_episode_script_unit_rejected_before_the_model_is_an_input_problem(
    fake_ctx: ToolHarness, monkeypatch, set_video_request_facts
) -> None:
    """Failed formal-unit authoring prechecks expose original problem codes/parameters as modify-input problems, not retryable internal_error."""
    set_video_request_facts(make_video_request_facts(route="reference_video", generation_type="i2v"))
    fake_ctx.pm.project_payload.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "characters": {"主角": {"description": "d"}},
            "scenes": {"酒馆": {"description": "d"}},
            "props": {},
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json", "generation_mode": "reference_video"}],
        }
    )
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(fake_ctx.pm.project_payload, ensure_ascii=False), encoding="utf-8"
    )
    # Shortened timeline duration no longer fits dialogue; block unit 2 before text calls.
    long_line = "他站在门口足足看了半晌才缓缓开口说出这句迟到了整整十年的道歉与告别" * 2
    scripts = fake_ctx.project_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "第1集",
                "content_mode": "narration",
                "video_units": [
                    {"unit_id": "E1U01", "text": "@[主角] 推开 @[酒馆] 的门", "duration_seconds": 4},
                    {
                        "unit_id": "E1U04",
                        "text": f"@[主角] 推开 @[酒馆] 的门\n@[主角]：{{{long_line}}}",
                        "duration_seconds": 4,
                        "pending_authoring": True,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _prepare_script_admission(fake_ctx.project_path)
    model = FakeTextGenerator(AssertionError("预判拦下的单元不应调用文本模型"))
    use_text_generator(monkeypatch, model.create)

    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1})

    problem = problem_of(out).model_dump()
    assert (problem["code"], problem["action"]) == (
        "prompt_authoring_dialogue_overload",
        GenerationAction.FIX_INPUT.value,
    )
    # Use UI position 视频单元 2 and unchanged parameters for Agent relay.
    assert {key: problem["params"][key] for key in ("unit_id", "position", "duration_seconds")} == {
        "unit_id": "E1U04",
        "position": 2,
        "duration_seconds": 4,
    }
    assert float(problem["params"]["spoken_seconds"]) > 4
    assert "generate_episode_script 失败" not in said(out)
    assert model.requests == []


async def test_generate_episode_script_without_anything_to_write_marks_the_result(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Missing-only requests without pending items call no text model and return no-op markers prompting Web overwrite selection."""
    _narration_formal_script(fake_ctx.project_path)
    script_path = fake_ctx.project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"][0].update(pending_authoring=False, image_prompt="手写画面", video_prompt="手写镜头")
    script_path.write_text(json.dumps(script), encoding="utf-8")
    original = script_path.read_bytes()

    model = FakeTextGenerator(AssertionError("没有待编写条目时不应调用文本模型"))
    use_text_generator(monkeypatch, model.create)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "entry_ids": ["E1S01"]})

    assert out.problem is None
    assert isinstance(out.value, TextGenerationResult)
    assert out.value.no_pending_authoring is True
    assert script_path.read_bytes() == original
    assert model.requests == []
    assert "没有待编写的条目" in out.value.message


async def test_generate_episode_script_facts_failure_reports_the_real_problem(
    fake_ctx: ToolHarness, set_video_request_facts
) -> None:
    """Unresolved storyboard video facts report actual codes/parameters and provider-configuration guidance, not internal_error retries."""
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "content_mode": "ad",
                "target_duration": 30,
                "brief": "夏季新品",
            }
        ),
        encoding="utf-8",
    )
    set_video_request_facts(
        VideoRequestFactsFailure(
            "video_supported_durations_incompatible",
            (("provider", "p"), ("model", "m"), ("resolution", "1080p"), ("capability", "i2v")),
        )
    )

    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "dry_run": True})

    assert out.problem is not None
    problem = problem_of(out).model_dump()
    assert (problem["code"], problem["action"], problem["params"]) == (
        "video_supported_durations_incompatible",
        "configure_provider",
        {"provider": "p", "model": "m", "resolution": "1080p", "capability": "i2v"},
    )
    assert "video_supported_durations_incompatible（provider=p, model=m, resolution=1080p" in said(out)


@pytest.mark.parametrize("scope", ["all", "stale", None])
async def test_generate_episode_script_rejects_removed_scope_with_migration_note(
    fake_ctx: ToolHarness, scope: str | None
) -> None:
    """scope is retired; reject all supplied values, directing default scope or entry_ids."""
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "scope": scope})
    assert problem_of(out).code == "invalid_request"
    text = said(out)
    assert "scope 参数已取消" in text
    assert "entry_ids" in text


async def test_generate_episode_script_does_not_wait_for_script_plan_review(fake_ctx: ToolHarness) -> None:
    """Authoring reads formal scripts only; unconfirmed replanning does not block, and real generators render from formal scripts."""
    project_path = fake_ctx.project_path
    scripts = project_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    pending_segment = {
        "segment_id": "E1S01",
        "duration_seconds": 4,
        "novel_text": "张三推门走进酒馆。",
        "characters_in_segment": [],
        "image_prompt": None,
        "video_prompt": None,
        "pending_authoring": True,
    }
    (scripts / "episode_1.json").write_text(
        json.dumps({"episode": 1, "content_mode": "narration", "segments": [pending_segment]}, ensure_ascii=False),
        encoding="utf-8",
    )
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_segments.json").write_text("rerun script_plan", encoding="utf-8")
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "narration",
        "episodes": [
            {"episode": 1, "script_plan_review": {"fingerprint": "sha256-v1:" + "0" * 64, "confirmed_at": "t"}}
        ],
    }
    (project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    assert script_review.review_status(project_path, project, 1) == "pending_review"

    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1, "dry_run": True})

    assert out.problem is None, out
    assert "张三推门走进酒馆。" in said(out)


def test_parse_normalized_content_uses_dynamic_duration_schema() -> None:
    """_parse_normalized_content reuses supported_durations dynamic schemas: valid durations pass models
    and default completion, while off-enum values raise ValueError rather than static ge=1/le=60 acceptance
    or degraded persistence of unvalidated content."""
    from lib.script.script_models import build_drama_normalized_script_model

    model = build_drama_normalized_script_model([4, 6, 8])
    base_scene = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "characters_in_scene": ["林清"],
        "scene_description": "林清立于窗前。",
    }

    valid = _parse_normalized_content(json.dumps({"title": "t", "scenes": [base_scene]}), model)
    # Valid durations pass models and fill DramaSceneContent defaults, including empty source_text.
    assert valid["scenes"][0]["duration_seconds"] == 8
    assert valid["scenes"][0]["source_text"] == ""

    bad = {**base_scene, "duration_seconds": 5}  # 5 is outside supported_durations.
    # Off-enum dynamic schema raises ValueError, never persisting unvalidated formal script_plan.
    with pytest.raises(ValueError, match="script_plan 规范化内容结构校验失败"):
        _parse_normalized_content(json.dumps({"title": "t", "scenes": [bad]}), model)


async def test_fetch_storyboard_durations_raises_typed_code_when_facts_fail(set_video_request_facts) -> None:
    """Unresolvable storyboard-bucket facts raise their problem codes without fallback tier sets."""
    from lib.generation.video_request_facts import VideoRequestFactsError
    from server import text_generation as mod

    set_video_request_facts(VideoRequestFactsFailure("video_capability_unavailable", (("capability", "i2v"),)))

    with pytest.raises(VideoRequestFactsError) as exc:
        await mod.fetch_storyboard_durations({})

    assert exc.value.code == "video_capability_unavailable"
    assert exc.value.params == {"capability": "i2v"}


async def test_fetch_storyboard_durations_drops_out_of_range_default(set_video_request_facts) -> None:
    """Saved default_duration outside narrowed sets becomes None/auto, without failing the whole tool.

    ``build_normalize_prompt`` fails loudly on nonmember defaults; saved 4 seconds at 720p becomes invalid
    after Veo narrows 1080p to [8], otherwise normalize_drama_script would raise ValueError.
    """
    from server import text_generation as mod

    set_video_request_facts(make_video_request_facts(supported_durations=(4, 6, 8), allowed_durations=(8,)))

    assert await mod.fetch_storyboard_durations({"default_duration": 4}) == (None, [8])
    assert await mod.fetch_storyboard_durations({"default_duration": 8}) == (8, [8])
    assert await mod.fetch_storyboard_durations({"default_duration": "8"}) == (8, [8])
    assert await mod.fetch_storyboard_durations({"default_duration": True}) == (None, [8])


async def test_fetch_storyboard_durations_borrows_planning_tiers_when_endpoint_fixed(set_video_request_facts) -> None:
    """Endpoint-fixed buckets have no selectable tiers; script planning borrows shared planning tiers for content length."""
    from lib.generation.video_request_facts import ENDPOINT_FIXED_PLANNING_DURATIONS
    from server import text_generation as mod

    set_video_request_facts(
        make_video_request_facts(supported_durations=(), allowed_durations=(), duration_endpoint_fixed=True)
    )

    default, durations = await mod.fetch_storyboard_durations({"default_duration": 8})
    assert durations == ENDPOINT_FIXED_PLANNING_DURATIONS
    assert default == 8


async def test_fetch_storyboard_durations_reads_the_bucket_of_the_generation_mode(set_video_request_facts) -> None:
    """Storyboard buckets follow project modes: reference-to-video uses r2v, others i2v."""
    from server import text_generation as mod

    set_video_request_facts(
        {
            "i2v": make_video_request_facts(
                generation_type="i2v", supported_durations=(4, 6, 8), allowed_durations=(4, 6)
            ),
            "r2v": make_video_request_facts(
                generation_type="r2v", supported_durations=(4, 6, 8), allowed_durations=(8,)
            ),
        }
    )

    assert (await mod.fetch_storyboard_durations({"generation_mode": "storyboard"}))[1] == [4, 6]
    assert (await mod.fetch_storyboard_durations({"generation_mode": "reference_video"}))[1] == [8]


async def test_normalize_drama_script_dry_run(fake_ctx: ToolHarness, video_request_facts) -> None:
    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("从前有座山", encoding="utf-8")

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})
    assert out.problem is None
    assert "DRY RUN" in said(out)


async def test_normalize_drama_script_projects_durable_inputs_once(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    from lib.artifacts import artifact_provenance

    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("从前有座山", encoding="utf-8")
    calls = 0
    original = artifact_provenance.project_script_plan_prompt_inputs

    def counted_projection(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(artifact_provenance, "project_script_plan_prompt_inputs", counted_projection)

    result = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})

    assert result.problem is None, result
    assert calls == 1


async def test_normalize_drama_script_wires_target_language(fake_ctx: ToolHarness, video_request_facts) -> None:
    """normalize passes project source_language as build_normalize_prompt target_language;
    non-Chinese script_plan languages follow it instead of always defaulting to Chinese."""

    # Tools read projects through ctx.pm.load_project; source_language alone determines output language.
    fake_ctx.pm.project_payload["source_language"] = "English"
    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("once upon a time", encoding="utf-8")

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})
    assert out.problem is None
    assert "English" in said(out)


async def test_normalize_drama_script_rejects_empty_scenes(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Empty normalize scenes return tool errors, never successful empty plans on disk, matching _load_drama_script_plan_content."""

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("从前有座山", encoding="utf-8")

    class _EmptyGenerator:
        async def generate(self, _request, project_name=None):
            class _R:
                text = json.dumps({"title": "第一集", "scenes": []}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None, **kwargs):
        return _EmptyGenerator()

    use_text_generator(monkeypatch, fake_create)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})
    assert out.problem is not None
    # Do not persist empty scenes destined to fail generation.
    assert not (project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json").exists()


async def test_normalize_drama_script_injects_episode_into_prompt(fake_ctx: ToolHarness, video_request_facts) -> None:
    """Tools inject episode into build_normalize_prompt to prevent incorrect LLM E\\d+ prefixes."""

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter2.txt").write_text("第二集开场", encoding="utf-8")

    out = await run_declared_tool(
        "generate_script_plan", fake_ctx, {"episode_id": 2, "dry_run": True, "source": "source/chapter2.txt"}
    )
    assert out.problem is None, out
    prompt_text = said(out)
    assert "E2S01" in prompt_text
    assert "episode 2" in prompt_text or "E2S{two-digit sequence}" in prompt_text
    assert "E1S01" not in prompt_text


async def test_normalize_drama_script_injects_episode_outline(fake_ctx: ToolHarness, video_request_facts) -> None:
    """Episode outlines (story beats/hooks) enter normalize prompts with script_plan (ADR 0041)."""

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("从前有座山", encoding="utf-8")
    fake_ctx.pm.project_payload["episodes"] = [
        {
            "episode": 1,
            "title": "初入江湖",
            "hook": "少年坠崖生死未卜",
            "outline": {"story_beats": ["少年下山"], "next_episode_teaser": None},
            "source_origin": "own",
        }
    ]

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})
    assert out.problem is None, out
    prompt_text = said(out)
    assert "少年下山" in prompt_text
    assert "少年坠崖生死未卜" in prompt_text


async def test_normalize_drama_script_passes_project_name_to_backend(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    """Pass ctx.project_name to TextGenerator.create/generate,
    preserving project text-tier overrides and usage-tracking project attribution."""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("从前有座山", encoding="utf-8")

    captured: dict[str, Any] = {}

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}

        async def generate(self, _request, project_name=None):
            captured["generate_project_name"] = project_name

            class _R:
                # script_plan yields DramaNormalizedScript structured JSON, not Markdown tables.
                text = json.dumps(
                    {
                        "title": "第一集",
                        "scenes": [
                            {
                                "scene_id": "E1S01",
                                "duration_seconds": 4,
                                "segment_break": False,
                                "characters_in_scene": [],
                                "scenes": [],
                                "props": [],
                                "scene_description": "山中清晨",
                                "utterances": [],
                                "source_text": "从前有座山",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

            return _R()

    async def fake_create(task_type, project_name=None, **kwargs):
        captured["task_type"] = task_type
        captured["create_project_name"] = project_name
        captured["purpose"] = kwargs.get("purpose")
        return _FakeGenerator()

    use_text_generator(monkeypatch, fake_create)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})

    assert out.problem is None, out
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["purpose"] is CallPurpose.SCRIPT_GENERATION
    assert captured["create_project_name"] == "demo", (
        f"normalize_drama_script 必须向 TextGenerator.create 传入 project_name，"
        f"实际传入: {captured.get('create_project_name')!r}"
    )
    assert captured["generate_project_name"] == "demo", (
        f"normalize_drama_script 必须向 TextGenerator.generate 传入 project_name，"
        f"实际传入: {captured.get('generate_project_name')!r}"
    )
    assert "drafts/episode_1/script_plan_normalized_script.json" in said(out)
    assert str(project_path) not in said(out)


async def test_normalize_drama_script_registers_the_frozen_explicit_source_basis(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifacts.artifact_provenance import build_script_plan_basis

    project = {
        **fake_ctx.pm.project_payload,
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
    }
    fake_ctx.pm.project_payload = project
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_path = fake_ctx.project_path / "source" / "selected.txt"
    source_path.parent.mkdir(parents=True)
    frozen_source = "被显式选中的生成原文"
    source_path.write_text(frozen_source, encoding="utf-8")
    expected = build_script_plan_basis(frozen_source, episode=1, project=project)

    class _Generator:
        async def generate(self, _request, project_name=None):
            # Other writers change files during provider waits outside the tested call's event loop.
            await asyncio.to_thread(source_path.write_text, "等待供应商期间改过的原文", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest
            await asyncio.to_thread(project_file.write_text, json.dumps(latest, ensure_ascii=False), encoding="utf-8")
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {
                            "title": "第一集",
                            "scenes": [
                                {
                                    "scene_id": "E1S01",
                                    "duration_seconds": 4,
                                    "segment_break": False,
                                    "characters_in_scene": [],
                                    "scenes": [],
                                    "props": [],
                                    "scene_description": "山中清晨",
                                    "utterances": [],
                                    "source_text": frozen_source,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return _Generator()

    use_text_generator(monkeypatch, fake_create)

    result = await run_declared_tool(
        "generate_script_plan",
        fake_ctx,
        {"episode_id": 1, "source": "source/selected.txt"},
    )

    assert result.problem is None, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_script_plan(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_normalize_drama_script_preserves_legacy_request_basis_when_manifest_activates(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:
    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifacts.artifact_provenance import build_script_plan_basis

    project = {
        **fake_ctx.pm.project_payload,
        "schema_version": 7,
        "title": "项目",
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
        "overview": {},
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    }
    fake_ctx.pm.project_payload = project
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    selected_source = source_dir / "selected.txt"
    selected_source.write_text("实际发送给供应商的原文", encoding="utf-8")
    (source_dir / "episode_1.txt").write_text("激活器可重建的另一份原文", encoding="utf-8")
    expected = build_script_plan_basis("实际发送给供应商的原文", episode=1, project=project)

    class _Generator:
        async def generate(self, _request, project_name=None):
            activated = {**project, "schema_version": 8}
            fake_ctx.pm.project_payload = activated
            await asyncio.to_thread(
                project_file.write_text, json.dumps(activated, ensure_ascii=False), encoding="utf-8"
            )
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {
                            "title": "第一集",
                            "scenes": [
                                {
                                    "scene_id": "E1S01",
                                    "duration_seconds": 4,
                                    "segment_break": False,
                                    "characters_in_scene": [],
                                    "scenes": [],
                                    "props": [],
                                    "scene_description": "山中清晨",
                                    "utterances": [],
                                    "source_text": "实际发送给供应商的原文",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return _Generator()

    use_text_generator(monkeypatch, fake_create)

    # Legacy preupgrade projects reject at tool-entry verdicts; call handlers directly here to observe retained generation-request bases.
    result = await generate_script_plan(
        ToolRequest(GenerateScriptPlanRequest(episode_id=1, source="source/selected.txt")),
        fake_ctx.scope,
        fake_ctx.caller,
        fake_ctx.services,
    )

    assert result.problem is None, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_script_plan(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_normalize_drama_script_marks_mixed_machine_candidate_before_review(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts
) -> None:

    project_path = fake_ctx.project_path
    fake_ctx.pm.project_payload["characters"]["阿离"] = {"description": "少女，青衣"}
    source_dir = project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("从前有座山", encoding="utf-8")

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}

        async def generate(self, _request, project_name=None):
            class _Result:
                text = json.dumps(
                    {
                        "title": "第一集",
                        "scenes": [
                            {
                                "scene_id": "E1S01",
                                "duration_seconds": 4,
                                "segment_break": False,
                                "characters_in_scene": ["阿离"],
                                "scenes": [],
                                "props": [],
                                "scene_description": "阿离站在山门前。",
                                "utterances": [
                                    {"kind": "dialogue", "speaker": "阿离", "text": "我回来了。"},
                                    {"kind": "voiceover", "speaker": None, "text": "三年后。"},
                                ],
                                "source_text": "三年后，阿离回到山门。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

            return _Result()

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return _FakeGenerator()

    use_text_generator(monkeypatch, fake_create)

    result = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})

    assert result.problem is None, result
    saved = json.loads(
        (project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json").read_text(encoding="utf-8")
    )
    assert saved["scenes"][0]["needs_replan"] is True
    assert [utterance["text"] for utterance in saved["scenes"][0]["utterances"]] == ["我回来了。", "三年后。"]


async def test_normalize_drama_script_defaults_to_the_episode_derived_source(
    fake_ctx: ToolHarness, video_request_facts
) -> None:
    """Without source, read only this episode's derived text; whole sources and other episode files under ``source/`` never enter prompts.

    The directory holds both whole sources and episode derivatives; including all would give every episode identical source.
    """
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("本集派生源文", encoding="utf-8")
    (source_dir / "novel.txt").write_text("整本小说原文", encoding="utf-8")
    (source_dir / "episode_2.txt").write_text("第二集派生源文", encoding="utf-8")

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})

    assert out.problem is None, out
    prompt_text = said(out)
    assert "本集派生源文" in prompt_text
    assert "整本小说原文" not in prompt_text
    assert "第二集派生源文" not in prompt_text


async def test_normalize_drama_script_rejects_an_empty_explicit_source(fake_ctx: ToolHarness) -> None:
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("本集派生源文", encoding="utf-8")

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "source": "", "dry_run": True})

    assert out.problem is not None
    assert "源文件路径不能为空" in problem_of(out).detail


async def test_normalize_drama_script_treats_a_default_source_symlink_escape_as_no_episode_source(
    fake_ctx: ToolHarness,
) -> None:
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    outside = fake_ctx.data_root / "outside.txt"
    outside.write_text("项目外内容", encoding="utf-8")
    (source_dir / "episode_1.txt").symlink_to(outside)

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})

    problem = problem_of(out)
    assert problem.params == {"operation": "prepare_script_plan", "reason": "episode_source_missing"}
    assert "项目外内容" not in problem.detail
    assert "source/episode_1.txt" in problem.detail


async def test_normalize_drama_script_refuses_when_the_episode_derived_source_is_missing(
    fake_ctx: ToolHarness,
    video_request_facts,
) -> None:
    """Missing episode derivatives error with rebuilding paths, never falling back to directory whole sources unrelated to this episode."""
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "novel.txt").write_text("整本小说原文", encoding="utf-8")

    out = await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True})

    assert out.problem is not None
    detail = problem_of(out).detail
    assert "source/episode_1.txt" in detail
    assert "plan_episodes" in detail


async def test_normalize_drama_script_injects_instructions(fake_ctx: ToolHarness, video_request_facts) -> None:
    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("从前有座山", encoding="utf-8")

    out = await run_declared_tool(
        "generate_script_plan", fake_ctx, {"episode_id": 1, "dry_run": True, "instructions": "打斗场面多拆几个短镜头"}
    )
    assert out.problem is None, out
    prompt_text = said(out)
    assert "# Additional instructions" in prompt_text
    assert "打斗场面多拆几个短镜头" in prompt_text


async def test_generate_episode_script_forwards_instructions(fake_ctx: ToolHarness, monkeypatch) -> None:
    """Handlers pass instructions unchanged to ScriptGenerator on both dry_run and generation paths."""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    _write_formal_script(project_path)
    (project_path / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "narration"}), encoding="utf-8"
    )

    captured: dict[str, Any] = {}

    class _FakeGenerator:
        project_json: ClassVar[dict[str, Any]] = {}
        content_mode = "narration"

        def __init__(self, _path, **_kwargs):
            pass

        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls(_path)

        async def build_prompt(self, _episode, *, instructions=None, **_kwargs):
            captured["build_prompt"] = instructions
            return "fake prompt"

        async def generate(self, *, episode, instructions=None, **_kwargs):
            captured["generate"] = instructions
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)

    _prepare_script_admission(fake_ctx.project_path)
    out = await run_declared_tool(
        "generate_episode_script", fake_ctx, {"episode_id": 1, "dry_run": True, "instructions": "偏好特写镜头"}
    )
    assert out.problem is None, out
    assert captured["build_prompt"] == "偏好特写镜头"

    out = await run_declared_tool(
        "generate_episode_script", fake_ctx, {"episode_id": 1, "instructions": "偏好特写镜头"}
    )
    assert out.problem is None, out
    assert captured["generate"] == "偏好特写镜头"


async def test_get_video_capabilities_annotates_each_formal_unit(
    fake_ctx: ToolHarness, set_video_request_facts
) -> None:
    """Agent per-unit annotations share server availability-based bucket decisions; registered characters without images select i2v."""
    set_video_request_facts(
        {
            "i2v": make_video_request_facts(
                route="reference_video", generation_type="i2v", supported_durations=(5, 10), allowed_durations=(5, 10)
            ),
            "r2v": make_video_request_facts(
                route="reference_video", generation_type="r2v", supported_durations=(4, 6, 8), allowed_durations=(8,)
            ),
        }
    )
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"
    fake_ctx.pm.script_payload = {
        "episode": 1,
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "video_units": [
            {"unit_id": "E1U1", "text": "镜头1：@[张三] 推门", "duration_seconds": 8},
            {"unit_id": "E1U2", "text": "镜头2：空镜", "duration_seconds": 8},
        ],
    }
    use_fake_caps(
        fake_ctx,
        provider_id="gemini-aistudio",
        model="veo-3.1-generate-preview",
        supported_durations=[4, 6, 8],
        generation_mode="reference_video",
    )
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool(GET_VIDEO_CAPABILITIES, fake_ctx, {})

    assert out.problem is None, out
    units = _as_json(out.value)["reference_unit_durations"]["units"]
    assert set(units) == {"E1U1", "E1U2"}
    assert (units["E1U1"]["declared_capability"], units["E1U1"]["hydrated_capability"]) == ("r2v", "i2v")
    assert units["E1U1"]["unavailable_references"] == [{"type": "character", "name": "张三"}]
    assert [problem["code"] for problem in units["E1U1"]["problems"]] == [
        "reference_asset_missing",
        "reference_capability_changed",
    ]
    assert units["E1U1"]["allowed_durations"] == [5, 10]
    assert units["E1U2"]["hydrated_capability"] == "i2v"
    assert units["E1U2"]["problems"] == []


# ---------------------------------------------------------------------------
# Output truncation: planning and authoring share one problem code.
# ---------------------------------------------------------------------------

_TRUNCATION_PARAMS = {"provider_id": "custom-3", "model": "my-llm", "custom_model": True}


def _truncated() -> TextOutputTruncatedError:
    return TextOutputTruncatedError(
        provider="openai", model="my-llm", output_tokens=8192, provider_id="custom-3", custom_model=True
    )


def _drama_source(fake_ctx: ToolHarness) -> None:
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("从前有座山", encoding="utf-8")


@pytest.mark.parametrize("prepare", [nr_source, rv_source, _drama_source], ids=["narration", "reference", "drama"])
async def test_generate_script_plan_truncation_offers_the_way_out(
    fake_ctx: ToolHarness, monkeypatch, video_request_facts, prepare
) -> None:
    """Truncated script planning shares episode-planning codes/recovery and reports provider/model/custom-model status."""

    prepare(fake_ctx)

    class _TruncatingGenerator:
        async def generate(self, _request, project_name=None):
            raise _truncated()

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return _TruncatingGenerator()

    use_text_generator(monkeypatch, fake_create)

    problem = problem_of(await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1}))

    assert (problem.code, problem.action, problem.params) == (
        "text_output_truncated",
        GenerationAction.CONFIGURE_PROVIDER,
        _TRUNCATION_PARAMS,
    )
    assert "my-llm" in problem.detail


async def test_generate_episode_script_truncation_offers_the_way_out(fake_ctx: ToolHarness, monkeypatch) -> None:

    project_path = fake_ctx.project_path
    _write_formal_script(project_path)
    (project_path / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "narration"}), encoding="utf-8"
    )

    use_text_generator(monkeypatch, FakeTextGenerator(_truncated()).create)
    _prepare_script_admission(project_path)

    problem = problem_of(await run_declared_tool("generate_episode_script", fake_ctx, {"episode_id": 1}))

    assert (problem.code, problem.action, problem.params) == (
        "text_output_truncated",
        GenerationAction.CONFIGURE_PROVIDER,
        _TRUNCATION_PARAMS,
    )
