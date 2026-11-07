"""Reference-video CRUD and generation routes.

Mount prefix: /api/v1/projects/{project_name}/reference-videos"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from lib.artifacts.artifact_activation import resolve_artifact_episode
from lib.artifacts.version_manager import VersionManager
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.batch_admission import BatchAdmission, BatchAdmissionDecision, refused_ticket
from lib.generation.generation_queue import get_generation_queue
from lib.generation.generation_queue_client import (
    TaskSpec,
    TaskSpecValidationError,
    batch_enqueue_only,
)
from lib.generation.generation_result import (
    GenerationAction,
    GenerationProblemCode,
    GenerationSelection,
    GenerationSelectionMode,
    normalize_requested_ids,
)
from lib.i18n import render_message
from lib.i18n.display_names import DisplayNames
from lib.infra.api_errors import ApiError, BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager, is_reference_video_project
from lib.project.resource_paths import resource_relative_path
from lib.script.reference_video.request_projection import (
    ReferenceRequestFactsLookup,
    ReferenceRequestOptions,
    ReferenceUnitRequestProjection,
    configured_reference_request_facts,
    project_reference_unit_request,
)
from lib.script.reference_video.script_preview import build_script_preview
from lib.script.reference_video.unit_capabilities import hydrate_reference_units
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_batch_edit import ScriptBatchEditResult
from lib.script.script_editor import ScriptEditError, new_item_id
from lib.speech.speech_composition import admit_script_unit, refresh_video_unit_replan_state
from server.auth import CurrentUser
from server.error_handlers import script_edit_detail
from server.i18n import DisplayNamesCatalog, Translator
from server.routers._batch_admission import enqueue_failure_payload, localized_admission_payload
from server.routers._script_edits import execute_current_episode_edit, require_script_edit_result
from server.routers._validators import reject_retired_query_params
from server.services.admission.cost_estimation import BatchCostEstimate
from server.services.admission.reference_prompt_preview import render_reference_prompt_preview
from server.services.admission.video_batch_admission import (
    admit_reference_video_batch,
    artifact_state_tickets,
    reference_unit_task_spec,
    request_options_for_unit,
    resolve_reference_batch_targets,
    screen_script_entries,
)
from server.services.currency.upload_finalize import (
    UploadValidationError,
    commit_manual_video_upload,
    stage_uploaded_video_stream,
    validate_upload,
)
from server.services.tasks.generation_tasks import emit_generation_success_batch
from server.services.tasks.reference_video_tasks import (
    apply_unit_video_assets,
    default_unit_duration,
)
from server.services.tasks.video_caps import (
    project_video_caps,
    reference_request_facts_lookup,
    reference_unit_capabilities,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_name}/reference-videos",
    tags=["reference-videos"],
)

# ============ Request models ============


class ScriptPreviewRequest(BaseModel):
    prompt: str = ""


class AddUnitRequest(BaseModel):
    # extra="forbid": unit bodies are authoritative and references derive at execution. Legacy clients supplying
    # ``references`` must receive 422 rather than silent omission, which would falsely suggest
    # their selected references took effect.
    model_config = ConfigDict(extra="forbid")

    prompt: str
    duration_seconds: int | None = Field(default=None, ge=1)
    note: str | None = None
    #: Insert the new unit after this unit; omission appends.
    after_unit_id: str | None = Field(default=None, min_length=1)


class GenerateUnitRequest(BaseModel):
    # Narration delivery is project configuration independent of video requests; reject removed per-request fields as unknown.
    model_config = ConfigDict(extra="forbid")

    confirmed_request_duration_seconds: int | None = Field(default=None, gt=0)

    def projection_options(self) -> ReferenceRequestOptions:
        return ReferenceRequestOptions(confirmed_request_duration_seconds=self.confirmed_request_duration_seconds)


class GenerateUnitsBatchRequest(BaseModel):
    """Batch video-generation request.

    Omitted ``unit_ids`` fills missing items only; selected IDs regenerate those units.
    ``confirmed_request_durations`` contains tiers the user accepted through aggregated confirmation."""

    model_config = ConfigDict(extra="forbid")

    unit_ids: list[str] | None = None
    confirmed_request_durations: dict[str, PositiveInt] = Field(default_factory=dict)


# ============ Helpers ============


def _load_episode_script(
    project_name: str, episode: int, _t: Translator, *, readonly: bool = False
) -> tuple[dict, dict, str]:
    """Load project.json and the selected episode script; return (project, script, script_file)."""
    try:
        project = get_project_manager().load_project(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    episodes = project.get("episodes") or []
    meta = next((e for e in episodes if e.get("episode") == episode), None)
    if meta is None or not meta.get("script_file"):
        raise HTTPException(status_code=404, detail=_t("ref_episode_not_found", episode=episode))
    script_file = meta["script_file"]
    try:
        pm = get_project_manager()
        load = pm.load_script_readonly if readonly else pm.load_script
        script = load(project_name, script_file)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    if not is_reference_video_project(project):
        raise HTTPException(status_code=409, detail=_t("ref_not_reference_video_mode"))
    return project, script, script_file


async def _read_episode_script(project_name: str, episode: int, _t: Translator) -> tuple[dict, dict, str]:
    """Async ``_load_episode_script`` entry point: disk reads, parsing and lock waits run in a worker thread.

    Script reads may persist legacy migrations under the script lock, so use ``run_sync_transaction`` as a formal write."""
    return await run_sync_transaction(_load_episode_script, project_name, episode, _t)


async def _project_path(project_name: str) -> Path:
    return await asyncio.to_thread(get_project_manager().get_project_path, project_name)


async def _read_script(project_name: str, script_file: str) -> dict:
    """Reread scripts after edits; may persist migrations like ``_read_episode_script``."""
    return await run_sync_transaction(get_project_manager().load_script, project_name, script_file)


async def _commit_episode_edit(
    project_name: str,
    episode: int,
    script_file: str,
    current: dict,
    operations: list[dict[str, Any]],
) -> ScriptBatchEditResult:
    """Edit scripts in a worker thread so project-lock waits and writes do not block the event loop.

    Once started, finish before propagating cancellation, keeping caller outcomes consistent with persistence. Broadcast
    as ``webui``; artifacts moved to trash on unit deletion are also recorded as user deletions."""
    with project_change_source("webui"):
        return await run_sync_transaction(
            execute_current_episode_edit,
            get_project_manager(),
            project_name,
            episode,
            script_file,
            current,
            operations,
        )


def _problem_payload(
    projection: ReferenceUnitRequestProjection, _t: Translator, names: DisplayNames
) -> list[dict[str, Any]]:
    payloads = projection.problem_payloads()
    for payload, problem in zip(payloads, projection.problems, strict=True):
        payload["message"] = render_message(problem.code, problem.parameters(), _t, names)
    return payloads


def _raise_projection_blocker(
    projection: ReferenceUnitRequestProjection,
    _t: Translator,
    names: DisplayNames,
    *,
    allow_duration_confirmation: bool,
) -> None:
    blockers = [
        problem
        for problem in projection.blocking_problems
        if not (allow_duration_confirmation and problem.code == "reference_duration_confirmation_required")
    ]
    if not blockers:
        return
    detail = projection.to_advisory_payload()
    detail["allowed"] = False
    detail["problems"] = _problem_payload(projection, _t, names)
    raise HTTPException(
        status_code=400,
        detail=detail,
    )


def _build_unit_dict(
    *,
    unit_id: str,
    prompt: str,
    duration_seconds: int,
    note: str | None,
) -> dict:
    unit = {
        "unit_id": unit_id,
        "text": prompt,
        "duration_seconds": duration_seconds,
        "note": note,
        "generated_assets": {
            "storyboard_image": None,
            "storyboard_last_image": None,
            "grid_id": None,
            "grid_cell_index": None,
            "video_clip": None,
            "video_uri": None,
            "status": "pending",
        },
    }
    refresh_video_unit_replan_state(unit)
    return unit


def _require_unit_ready(unit: dict, *, ignore_marker: bool = False, allow_blank_draft: bool = False) -> None:
    if allow_blank_draft and not str(unit.get("text") or "").strip():
        return
    admission = admit_script_unit("video_units", unit, ignore_marker=ignore_marker)
    if not admission.allowed:
        raise HTTPException(status_code=409, detail=admission.to_dict())


# ============ List/create endpoints ============


async def _unit_capabilities(
    project_name: str,
    project: dict,
    units: list[dict],
    request_facts: ReferenceRequestFactsLookup | None = None,
) -> dict[str, dict[str, object]]:
    """Server per-unit bucket decisions based on available references, returned with units to the canvas."""
    return await reference_unit_capabilities(
        project,
        await _project_path(project_name),
        units,
        request_facts=request_facts or reference_request_facts_lookup(project),
    )


async def _unit_capability(
    project_name: str, project: dict, unit: dict, request_facts: ReferenceRequestFactsLookup | None = None
) -> dict[str, object]:
    return (await _unit_capabilities(project_name, project, [unit], request_facts))[str(unit.get("unit_id") or "")]


@router.get("/episodes/{episode}/units")
async def list_units(project_name: str, episode: int, _t: Translator) -> dict[str, Any]:
    project, script, _sf = await _read_episode_script(project_name, episode, _t)
    units = script.get("video_units") or []
    return {"units": units, "unit_capabilities": await _unit_capabilities(project_name, project, units)}


@router.post("/episodes/{episode}/units", status_code=status.HTTP_201_CREATED)
async def add_unit(
    project_name: str,
    episode: int,
    req: AddUnitRequest,
    _t: Translator,
) -> dict[str, Any]:
    project, current, script_file = await _read_episode_script(project_name, episode, _t)
    request_facts = reference_request_facts_lookup(project)

    # Duration is unit-level authority; omission selects its bucket’s default tier, with async I/O outside the project lock.
    # Determine buckets by available references, sharing response per-unit decisions and runtime projection.
    duration_seconds = req.duration_seconds
    if duration_seconds is None:
        (hydration,) = await asyncio.to_thread(
            hydrate_reference_units, project, await _project_path(project_name), [{"text": req.prompt}]
        )
        duration_seconds = default_unit_duration(await request_facts(hydration.hydrated_generation_type), project)

    units = current.get("video_units") if isinstance(current.get("video_units"), list) else []
    if req.after_unit_id is not None:
        _find_unit(current, req.after_unit_id, _t)
    # IDs are placeholders; batch editing allocates never-used episode IDs, so reread using returned IDs.
    unit = _build_unit_dict(
        unit_id=new_item_id(current),
        prompt=req.prompt,
        duration_seconds=int(duration_seconds),
        note=req.note,
    )
    result = await _commit_episode_edit(
        project_name,
        episode,
        script_file,
        current,
        [
            {
                "op": "insert_after",
                "after_id": req.after_unit_id or (units[-1].get("unit_id") if units else None),
                "item": unit,
            }
        ],
    )
    require_script_edit_result(result)
    saved = await _read_script(project_name, result.script)
    inserted = _find_unit(saved, result.affected_ids[0], _t)
    return {
        "unit": inserted,
        "unit_capability": await _unit_capability(project_name, project, inserted, request_facts),
        "edit_result": result.model_dump(mode="json"),
    }


# ============ PATCH/DELETE endpoints ============


class PatchUnitRequest(BaseModel):
    # extra="forbid" matches ``AddUnitRequest``.
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    note: str | None = None


def _find_unit(script: dict, unit_id: str, _t: Translator) -> dict:
    for u in script.get("video_units") or []:
        if u.get("unit_id") == unit_id:
            return u
    raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id))


def _find_unit_for_project(_project: dict, script: dict, unit_id: str, _t: Translator) -> dict:
    return _find_unit(script, unit_id, _t)


@router.patch("/episodes/{episode}/units/{unit_id}")
async def patch_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    req: PatchUnitRequest,
    _t: Translator,
) -> dict[str, Any]:
    project, current, script_file = await _read_episode_script(project_name, episode, _t)
    _find_unit(current, unit_id, _t)
    fields: dict[str, Any] = {}
    if req.prompt is not None:
        fields["text"] = req.prompt
    if req.duration_seconds is not None:
        fields["duration_seconds"] = req.duration_seconds
    if req.note is not None:
        fields["note"] = req.note
    if not fields:
        unit = _find_unit(current, unit_id, _t)
        return {"unit": unit, "unit_capability": await _unit_capability(project_name, project, unit)}
    result = await _commit_episode_edit(
        project_name,
        episode,
        script_file,
        current,
        [{"op": "update", "id": unit_id, "fields": fields}],
    )
    require_script_edit_result(result, operation_not_found=True)
    saved = await _read_script(project_name, result.script)
    unit = _find_unit(saved, unit_id, _t)
    return {
        "unit": unit,
        "unit_capability": await _unit_capability(project_name, project, unit),
        "edit_result": result.model_dump(mode="json"),
    }


@router.delete("/episodes/{episode}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _t: Translator,
) -> Response:
    _project, current, script_file = await _read_episode_script(project_name, episode, _t)
    _find_unit(current, unit_id, _t)
    result = await _commit_episode_edit(
        project_name,
        episode,
        script_file,
        current,
        [{"op": "remove", "id": unit_id}],
    )
    require_script_edit_result(result, operation_not_found=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class MoveUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Move after this unit; null moves to the beginning.
    after_unit_id: str | None = Field(min_length=1)


@router.post("/episodes/{episode}/units/{unit_id}/move")
async def move_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    req: MoveUnitRequest,
    _t: Translator,
) -> dict[str, Any]:
    """Move a unit after ``after_unit_id`` through ``move_after`` using current script revision; artifacts move with it."""
    _project, current, script_file = await _read_episode_script(project_name, episode, _t)
    _find_unit(current, unit_id, _t)
    if req.after_unit_id is not None:
        _find_unit(current, req.after_unit_id, _t)
    result = await _commit_episode_edit(
        project_name,
        episode,
        script_file,
        current,
        [{"op": "move_after", "id": unit_id, "after_id": req.after_unit_id}],
    )
    require_script_edit_result(result)
    moved = (await _read_script(project_name, result.script))["video_units"]
    return {"units": moved, "edit_result": result.model_dump(mode="json")}


@router.get("/episodes/{episode}/units/{unit_id}/duration-precheck")
async def precheck_unit_duration(
    project_name: str,
    episode: int,
    unit_id: str,
    request: Request,
    _t: Translator,
    names: DisplayNamesCatalog,
) -> dict[str, Any]:
    """Pre-enqueue tier preflight: frontend must obtain user confirmation when requested seconds differ from planned duration.

    False ``needs_confirmation`` only means planned duration belongs to current tiers. Unresolved capabilities/tier
    metadata return structured blockers, never unconstrained requests."""
    reject_retired_query_params(request, "narration_delivery")
    project, script, _script_file = await _read_episode_script(project_name, episode, _t)
    unit = _find_unit(script, unit_id, _t)
    _require_unit_ready(unit)
    projection = await project_reference_unit_request(
        request_facts_lookup=configured_reference_request_facts(project, ConfigResolver(async_session_factory)),
        project=project,
        script=script,
        unit=unit,
        project_path=await _project_path(project_name),
    )
    _raise_projection_blocker(projection, _t, names, allow_duration_confirmation=True)
    slot = projection.request_duration
    if slot is None:
        raise BadRequestError("reference_supported_durations_missing")
    return {
        **projection.to_advisory_payload(),
        "needs_confirmation": any(
            problem.blocking and problem.code == "reference_duration_confirmation_required"
            for problem in projection.problems
        ),
        "script_duration": projection.planned_duration,
        "duration_input": projection.duration_input,
        "request_duration": slot.seconds,
        "adjustment": slot.adjustment,
        "declared_capability": projection.declared_generation_type,
        "hydrated_capability": projection.hydrated_generation_type,
        "provider_id": projection.provider_id,
        "model_id": projection.model_id,
        "problems": _problem_payload(projection, _t, names),
    }


class UnitPromptPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str


@router.post("/episodes/{episode}/units/{unit_id}/prompt-preview")
async def preview_unit_prompt(
    project_name: str,
    episode: int,
    unit_id: str,
    req: UnitPromptPreviewRequest,
    _t: Translator,
    names: DisplayNamesCatalog,
) -> dict[str, Any]:
    """Project/render draft text without saving or enqueueing."""
    project, script, _sf = await _read_episode_script(project_name, episode, _t)
    unit = {**_find_unit(script, unit_id, _t), "text": req.prompt}
    project_path = await _project_path(project_name)
    projection = await project_reference_unit_request(
        project=project,
        script=script,
        unit=unit,
        project_path=project_path,
    )
    return await asyncio.to_thread(
        render_reference_prompt_preview,
        project=project,
        unit=unit,
        project_path=project_path,
        projection=projection,
        translate=_t,
        names=names,
    )


@router.post("/episodes/{episode}/script-preview")
async def preview_script(
    project_name: str,
    episode: int,
    req: ScriptPreviewRequest,
    _t: Translator,
    names: DisplayNamesCatalog,
) -> dict[str, Any]:
    """Read-time unit-body preview: derived utterances and visible degradation warnings.

    Read-only, without persistence: the body is authoritative; utterances/references are mechanical derivations.
    Voice warnings depend on episode backend ``voice_consistency``, reference-audio segment limits and audio-disabled
    settings, sharing execution resolution. Capability failures degrade to ``soft``, merely omitting those warnings."""
    project, _script, _sf = await _read_episode_script(project_name, episode, _t)
    caps = await project_video_caps(project, degraded_to="解析预览不发声音相关提示")
    preview = build_script_preview(
        req.prompt,
        project,
        VoiceRenderSettings.from_caps(caps),
        max_reference_images=caps.get("max_reference_images"),
    )
    return {
        "utterances": [
            {"index": index, "kind": u.kind, "speaker": u.speaker, "text": u.text}
            for index, u in enumerate(preview.utterances, start=1)
        ],
        "warnings": [
            {"key": w["key"], "message": render_message(w["key"], w["params"], _t, names)} for w in preview.warnings
        ],
    }


@router.post(
    "/episodes/{episode}/units/{unit_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    user: CurrentUser,
    _t: Translator,
    names: DisplayNamesCatalog,
    req: GenerateUnitRequest | None = None,
) -> dict[str, Any]:
    project, script, script_file = await _read_episode_script(project_name, episode, _t)
    unit = _find_unit(script, unit_id, _t)  # raises 404 if missing
    _require_unit_ready(unit)
    guard_prompt = str(unit.get("text") or "")
    request_options = (req or GenerateUnitRequest()).projection_options()
    projection = await project_reference_unit_request(
        request_facts_lookup=configured_reference_request_facts(project, ConfigResolver(async_session_factory)),
        project=project,
        script=script,
        unit=unit,
        project_path=await _project_path(project_name),
        options=request_options,
    )
    _raise_projection_blocker(projection, _t, names, allow_duration_confirmation=False)

    # Use shared guards to reject structurally empty prompts here with 400, matching SDK enqueueing,
    # rather than deferring failure to execution (ADR-0001).
    try:
        spec = TaskSpec.from_request(
            task_type="reference_video",
            media_type="video",
            resource_id=unit_id,
            prompt=guard_prompt,
            script_file=script_file,
            extra_payload={"reference_request_options": request_options.to_payload()},
        )
    except TaskSpecValidationError as exc:
        raise HTTPException(status_code=400, detail=_t(exc.code, **exc.params)) from exc

    result = await get_generation_queue().enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        script_file=spec.script_file,
        source="webui",
        user_id=user.id,
    )
    return {
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "projection": {**projection.to_advisory_payload(), "problems": _problem_payload(projection, _t, names)},
    }


async def _admit_units_batch(
    project_name: str,
    episode: int,
    user_id: str,
    body: GenerateUnitsBatchRequest,
    _t: Translator,
) -> tuple[BatchAdmission, GenerationSelection, list[dict[str, Any]], str]:
    """Resolve one batch request's targets and admit them, without creating anything.

    The preview and the submission both go through here, so the units a confirmation
    lists are the units the submission would request.
    """

    project, script, script_file = await asyncio.to_thread(
        _load_episode_script, project_name, episode, _t, readonly=True
    )
    try:
        requested_ids = normalize_requested_ids(body.unit_ids, field="unit_ids")
    except ValueError as exc:
        raise BadRequestError("ref_batch_empty_selection") from exc

    # Screen containers unchanged: `or []` turns false / 0 / "" into valid empty arrays, falsely reporting
    # accepted zero-task requests instead of corrupt video_units. Nontargetable entries, including nonobjects,
    # missing unit_id, nonscalar IDs or duplicates from external/Agent direct edits, belong to missing-only
    # requests; silently skipping them lets healthy units enqueue and incur charges alone.
    units, malformed = screen_script_entries(script.get("video_units", []), requested_ids=requested_ids)

    project_path = await _project_path(project_name)

    def _resolve_targets() -> tuple[list[dict[str, Any]], GenerationSelection]:
        # Read Manifest, including lock waits, and content digests read-only in a worker thread.
        artifact_episode = (
            resolve_artifact_episode(project=project, script=script, script_filename=script_file) or episode
        )
        targets, selection, _states = resolve_reference_batch_targets(
            units=units,
            requested_ids=requested_ids,
            project=project,
            project_path=project_path,
            episode=artifact_episode,
        )
        return targets, selection

    targets, selection = await asyncio.to_thread(_resolve_targets)
    unmatched = [
        refused_ticket(
            unit_id,
            code=GenerationProblemCode.UNIT_NOT_FOUND,
            detail=f"unit {unit_id} 不在 video_units 中",
            action=GenerationAction.FIX_INPUT,
        )
        for unit_id in selection.unmatched_ids
    ]
    admission = await admit_reference_video_batch(
        project_name=project_name,
        project=project,
        project_path=project_path,
        script=script,
        script_file=script_file,
        units=targets,
        request_options=ReferenceRequestOptions(),
        operation="generate_reference_videos_batch",
        selection=(
            GenerationSelectionMode.EXPLICIT if requested_ids is not None else GenerationSelectionMode.MISSING_ONLY
        ),
        confirmed_request_durations=body.confirmed_request_durations,
        spec_check=lambda unit: reference_unit_task_spec(unit, script_file),
        # Unreadable-artifact units are excluded by target selection but belong to the request; omitting admission
        # would enqueue healthy units while silently skipping this one.
        extra_tickets=[*unmatched, *malformed, *artifact_state_tickets(selection.unavailable)],
        user_id=user_id,
        queue=get_generation_queue(),
    )
    return admission, selection, targets, script_file


def _skipped_unit_ids(selection: GenerationSelection) -> list[str]:
    return sorted(state.unit_id for state in selection.skipped)


@router.post("/episodes/{episode}/units/generate-batch/preview")
async def preview_units_batch(
    project_name: str,
    episode: int,
    user: CurrentUser,
    _t: Translator,
    names: DisplayNamesCatalog,
    req: GenerateUnitsBatchRequest,
) -> dict[str, Any]:
    """Admit a batch of reference units without creating tasks, for the confirmation dialog.

    Returns the same verdict the submission would, the units skipped for already having
    a clip, and the quoted cost of the priced units together with how many have no quote.
    """

    admission, selection, _targets, _script_file = await _admit_units_batch(project_name, episode, user.id, req, _t)
    total, unpriced = admission.quoted_total()
    return {
        **localized_admission_payload(admission, _t, names),
        "skipped_unit_ids": _skipped_unit_ids(selection),
        **BatchCostEstimate(total=total, unpriced_count=unpriced).to_payload(),
    }


@router.post("/episodes/{episode}/units/generate-batch")
async def generate_units_batch(
    project_name: str,
    episode: int,
    user: CurrentUser,
    _t: Translator,
    names: DisplayNamesCatalog,
    req: GenerateUnitsBatchRequest,
) -> dict[str, Any]:
    """Admit a whole batch of reference units, then create their tasks.

    The verdict is returned with HTTP 200 in all three outcomes: an evaluation
    that refuses the request is a successful evaluation, and collapsing it into a
    generic 4xx would hide every gap after the first one. Callers branch on
    ``decision``. An admitted batch whose enqueue is interrupted is likewise a
    200: the tasks already created run on, and ``enqueue_failures`` names the
    targets that never reached the queue.
    """

    admission, selection, targets, script_file = await _admit_units_batch(project_name, episode, user.id, req, _t)
    payload = localized_admission_payload(admission, _t, names)
    payload["skipped_unit_ids"] = _skipped_unit_ids(selection)
    if admission.decision is not BatchAdmissionDecision.ADMITTED:
        payload["task_ids"] = []
        payload["task_ids_by_unit"] = {}
        payload["enqueue_failures"] = []
        payload["deduped"] = False
        return payload

    specs = [reference_unit_task_spec(unit, script_file) for unit in targets]
    for spec in specs:
        spec.source = "webui"
        # Record confirmed tiers per unit in request facts, part of this request rather than one batch-wide value.
        # Reuse admission derivation; computing separately creates divergence.
        options = request_options_for_unit(
            ReferenceRequestOptions(),
            spec.resource_id,
            req.confirmed_request_durations,
        )
        spec.payload = {
            **(spec.payload or {}),
            "reference_request_options": options.to_payload(),
        }
    enqueued, enqueue_failures = await batch_enqueue_only(
        project_name=project_name,
        specs=specs,
        user_id=user.id,
        queue=get_generation_queue(),
    )
    # Enqueue interruption does not cancel created tasks: admitted complete paid units execute normally. Report unreached targets
    # per ID so UI releases optimistic occupancy and next missing-only generation fills only those.
    payload["enqueue_failures"] = [enqueue_failure_payload(failure, _t) for failure in enqueue_failures]
    payload["task_ids"] = [item.task_id for item in enqueued]
    # Return each unit’s own task rows so optimistic occupancy waits independently; batch-wide lists make each unit
    # wait for the whole batch to persist.
    payload["task_ids_by_unit"] = {item.resource_id: item.task_id for item in enqueued}
    payload["deduped"] = bool(enqueued) and all(item.deduped for item in enqueued)
    return payload


@router.post("/episodes/{episode}/units/{unit_id}/upload-video")
async def upload_unit_video(
    project_name: str,
    episode: int,
    unit_id: str,
    _t: Translator,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a unit clip to replace its AI-generated video.

    Reuse generation finalization for thumbnails, old video_uri cleanup and status=completed, with version tracking.
    Reference images use existing project-asset upload paths, outside this endpoint."""
    try:
        max_bytes = validate_upload(file.filename, file.size, kind="video")

        relative_path = resource_relative_path("reference_videos", unit_id)

        def _validate_unit() -> tuple[Path, VersionManager, str]:
            project, script, script_file = _load_episode_script(project_name, episode, _t)
            _find_unit_for_project(project, script, unit_id, _t)  # raises 404 if missing
            project_path = get_project_manager().get_project_path(project_name)
            # Path traversal guard: unit_id-derived absolute paths must stay inside projects, matching versions.py.
            try:
                safe_join(project_path, relative_path)
            except PathTraversalError as exc:
                raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=unit_id)) from exc
            return project_path, VersionManager(project_path), script_file

        project_path, versions, script_file = await run_sync_transaction(_validate_unit)
        target = project_path / relative_path

        with project_change_source("webui"):
            staged_video = await stage_uploaded_video_stream(file.file, target, max_bytes=max_bytes)

            # Uploads can span hundreds of MB and seconds, allowing concurrent episode→script rebinding
            # through PATCH/Agent script synchronization. Re-resolve after staging to write metadata into the active script.
            def _recheck_binding() -> str:
                project2, script2, script_file2 = _load_episode_script(project_name, episode, _t)
                _find_unit_for_project(project2, script2, unit_id, _t)
                return script_file2

            try:
                script_file = await run_sync_transaction(_recheck_binding)

                def _commit_metadata(thumb_rel: str | None, on_commit: Callable[[Path], None]) -> None:
                    pm = get_project_manager()
                    with pm.locked_script(
                        project_name,
                        script_file,
                        validate=False,
                        on_commit=on_commit,
                    ) as script:
                        apply_unit_video_assets(script, unit_id, video_uri=None, thumb_rel=thumb_rel)

                version = await commit_manual_video_upload(
                    project_path=project_path,
                    versions=versions,
                    resource_type="reference_videos",
                    resource_id=unit_id,
                    script_file=script_file,
                    staged_video=staged_video,
                    current_video=target,
                    thumbnail_file=project_path / "reference_videos" / "thumbnails" / f"{unit_id}.jpg",
                    thumbnail_rel=f"reference_videos/thumbnails/{unit_id}.jpg",
                    original_filename=file.filename,
                    commit_metadata=_commit_metadata,
                )
            finally:
                await asyncio.to_thread(staged_video.unlink, missing_ok=True)  # hard-delete: temp -- staged upload file
            # emit reads scripts/resolves episodes/computes fingerprints; use a thread pool to avoid event-loop blocking;
            # reuse returned fingerprints in responses without recomputation.
            fingerprints = await run_sync_transaction(
                emit_generation_success_batch,
                task_type="reference_video",
                project_name=project_name,
                resource_id=unit_id,
                payload={"script_file": script_file},
            )

        return {
            "success": True,
            "path": relative_path,
            "version": version,
            "asset_fingerprints": fingerprints,
        }
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=_t(exc.key, **exc.params)) from exc
    except FileNotFoundError as exc:
        # Never return str(exc): load_script errors include absolute server paths.
        raise NotFoundError("ref_script_missing") from exc
    except KeyError as exc:
        # Unit deleted concurrently before finalization, in the narrow race between post-staging binding checks and locked writeback.
        raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id)) from exc
    except ScriptEditError as exc:
        raise HTTPException(status_code=400, detail=script_edit_detail(exc, _t)) from exc
    except (HTTPException, ApiError):
        # Catch ApiError alongside HTTPException: _load_episode_script NotFoundError
        # does not subclass HTTPException and would otherwise become 500 in the generic handler.
        raise
    except Exception as exc:
        # Never return str(exc): unexpected errors may include server paths/internals; log tracebacks only.
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
