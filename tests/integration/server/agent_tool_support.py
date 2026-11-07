"""Shared assembly, substitutes, and helpers for Agent-tool tests; fixtures such as ``fake_ctx`` live in ``tests/integration/server/conftest.py``."""

from __future__ import annotations

import json
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, overload

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_queue import GenerationQueue, get_generation_queue
from lib.generation.generation_queue_client import batch_enqueue_and_wait
from lib.generation.generation_result import (
    GenerationBatchResult,
)
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    quarantine_path,
)
from server.agent_toolset.declaration import ToolDeclaration, invoke_declaration
from server.agent_toolset.envelope import json_value
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.services.project import workflow_planner
from server.tool_runtime import (
    BatchWaiter,
    CallerContext,
    ProjectScope,
    Services,
    TextGenerationResult,
    ToolOutcome,
    ToolProblem,
)
from tests.factories import make_video_request_facts
from tests.fakes import FakeConfigResolver, use_text_generator

# ---------------------------------------------------------------------------
# Generation result contract helpers
# ---------------------------------------------------------------------------


def read_generation_result(out: dict[str, Any] | ToolOutcome[Any]) -> GenerationBatchResult:
    """Read the structured contract out of a tool response, never its text."""

    if isinstance(out, ToolOutcome):
        assert isinstance(out.value, dict)
        return GenerationBatchResult.model_validate(out.value["generation_result"])
    return GenerationBatchResult.model_validate(out["generation_result"])


class ToolHarness:
    """Assembly for one Agent-tool call: session project, caller, and ``Services``; project manager, capability resolver, and queue are replaceable.

    ``services`` rebuilds from current attributes each time, so changes such as ``config_resolver`` affect the next call.
    """

    def __init__(
        self,
        project_name: str,
        data_root: Path,
        pm: ProjectManager | None = None,
        *,
        config_resolver: ConfigResolver | None = None,
        caller: CallerContext | None = None,
        queue: GenerationQueue | None = None,
    ):
        self.project_name = project_name
        self.data_root = data_root
        self.pm: ProjectManager = pm if pm is not None else ProjectManager(data_root)
        self.config_resolver = config_resolver
        self.caller = caller or CallerContext(user_id=DEFAULT_USER_ID, source="embedded")
        self.queue = queue or get_generation_queue()

    @property
    def project_path(self) -> Path:
        return self.pm.get_project_path(self.project_name)

    @property
    def scope(self) -> ProjectScope:
        return ProjectScope(project_name=self.project_name, data_root=self.data_root)

    @property
    def services(self) -> Services:
        return Services(
            projects=self.pm,
            workflow_planner=workflow_planner.get_workflow_planner(self.pm),
            capabilities=self.config_resolver or ConfigResolver(async_session_factory),
            queue=self.queue,
        )


@overload
async def run_declared_tool[ResultT](
    declaration: ToolDeclaration[Any, ResultT],
    ctx: ToolHarness,
    arguments: dict[str, Any],
    *,
    batch_waiter: BatchWaiter = ...,
    **collaborators: Any,
) -> ToolOutcome[ResultT]: ...


@overload
async def run_declared_tool(
    declaration: str,
    ctx: ToolHarness,
    arguments: dict[str, Any],
    *,
    batch_waiter: BatchWaiter = ...,
    **collaborators: Any,
) -> ToolOutcome[Any]: ...


async def run_declared_tool(
    declaration: ToolDeclaration[Any, Any] | str,
    ctx: ToolHarness,
    arguments: dict[str, Any],
    *,
    batch_waiter: BatchWaiter = batch_enqueue_and_wait,
    **collaborators: Any,
) -> ToolOutcome[Any]:
    """Call the shared declaration entry (identical for both hosts) and obtain the handler's ``ToolOutcome``.

    ``declaration`` accepts a declaration or tool name; inject the embedded batch waiter through caller and
    domain substitutes as handler keyword arguments through ``collaborators``.
    """

    if isinstance(declaration, str):
        found = next(item for item in AGENT_TOOLSET if item.name == declaration)
        assert isinstance(found, ToolDeclaration), f"{declaration} 不作用于项目"
        declaration = found
    if collaborators:
        declaration = replace(declaration, handler=partial(declaration.handler, **collaborators))
    caller = replace(ctx.caller, batch_waiter=batch_waiter)
    return await invoke_declaration(declaration, arguments, ctx.scope, caller, ctx.services)


