"""Host-neutral tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    artifact_is_usable,
    resolve_artifact_episode,
)
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.episode.episode_ids import describe_episode_for_agent
from lib.generation.batch_admission import (
    BatchAdmission,
    BatchAdmissionDecision,
    UnitAdmissionTicket,
    refused_ticket,
)
from lib.generation.generation_batch import GenerationBatchReadModel
from lib.generation.generation_queue_client import (
    BatchTaskResult,
    TaskSpec,
)
from lib.generation.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationCandidate,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelection,
    GenerationSelectionMode,
    GenerationTargetState,
    artifact_is_reusable,
    normalize_requested_ids,
    record_batch_outcomes,
    select_generation_targets,
)
from lib.generation.video_request_facts import VideoRequestFacts, VideoRequestFactsFailure
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import ProjectManager, is_reference_video_project
from lib.script.reference_video.request_projection import ReferenceRequestOptions
from lib.script.script_models import get_generated_assets, resolve_content_mode
from lib.script.script_skeleton import ensure_route_skeleton, resolve_script_kind
from lib.script.storyboard_sequence import get_storyboard_items
from lib.speech.speech_composition import (
    SpeechAdmissionError,
    video_unit_replan_problems,
)
from server.media_tools.context import (
    GenerationToolValue,
    RequestedIds,
    ScriptFilename,
    generation_batch_submission_outcome,
    generation_result_outcome,
    tool_error,
)
from server.services.admission.video_batch_admission import (
    admit_reference_video_batch,
    admit_storyboard_video_request,
    artifact_state_tickets,
    build_storyboard_video_specs,
    reference_unit_task_spec,
    request_options_for_unit,
    resolve_voice_context,
    screen_script_entries,
    screen_storyboard_items,
    speech_admission_ticket,
    storyboard_item_aliases,
    storyboard_item_id,
    storyboard_video_request_facts,
    video_target_states,
)
from server.services.admission.video_quote import quote_sheet, quote_storyboard_video_units
from server.tool_runtime import (
    CallerContext,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    submit_media_generation,
)

logger = logging.getLogger(__name__)

#: Retired parameter names → current replacements. Keys were real parameters or names matching old video-unit structure:
#: the first three are old target-ID parameters; ``shots`` and reference lists are removed unit fields;
#: ``narration_delivery`` is the removed per-request delivery setting. Units now hold only ``text`` and
#: ``duration_seconds``; references derive at execution from first-occurrence body ``@[名称]`` mentions;
#: narration delivery is project configuration and does not affect video requests.
_RETIRED_PARAMS: dict[str, str] = {
    "resume": "查询 durable batch，并用 selected scope、force=false 只重发未成功的 ID",
    "shot_ids": "改用 target.ids，并选择 scene（单个）或 selected（批量）scope",
    "unit_id": "改用 target.ids——参考生视频项目直接传 unit_id，并选择 scene scope",
    "unit_ids": "改用 target.ids——参考生视频项目直接传 unit_id 列表，并选择 selected scope",
    "shots": "视频单元不再有 shots 数组；正文写在剧本的 text 字段里，经 patch_episode_script 修改",
    "references": "视频单元不再有参考清单；参考图由正文的 @[名称] 提及在执行期派生",
    "reference_images": "视频单元不再有参考清单；参考图由正文的 @[名称] 提及在执行期派生",
    "narration_delivery": "视频生成不再按请求选择旁白交付方式；视频一律按剧本计划时长请求，旁白交付方式是项目配置",
}


def _reject_retired_params(args: Mapping[str, Any]) -> None:
    """Fail loudly on retired parameter names and explain the current replacement.

    Raises:
        ValueError: A parameter matches :data:`_RETIRED_PARAMS`."""
    for name, guidance in _RETIRED_PARAMS.items():
        if name in args:
            raise ValueError(f"参数 {name!r} 已不存在：{guidance}")


_PositiveInt = Annotated[StrictInt, Field(ge=1)]


class EpisodeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["episode"] = Field(
        description="Whole episode: fill only units missing video; always reuse existing usable videos"
    )
    episode_id: _PositiveInt = Field(description="Episode ID, which must match the episode owning script")


class AllTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["all"] = Field(
        description="All units in the script: fill only units missing video; always reuse existing usable videos"
    )


class SceneTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["scene"] = Field(description="Single unit")
    ids: list[str] = Field(
        min_length=1,
        max_length=1,
        description="Exactly one target ID: segment_id / scene_id for storyboard-to-video, unit_id for reference-to-video",
    )


class SelectedTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["selected"] = Field(description="An explicitly named batch of units")
    ids: RequestedIds = Field(
        description="Target IDs: segment_id / scene_id for storyboard-to-video, unit_id for reference-to-video"
    )


VideoTarget = Annotated[
    EpisodeTarget | AllTarget | SceneTarget | SelectedTarget,
    Field(discriminator="scope"),
]


class GenerateVideosRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="Script filename only (no directory), for example episode_1.json")
    target: VideoTarget = Field(description="Target selector; scope is episode / all / scene / selected")
    force: StrictBool = Field(
        default=False,
        description="Whether to force regeneration of existing usable videos; default reuses current / stale videos. Allowed only for scene / selected",
    )
    confirmed_request_duration_seconds: _PositiveInt | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Video-request duration tier in seconds explicitly accepted by the user; fill only for reference-to-video after a cross-tier confirmation response. It does not freeze text, references or provider; reconfirm if the current projection changes to another tier."
        ),
    )
    confirmed_request_durations: dict[str, _PositiveInt] | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            'Per-unit_id tier confirmations ({"E1U1": 8}); use when units in one request have different tiers so the original target set is resubmitted as one batch. Splitting into calls would spend on the first queued tier before the others. When also supplying confirmed_request_duration_seconds, this field overrides it per unit. Pass the same-named preflight field unchanged; units absent from this map are unaffected.'
        ),
    )
    preview: StrictBool = Field(
        default=False,
        description=(
            "When true, preflight only without enqueueing: use the same admission as formal submission and return video_quote (per-unit outcome, orchestration duration, requested tier, tier changes, estimated cost and gaps)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _refuse_retired_params(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            _reject_retired_params(data)
        return data

    @model_validator(mode="after")
    def _force_needs_explicit_ids(self) -> Self:
        if self.force and self.target.scope not in ("scene", "selected"):
            raise ValueError("force=true 只允许用于带显式 ID 的 scene/selected scope")
        return self


@dataclass(frozen=True, slots=True)
class _VideoCall:
    """One video-tool call: target project, caller and collaborators."""

    scope: ProjectScope
    caller: CallerContext
    services: Services
    #: Preflight only, without queueing: run through whole-batch admission and return the decision as a quote.
    preview: bool = False

    @property
    def project_name(self) -> str:
        return self.scope.project_name

    @property
    def projects(self) -> ProjectManager:
        return self.services.projects

    @property
    def project_path(self) -> Path:
        return self.services.projects.get_project_path(self.scope.project_name)

    def stop_on_failure_caller(self) -> CallerContext:
        """A caller whose waiter stops awaiting remaining batch tasks when any task fails."""
        return self.caller.waiting_with(stop_on_failure=True)


_OPERATION = "generate_videos"


def _batch_video_is_reusable(
    *,
    currency: ArtifactCurrencyResolver,
    episode: int,
    resource_id: str,
    artifact_path: object,
) -> bool:
    """Admit a batch skip from verified currency; uploads are registered like any other video."""

    return artifact_is_usable(
        currency,
        ArtifactKey.episode_video(episode, resource_id),
        artifact_path,
    )


def _state_for(states: dict[str, GenerationTargetState], unit_id: str) -> GenerationTargetState:
    return states.get(unit_id) or GenerationTargetState(candidate=GenerationCandidate(unit_id=unit_id))


def _missing_only_reusable_ids(states: dict[str, GenerationTargetState]) -> list[str]:
    """Storyboards preserved unchanged by missing-only selection: Manifest current / stale, including registered uploaded videos."""

    return [unit_id for unit_id, state in states.items() if artifact_is_reusable(state)]


def _sole_speech_admission(result: GenerationBatchResult) -> dict[str, Any]:
    """Return the admission payload unchanged at the response top level when only one unit’s speech admission blocks the request.

    Unit-level locations (which dialogue line or path) are more precise than the per-ID contract, allowing single-unit callers
    to locate the required change directly. Batch requests have no single unit to identify, so omit this payload."""

    admissions = [
        item.problem.params["speech_admission"]
        for item in result.items
        if item.problem is not None and "speech_admission" in item.problem.params
    ]
    if len(result.requested) == 1 and len(admissions) == 1:
        return {"speech_admission": admissions[0]}
    return {}


def _reference_request_options(request: GenerateVideosRequest) -> ReferenceRequestOptions:
    """Project tool arguments into options for one reference-video request."""

    return ReferenceRequestOptions(confirmed_request_duration_seconds=request.confirmed_request_duration_seconds)


def _speech_admission_error(name: str, exc: SpeechAdmissionError, log: list[str] | None = None) -> ToolOutcome[Any]:
    payload = exc.admission.to_dict()
    text = f"{name} 失败: unit {exc.admission.unit_id} 发声准入未通过；请按 problems 的 action 修复"
    if log:
        text = "\n".join([text, *log])
    return ToolOutcome(problem=ToolProblem("speech_admission_refused", text, params={"speech_admission": payload}))


@dataclass(frozen=True)
class ReferenceGenerationComplete:
    """Reference-unit generation results and pre-enqueue current-state projections."""

    projections: list[dict[str, object]]
    batch: GenerationBatchReadModel | None = None


@dataclass(frozen=True)
class BatchAdmissionRefused:
    """Whole-batch admission refused: this call creates no tasks."""

    admission: BatchAdmission


@dataclass(frozen=True)
class ReferenceGenerationPreview:
    """Preflight: whole-batch admission for reference units, without enqueueing."""

    admission: BatchAdmission


def _preview_outcome(
    admission: BatchAdmission | None,
    builder: GenerationResultBuilder,
    log: list[str],
    *,
    storyboard_durations: Mapping[str, object] | None = None,
    storyboard_costs: Mapping[str, Mapping[str, object] | None] | None = None,
) -> ToolOutcome[Any]:
    """Combine preflight admission and reuse accounting into a quote response."""

    sheet = quote_sheet(
        admission,
        reused_ids=[item.unit_id for item in builder.build().skipped],
        storyboard_durations=storyboard_durations,
        storyboard_costs=storyboard_costs,
    )
    return ToolOutcome(value={"video_quote": sheet.to_payload(), "summary": sheet.summary(log)})


def _confirmation_lines(admission: BatchAdmission) -> list[str]:
    lines = ["以下 unit 将改用不同的视频时长档位，需先向用户确认，本次未入队任何任务："]
    for tier in admission.confirmation_tiers():
        # Unresolved tiers have no confirmable seconds; say so rather than interpolating a "None s tier" that suggests
        # a tier named None exists.
        tier_label = (
            f"{tier.request_duration_seconds}s 档位" if tier.request_duration_seconds is not None else "档位待定"
        )
        headline = f"- {tier_label} × {tier.unit_count}：{'、'.join(tier.unit_ids)}"
        if tier.cost_amount is not None:
            suffix = "（仅含已定价的 unit）" if tier.unpriced_count else ""
            headline += f"；合计 {tier.cost_amount} {tier.cost_currency}{suffix}"
        if tier.unpriced_count:
            headline += f"；其中 {tier.unpriced_count} 个 unit 未定价，费用未知，须如实告知用户"
        lines.append(headline)
    for ticket in admission.tickets:
        if not ticket.confirmation_only:
            continue
        planned = ticket.problems[0].params.get("script_duration")
        requested = ticket.request_duration_seconds
        # Show the difference from script-planned duration directly; tier numbers alone do not indicate longer or shorter output.
        delta = ""
        if isinstance(planned, int) and isinstance(requested, int) and requested != planned:
            direction = "更长" if requested > planned else "更短"
            delta = f"（成片{direction} {abs(requested - planned)}s）"
        requested_label = f"{requested}s" if isinstance(requested, int) else "档位待定"
        lines.append(f"  · {ticket.unit_id}：剧本时长 {planned}s，将申请 {requested_label}{delta}")
    lines.append(
        "视频费用按上述申请档位计算，确认仅对本次请求有效。用户同意后，带 "
        "confirmed_request_durations={<unit_id>: <request_duration>} 把原来这一批目标一次性重发；"
        "整批只有一个档位时也可以用 confirmed_request_duration_seconds=<request_duration>。"
        "不要按档位拆成多次调用——先入队的那一档已经花了钱，这批目标就不再是一次全有或全无的请求。"
    )
    return lines


def _blocked_lines(admission: BatchAdmission) -> list[str]:
    lines = ["本次批量请求未通过准入，未入队任何任务。逐 unit 缺口如下："]
    for ticket in admission.refused_tickets:
        lines.extend(
            f"- {ticket.unit_id}：{problem.code}（下一步：{problem.action.value}）{problem.detail}"
            for problem in ticket.problems
        )
    lines.append("修复全部缺口后重试即可一次性提交整批。")
    return lines


def _batch_admission_response(
    refusal: BatchAdmissionRefused,
    log: list[str],
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState] | None = None,
) -> ToolOutcome[Any]:
    """Relay whole-batch refusal: enqueue no tasks and provide a machine-readable outcome for every target.

    Awaiting tier confirmation is normal pre-enqueue gating, not an exception: ``is_error`` does not follow blocked.
    These units await the user’s decision rather than indicating request failure."""

    admission = refusal.admission
    admission.record_refusal(builder, states=states)
    confirmation_required = admission.decision is BatchAdmissionDecision.CONFIRMATION_REQUIRED
    lines = [*log, *(_confirmation_lines(admission) if confirmation_required else _blocked_lines(admission))]
    result = builder.build()
    payload: dict[str, Any] = {
        "generation_result": result,
        "batch_admission": admission.to_payload(),
        "request_projections": admission.projections(),
        **_sole_speech_admission(result),
        "summary": "\n".join(lines),
    }
    return ToolOutcome(value=payload)


async def _admit_storyboard_specs(
    *,
    call: _VideoCall,
    project: dict[str, Any],
    script: dict[str, Any],
    script_filename: str,
    items: list[dict[str, Any]],
    id_field: str,
    specs: list[TaskSpec],
    operation: str,
    selection: GenerationSelectionMode,
    extra_tickets: list[UnitAdmissionTicket],
    video_request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None,
) -> BatchAdmission:
    """Admit the Storyboard-mode specs.

    The admission is the shared one the read-only plan also consults, so a preview
    and the submission it predicts cannot reach different verdicts.
    """

    return await admit_storyboard_video_request(
        project_name=call.project_name,
        project=project,
        script_file=script_filename,
        items=items,
        id_field=id_field,
        specs=specs,
        operation=operation,
        selection=selection,
        extra_tickets=extra_tickets,
        user_id=call.caller.user_id,
        queue=call.services.queue,
        config_resolver=call.services.capabilities,
        video_request_facts=video_request_facts,
    )


def _resolve_reference_route(call: _VideoCall, script: dict[str, Any]) -> str | None:
    """Resolve generation mode and enforce skeleton gating.

    Return ``"reference"`` for reference-video projects, ``None`` for storyboard-to-video.
    The sole mode authority is project.json ``generation_mode``; all content modes share
    the same ``video_units`` skeleton.

    Raises:
        SkeletonRouteMismatchError: Script skeleton mismatches project mode, rejecting generation."""
    project = call.projects.load_project(call.project_name)
    content_mode = resolve_content_mode(script, project)
    ensure_route_skeleton(script, content_mode, project.get("generation_mode"))
    if not is_reference_video_project(project):
        return None
    return "reference"


def _build_reference_specs(
    *,
    units: list[Any],
    script_filename: str,
    skip_ids: list[str] | None,
) -> tuple[list[TaskSpec], list[UnitAdmissionTicket]]:
    """Build the reference-route specs, refusing each unit that cannot be requested."""

    skip_set = set(skip_ids or [])
    specs: list[TaskSpec] = []
    refused: list[UnitAdmissionTicket] = []
    # Units here were screened/selected; unit_id is a nonempty scalar, so outcome recording needs no fallback name.
    for unit in units:
        unit_id = str(unit["unit_id"])
        if unit_id in skip_set:
            continue
        # Invalid units (no shots, empty prompts, or plain ValueError from from_request for empty resource_id)
        # are blocked individually rather than interrupting the batch. TaskSpecValidationError
        # subclasses ValueError, so catching ValueError covers both.
        try:
            spec = reference_unit_task_spec(unit, script_filename)
        except SpeechAdmissionError as exc:
            refused.append(speech_admission_ticket(unit_id, exc))
            continue
        except ValueError as exc:
            refused.append(
                refused_ticket(
                    unit_id,
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"入队校验未通过：{exc}",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        specs.append(spec)
    return specs, refused


def _scene_fallback_relpath(resource_id: str) -> str:
    return f"videos/scene_{resource_id}.mp4"


def _reference_fallback_relpath(resource_id: str) -> str:
    return f"reference_videos/{resource_id}.mp4"


async def _generate_reference_units(
    *,
    call: _VideoCall,
    units: list[Any],
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState],
    resolver: ArtifactCurrencyResolver,
    build_specs: Callable[[list[Any], list[str]], tuple[list[TaskSpec], list[UnitAdmissionTicket]]],
    project: dict[str, Any],
    script: dict[str, Any],
    script_filename: str,
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    reuse_existing: Callable[[dict[str, Any]], bool],
    operation: str,
    selection: GenerationSelectionMode,
    extra_tickets: list[UnitAdmissionTicket] | None = None,
) -> ReferenceGenerationComplete | ReferenceGenerationPreview | BatchAdmissionRefused:
    """Shared unit-batch generation: duration confirmation, existing-output scanning and durable batch submission.

    All content modes construct ``video_units`` through the same path. ``build_specs`` is the sole eligibility authority
    for this batch, running before admission. Units without constructible TaskSpec enter admission with their own
    problem codes and are neither parsed further nor quoted.

    ``reuse_existing`` decides whether an existing ``{unit_id}.mp4`` can be reused as the unit’s current artifact.
    Callers must use persisted artifact ownership, never infer ownership from a matching filename alone. Shared
    generation first applies replan gating; migration-preserved old artifacts cannot let ``needs_replan`` units bypass repair.

    Admission evaluates all targets together once through :func:`admit_reference_video_batch`. Any target problem returns
    :class:`BatchAdmissionRefused` and creates no tasks, sharing Web batch admission. Cross-tier requested durations
    not exactly equal to ``confirmed_request_duration_seconds`` also fail admission; after user agreement, callers
    resubmit with the corresponding tier to enqueue."""

    def scan_existing() -> tuple[Path, list[UnitAdmissionTicket], list[TaskSpec], list[UnitAdmissionTicket]]:
        """Read the project directory and currency, record reuse of existing usable clips, and build this batch’s TaskSpec objects."""

        project_dir = call.project_path
        output_dir = project_dir / "reference_videos"
        output_dir.mkdir(parents=True, exist_ok=True)

        already_done: list[str] = []
        manifest_blocked: list[str] = []
        refused: list[UnitAdmissionTicket] = list(extra_tickets or [])
        for unit in units:
            if not isinstance(unit, dict):
                continue
            unit_id = str(unit.get("unit_id") or "")
            if not unit_id:
                continue
            candidate = output_dir / f"{unit_id}.mp4"
            reusable = False
            if candidate.exists() and not video_unit_replan_problems(unit):
                try:
                    reusable = reuse_existing(unit)
                except ArtifactManifestError:
                    # Reuse checks (named forced routes always pass False for ``reuse_existing`` and never call
                    # ``artifact_is_usable``; only whole-episode reuse checks inspect the Manifest) fail loudly on
                    # BLOCKED: an unreadable comparison for an existing clip must not turn the
                    # entire batch into tool_error; block per unit and hand back sidecar repair instead.
                    refused.append(
                        refused_ticket(
                            unit_id,
                            code=GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE,
                            detail=f"unit {unit_id} 的产物状态不可读，跳过自动重生",
                            action=GenerationAction.REPAIR_ARTIFACT_STATE,
                        )
                    )
                    manifest_blocked.append(unit_id)
                    continue
            if reusable:
                already_done.append(unit_id)
                state = _state_for(states, unit_id)
                builder.skip_unit(
                    unit_id,
                    artifact_key=state.artifact_key,
                    artifact_path=state.artifact_path,
                    artifact_status=state.status,
                )

        # Check eligibility first: TaskSpec construction defines this batch’s target boundary; ineligible units enter admission
        # with their own problem codes, without further parsing or silent omission.
        specs, spec_refused = build_specs(units, [*already_done, *manifest_blocked])
        return project_dir, refused, specs, spec_refused

    project_dir, refused, specs, spec_refused = await asyncio.to_thread(scan_existing)
    buildable = {spec.resource_id for spec in specs}
    targets = [unit for unit in units if isinstance(unit, dict) and str(unit.get("unit_id") or "") in buildable]
    admission = await admit_reference_video_batch(
        project_name=call.project_name,
        project=project,
        project_path=project_dir,
        script=script,
        script_file=script_filename,
        units=targets,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=selection,
        extra_tickets=[*refused, *spec_refused],
        user_id=call.caller.user_id,
        queue=call.services.queue,
        config_resolver=call.services.capabilities,
    )
    if call.preview:
        return ReferenceGenerationPreview(admission)
    if not admission.admitted:
        return BatchAdmissionRefused(admission)
    projections = admission.projections()

    for spec in specs:
        unit_options = request_options_for_unit(request_options, spec.resource_id, confirmed_request_durations)
        spec.payload = {**(spec.payload or {}), "reference_request_options": unit_options.to_payload()}
        spec.unit_id = spec.resource_id
        spec.source = call.caller.source

    submitted = await submit_media_generation(
        scope=call.scope,
        caller=call.stop_on_failure_caller(),
        services=call.services,
        operation=operation,
        preflight=builder.build(),
        pending_ids=[spec.resource_id for spec in specs],
        specs=specs,
        states=states,
        admission={str(item["unit_id"]): item for item in projections},
    )
    if submitted.successes is None or submitted.failures is None:
        return ReferenceGenerationComplete(projections=projections, batch=submitted.batch)
    await asyncio.to_thread(
        record_batch_outcomes,
        builder,
        successes=submitted.successes,
        failures=submitted.failures,
        states=states,
        resolver=resolver,
        fallback_path=_reference_fallback_relpath,
    )
    return ReferenceGenerationComplete(projections=projections, batch=submitted.batch)


def _reference_episode(project: dict[str, Any], script: dict[str, Any], script_filename: str) -> int:
    """Reference-video episode identity: prefer bound identity, falling back to script/filename inference when unavailable."""

    return resolve_artifact_episode(
        project=project,
        script=script,
        script_filename=script_filename,
    )


async def _run_reference_batch(
    *,
    call: _VideoCall,
    project: dict[str, Any],
    script: dict[str, Any],
    script_filename: str,
    episode: int,
    units: list[Any],
    selection: GenerationSelectionMode,
    extra_tickets: list[UnitAdmissionTicket],
    reuse_existing: Callable[[ArtifactCurrencyResolver, dict[str, Any]], bool],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
) -> ToolOutcome[Any]:
    """Shared reference-video completion: target state → batch generation → admission refusal or result response.

    ``reuse_existing`` receives the currency resolver and unit, sharing one interface between whole-episode reuse
    and named-target routes that never reuse."""

    def load_states() -> tuple[ArtifactCurrencyResolver, dict[str, GenerationTargetState]]:
        resolver = active_artifact_currency_resolver(call.project_path, project)
        return resolver, video_target_states(units, "unit_id", episode=episode, resolver=resolver)

    currency, states = await asyncio.to_thread(load_states)
    builder = GenerationResultBuilder(operation, selection)
    result = await _generate_reference_units(
        call=call,
        units=units,
        builder=builder,
        states=states,
        resolver=currency,
        build_specs=lambda u, skip: _build_reference_specs(units=u, script_filename=script_filename, skip_ids=skip),
        project=project,
        script=script,
        script_filename=script_filename,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=selection,
        extra_tickets=extra_tickets,
        reuse_existing=lambda unit: reuse_existing(currency, unit),
    )
    if isinstance(result, ReferenceGenerationPreview):
        return _preview_outcome(result.admission, builder, log)
    if isinstance(result, BatchAdmissionRefused):
        return _batch_admission_response(result, log, builder, states)
    if call.caller.source == "mcp" and result.batch is not None:
        return generation_batch_submission_outcome(result.batch)
    batch = builder.build()
    return generation_result_outcome(
        batch,
        log,
        request_projections=result.projections,
        batch_id=result.batch.batch_id if result.batch is not None else None,
        **_sole_speech_admission(batch),
    )


async def _run_reference_episode(
    *,
    call: _VideoCall,
    script: dict[str, Any],
    script_filename: str,
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
) -> ToolOutcome[Any]:
    """Run reference_video-mode generation and format the tool response.

    All 4 video handlers fall through to whole-episode reference generation
    when ``_resolve_reference_route`` reports the episode branch; this captures
    the shared tail (resolve episode → generate units → header + log).
    """
    project = await asyncio.to_thread(call.projects.load_project, call.project_name)
    episode = _reference_episode(project, script, script_filename)
    units = script.get("video_units")
    if "video_units" in script and not isinstance(units, list):
        # Mode gating checks key presence, not value type, so validate containers here: imported/externally edited malformed values
        # such as dicts/strings would otherwise reach unit iteration and raise an unlocatable TypeError.
        logger.debug("第 %d 集 video_units 类型非法: %s (%s)", episode, type(units).__name__, script_filename)
        raise ValueError(f"{describe_episode_for_agent(project, episode)} 的 video_units 必须是数组：{script_filename}")
    if not units:
        raise ValueError(f"{describe_episode_for_agent(project, episode)} 的 video_units 为空：{script_filename}")
    units, malformed = screen_script_entries(units, requested_ids=None)
    return await _run_reference_batch(
        call=call,
        project=project,
        script=script,
        script_filename=script_filename,
        episode=episode,
        units=units,
        selection=GenerationSelectionMode.MISSING_ONLY,
        extra_tickets=malformed,
        reuse_existing=lambda currency, unit: _batch_video_is_reusable(
            currency=currency,
            episode=episode,
            resource_id=str(unit.get("unit_id") or ""),
            artifact_path=get_generated_assets(unit).get("video_clip"),
        ),
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        log=log,
        operation=operation,
    )


def _select_reference_units(
    script: dict[str, Any], unit_ids: list[str]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Select named units from ``video_units`` by unit_id.

    Return ``(selected, unmatched_ids, duplicated_ids)``. Callers report missing IDs individually as blocked, without
    silently dropping them here or interrupting other units. Duplicate script entries for a selected ID cannot be
    disambiguated, so exclude the ID and let callers reject the entire batch instead of silently charging to queue the first entry."""
    indexed = script.get("video_units")
    by_id: dict[str, dict[str, Any]] = {}
    duplicated: set[str] = set()
    if isinstance(indexed, list):
        for unit in indexed:
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str) and unit["unit_id"]:
                if unit["unit_id"] in by_id:
                    duplicated.add(unit["unit_id"])
                    continue
                by_id[unit["unit_id"]] = unit

    selected: list[dict[str, Any]] = []
    unmatched: list[str] = []
    named = list(dict.fromkeys(unit_ids))
    for unit_id in named:
        if unit_id in duplicated:
            continue
        unit = by_id.get(unit_id)
        if unit is None:
            unmatched.append(unit_id)
            continue
        selected.append(unit)
    return selected, unmatched, [unit_id for unit_id in named if unit_id in duplicated]


