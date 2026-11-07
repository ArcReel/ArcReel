"""Project-state adapter for the all-or-nothing batch video admission.

Web and Agent batch entries share this seam. It resolves the current
request-projection, quote and queue state for every target of one request, then
folds the per-unit verdicts with :mod:`lib.generation.batch_admission`. Durable request
facts stay in ``lib.script.reference_video``; this module only adapts server state onto
them and never submits a task.

Both entries evaluate the same request options once — any confirmed request tiers —
so the two cannot reach different conclusions about the same project.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.config.resolver import ConfigResolver, video_bucket_for_generation_mode
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.generation.batch_admission import (
    BatchAdmission,
    UnitAdmissionTicket,
    refused_ticket,
)
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import TaskSpec, get_active_tasks_for_resources
from lib.generation.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationSelection,
    GenerationSelectionMode,
    GenerationTargetState,
    artifact_state_problem,
    observe_artifact_status,
    select_generation_targets,
)
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    VideoRequestCostFacts,
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from lib.prompts.prompt_utils import (
    is_structured_video_prompt,
    render_storyboard_video_prompt,
)
from lib.references.reference_admission import admit_storyboard_item
from lib.references.reference_catalog import build_reference_catalog
from lib.script.reference_video.artifact_selection import CurrentReferenceAssets
from lib.script.reference_video.provider_asset_links import configured_asset_link_check
from lib.script.reference_video.request_projection import (
    ProjectionProblem,
    ReferenceRequestOptions,
    ReferenceUnitRequestProjection,
    configured_reference_request_facts,
    project_reference_unit_request,
)
from lib.script.script_models import get_generated_assets
from lib.script.storyboard_sequence import StoryboardImageUnavailable
from lib.speech.speech_composition import SpeechAdmission, SpeechAdmissionError, require_script_unit_admitted
from server.services.admission.cost_estimation import quote_video_request
from server.services.admission.reference_admission import reference_admission_problems
from server.services.tasks.video_caps import (
    AudioSwitchConflict,
    assert_audio_switch_supported,
    resolve_project_is_silent,
)


def video_target_states(
    units: Sequence[Any],
    id_field: str,
    *,
    episode: int,
    resolver: ArtifactCurrencyResolver,
) -> dict[str, GenerationTargetState]:
    """Observe each unit's video artifact standing once, for the whole request.

    Every video entry reports the artifact axis from this one observation, so a
    task result and the artifact's current/stale standing never come from two
    different reads.
    """

    states: dict[str, GenerationTargetState] = {}
    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_id = str(storyboard_item_id(unit, id_field) or "")
        if not unit_id or unit_id in states:
            continue
        artifact_path = get_generated_assets(unit).get("video_clip")
        key = ArtifactKey.episode_video(episode, unit_id)
        status, blocker = observe_artifact_status(resolver=resolver, key=key, artifact_path=artifact_path)
        states[unit_id] = GenerationTargetState(
            candidate=GenerationCandidate(
                unit_id=unit_id,
                artifact_key=key,
                artifact_path=artifact_path if isinstance(artifact_path, str) else None,
            ),
            status=status,
            blocker=blocker,
        )
    return states


def artifact_state_tickets(states: Sequence[GenerationTargetState]) -> list[UnitAdmissionTicket]:
    """Turn targets with unreadable artifact state into admission tickets participating in the same all-or-nothing decision.

    This state means neither reusable nor missing-and-regenerable. Recording it only in the result contract
    without admission would still enqueue and bill healthy siblings, exactly the partial batch this gate prevents."""

    return [UnitAdmissionTicket(unit_id=state.unit_id, problems=(artifact_state_problem(state),)) for state in states]


def storyboard_item_id(item: dict[str, Any], id_field: str) -> object:
    """An item's self-reported ID, using the fallback order shared by entry points.

    Fall back to aliases only when the canonical field is missing or empty. Falling back for nonstrings
    such as ``0`` / ``[]`` would let aliases disguise invalid data: enqueue and bill by alias, while execution
    locates by canonical field and cannot produce the result."""

    canonical = item.get(id_field)
    if canonical is None or (isinstance(canonical, str) and not canonical.strip()):
        return item.get("scene_id") or item.get("segment_id")
    return canonical


def diagnostic_unit_id(base: str, taken: Collection[str]) -> str:
    """Name untargetable entries by position, ensuring no collision with valid script IDs.

    The result contract records unit_id once: a diagnostic name colliding with a real ID merges two distinct
    entries, failing loudly on the second write and giving users a generic error instead of per-target conclusions."""

    unit_id = base
    while unit_id in taken:
        unit_id += "*"
    return unit_id


def screen_script_entries(
    entries: object,
    *,
    requested_ids: Collection[str] | None,
) -> tuple[list[dict[str, Any]], list[UnitAdmissionTicket]]:
    """Partition video_units into targetable and untargetable entries, naming the latter by position.

    Nonobjects, nonstring/empty unit_id, and duplicate unit_id values break downstream ID indexing.
    Silently filtering them lets healthy siblings enqueue and incur charges alone; set lookups collapse
    per-target refusals into a generic error. Numeric, boolean, and object IDs can also pass through ``str()``
    into the queue, failing only when execution cannot match the original value; siblings may already be running and billed.

    Generate-missing targets the entire script, so every dirty entry participates. Named generation uses
    the caller's target set, so only dirt on named IDs (copies sharing that ID) participates; unrelated dirt
    must not veto a precisely targeted redo.

    Also name nonarray containers here: iteration would cause 500, while falsy values such as ``false``
    would pass as empty batches. Bracketed positional names use a separate namespace from valid IDs."""

    if not isinstance(entries, list):
        return [], [
            refused_ticket(
                "video_units",
                code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                detail=f"video_units 必须是数组，当前为 {type(entries).__name__}",
                action=GenerationAction.FIX_INPUT,
            )
        ]

    clean: list[dict[str, Any]] = []
    tickets: list[UnitAdmissionTicket] = []
    seen: set[str] = set()
    taken = {
        entry["unit_id"].strip()
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("unit_id"), str)
    }
    named = set(requested_ids) if requested_ids is not None else None
    if named is not None:
        # Named IDs also occupy the naming namespace: upstream records a missing target for names absent from the script,
        # so diagnostic-name collisions would merge those entries too.
        taken |= named
    for index, entry in enumerate(entries):
        detail: str | None = None
        unit_id = ""
        if not isinstance(entry, dict):
            detail = f"该条目不是对象，当前为 {type(entry).__name__}"
        else:
            raw_id = entry.get("unit_id")
            if raw_id is not None and not isinstance(raw_id, str):
                detail = f"该 unit 的 ID 不是字符串，当前为 {type(raw_id).__name__}"
            else:
                unit_id = (raw_id or "").strip()
                if not unit_id:
                    detail = "该 unit 没有可用的 unit_id"
        if detail is not None:
            if named is None:
                tickets.append(
                    refused_ticket(
                        diagnostic_unit_id(f"video_units[{index}]", taken),
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail=detail,
                        action=GenerationAction.FIX_INPUT,
                    )
                )
            continue
        entry_dict = cast("dict[str, Any]", entry)
        if named is not None and unit_id not in named:
            clean.append(entry_dict)
            continue
        if unit_id in seen:
            tickets.append(
                refused_ticket(
                    diagnostic_unit_id(f"{unit_id}#{index}", taken),
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"unit {unit_id} 在剧本中重复出现",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        seen.add(unit_id)
        clean.append(entry_dict)
    return clean, tickets


def storyboard_item_aliases(item: dict[str, Any], id_field: str) -> set[str]:
    """All names that can address this item in named generation.

    Entry points accept canonical ``id_field`` and ``scene_id`` aliases, which can differ in scripts.
    Either must address the same item, preventing one name selecting different items across entry points."""

    # Filter types before set construction: dirty-script list/dict aliases are unhashable and would raise TypeError,
    # collapsing the per-target refusal contract into a generic error.
    aliases = (item.get(id_field), item.get("scene_id"))
    return {alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()}


def screen_storyboard_items(
    items: Sequence[Any],
    id_field: str,
    *,
    requested_ids: Collection[str] | None,
) -> tuple[list[dict[str, Any]], list[UnitAdmissionTicket]]:
    """Partition script entries into targetable and untargetable entries, naming the latter by position.

    Nonobjects, nonstring/empty IDs, and duplicate IDs break downstream ID indexing. Silently filtering
    entries lets healthy targets enqueue and incur charges alone; set lookups collapse per-target refusals
    into a generic error. Numeric and boolean IDs passing through ``str()`` into the queue also fail to
    match original values at execution. Every entry point screens before reading IDs, turning these failures into named admission tickets.

    Generate-missing targets the entire script, so every dirty entry participates. Named generation uses
    the caller's target set, so only dirt on named IDs (copies sharing that ID) participates; unrelated dirt
    must not veto a precisely targeted redo.

    Bracketed positional names use a separate namespace from valid IDs. Collisions make the result
    contract merge two entries and fail loudly on the second write, again giving users a generic error."""

    clean: list[dict[str, Any]] = []
    tickets: list[UnitAdmissionTicket] = []
    seen: set[str] = set()
    addressable: list[tuple[dict[str, Any], str]] = []
    taken = {
        str(storyboard_item_id(item, id_field)).strip()
        for item in items
        if isinstance(item, dict) and isinstance(storyboard_item_id(item, id_field), str)
    }
    named = set(requested_ids) if requested_ids is not None else None
    if named is not None:
        # Named IDs also occupy the namespace: upstream records missing targets for absent names,
        # and diagnostic-name collisions would merge them.
        taken |= named
    refused_names: set[str] = set()
    for index, item in enumerate(items):
        detail: str | None = None
        item_id = ""
        if not isinstance(item, dict):
            detail = "该条目不是对象"
        else:
            raw_id = storyboard_item_id(item, id_field)
            if raw_id is not None and not isinstance(raw_id, str):
                detail = "该条目的 ID 不是字符串"
            else:
                item_id = (raw_id or "").strip()
                if not item_id:
                    detail = "该条目没有可用的 ID"
        if detail is not None:
            if named is None:
                tickets.append(
                    refused_ticket(
                        diagnostic_unit_id(f"items[{index}]", taken),
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail=detail,
                        action=GenerationAction.FIX_INPUT,
                    )
                )
            elif isinstance(item, dict):
                # The requested name addresses this dirty entry: report using the requested form, or callers receive only
                # a missing-target result despite the script containing that name.
                for name in sorted(storyboard_item_aliases(item, id_field) & named):
                    if name in refused_names:
                        continue
                    refused_names.add(name)
                    tickets.append(
                        refused_ticket(
                            name,
                            code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                            detail=detail,
                            action=GenerationAction.FIX_INPUT,
                        )
                    )
            continue
        if named is not None:
            addressable.append((item, item_id))
            continue
        if item_id in seen:
            tickets.append(
                refused_ticket(
                    diagnostic_unit_id(f"{item_id}#{index}", taken),
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"ID {item_id} 在剧本中重复出现",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        seen.add(item_id)
        clean.append(item)
    if named is None:
        return clean, tickets

    # Named generation accepts canonical IDs or ``scene_id`` aliases, which may differ; execution locates by canonical ID.
    # To count entries addressed by a name, include both directly addressed entries and siblings sharing their canonical ID.
    # Counting names alone misses different aliases with identical canonical IDs; entry points using different lookup rules
    # would choose the first or last, making the same requested name operate on different entries.
    by_canonical: dict[str, list[dict[str, Any]]] = {}
    for item, item_id in addressable:
        by_canonical.setdefault(item_id, []).append(item)
    ambiguous: set[int] = set()
    for name in sorted(named - refused_names):
        owners = {id(item) for item, _ in addressable if name in storyboard_item_aliases(item, id_field)}
        targets = {
            id(sibling) for item, item_id in addressable if id(item) in owners for sibling in by_canonical[item_id]
        }
        if len(targets) <= 1:
            continue
        ambiguous |= targets
        tickets.append(
            refused_ticket(
                name,
                code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                detail=f"ID {name} 在剧本中指向多个条目",
                action=GenerationAction.FIX_INPUT,
            )
        )
    clean.extend(item for item, _ in addressable if id(item) not in ambiguous)
    return clean, tickets


def resolve_reference_batch_targets(
    *,
    units: Sequence[Any],
    requested_ids: Sequence[str] | None,
    project: dict[str, Any],
    project_path: Path,
    episode: int,
) -> tuple[list[dict[str, Any]], GenerationSelection, dict[str, GenerationTargetState]]:
    """Resolve one request's stable target set before anything is evaluated.

    ``requested_ids`` of ``None`` means missing-only: a unit whose paid video is
    still usable — current *or* stale — stays out of the request, because a stale
    artifact is the user's to replace by naming it, not the batch's to silently
    re-bill. Named ids are taken as given and always regenerate.
    """

    resolver = active_artifact_currency_resolver(project_path, project)
    states = video_target_states(units, "unit_id", episode=episode, resolver=resolver)
    selection = select_generation_targets(
        candidates=[state.candidate for state in states.values()],
        requested_ids=requested_ids,
        resolver=resolver,
    )
    target_ids = set(selection.target_ids)
    targets = [unit for unit in units if isinstance(unit, dict) and str(unit.get("unit_id") or "") in target_ids]
    return targets, selection, states


def reference_unit_task_spec(unit: object, script_file: str) -> TaskSpec:
    """Build a single unit's TaskSpec, sharing structural validation across batch admission, batch enqueue,
    and duration preflight (see ADR-0001). ``TaskSpec.from_request`` is the sole source for enqueueability;
    separate implementations must not diverge (e.g. preflight allowing an empty prompt that enqueue rejects)."""

    # Normalize with .get: bad data lacking unit_id (Agent can directly write script JSON) is rejected by from_request
    # as empty resource_id instead of raising KeyError here and interrupting the batch.
    if not isinstance(unit, dict):
        raise ValueError("unit 必须是对象")
    unit_id = str(unit.get("unit_id") or "")
    if unit.get("needs_replan") is True:
        require_script_unit_admitted("video_units", unit)
    text = unit.get("text")
    if not isinstance(text, str):
        # Centralize container checks at enqueue validation: dirty dict/numeric values (import / direct Agent script writes)
        # would otherwise raise TypeError in downstream prompt assembly, turning the batch into 500 instead of letting this unit
        # enter admission conclusions with its own problem code.
        raise ValueError(f"text 必须是字符串，当前为 {type(text).__name__}")
    if not text.strip():
        raise ValueError("正文为空")
    spec = TaskSpec.from_request(
        task_type="reference_video",
        media_type="video",
        resource_id=unit_id,
        prompt=text,
        script_file=script_file,
    )
    require_script_unit_admitted("video_units", unit)
    return spec


_SPEECH_ACTIONS: dict[str, GenerationAction] = {
    "replan_unit": GenerationAction.REPLAN_UNIT,
    "assign_speaker": GenerationAction.FIX_INPUT,
    "fix_input": GenerationAction.FIX_INPUT,
}

_PROBLEM_ACTIONS: dict[str, GenerationAction] = {
    "replan_unit": GenerationAction.REPLAN_UNIT,
    "confirm_request_duration": GenerationAction.CONFIRM_REQUEST_DURATION,
    "configure_provider": GenerationAction.CONFIGURE_PROVIDER,
    "fix_input": GenerationAction.FIX_INPUT,
    "assign_speaker": GenerationAction.FIX_INPUT,
    "confirm_duration": GenerationAction.CONFIRM_REQUEST_DURATION,
    "configure_video_model": GenerationAction.CONFIGURE_PROVIDER,
    "enable_model_audio": GenerationAction.CONFIGURE_PROVIDER,
    "repair_reference_assets": GenerationAction.GENERATE_DEPENDENCY,
    "review_reference_selection": GenerationAction.FIX_INPUT,
    "review_request_configuration": GenerationAction.FIX_INPUT,
}


def generation_action_for(raw: object) -> GenerationAction:
    """Map a request-planning action onto the generation-result action set.

    Anything the planning modules add later degrades to ``FIX_INPUT``: telling a
    caller to look at its own request is the only next step that is safe when the
    verdict's meaning is not yet known here.
    """

    return _PROBLEM_ACTIONS.get(str(raw), GenerationAction.FIX_INPUT)


def _facts_failure_problem(failure: VideoRequestFactsFailure, unit_id: str) -> GenerationProblem:
    """Turn video-request-fact failures into target blockers: share the projection-side source for problem codes, parameters, and repair guidance."""

    return _generation_problem(ProjectionProblem.from_request_facts_failure(failure), unit_id=unit_id)


def _generation_problem(problem: ProjectionProblem, *, unit_id: str) -> GenerationProblem:
    payload = problem.to_payload(unit_id=unit_id)
    params = payload.get("params")
    return GenerationProblem(
        code=problem.code,
        detail=str(payload.get("reason") or problem.code),
        action=generation_action_for(payload.get("action")),
        params=params if isinstance(params, dict) else {},
    )


def speech_admission_problems(admission: SpeechAdmission) -> tuple[GenerationProblem, ...]:
    """Lift every speech blocker into the shared generation problem contract."""

    payload = admission.to_dict()
    return tuple(
        GenerationProblem(
            code=problem.code.value,
            detail=problem.reason.value,
            action=_SPEECH_ACTIONS.get(problem.action.value, GenerationAction.FIX_INPUT),
            params={"unit_id": admission.unit_id, "speech_admission": payload},
        )
        for problem in admission.problems
    )


def _speech_problem(exc: SpeechAdmissionError) -> GenerationProblem:
    return speech_admission_problems(exc.admission)[0]


def active_task_problem(task: Mapping[str, Any]) -> GenerationProblem:
    return GenerationProblem(
        code=GenerationProblemCode.ACTIVE_TASK_CONFLICT,
        detail=f"该 unit 已有在途任务（状态：{task.get('status')}），等待其结束后再提交本次批量请求",
        action=GenerationAction.WAIT_FOR_TASK,
        params={"task_id": str(task.get("id") or task.get("task_id") or ""), "status": str(task.get("status") or "")},
    )


async def _quote_for_display(cost: VideoRequestCostFacts | None) -> dict[str, object] | None:
    if cost is None:
        return None
    quote = await quote_video_request(cost, async_session_factory)
    return quote.to_payload() if quote is not None else None


async def _active_conflicts(
    *,
    project_name: str,
    task_type: str,
    script_file: str | None,
    unit_ids: Sequence[str],
    user_id: str = DEFAULT_USER_ID,
    queue: GenerationQueue | None = None,
) -> dict[str, dict[str, Any]]:
    """Map unit id → the active task that already occupies it, if any."""

    if not unit_ids:
        return {}
    active = await get_active_tasks_for_resources(
        project_name=project_name,
        task_type=task_type,
        resource_ids=list(unit_ids),
        script_file=script_file,
        user_id=user_id,
        queue=queue,
    )
    return {str(task["resource_id"]): task for task in active if task.get("resource_id")}


def request_options_for_unit(
    options: ReferenceRequestOptions,
    unit_id: str,
    confirmed: Mapping[str, int] | None,
) -> ReferenceRequestOptions:
    """Apply this request's per-unit tier consent, if the entry collected any.

    Admission and the task spec that follows it must read the same options, so
    both sides call this rather than re-deriving the consent from the request
    body — a second derivation is where the two can silently diverge.
    """

    if not confirmed or unit_id not in confirmed:
        return options
    return replace(options, confirmed_request_duration_seconds=confirmed[unit_id])


async def admit_reference_video_batch(
    *,
    project_name: str,
    project: dict[str, Any],
    project_path: Path,
    script: dict[str, Any],
    script_file: str,
    units: Sequence[dict[str, Any]],
    request_options: ReferenceRequestOptions,
    operation: str,
    selection: GenerationSelectionMode,
    confirmed_request_durations: Mapping[str, int] | None = None,
    spec_check: Callable[[dict[str, Any]], object] | None = None,
    extra_tickets: Sequence[UnitAdmissionTicket] = (),
    user_id: str = DEFAULT_USER_ID,
    queue: GenerationQueue | None = None,
    config_resolver: ConfigResolver | None = None,
) -> BatchAdmission:
    """Evaluate every reference unit of one request against the current state.

    ``spec_check`` is the caller's own "can this unit be requested at all" guard
    (structural enqueue validation and speech admission). Sharing it keeps the
    admission and the later spec construction on one verdict instead of two.

    ``extra_tickets`` are targets the caller already refused before reaching this
    seam (an unreadable artifact state, an id that is not in the script). They
    belong to the same request, so they take part in the same all-or-nothing fold
    rather than being reported next to a batch that went ahead without them.
    """

    resolver = config_resolver or ConfigResolver(async_session_factory)
    request_facts_lookup = configured_reference_request_facts(project, resolver)
    # Share provider-asset-association checks across the batch, assembling each execution model's call channel once.
    asset_links = configured_asset_link_check(project_path=project_path, resolver=resolver)
    unit_ids = [str(unit.get("unit_id") or "") for unit in units if str(unit.get("unit_id") or "")]
    conflicts = await _active_conflicts(
        project_name=project_name,
        task_type="reference_video",
        script_file=script_file,
        unit_ids=unit_ids,
        user_id=user_id,
        queue=queue,
    )

    tickets: list[UnitAdmissionTicket] = list(extra_tickets)
    seen_ids: set[str] = set()
    # Share availability checks across units on one manifest snapshot; construct on the first projected unit and retry on the next if construction fails.
    shared_assets = functools.cache(lambda: CurrentReferenceAssets(project_path, project))
    for index, unit in enumerate(units):
        unit_id = str(unit.get("unit_id") or "")
        # Never silently drop dirty entries (missing unit_id / duplicate IDs) from targets: healthy siblings would
        # enqueue and incur charges alone, exactly the partial batch this gate prevents. Report missing IDs by diagnostic ID and
        # reject duplicate copies; both stop the batch here for users to repair scripts.
        if not unit_id:
            tickets.append(
                refused_ticket(
                    f"video_units[{index}]",
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail="该 unit 没有可用的 unit_id",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        if unit_id in seen_ids:
            tickets.append(
                refused_ticket(
                    f"{unit_id}#{index}",
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"unit_id {unit_id} 在剧本中重复出现",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        seen_ids.add(unit_id)
        problems: list[GenerationProblem] = []
        if unit_id in conflicts:
            problems.append(active_task_problem(conflicts[unit_id]))
        if spec_check is not None:
            try:
                spec_check(unit)
            except SpeechAdmissionError as exc:
                problems.append(_speech_problem(exc))
            except ValueError as exc:
                problems.append(
                    GenerationProblem(
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail=f"入队校验未通过：{exc}",
                        action=GenerationAction.FIX_INPUT,
                    )
                )
        if problems:
            tickets.append(UnitAdmissionTicket(unit_id=unit_id, problems=tuple(problems)))
            continue

        unit_options = request_options_for_unit(request_options, unit_id, confirmed_request_durations)
        try:
            projection = await project_reference_unit_request(
                request_facts_lookup=request_facts_lookup,
                project=project,
                script=script,
                unit=unit,
                project_path=project_path,
                options=unit_options,
                resolver=resolver,
                shared_assets=shared_assets,
                asset_links=asset_links,
            )
        except VideoRequestFactsError as exc:
            tickets.append(
                UnitAdmissionTicket(unit_id=unit_id, problems=(_facts_failure_problem(exc.failure, unit_id),))
            )
            continue
        except ValueError as exc:
            # Projection reads script values (e.g. duration_seconds); letting dirty values escape here would collapse the request
            # to a generic error without other unit conclusions. Report accurate per-unit enqueueability problems.
            tickets.append(
                refused_ticket(
                    unit_id,
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"请求投影失败：{exc}",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        tickets.append(await _reference_ticket(projection))

    return BatchAdmission(
        operation=operation,
        selection=selection,
        tickets=tuple(tickets),
    )


async def _reference_ticket(projection: ReferenceUnitRequestProjection) -> UnitAdmissionTicket:
    unit_id = projection.unit_id
    payload = projection.to_advisory_payload()
    request_duration = projection.request_duration.seconds if projection.request_duration is not None else None
    cost_payload = await _quote_for_display(projection.cost)
    problems = [_generation_problem(problem, unit_id=unit_id) for problem in projection.blocking_problems]
    if cost_payload is not None:
        payload["request_cost"] = cost_payload
    return UnitAdmissionTicket(
        unit_id=unit_id,
        problems=tuple(problems),
        request_duration_seconds=request_duration,
        request_cost=cost_payload,
        projection=payload,
    )


async def admit_storyboard_video_batch(
    *,
    project_name: str,
    project: dict[str, Any],
    script_file: str,
    items: Sequence[tuple[str, dict[str, Any]]],
    operation: str,
    selection: GenerationSelectionMode,
    extra_tickets: Sequence[UnitAdmissionTicket] = (),
    user_id: str = DEFAULT_USER_ID,
    queue: GenerationQueue | None = None,
    config_resolver: ConfigResolver | None = None,
    video_request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None,
) -> BatchAdmission:
    """Evaluate every storyboard unit of one request against the current state.

    ``items`` are ``(resource_id, script item)`` pairs. This route takes its tier from
    the script alone at execution, so the shared gates here are the active-task
    conflict, reference admission and the route's video request facts; each remaining
    unit is admitted as it would be on its own.
    """

    resource_ids = [resource_id for resource_id, _item in items]
    conflicts = await _active_conflicts(
        project_name=project_name,
        task_type="video",
        script_file=script_file,
        unit_ids=resource_ids,
        user_id=user_id,
        queue=queue,
    )
    generation_type = video_bucket_for_generation_mode(project.get("generation_mode"))
    catalog = build_reference_catalog(project)
    if items and video_request_facts is None:
        video_request_facts = await evaluate_video_request_facts(
            project,
            route="storyboard",
            generation_type=generation_type,
            identity=CONFIGURED_VIDEO_IDENTITY,
            resolver=config_resolver or ConfigResolver(async_session_factory),
        )

    tickets: list[UnitAdmissionTicket] = list(extra_tickets)
    for resource_id, item in items:
        if resource_id in conflicts:
            tickets.append(
                UnitAdmissionTicket(unit_id=resource_id, problems=(active_task_problem(conflicts[resource_id]),))
            )
            continue
        reference_problems = reference_admission_problems(admit_storyboard_item(catalog, item), unit_id=resource_id)
        if isinstance(video_request_facts, VideoRequestFactsFailure):
            # Fact failures and reference gaps are both known target problems; report together so fixing model configuration does not expose another already-known reference gap.
            tickets.append(
                UnitAdmissionTicket(
                    unit_id=resource_id,
                    problems=(_facts_failure_problem(video_request_facts, resource_id), *reference_problems),
                )
            )
            continue
        tickets.append(UnitAdmissionTicket(unit_id=resource_id, problems=reference_problems))

    return BatchAdmission(
        operation=operation,
        selection=selection,
        tickets=tuple(tickets),
    )


def speech_admission_ticket(unit_id: str, exc: SpeechAdmissionError) -> UnitAdmissionTicket:
    """Refuse a target on its speech admission, carrying that verdict through.

    The admission's own problem code and action are kept unchanged, so a caller
    never has to read the rendered text to decide what to do next.
    """

    problem = exc.admission.problems[0]
    return refused_ticket(
        unit_id or exc.admission.unit_id,
        code=problem.code.value,
        detail=f"发声准入未通过：{json.dumps(exc.admission.to_dict(), ensure_ascii=False)}",
        action=(GenerationAction.REPLAN_UNIT if problem.action.value == "replan_unit" else GenerationAction.FIX_INPUT),
        params={"speech_admission": exc.admission.to_dict()},
    )


def storyboard_video_prompt(
    item: dict[str, Any], *, content_mode: str, voice_characters: dict[str, Any] | None = None
) -> str:
    prompt = item.get("video_prompt")
    if not prompt:
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"分镜缺少 video_prompt 字段: {item_id}")
    if isinstance(prompt, dict) and not is_structured_video_prompt(prompt):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"分镜 video_prompt 为对象但格式不符合结构化规范: {item_id}")
    if not isinstance(prompt, (dict, str)):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise TypeError(f"分镜 video_prompt 类型无效（期望 str 或 dict）: {item_id}")
    # Share render_storyboard_video_prompt with execution: enqueue snapshots and outgoing text have the same structure;
    # this exit centrally appends negative-constraint suffixes (idempotent on already-suffixed strings at execution).
    return render_storyboard_video_prompt(prompt, item, content_mode=content_mode, voice_characters=voice_characters)


async def resolve_voice_context(project: dict[str, Any], content_mode: str) -> dict[str, Any] | None:
    """Character assets for Voice_Profiles injection (``None`` means no injection).

    Inject only for drama, excluding silent cases (class C models produce no sound, or episode audio is
    disabled; both use the same rule). Dialogue is unaffected and still sent normally."""
    if content_mode != "drama":
        return None
    if await resolve_project_is_silent(project):
        return None
    return project.get("characters") or {}


async def audio_switch_conflict(
    project: dict[str, Any], *, request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None
) -> GenerationProblem | None:
    """Audio gate for storyboard image-to-video (``assert_audio_switch_supported``, same criterion as WebUI submission).

    Always-audible-output models cannot receive audio-off requests; allowing them would let the silence
    criterion remove voice constraints for the whole batch. The gate is independent of content mode;
    narration/ad are checked too.

    Call only after establishing actual request targets: completed episodes or entries all filtered by
    :func:`build_storyboard_video_specs` produce no requests; rejection would turn a normal no-op into an error.
    Reference-to-video uses the same audio-capability check through shared request projection.

    Return a conflict problem with provider/model parameters for UI display names, or ``None`` without
    conflict. Callers fold it into per-target admission alongside other gaps, reporting all before task creation."""
    try:
        await assert_audio_switch_supported(
            project,
            video_bucket_for_generation_mode(project.get("generation_mode")),
            request_facts=request_facts,
        )
    except AudioSwitchConflict as exc:
        return GenerationProblem(
            code="video_audio_switch_not_supported",
            detail=str(exc),
            action=GenerationAction.CONFIGURE_PROVIDER,
            params={"provider": exc.provider_id, "model": exc.model_id},
        )
    return None


def build_storyboard_video_specs(
    *,
    items: list[dict[str, Any]],
    id_field: str,
    content_mode: str,
    skeleton_kind: str,
    script_filename: str,
    project_dir: Path,
    skip_ids: list[str] | None,
    project: dict[str, Any] | None = None,
    episode: int = 1,
    resolver: ArtifactCurrencyResolver | None = None,
    voice_characters: dict[str, Any] | None = None,
) -> tuple[list[TaskSpec], list[UnitAdmissionTicket]]:
    """Build the Storyboard-mode specs, refusing each unit that cannot be requested.

    A unit whose speech, inputs or prompt are unusable is refused with its own code
    and next action instead of being dropped into a log line, so one bad unit never
    silently shrinks the batch, and because the refusal is a ticket rather than a
    recorded block, it can hold the whole batch back before any task exists.

    ``skeleton_kind`` is the actual script skeleton from the generation-mode gate, not inferred from content
    mode: adapting historical in-family shapes (narration data under ``scenes``) by inference would reject
    valid old scripts as batch-wide parse failures.

    Speech admission occurs here, corresponding to ``reference_unit_task_spec`` for reference-to-video:
    TaskSpec construction is the sole criterion for whether this unit can be requested. All four storyboard
    entry points (episode / all / named / single) and read-only workflow plans pass through it. Mixed speech
    and needs_replan therefore hold the entire batch before task creation at every entry point, and plan
    admission previews agree with actual submission."""

    item_type = "分镜"
    skip_set = set(skip_ids or [])

    specs: list[TaskSpec] = []
    refused: list[UnitAdmissionTicket] = []
    project = project or {}
    if resolver is None:
        resolver = active_artifact_currency_resolver(project_dir, project)
    for item in items:
        item_id = str(storyboard_item_id(item, id_field))
        if item_id in skip_set:
            continue

        try:
            require_script_unit_admitted(skeleton_kind, item)
        except SpeechAdmissionError as exc:
            # Per-ID contracts coexist with existing speech_admission payloads: the former says which ID failed and
            # what to do next, while the latter carries admission's full location information.
            refused.append(speech_admission_ticket(str(item_id), exc))
            continue

        try:
            resolve_usable_storyboard_video_inputs(
                project_path=project_dir,
                project=project,
                episode=episode,
                resource_id=str(item_id),
                item=item,
                resolver=resolver,
            )
        except (OSError, ValueError) as exc:
            # An unusable storyboard image is the most common case; give the next operation instead of only reporting a path.
            advice = "，请先运行 generate_storyboards" if isinstance(exc, StoryboardImageUnavailable) else ""
            refused.append(
                refused_ticket(
                    str(item_id),
                    code=GenerationProblemCode.UNIT_INPUT_UNUSABLE,
                    detail=f"{item_type} {item_id} 的视频输入不可用: {exc}{advice}",
                    action=GenerationAction.GENERATE_DEPENDENCY,
                )
            )
            continue

        try:
            prompt = storyboard_video_prompt(item, content_mode=content_mode, voice_characters=voice_characters)
        except Exception as exc:
            refused.append(
                refused_ticket(
                    str(item_id),
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"{item_type} {item_id} 的 video_prompt 无效: {exc}",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue

        # Duration is a capability dimension, validated by execution after provider resolution (ADR-0001).
        # Pass explicit caller values through unchanged, without enqueue-side int() truncation
        # silently correcting values execution should reject. Execution centralizes omitted defaults using capabilities.
        extra_payload: dict[str, Any] = {}
        duration = item.get("duration_seconds")
        if duration is not None:
            extra_payload["duration_seconds"] = duration

        # TaskSpec construction itself decides enqueueability (empty prompts, etc.; ``TaskSpecValidationError``
        # subclasses ValueError). Without catching it, one bad entry collapses the batch into a generic error,
        # hiding every other entry's conclusion.
        try:
            spec = TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id=item_id,
                prompt=prompt,
                script_file=script_filename,
                extra_payload=extra_payload or None,
            )
        except ValueError as exc:
            refused.append(
                refused_ticket(
                    str(item_id),
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"{item_type} {item_id} 无法构造入队请求: {exc}",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        specs.append(spec)
    return specs, refused


async def storyboard_video_request_facts(
    project: dict[str, Any], config_resolver: ConfigResolver | None = None
) -> VideoRequestFacts | VideoRequestFactsFailure:
    """Storyboard image-to-video request facts for current project configuration, shared by admission and quotation."""

    return await evaluate_video_request_facts(
        project,
        route="storyboard",
        generation_type=video_bucket_for_generation_mode(project.get("generation_mode")),
        identity=CONFIGURED_VIDEO_IDENTITY,
        resolver=config_resolver or ConfigResolver(async_session_factory),
    )


async def admit_storyboard_video_request(
    *,
    project_name: str,
    project: dict[str, Any],
    script_file: str,
    items: Sequence[dict[str, Any]],
    id_field: str,
    specs: Sequence[TaskSpec],
    operation: str,
    selection: GenerationSelectionMode,
    extra_tickets: Sequence[UnitAdmissionTicket],
    user_id: str = DEFAULT_USER_ID,
    queue: GenerationQueue | None = None,
    config_resolver: ConfigResolver | None = None,
    video_request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None,
) -> BatchAdmission:
    """Admit one Storyboard-mode request from the specs it would actually enqueue.

    Targets come from upcoming enqueue specs, excluding refused units. Callers may pass already-evaluated
    ``video_request_facts`` (batch previews also quote from the same facts); evaluate here if omitted.

    Audio-switch conflicts are known configuration gaps of the request itself; fold them into the same
    per-target conclusions alongside other gaps. Short-circuiting would report only this one, making users
    fix configuration and retry before encountering the next already-known gap."""

    items_by_id = {str(storyboard_item_id(item, id_field) or ""): item for item in items}
    targets: list[tuple[str, dict[str, Any]]] = []
    for spec in specs:
        item = items_by_id.get(spec.resource_id)
        if item is None:
            raise ValueError(f"找不到待生成条目: {spec.resource_id}")
        targets.append((spec.resource_id, item))
    request_facts = (
        (video_request_facts or await storyboard_video_request_facts(project, config_resolver)) if specs else None
    )
    conflict = await audio_switch_conflict(project, request_facts=request_facts) if specs else None
    admission = await admit_storyboard_video_batch(
        project_name=project_name,
        project=project,
        script_file=script_file,
        items=targets,
        operation=operation,
        selection=selection,
        extra_tickets=extra_tickets,
        user_id=user_id,
        queue=queue,
        config_resolver=config_resolver,
        video_request_facts=request_facts,
    )
    if conflict is None:
        return admission
    # Fold audio-switch conflicts and projection gaps into the same tickets: short-circuiting reports only this one, requiring configuration
    # fixes and retries to discover the next known gap, precisely the one-by-one probing this gate prevents.
    target_ids = {spec.resource_id for spec in specs}
    return replace(
        admission,
        tickets=tuple(
            replace(ticket, problems=(*ticket.problems, conflict)) if ticket.unit_id in target_ids else ticket
            for ticket in admission.tickets
        ),
    )


__all__ = [
    "active_task_problem",
    "admit_reference_video_batch",
    "admit_storyboard_video_batch",
    "admit_storyboard_video_request",
    "artifact_state_tickets",
    "audio_switch_conflict",
    "build_storyboard_video_specs",
    "reference_unit_task_spec",
    "request_options_for_unit",
    "resolve_reference_batch_targets",
    "resolve_voice_context",
    "screen_storyboard_items",
    "speech_admission_problems",
    "speech_admission_ticket",
    "storyboard_item_aliases",
    "storyboard_video_prompt",
    "storyboard_video_request_facts",
    "video_target_states",
]