def said(outcome: ToolOutcome[Any]) -> str:
    """Readable result: problem detail on failure, message for text generation, otherwise JSON of the value."""

    if outcome.problem is not None:
        return outcome.problem.detail
    if isinstance(outcome.value, TextGenerationResult):
        return outcome.value.message
    return json.dumps(json_value(outcome.value), ensure_ascii=False)


def draft_of(outcome: ToolOutcome[Any]) -> dict[str, Any]:
    """The draft returned by a successful draft tool."""

    assert outcome.problem is None, outcome.problem
    assert isinstance(outcome.value, dict)
    return outcome.value


def problem_of(outcome: ToolOutcome[Any]) -> ToolProblem:
    assert outcome.problem is not None, outcome.value
    return outcome.problem


async def run_generate_videos(
    ctx: ToolHarness,
    target: dict[str, Any],
    *,
    script: str = "episode_1.json",
    batch_waiter: BatchWaiter = batch_enqueue_and_wait,
    **arguments: Any,
) -> ToolOutcome[Any]:
    """Generate videos through the ``generate_videos`` declaration entry and obtain the handler's ``ToolOutcome``."""

    return await run_declared_tool(
        "generate_videos",
        ctx,
        {"script": script, "target": target, **arguments},
        batch_waiter=batch_waiter,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


CLAIMED_BASIS_DIGEST = "sha256-v1:" + "a" * 64


def fake_reference_projection(
    slot_for=None,
    calls: list[str] | None = None,
):
    """In-process request-projection adapter for Agent-tool tests."""

    async def _project(*, project, script, unit, options=None, **_kwargs):
        from lib.script.reference_video.request_projection import (
            ReferenceUnitRequestProjector,
            ResolvedReferenceAsset,
            unit_reference_declarations,
        )

        references = unit_reference_declarations(project, unit)
        generation_type = "r2v" if references else "i2v"
        if calls is not None:
            calls.append(generation_type)
        if slot_for is None:
            requested_seconds = int(unit.get("duration_seconds") or 8)
        else:
            requested_seconds = int(slot_for(None, unit).seconds)

        async def _request_facts(generation_type):
            return make_video_request_facts(
                route="reference_video",
                generation_type=generation_type,
                provider_id="fake",
                model_id=f"fake-{generation_type}",
                resolution="1080p",
                supported_durations=(requested_seconds,),
                allowed_durations=(requested_seconds,),
                max_reference_images=9,
                audio_switch_controllable=True,
            )

        class _Available:
            def is_available(self, asset):
                del asset
                return True

        resolved_assets = [
            ResolvedReferenceAsset(path=Path(f"{reference.type}/{reference.name}.png"), reference=reference)
            for reference in references
        ]
        return await ReferenceUnitRequestProjector(_request_facts, _Available()).project_current(
            project=project,
            script=script,
            unit=unit,
            resolved_assets=resolved_assets,
            options=options,
        )

    return _project


def fake_caps_resolver(**kwargs: Any) -> Any:
    """Build a fake capability resolver (see ``tests.fakes.FakeConfigResolver``).

    Return ``Any``: injection points require production ``ConfigResolver`` but the substitute implements
    only called methods. Adapt once at this constructor instead of suppressing types at each call site.
    """
    return FakeConfigResolver(**kwargs)


def use_fake_caps(fake_ctx: ToolHarness, **kwargs: Any) -> Any:
    """Install and return this session's fake capability resolver.

    ``ToolHarness.config_resolver`` reaches capability-dict/image-capability readers through ``Services.capabilities``
    (e.g. ``get_video_capabilities`` payloads and image-to-image gates). Duration/audio tiers instead read video-request
    facts, supplied through ``set_video_request_facts`` / ``video_request_facts``.
    """
    resolver = fake_caps_resolver(**kwargs)
    fake_ctx.config_resolver = resolver
    return resolver


def activate_unbound_project(fake_ctx: ToolHarness, *, generation_mode: str = "storyboard") -> None:
    project = fake_ctx.pm.project_payload
    project.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": generation_mode,
            "episodes": [],
        }
    )
    (fake_ctx.project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")


def reference_video_script(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [
            {
                "unit_id": "E1U1",
                "text": "@张三 推门",
                "duration_seconds": 5,
            }
        ],
    }
    payload.update(overrides)
    return payload


def use_reference_route(fake_ctx: ToolHarness) -> None:
    """Switch the fake project to reference-video generation: generation mode is a project fact, not a script stamp."""
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"


def rv_generator_returning(
    units: list[dict], captured: dict[str, Any] | None = None, *, new_assets: list[dict] | None = None
):
    """Build fake TextGenerator.create returning specified flat units JSON, optionally capturing task_type / project_name."""

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            if captured is not None:
                captured["generate_project_name"] = project_name

            class _R:
                text = json.dumps({"units": units, "new_assets": new_assets or []}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None, **kwargs):
        if captured is not None:
            captured["task_type"] = task_type
            captured["create_project_name"] = project_name
            captured["purpose"] = kwargs.get("purpose")
        return _FakeGenerator()

    return fake_create


_RV_NOVEL = "张三在村口等人"


def rv_project(fake_ctx: ToolHarness, generation_mode: str = "reference_video") -> None:
    """Declare the reference-video route, a prerequisite for draft splitting/promotion/blocking decisions.

    Synchronize disk project.json and pm memory: generation reads disk; promotion reads ``pm.load_project``.
    """
    fake_ctx.pm.project_payload["content_mode"] = "narration"
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(fake_ctx.pm.project_payload, ensure_ascii=False),
        encoding="utf-8",
    )