async def _run_reference_units(
    *,
    call: _VideoCall,
    script_filename: str,
    unit_ids: list[str],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
    force: bool = True,
) -> ToolOutcome[Any]:
    """Generate named reference-video units; the unified entry point defaults to reusing existing usable clips."""

    def load_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            call.projects.load_project(call.project_name),
            call.projects.load_script(call.project_name, script_filename),
        )

    project, script = await run_sync_transaction(load_inputs)
    episode = _reference_episode(project, script, script_filename)

    selected, unmatched, duplicated = _select_reference_units(script, unit_ids)
    if selected:
        action = "重新生成（已有成片一律覆盖）" if force else "生成（已有可用成片复用）"
        log.append(f"{action} {len(selected)} 个 unit：{', '.join(u['unit_id'] for u in selected)}")

    unmatched_tickets = [
        refused_ticket(
            unit_id,
            code=GenerationProblemCode.UNIT_NOT_FOUND,
            detail=f"unit {unit_id} 不在 video_units 中",
            action=GenerationAction.FIX_INPUT,
        )
        for unit_id in unmatched
    ]
    unmatched_tickets += [
        refused_ticket(
            unit_id,
            code=GenerationProblemCode.UNIT_REQUEST_INVALID,
            detail=f"unit {unit_id} 在剧本中重复出现",
            action=GenerationAction.FIX_INPUT,
        )
        for unit_id in duplicated
    ]

    return await _run_reference_batch(
        call=call,
        project=project,
        script=script,
        script_filename=script_filename,
        episode=episode,
        units=selected,
        selection=GenerationSelectionMode.EXPLICIT,
        extra_tickets=unmatched_tickets,
        reuse_existing=lambda currency, unit: (
            not force
            and _batch_video_is_reusable(
                currency=currency,
                episode=episode,
                resource_id=str(unit.get("unit_id") or ""),
                artifact_path=get_generated_assets(unit).get("video_clip"),
            )
        ),
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        log=log,
        operation=operation,
    )


@dataclass(frozen=True)
class _VideoRequestContext:
    """Video-tool request preparation: argument projection, script and generation-mode resolution."""

    script_filename: str
    request_options: ReferenceRequestOptions
    confirmed_request_durations: dict[str, int]
    project_dir: Path
    script: dict[str, Any]
    reference_route: str | None


def _video_request_context(call: _VideoCall, request: GenerateVideosRequest) -> _VideoRequestContext:
    """Project arguments into this request’s options, load the script and resolve its generation route."""

    script = call.projects.load_script(call.project_name, request.script)
    return _VideoRequestContext(
        script_filename=request.script,
        request_options=_reference_request_options(request),
        confirmed_request_durations=dict(request.confirmed_request_durations or {}),
        project_dir=call.project_path,
        script=script,
        reference_route=_resolve_reference_route(call, script),
    )


@dataclass(frozen=True)
class _StoryboardScreening:
    """Storyboard-to-video target entries, ID field, skeleton kind and screening outcomes."""

    items: list[dict[str, Any]]
    id_field: str
    skeleton_kind: str
    refused: list[UnitAdmissionTicket]


def _screen_script_targets(
    script: dict[str, Any],
    *,
    requested_ids: Collection[str] | None,
) -> _StoryboardScreening:
    """Read storyboard entries and screen malformed entries that cannot become targets.

    Derive skeleton kind from actual script shape, sharing generation-mode gating, so historical shapes within a family
    are not mistaken for parsing failures based on a kind inferred from content mode."""

    items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
    skeleton_kind = resolve_script_kind(script)
    items, screen_refused = screen_storyboard_items(items, id_field, requested_ids=requested_ids)
    return _StoryboardScreening(
        items=items,
        id_field=id_field,
        skeleton_kind=skeleton_kind,
        refused=screen_refused,
    )