def rv_source(fake_ctx: ToolHarness) -> None:
    rv_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(_RV_NOVEL, encoding="utf-8")


def rv_unit(text: str, *, duration: int = 8, source_text: str = _RV_NOVEL) -> dict:
    """script_plan LLM output shape: flat duration + source anchor + reference-syntax text."""
    return {"duration_seconds": duration, "source_text": source_text, "text": text}


def derived_reference_names(fake_ctx: ToolHarness, text: str) -> list[str]:
    """Text to reference-image names: the sole read-side derivation entry; persisted data has no references."""
    from lib.script.reference_video.text_parser import derive_references_from_text

    project = json.loads((fake_ctx.project_path / "project.json").read_text(encoding="utf-8"))
    references, _missing = derive_references_from_text(text, project)
    return [reference.name for reference in references]


def rv_script_plan_path(fake_ctx: ToolHarness):
    return fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_reference_units.json"


async def run_rv_split(
    fake_ctx: ToolHarness, monkeypatch, units: list[dict], *, new_assets: list[dict] | None = None, **caps_kwargs
) -> ToolOutcome[Any]:

    use_fake_caps(fake_ctx, **caps_kwargs)
    use_text_generator(monkeypatch, rv_generator_returning(units, new_assets=new_assets))
    return await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})


def rv_quarantine_path(fake_ctx: ToolHarness):
    return quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)


def read_rv_quarantine(fake_ctx: ToolHarness) -> dict:
    return json.loads(rv_quarantine_path(fake_ctx).read_text(encoding="utf-8"))


async def promote_reference_draft(fake_ctx: ToolHarness, **caps_kwargs) -> ToolOutcome[Any]:
    if not (fake_ctx.project_path / "project.json").exists():
        rv_project(fake_ctx)
    use_fake_caps(fake_ctx, **caps_kwargs)
    if rv_quarantine_path(fake_ctx).exists():
        doc_type = "reference_script_plan"
    elif quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).exists():
        doc_type = "reference_prompt_authoring"
    else:
        doc_type = "reference_script_plan"
    args = {"episode_id": 1, "doc_type": doc_type}
    opened = await run_declared_tool("open_draft", fake_ctx, args)
    revision = draft_of(opened)["revision"] if opened.problem is None else ""
    return await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": revision})


def write_rv_script_plan(fake_ctx: ToolHarness, units: list[dict]) -> None:
    """Lay out a formal script_plan directly, simulating the previous split round's persisted output."""
    path = rv_script_plan_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": units}, ensure_ascii=False), encoding="utf-8")


def rv_saved_unit(text: str, *, unit_id: str = "E1U01", duration: int = 8) -> dict:
    """Persisted formal script_plan shape: text + machine-derived unit_id."""
    return {
        "unit_id": unit_id,
        "text": text,
        "duration_seconds": duration,
        "source_text": _RV_NOVEL,
    }


async def open_for_edit(fake_ctx: ToolHarness, **args) -> ToolOutcome[Any]:
    if not (fake_ctx.project_path / "project.json").exists():
        rv_project(fake_ctx)
    return await run_declared_tool(
        "open_draft", fake_ctx, {"episode_id": 1, "doc_type": "reference_script_plan", **args}
    )


def nr_source(fake_ctx: ToolHarness) -> None:
    rv_project(fake_ctx, generation_mode="storyboard")
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(_RV_NOVEL, encoding="utf-8")