@dataclass(frozen=True)
class _StoryboardContext:
    """Project-side storyboard-to-video context: project, episode identity and content mode."""

    project: dict[str, Any]
    episode: int
    content_mode: str


def _storyboard_context(call: _VideoCall, request: _VideoRequestContext) -> _StoryboardContext:
    project = call.projects.load_project(call.project_name)
    episode = resolve_artifact_episode(
        project=project,
        script=request.script,
        script_filename=request.script_filename,
    )
    return _StoryboardContext(
        project=project,
        episode=episode,
        content_mode=resolve_content_mode(request.script, project),
    )


@dataclass(frozen=True)
class _StoryboardBatch:
    """Batch context for one storyboard-to-video request: target rules, admission identity and result builder.

    The four scopes select and submit differently, but share TaskSpec construction, whole-batch admission and per-target
    outcome recording. Centralize those rules here so changing a decision does not affect only one entry point."""

    call: _VideoCall
    request: _VideoRequestContext
    sb: _StoryboardContext
    screening: _StoryboardScreening
    resolver: ArtifactCurrencyResolver
    operation: str
    selection: GenerationSelectionMode
    builder: GenerationResultBuilder
    states: dict[str, GenerationTargetState]
    log: list[str]

    def result(self) -> ToolOutcome[Any]:
        """Build a response from recorded per-target outcomes, or a quote for preflight."""

        if self.call.preview:
            return _preview_outcome(None, self.builder, self.log)
        return generation_result_outcome(self.builder.build(), self.log)

    async def build_specs(
        self,
        *,
        items: list[dict[str, Any]],
        skip_ids: list[str] | None,
    ) -> tuple[list[TaskSpec], list[UnitAdmissionTicket]]:
        """Build storyboard-to-video TaskSpec objects from this request’s target entries."""

        voice_characters = await resolve_voice_context(self.sb.project, self.sb.content_mode)
        # Content-based currency checks for storyboard references and first/end frames use synchronous I/O and hashing.
        return await asyncio.to_thread(
            build_storyboard_video_specs,
            items=items,
            id_field=self.screening.id_field,
            content_mode=self.sb.content_mode,
            skeleton_kind=self.screening.skeleton_kind,
            script_filename=self.request.script_filename,
            project_dir=self.request.project_dir,
            project=self.sb.project,
            episode=self.sb.episode,
            resolver=self.resolver,
            skip_ids=skip_ids,
            voice_characters=voice_characters,
        )

    def _record(self, successes: list[BatchTaskResult], failures: list[BatchTaskResult]) -> None:
        record_batch_outcomes(
            self.builder,
            successes=successes,
            failures=failures,
            states=self.states,
            resolver=self.resolver,
            fallback_path=_scene_fallback_relpath,
        )

    async def admit_and_submit(
        self,
        *,
        items: list[dict[str, Any]],
        specs: list[TaskSpec],
        extra_tickets: list[UnitAdmissionTicket],
    ) -> ToolOutcome[Any]:
        """Submit after whole-batch admission; relay refusal without queueing tasks if admission fails, or quote only for preflight."""

        # Admission and preflight quotes use the same video-request facts; configuration changes between evaluations would make quotes diverge from admission.
        facts = (
            await storyboard_video_request_facts(self.sb.project, self.call.services.capabilities) if specs else None
        )
        admission = await _admit_storyboard_specs(
            call=self.call,
            project=self.sb.project,
            script=self.request.script,
            script_filename=self.request.script_filename,
            items=items,
            id_field=self.screening.id_field,
            specs=specs,
            operation=self.operation,
            selection=self.selection,
            extra_tickets=extra_tickets,
            video_request_facts=facts,
        )
        if self.call.preview:
            items_by_id = {str(storyboard_item_id(item, self.screening.id_field) or ""): item for item in items}
            durations = {spec.resource_id: items_by_id[spec.resource_id].get("duration_seconds") for spec in specs}
            return _preview_outcome(
                admission,
                self.builder,
                self.log,
                storyboard_durations=durations,
                storyboard_costs=await quote_storyboard_video_units(
                    facts, durations, aspect_ratio=resolve_video_aspect_ratio(self.sb.project)
                ),
            )
        if not admission.admitted:
            return _batch_admission_response(BatchAdmissionRefused(admission), self.log, self.builder, self.states)

        for spec in specs:
            spec.unit_id = spec.resource_id
            spec.source = self.call.caller.source

        submitted = await submit_media_generation(
            scope=self.call.scope,
            caller=self.call.stop_on_failure_caller(),
            services=self.call.services,
            operation=self.operation,
            preflight=self.builder.build(),
            pending_ids=[spec.resource_id for spec in specs],
            specs=specs,
            states=self.states,
            admission={str(item["unit_id"]): item for item in admission.projections()},
        )
        if submitted.successes is None or submitted.failures is None:
            return generation_batch_submission_outcome(submitted.batch)
        await asyncio.to_thread(self._record, submitted.successes, submitted.failures)
        return generation_result_outcome(self.builder.build(), self.log, batch_id=submitted.batch.batch_id)


def _check_target_episode(call: _VideoCall, request: _VideoRequestContext, episode: int) -> None:
    """The whole-episode selector must identify the script’s owning episode; selecting the wrong episode must not spend money on the script’s episode."""

    project = call.projects.load_project(call.project_name)
    actual_episode = resolve_artifact_episode(
        project=project,
        script=request.script,
        script_filename=request.script_filename,
    ) or ProjectManager.resolve_episode_from_script(request.script, request.script_filename)
    if episode != actual_episode:
        raise ValueError(f"target.episode_id={episode} 与剧本所属的集 ID {actual_episode} 不一致")


async def _generate_episode(call: _VideoCall, request: _VideoRequestContext, log: list[str]) -> ToolOutcome[Any]:
    script_filename = request.script_filename
    project_dir = request.project_dir

    if request.reference_route is not None:
        return await _run_reference_episode(
            call=call,
            script=request.script,
            script_filename=script_filename,
            request_options=request.request_options,
            confirmed_request_durations=request.confirmed_request_durations,
            log=log,
            operation=_OPERATION,
        )
    screening = _screen_script_targets(request.script, requested_ids=None)
    items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
    sb = await asyncio.to_thread(_storyboard_context, call, request)
    episode = sb.episode
    if not items and not screen_refused:
        raise ValueError(f"{describe_episode_for_agent(sb.project, episode)} 的剧本为空：{script_filename}")

    def load_states() -> tuple[ArtifactCurrencyResolver, dict[str, GenerationTargetState]]:
        resolver = active_artifact_currency_resolver(project_dir, sb.project)
        return resolver, video_target_states(items, id_field, episode=episode, resolver=resolver)

    currency, states = await asyncio.to_thread(load_states)
    # Whole-episode generation only fills gaps, never forces regeneration: preserve usable old clips, including stale and uploaded videos.
    already_done = _missing_only_reusable_ids(states)
    builder = GenerationResultBuilder(_OPERATION, GenerationSelectionMode.MISSING_ONLY)
    batch = _StoryboardBatch(
        call=call,
        request=request,
        sb=sb,
        screening=screening,
        resolver=currency,
        operation=_OPERATION,
        selection=GenerationSelectionMode.MISSING_ONLY,
        builder=builder,
        states=states,
        log=log,
    )
    for done_id in already_done:
        state = _state_for(states, str(done_id))
        builder.skip(state)

    # A third state beyond currency: unreadable Manifest artifact state (BLOCKED) cannot be treated
    # as reusable (already_done), nor safely missing for enqueueing. Unreadable does not mean absent;
    # paid regeneration could overwrite a still-usable clip. The all scope uses
    # select_generation_targets to record this in selection.unavailable; this is
    # the other manually implemented path of the same decision and must handle it consistently.
    already_done_set = set(already_done)
    blocked_states = [
        state
        for unit_id, state in states.items()
        if unit_id not in already_done_set and state.status == ArtifactStatus.BLOCKED
    ]
    blocked_ids = [state.unit_id for state in blocked_states]
    refused = artifact_state_tickets(blocked_states)
    refused.extend(screen_refused)

    specs, spec_refused = await batch.build_specs(
        items=items,
        skip_ids=[*already_done, *blocked_ids],
    )
    refused.extend(spec_refused)

    if not specs and not refused and not builder.recorded_ids:
        raise RuntimeError("没有可生成的分镜")

    return await batch.admit_and_submit(
        items=items,
        specs=specs,
        extra_tickets=refused,
    )