def nr_generator_returning(
    segments: list[dict], captured: dict[str, Any] | None = None, *, new_assets: list[dict] | None = None
):
    """Build fake TextGenerator.create returning specified segments JSON, optionally capturing task_type / project_name."""

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            if captured is not None:
                captured["generate_project_name"] = project_name

            class _R:
                text = json.dumps(
                    {"episode": 1, "segments": segments, "new_assets": new_assets or []}, ensure_ascii=False
                )

            return _R()

    async def fake_create(task_type, project_name=None, **kwargs):
        if captured is not None:
            captured["task_type"] = task_type
            captured["create_project_name"] = project_name
            captured["purpose"] = kwargs.get("purpose")
        return _FakeGenerator()

    return fake_create


def nr_segment(segment_id="E1S01", duration=4, novel_text="张三走向村口。", **extra):
    seg = {
        "segment_id": segment_id,
        "novel_text": novel_text,
        "duration_seconds": duration,
        "segment_break": False,
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
    }
    seg.update(extra)
    return seg


_DRAMA_NOVEL = "三年后，阿离回到山门。"


def drama_project(fake_ctx: ToolHarness) -> None:
    """Declare drama + storyboard-image-to-video, register characters referenced by ``drama_scene``, and prepare source text;
    formal script_plan write prohibitions and draft channels depend on this.
    """
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "content_mode": "drama",
                "generation_mode": "storyboard",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_ctx.pm.project_payload["content_mode"] = "drama"
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["characters"]["阿离"] = {"description": "少女，青衣"}
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True, exist_ok=True)
    (src / "episode_1.txt").write_text(_DRAMA_NOVEL, encoding="utf-8")


def drama_scene(**overrides) -> dict:
    scene = {
        "scene_id": "E1S01",
        "duration_seconds": 4,
        "segment_break": False,
        "characters_in_scene": ["阿离"],
        "scenes": [],
        "props": [],
        "scene_description": "阿离站在山门前。",
        "utterances": [{"kind": "dialogue", "speaker": "阿离", "text": "我回来了。"}],
        "source_text": _DRAMA_NOVEL,
    }
    scene.update(overrides)
    return scene


def drama_script_plan_path(fake_ctx: ToolHarness) -> Path:
    return fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json"


def drama_quarantine_path(fake_ctx: ToolHarness) -> Path:
    return quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)


def write_drama_script_plan(fake_ctx: ToolHarness, scenes: list[dict]) -> None:
    path = drama_script_plan_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"title": "第一集", "scenes": scenes}, ensure_ascii=False), encoding="utf-8")


def read_drama_quarantine(fake_ctx: ToolHarness) -> dict:
    return json.loads(drama_quarantine_path(fake_ctx).read_text(encoding="utf-8"))


async def open_drama_for_edit(fake_ctx: ToolHarness, **args) -> ToolOutcome[Any]:
    return await run_declared_tool("open_draft", fake_ctx, {"episode_id": 1, "doc_type": "drama_script_plan", **args})


async def promote_drama(fake_ctx: ToolHarness, durations=(4, 6, 8)) -> ToolOutcome[Any]:
    use_fake_caps(fake_ctx, supported_durations=durations, default_duration=durations[0])
    args = {"episode_id": 1, "doc_type": "drama_script_plan"}
    opened = await run_declared_tool("open_draft", fake_ctx, args)
    revision = draft_of(opened)["revision"]
    return await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": revision})


def nr_script_plan_path(fake_ctx: ToolHarness) -> Path:
    return fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json"


def nr_quarantine_path(fake_ctx: ToolHarness) -> Path:
    return quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)


def read_nr_quarantine(fake_ctx: ToolHarness) -> dict:
    return json.loads(nr_quarantine_path(fake_ctx).read_text(encoding="utf-8"))


def write_nr_script_plan(fake_ctx: ToolHarness, segments: list[dict]) -> None:
    path = nr_script_plan_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")


async def open_nr_for_edit(fake_ctx: ToolHarness, **args) -> ToolOutcome[Any]:
    return await run_declared_tool(
        "open_draft", fake_ctx, {"episode_id": 1, "doc_type": "narration_script_plan", **args}
    )


async def promote_nr(fake_ctx: ToolHarness, durations=(4, 6, 8)) -> ToolOutcome[Any]:
    use_fake_caps(fake_ctx, supported_durations=durations, default_duration=durations[0])
    args = {"episode_id": 1, "doc_type": "narration_script_plan"}
    opened = await run_declared_tool("open_draft", fake_ctx, args)
    revision = draft_of(opened)["revision"]
    return await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": revision})