async def _generate_all(call: _VideoCall, request: _VideoRequestContext, log: list[str]) -> ToolOutcome[Any]:
    script_filename = request.script_filename
    project_dir = request.project_dir

    if request.reference_route is not None:
        return await _run_reference_episode(
            call=call,
            script=request.script,
            script_filename=script_filename,
            request_options=request.request_options,
            confirmed_request_durations=request.confirmed_request_durations,
            log=log,
            operation=_OPERATION,
        )
    screening = _screen_script_targets(request.script, requested_ids=None)
    items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
    sb = await asyncio.to_thread(_storyboard_context, call, request)

    def select_targets() -> tuple[ArtifactCurrencyResolver, dict[str, GenerationTargetState], GenerationSelection]:
        resolver = active_artifact_currency_resolver(project_dir, sb.project)
        states = video_target_states(items, id_field, episode=sb.episode, resolver=resolver)
        selection = select_generation_targets(
            candidates=[state.candidate for state in states.values()],
            requested_ids=None,
            resolver=resolver,
        )
        return resolver, states, selection

    currency, states, selection = await asyncio.to_thread(select_targets)
    # Admission reports unreadable artifact states via admission tickets; do not duplicate them in result records:
    # recording one unit twice makes the result builder fail loudly.
    unavailable_tickets = artifact_state_tickets(selection.unavailable)
    builder = GenerationResultBuilder.from_selection(_OPERATION, replace(selection, unavailable=()))
    batch = _StoryboardBatch(
        call=call,
        request=request,
        sb=sb,
        screening=screening,
        resolver=currency,
        operation=_OPERATION,
        selection=GenerationSelectionMode.MISSING_ONLY,
        builder=builder,
        states=states,
        log=log,
    )
    if not selection.targets and not unavailable_tickets and not screen_refused:
        if call.preview:
            return batch.result()
        submitted = await submit_media_generation(
            scope=call.scope,
            caller=call.caller,
            services=call.services,
            operation=_OPERATION,
            preflight=builder.build(),
            pending_ids=[],
            specs=[],
            states=states,
        )
        if submitted.successes is None:
            return generation_batch_submission_outcome(submitted.batch)
        return generation_result_outcome(
            builder.build(),
            log,
            batch_id=submitted.batch.batch_id,
        )

    # Use the same ID fallback as ``_video_target_states``: if an entry lacks ``id_field``
    # but has ``scene_id``/``segment_id``, selection already recorded the fallback target;
    # recognizing only ``id_field`` here would remove it, leaving it requested but never queued.
    target_id_set = set(selection.target_ids)
    pending = [item for item in items if str(storyboard_item_id(item, id_field) or "") in target_id_set]
    specs, refused = await batch.build_specs(items=pending, skip_ids=None)
    # Target selection excludes unreadable-artifact storyboards from targets, but they still belong to this request:
    # omitting them from admission would queue and charge healthy storyboards while silently skipping this one.
    refused.extend(unavailable_tickets)
    refused.extend(screen_refused)
    if not specs and not refused:
        return batch.result()

    return await batch.admit_and_submit(
        items=pending,
        specs=specs,
        extra_tickets=refused,
    )


async def _generate_selected(
    call: _VideoCall,
    request: _VideoRequestContext,
    log: list[str],
    *,
    scene_ids: list[str],
    force: bool,
) -> ToolOutcome[Any]:
    script_filename = request.script_filename
    project_dir = request.project_dir

    if request.reference_route is not None:
        return await _run_reference_units(
            call=call,
            script_filename=script_filename,
            unit_ids=scene_ids,
            request_options=request.request_options,
            confirmed_request_durations=request.confirmed_request_durations,
            log=log,
            operation=_OPERATION,
            force=force,
        )

    screening = _screen_script_targets(request.script, requested_ids=set(scene_ids))
    items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
    sb = await asyncio.to_thread(_storyboard_context, call, request)
    episode = sb.episode

    items_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        # Index using the same addressable forms; raw list/dict aliases in malformed scripts would
        # raise TypeError as keys and collapse per-target outcomes into a generic error.
        for alias in storyboard_item_aliases(item, id_field):
            items_by_id[alias] = item

    builder = GenerationResultBuilder(_OPERATION, GenerationSelectionMode.EXPLICIT)
    selected: list[dict[str, Any]] = []
    refused: list[UnitAdmissionTicket] = []
    seen_canonical: set[str] = set()
    # ``items_by_id`` indexes the same item by both ``id_field`` and ``scene_id``;
    # callers listing both in ``scene_ids`` would queue the storyboard twice, so deduplicate again by
    # canonical ``id_field``.
    screened_ids = {ticket.unit_id for ticket in screen_refused}
    for sid in scene_ids:
        if sid in screened_ids:
            # Screening already recorded an outcome for this name; repeating it violates result-contract uniqueness.
            continue
        if sid not in items_by_id:
            refused.append(
                refused_ticket(
                    sid,
                    code=GenerationProblemCode.UNIT_NOT_FOUND,
                    detail=f"分镜 '{sid}' 不存在",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        item = items_by_id[sid]
        canonical = str(item.get(id_field, ""))
        if canonical and canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        selected.append(item)
    if not selected and not refused and not screen_refused:
        if call.preview:
            return _preview_outcome(None, builder, log)
        return generation_result_outcome(builder.build(), log)

    def load_states() -> tuple[ArtifactCurrencyResolver, dict[str, GenerationTargetState]]:
        resolver = active_artifact_currency_resolver(project_dir, sb.project)
        return resolver, video_target_states(selected, id_field, episode=episode, resolver=resolver)

    currency, states = await asyncio.to_thread(load_states)
    already_done: list[str] = []
    if not force:
        already_done = list(dict.fromkeys(state.unit_id for state in states.values() if artifact_is_reusable(state)))
    for done_id in already_done:
        builder.skip(_state_for(states, str(done_id)))
    batch = _StoryboardBatch(
        call=call,
        request=request,
        sb=sb,
        screening=screening,
        resolver=currency,
        operation=_OPERATION,
        selection=GenerationSelectionMode.EXPLICIT,
        builder=builder,
        states=states,
        log=log,
    )

    specs, spec_refused = await batch.build_specs(items=selected, skip_ids=already_done)
    refused.extend(spec_refused)
    refused.extend(screen_refused)

    return await batch.admit_and_submit(
        items=selected,
        specs=specs,
        extra_tickets=refused,
    )


async def generate_videos(
    request: ToolRequest[GenerateVideosRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GenerationToolValue]:
    args = request.value
    call = _VideoCall(scope=scope, caller=caller, services=services, preview=args.preview)
    target = args.target
    log: list[str] = []
    try:
        context = await run_sync_transaction(_video_request_context, call, args)
        if isinstance(target, EpisodeTarget):
            await asyncio.to_thread(_check_target_episode, call, context, target.episode_id)
            return await _generate_episode(call, context, log)
        if isinstance(target, AllTarget):
            return await _generate_all(call, context, log)
        # Deduplicate to avoid queueing the same ID twice; preserve first-occurrence order for readable logs.
        scene_ids = normalize_requested_ids(target.ids, field="target.ids") or []
        return await _generate_selected(call, context, log, scene_ids=scene_ids, force=args.force)
    except SpeechAdmissionError as exc:
        return _speech_admission_error(_OPERATION, exc, log)
    except Exception as exc:
        return tool_error(_OPERATION, exc, log)


__all__ = [
    "AllTarget",
    "EpisodeTarget",
    "GenerateVideosRequest",
    "SceneTarget",
    "SelectedTarget",
    "generate_videos",
]
