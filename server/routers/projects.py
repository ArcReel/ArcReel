"""Project-management routes.

Handle CRUD through lib/project/project_manager.py.

Most handlers use ``except Exception`` fallback to 500. Domain exceptions (``ApiError`` subclasses)
can arise inside covered persistence closures (backend-field/script-structure validation). Thus
passthrough clauses use ``except (HTTPException, ApiError)``; catching only ``HTTPException``
would silently downgrade these 4xx responses to 500."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi import Path as FastAPIPath
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

# fastapi reexports only plural BackgroundTasks; get singular BackgroundTask from starlette.
from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)

from lib.agent.agent_session_store import default_sdk_config_dir
from lib.agent.profile_manifest import ContentMode
from lib.backends.text_backends.base import TextOutputTruncatedError
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import (
    ConfigResolver,
    VideoBucketCapabilityError,
    caps_generation_mode,
    video_bucket_for_generation_mode,
)
from lib.db import async_session_factory
from lib.episode.episode_ledger import is_derived_episode_name
from lib.episode.episode_source_commands import (
    EpisodeSourceError,
    register_whole_source_file,
    set_episode_source_text,
)
from lib.episode.episode_target_duration import (
    EPISODE_TARGET_DURATION_FIELD,
    MAX_EPISODE_TARGET_DURATION,
    MIN_EPISODE_TARGET_DURATION,
    is_valid_episode_target_duration,
)
from lib.episode.source_kinds import SourceKind
from lib.generation.task_failure import failure_message_key
from lib.generation.video_request_facts import ResolutionOverride, VideoRequestFactsError, planning_durations
from lib.i18n import render_generation_input_error, render_message
from lib.infra.api_errors import ApiError, BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.infra.async_thread import (
    EventLoopBridge,
    run_noninterruptible_async,
    run_noninterruptible_sync,
    run_sync_transaction,
)
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.json_io import domain_error_on_value_error
from lib.material.formal_objects import formal_commit_on, release_holder
from lib.project import deleted_projects
from lib.project.asset_fingerprints import compute_asset_fingerprints
from lib.project.asset_types import asset_name_comparison_key
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import EmptySourceError, EpisodeScriptReboundError, get_project_manager
from lib.prompts.style_templates import is_known_template, resolve_template_prompt
from lib.script.blank_script import BlankScriptError, start_blank_script
from lib.script.script_batch_edit import ScriptBatchEditCommand, ScriptBatchEditor, blank_item_after, script_revision
from lib.script.script_editor import resolve_items
from lib.script.script_references import annotate_derivative_references
from lib.speech.character_voice import PROJECT_FIELD as CHARACTER_VOICE_BINDING_FIELD
from lib.speech.character_voice import VALID_CHARACTER_VOICE_BINDINGS
from lib.speech.narration_config import (
    NARRATION_DELIVERY_FIELD,
    POST_PRODUCTION,
    TTS_BACKEND_FIELD,
    TTS_SPEED_FIELD,
    TTS_VOICE_FIELD,
    NarrationConfigError,
    NarrationDelivery,
    validate_project_narration_config,
)
from lib.speech.speech_rate import MAX_SPEECH_RATE_UPS, MIN_SPEECH_RATE_UPS, SPEECH_RATE_FIELD, is_valid_speech_rate
from lib.workflow.workflow_plan import WorkflowPlan, WorkflowPlanRequest
from lib.workflow.workflow_state import (
    EpisodeNextStep,
    ProjectSummary,
    WorkflowRequestError,
    WorkflowStateService,
    WorkflowStatus,
)
from server.auth import CurrentUser, create_download_token, reject_embedded_agent, verify_download_token
from server.dependencies import require_project_migration_ok
from server.i18n import DisplayNamesCatalog, Locale, Translator
from server.routers._coalesce import RequestCoalescer
from server.routers._episode_source_errors import episode_source_http_error
from server.routers._script_edits import (
    execute_current_script_edit,
    require_script_edit_result,
    script_batch_status,
)
from server.routers._validators import split_video_backend_query, validate_backend_value
from server.services.admission.prompt_preview import ScriptItemNotFound, preview_item_prompts
from server.services.project import workflow_planner as workflow_plan_service
from server.services.project.episode_display import present_episode_diagnostics
from server.services.project.episode_id_records import recorded_episode_ids_on
from server.services.project.narration_settings import (
    NarrationSettingsInput,
    new_project_narration_fields,
    validate_tts_backend,
    validate_tts_speed,
)
from server.services.project.project_activity import project_last_activity_at
from server.services.project.project_archive import (
    ProjectArchiveService,
    ProjectArchiveValidationError,
    provider_asset_link_archive_on,
)
from server.services.project.project_cover import resolve_project_cover
from server.services.project.project_retirement import restore_retired_project, retire_project_on, retiring_project
from server.services.tasks.video_caps import (
    capability_request_facts,
    duration_constraints_payload,
)
from server.tool_runtime import truncation_problem

router = APIRouter()

# Self-authenticating endpoints: native browser downloads cannot carry Authorization headers;
# verify_download_token checks short-lived tokens inside endpoints, without registered Bearer dependencies.
self_auth_router = APIRouter()

_project_detail_coalescer = RequestCoalescer()


def get_project_detail_coalescer() -> RequestCoalescer:
    return _project_detail_coalescer


def get_workflow_state_service() -> WorkflowStateService:
    return WorkflowStateService(get_project_manager())


WorkflowStateServiceDep = Annotated[WorkflowStateService, Depends(get_workflow_state_service)]


def _project_status_payload(
    summary: ProjectSummary, project: dict[str, Any], translate: Callable[..., str]
) -> dict[str, Any]:
    """Project-level status: summary without individual episode details.

    List/detail ``status`` contains only repair flags, asset counts, and episode aggregates. Keep per-episode
    details in services, avoiding N×episode objects on N-project lists; episode consumers use episode APIs."""

    return present_episode_diagnostics(summary.model_dump(mode="json", exclude={"episodes"}), project, translate)


def _merge_episode_summaries(project: dict[str, Any], summary: ProjectSummary) -> dict[str, Any]:
    """Merge summary episode details into ``project["episodes"]`` at read time, without persistence.

    Project summaries alone provide script progress, artifact counts, and durations, using manifests:
    available = current ∪ stale, with stale counted separately. Episode cards/headers/canvases therefore
    share dashboard values. Preserve project.json fields (title / script_file / hook / outline, etc.) unchanged."""

    per_episode = {item.episode: item for item in summary.episodes}
    episodes = []
    for entry in project.get("episodes", []):
        if not isinstance(entry, dict):
            continue
        merged = dict(entry)
        number = entry.get("episode")
        item = per_episode.get(number) if isinstance(number, int) else None
        if item is not None:
            merged.update(item.model_dump(mode="json", exclude={"episode"}))
        episodes.append(merged)
    project["episodes"] = episodes
    return project


async def get_archive_service() -> ProjectArchiveService:
    # Construct on the event loop: worker exports/imports use the captured loop to retire overwritten projects, save imported images by global settings,
    # and read/write archived provider associations.
    bridge = EventLoopBridge.capture()
    return ProjectArchiveService(
        get_project_manager(),
        retire_project=retire_project_on(bridge, async_session_factory),
        formal_commit=formal_commit_on(bridge, async_session_factory),
        provider_asset_links=provider_asset_link_archive_on(bridge, async_session_factory),
    )


ArchiveServiceDep = Annotated[ProjectArchiveService, Depends(get_archive_service)]


def get_script_batch_editor(manager: Any | None = None) -> ScriptBatchEditor:
    return ScriptBatchEditor(manager or get_project_manager())


def get_script_batch_editor_factory() -> Callable[[Any], ScriptBatchEditor]:
    """Batch-editor route dependency: inject a factory because editors bind managers resolved inside handlers."""
    return get_script_batch_editor


ScriptBatchEditorFactoryDep = Annotated[Callable[[Any], ScriptBatchEditor], Depends(get_script_batch_editor_factory)]


# Project model fields validated/written individually on creation; PATCH additionally supports audio_backend.
# Values are provider/model or bare provider; empty clears this layer and falls back to the next.
_PROJECT_BACKEND_FIELDS = (
    "video_backend",
    "video_provider_i2v",
    "video_provider_r2v",
    "image_provider_t2i",
    "image_provider_i2i",
    "default_image_backend",
    "text_backend_simple",
    "text_backend_complex",
    "default_text_backend",
)


def _reject_bool_speech_rate(value: object) -> object:
    """Booleans are not speech rates: nonstrict Pydantic converts JSON ``true`` to 1.0 and ``false`` to 0.0.

    Truth sources/validators reject bool. Accepting writes loses boolean provenance after 1.0 persistence,
    while 0.0 skips writes as unset, giving one input class different outcomes. Reject before range validation."""
    if isinstance(value, bool):
        raise ValueError("speech rate must be a number, not a boolean")
    return value


#: Shared create/PATCH estimated speech-rate type with the same boolean guard.
SpeechRateOverride = Annotated[float | None, BeforeValidator(_reject_bool_speech_rate)]


def _validated_episode_target_duration(value: int, _t: Translator) -> int:
    """Constrain create/PATCH episode targets to the hard range, returning 422 outside it.

    Share ``is_valid_episode_target_duration`` with read guards in ``lib.episode.episode_target_duration``,
    ``patch_project`` coercion, and frontend validation; do not duplicate boundary numbers."""
    if not is_valid_episode_target_duration(value):
        raise HTTPException(
            status_code=422,
            detail=_t(
                "episode_target_duration_out_of_range",
                min=MIN_EPISODE_TARGET_DURATION,
                max=MAX_EPISODE_TARGET_DURATION,
            ),
        )
    return value


def _validated_speech_rate(value: float, _t: Translator) -> float:
    """Constrain create/PATCH estimated speech rates to the hard range, returning 422 outside it.

    Share ``is_valid_speech_rate`` with ``lib.speech.speech_rate`` read guards and frontend validation;
    do not duplicate boundary numbers."""
    rate = float(value)
    if not is_valid_speech_rate(rate):
        raise HTTPException(
            status_code=422, detail=_t("speech_rate_out_of_range", min=MIN_SPEECH_RATE_UPS, max=MAX_SPEECH_RATE_UPS)
        )
    return rate


class CreateProjectRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    style: str | None = ""  # Retained but no longer a user entry point.
    content_mode: ContentMode | None = "narration"
    aspect_ratio: str | None = "9:16"
    default_duration: int | None = None
    # Episode target seconds: optional soft preference for non-ad projects; range checked by _validated_episode_target_duration.
    episode_target_duration: int | None = None
    # Ad-only total target seconds. UI offers 15/30/60/90, default 60;
    # data does not enforce enums; any positive integer is valid.
    target_duration: int | None = Field(default=None, gt=0)
    # Ad-only short creative brief, nullable and outside source_loader.
    brief: str | None = None
    # Creation requires explicit storyboard/reference_video generation mode; missing/legacy grid values
    # return Pydantic 422. Immutable afterward, structurally absent from PATCH.
    generation_mode: Literal["storyboard", "reference_video"]
    # Grid storyboards change image production, not generation mode; meaningful only for storyboard,
    # toggleable by PATCH anytime after creation. Reject enabled grids for ad.
    grid_storyboard: bool = False
    # Estimated speech-rate override (reading units/second); empty uses lib.speech.speech_rate language defaults.
    # Independent of TTS narration_speed provider rate; no coupling.
    speech_rate_units_per_second: SpeechRateOverride = None
    style_template_id: str | None = None
    video_backend: str | None = None
    # Project video buckets (docs/adr/0054): i2v = image-to-video/grids, r2v = reference-to-video;
    # empty falls back to project video_backend and global layers.
    video_provider_i2v: str | None = None
    video_provider_r2v: str | None = None
    # Project image buckets/defaults (docs/adr/0054): t2i = text-to-image, i2i = image-to-image;
    # empty buckets use project default_image_backend then globals.
    image_provider_t2i: str | None = None
    image_provider_i2i: str | None = None
    default_image_backend: str | None = None
    # Project text tiers/default model (docs/adr/0051); empty inherits globals.
    text_backend_simple: str | None = None
    text_backend_complex: str | None = None
    default_text_backend: str | None = None
    model_settings: dict[str, dict[str, str | None]] | None = None
    # Narration delivery (docs/adr/0089): required project configuration, default postproduction dubbing. TTS omitted model/voice/
    # rate prefill global defaults, then become project snapshots without global inheritance. Explicit null rate omits rate.
    narration_delivery: NarrationDelivery = POST_PRODUCTION
    audio_backend: str | None = None
    narration_voice: str | None = None
    narration_speed: float | None = None


class EpisodePatch(BaseModel):
    """Episode update body, writable fields only; ignore undeclared fields."""

    model_config = ConfigDict(extra="ignore")
    episode: int
    script_file: str | None = None


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    style: str | None = None
    aspect_ratio: str | None = None
    default_duration: int | None = None
    # Episode target seconds: explicit null clears; ad rejects field presence itself.
    episode_target_duration: int | None = None
    # Ad-only total target seconds: any positive integer, never clearable.
    target_duration: int | None = Field(default=None, gt=0)
    # Ad-only brief; explicit null clears to empty string.
    brief: str | None = None
    # Generation mode is creation-fixed, structurally absent from PATCH; grid toggles remain mutable.
    grid_storyboard: bool | None = None
    video_backend: str | None = None
    video_provider_i2v: str | None = None
    video_provider_r2v: str | None = None
    image_provider_t2i: str | None = None
    image_provider_i2i: str | None = None
    default_image_backend: str | None = None
    video_generate_audio: bool | None = None
    # Character voice binding: prompt (default, soft voice_style constraints) / reference_audio (character audio).
    character_voice_binding: str | None = None
    # Narration delivery (docs/adr/0089): mutable without staling existing artifacts; not clearable.
    narration_delivery: NarrationDelivery | None = None
    # TTS snapshot: provider/model, voice, speech rate, without global inheritance.
    # TTS model/voice cannot clear; null rate means omit provider rate.
    audio_backend: str | None = None
    narration_voice: str | None = None
    narration_speed: float | None = None
    # Estimated speech rate (reading units/second) override; null clears to language defaults.
    speech_rate_units_per_second: SpeechRateOverride = None
    # Project text tiers/default (docs/adr/0051); empty clears to global inheritance.
    text_backend_simple: str | None = None
    text_backend_complex: str | None = None
    default_text_backend: str | None = None
    style_template_id: str | None = None
    clear_style_image: bool | None = None
    episodes: list[EpisodePatch] | None = None
    model_settings: dict[str, dict[str, str | None]] | None = None


def _apply_narration_patch(project: dict, req: UpdateProjectRequest) -> None:
    """Persist narration delivery/TTS snapshots; resulting TTS projects still require complete snapshots or the whole PATCH returns 422.

    Delivery is excluded from generation basis: switching leaves artifacts current, retaining TTS snapshots
    when switching to postproduction dubbing."""

    fields = req.model_fields_set
    try:
        if "narration_delivery" in fields:
            if req.narration_delivery is None:
                raise NarrationConfigError("narration_delivery_required")
            project[NARRATION_DELIVERY_FIELD] = req.narration_delivery
        if TTS_BACKEND_FIELD in fields:
            if req.audio_backend:
                validate_backend_value(req.audio_backend, TTS_BACKEND_FIELD)
                project[TTS_BACKEND_FIELD] = validate_tts_backend(req.audio_backend)
            else:
                project.pop(TTS_BACKEND_FIELD, None)
        # Voice is a string ID from provider documentation; empty clears.
        if TTS_VOICE_FIELD in fields:
            voice = (req.narration_voice or "").strip()
            if voice:
                project[TTS_VOICE_FIELD] = voice
            else:
                project.pop(TTS_VOICE_FIELD, None)
        # Speech-rate hygiene: positive/finite only; providers constrain actual ranges.
        if TTS_SPEED_FIELD in fields:
            if req.narration_speed is None:
                project.pop(TTS_SPEED_FIELD, None)
            else:
                project[TTS_SPEED_FIELD] = validate_tts_speed(req.narration_speed)
        validate_project_narration_config(project)
    except NarrationConfigError as exc:
        raise UnprocessableError(exc.code) from exc


def _cleanup_temp_file(path: str) -> None:
    try:
        os.unlink(path)  # hard-delete: temp -- Archive import/export temporary files.
    except FileNotFoundError:
        return


@router.post("/projects/import")
async def import_project_archive(
    _t: Translator,
    archive_service: ArchiveServiceDep,
    file: UploadFile = File(...),
    conflict_policy: str = Form("prompt"),
    confirm_report: bool = Form(False),
):
    """Import projects from ZIP.

    If upgrades adjust/omit content and ``confirm_report`` is absent, return 409 with
    ``confirmation_required`` and ``migration_report``, leaving no project data. Resubmit with ``confirm_report`` after user confirmation."""
    # Register temporary paths immediately after creation for finally cleanup on failure/cancellation.
    upload_paths: list[str] = []
    try:
        # Use SpooledTemporaryFile synchronous handles; offload temp creation and the entire write loop
        # rather than mixing async reads with synchronous writes on the event loop.
        raw_file = file.file

        def _write_upload() -> str:
            fd, path = tempfile.mkstemp(prefix="arcreel-upload-", suffix=".zip")
            upload_paths.append(path)
            with os.fdopen(fd, "wb") as target:
                while True:
                    chunk = raw_file.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
            return path

        upload_path = await run_sync_transaction(_write_upload)

        def _sync():
            return archive_service.import_project_archive(
                Path(upload_path),
                uploaded_filename=file.filename,
                conflict_policy=conflict_policy,
                confirm_report=confirm_report,
                translate=_t,
            )

        result = await run_sync_transaction(_sync)
        return {
            "success": True,
            "project_name": result.project_name,
            "project": result.project,
            "warnings": [warning.render(_t) for warning in result.warnings],
            "conflict_resolution": result.conflict_resolution,
            "diagnostics": result.diagnostics,
        }
    except ProjectArchiveValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail.render(_t),
                "errors": exc.render_errors(_t),
                "warnings": exc.render_warnings(_t),
                "diagnostics": exc.diagnostics_payload(_t),
                **exc.extra,
            },
        )
    except ApiError:
        # Imported-image storage failure (502) or unconfigured connection (409) returns explicit reasons without installing projects.
        raise
    except Exception:
        logger.exception("请求处理失败")
        return JSONResponse(
            status_code=500,
            content={"detail": _t("internal_server_error"), "errors": [], "warnings": []},
        )
    finally:
        await file.close()
        for path in upload_paths:
            await run_noninterruptible_sync(_cleanup_temp_file, path)


@router.post("/projects/{name}/export/token")
async def create_export_token(
    name: str,
    current_user: CurrentUser,
    _t: Translator,
    archive_service: ArchiveServiceDep,
    scope: str = Query("full"),
):
    """Issue short-lived download tokens for authentication of native browser downloads."""
    try:
        if scope not in ("full", "current"):
            raise HTTPException(status_code=422, detail=_t("scope_invalid"))

        def _sync():
            if not get_project_manager().project_exists(name):
                raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
            return archive_service.get_export_diagnostics(name, scope=scope, translate=_t)

        async with archive_service.export_materials_held(name, scope=scope):
            diagnostics = await asyncio.to_thread(_sync)
        username = current_user.sub
        download_token = create_download_token(username, name)
        return {
            "download_token": download_token,
            "expires_in": 300,
            "diagnostics": diagnostics,
        }
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@self_auth_router.get("/projects/{name}/export")
async def export_project_archive(
    name: str,
    _t: Translator,
    archive_service: ArchiveServiceDep,
    download_token: str = Query(...),
    scope: str = Query("full"),
):
    """Export projects as ZIP; require download_token from POST /export/token."""
    if scope not in ("full", "current"):
        raise HTTPException(status_code=422, detail=_t("scope_invalid"))

    # Verify download_token.
    import jwt as pyjwt

    try:
        verify_download_token(download_token, name)
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail=_t("download_expired")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=_t("download_token_mismatch")) from exc
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=_t("download_token_invalid")) from exc

    # Register ZIP paths immediately: cancellation during packaging loses returns, so finally cleans registration;
    # after FileResponse takeover, its background task cleans.
    archive_paths: list[Path] = []

    def _export() -> tuple[Path, str]:
        archive_path, download_name = archive_service.export_project(name, scope=scope)
        archive_paths.append(archive_path)
        return archive_path, download_name

    handed_off = False
    try:
        # Archives need real remote-image content: retrieve cleaned working files and register use while packing.
        async with archive_service.export_materials_held(name, scope=scope):
            archive_path, download_name = await run_sync_transaction(_export)
        response = FileResponse(
            archive_path,
            media_type="application/zip",
            filename=download_name,
            background=BackgroundTask(_cleanup_temp_file, str(archive_path)),
        )
        handed_off = True
        return response
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
    finally:
        if not handed_off:
            for path in archive_paths:
                await run_noninterruptible_sync(_cleanup_temp_file, str(path))


_NO_ACTIVITY = datetime.min.replace(tzinfo=UTC)


@router.get("/projects")
async def list_projects(summaries: WorkflowStateServiceDep, _t: Translator):
    """List all projects, most recent activity first."""

    def _sync():
        manager = get_project_manager()
        projects = []
        last_activity: dict[str, datetime] = {}
        for name in manager.list_projects():
            try:
                # Omit projects deleted after enumeration.
                if manager.project_exists(name):
                    project = manager.load_project(name)
                    # Preload each episode script once for cover/status consumers, eliminating duplicate JSON I/O.
                    # Keys preserve episode['script_file'] (matching resolve_project_cover /
                    # summary projection expectations). Individual episode-load failures never fail lists:
                    # omit from maps; consumers naturally use missing fallbacks.
                    preloaded_scripts: dict[str, dict] = {}
                    for ep in project.get("episodes") or []:
                        script_file = ep.get("script_file")
                        if not script_file:
                            continue
                        try:
                            preloaded_scripts[script_file] = manager.load_script(name, script_file)
                        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as load_err:
                            # Match resolve_project_cover / summaries: missing I/O +
                            # JSON/schema parsing failures skip episodes and continue others;
                            # unexpected RuntimeError/MemoryError, etc. reach outer try and basic-info fallback rows.
                            logger.debug(
                                "list_projects 预加载剧本失败 project=%s script=%s err=%s",
                                name,
                                script_file,
                                load_err,
                            )

                    # Cover uses resolve_project_cover fallback chain:
                    # video_thumbnail → storyboard_image → scene_sheet → character_sheet
                    # covering storyboard image-to-video (including grids) and reference-to-video.
                    thumbnail = resolve_project_cover(manager, name, project, preloaded_scripts=preloaded_scripts)

                    # Stages/artifact counts always derive from read-time summaries, using manifests.
                    # Lists check manifest registration/file presence only: canonical-state comparison would hash all storyboard/reference
                    # images across projects, unaffordable for N-project lists.
                    status = _project_status_payload(
                        summaries.get_project_summary(
                            name,
                            preloaded_scripts=preloaded_scripts,
                            currency="registered",
                        ),
                        project,
                        _t,
                    )

                    activity = project_last_activity_at(
                        manager.get_project_path(name), project, preloaded_scripts.values()
                    )
                    if activity is not None:
                        last_activity[name] = activity

                    raw_title = project.get("title")
                    projects.append(
                        {
                            "name": name,
                            # Missing/None/invalid title becomes empty str, letting frontend i18n
                            # show untitled-project fallback while APIs always return str.
                            "title": raw_title if isinstance(raw_title, str) else "",
                            "style": project.get("style", ""),
                            "style_template_id": project.get("style_template_id"),
                            "style_image": project.get("style_image"),
                            "thumbnail": thumbnail,
                            "status": status,
                            "last_activity_at": activity.isoformat() if activity is not None else None,
                        }
                    )
            except Exception as e:
                # Return basic info on errors.
                logger.warning("加载项目 '%s' 元数据失败: %s", name, e)
                projects.append(
                    {"name": name, "title": "", "style": "", "thumbnail": None, "status": {}, "last_activity_at": None}
                )

        # Projects without activity timestamps go last, preserving name-enumeration order among them.
        projects.sort(key=lambda p: last_activity.get(p["name"], _NO_ACTIVITY), reverse=True)
        return {"projects": projects}

    return await run_sync_transaction(_sync)


@router.post("/projects")
async def create_project(
    req: CreateProjectRequest,
    _t: Translator,
):
    """Create a project."""
    try:
        try:
            narration_fields = await new_project_narration_fields(
                NarrationSettingsInput(
                    delivery=req.narration_delivery,
                    audio_backend=req.audio_backend,
                    narration_voice=req.narration_voice,
                    narration_speed=req.narration_speed,
                    provided=frozenset(req.model_fields_set & {TTS_BACKEND_FIELD, TTS_VOICE_FIELD, TTS_SPEED_FIELD}),
                ),
                resolver=ConfigResolver(async_session_factory),
            )
        except NarrationConfigError as exc:
            raise UnprocessableError(exc.code) from exc

        def _sync():
            manager = get_project_manager()
            title = (req.title or "").strip()
            manual_name = (req.name or "").strip()
            if not title and not manual_name:
                raise HTTPException(status_code=400, detail=_t("title_required"))
            project_name = manual_name or manager.generate_project_name(title)

            style_prompt = req.style or ""
            if req.style_template_id:
                if not is_known_template(req.style_template_id):
                    raise HTTPException(
                        status_code=400,
                        detail=_t("unknown_style_template", template_id=req.style_template_id),
                    )
                style_prompt = resolve_template_prompt(req.style_template_id)

            # Mode-exclusive fields: target_duration/brief are ad-only;
            # ad exposes neither default_duration nor grids.
            content_mode = req.content_mode or "narration"
            if content_mode == "ad":
                if req.default_duration is not None:
                    raise HTTPException(status_code=400, detail=_t("ad_no_default_duration"))
                if req.episode_target_duration is not None:
                    raise HTTPException(status_code=400, detail=_t("ad_no_episode_target_duration"))
                if req.grid_storyboard:
                    raise HTTPException(status_code=400, detail=_t("ad_grid_not_supported"))
            else:
                if req.target_duration is not None:
                    raise HTTPException(status_code=400, detail=_t("ad_only_field", field="target_duration"))
                if req.brief is not None:
                    raise HTTPException(status_code=400, detail=_t("ad_only_field", field="brief"))

            # Validate all backend fields symmetrically with updates.
            for field_name in _PROJECT_BACKEND_FIELDS:
                value = getattr(req, field_name)
                if value:
                    validate_backend_value(value, field_name)

            # Optional estimated speech rates omit persistence when absent, using language defaults from lib.speech.speech_rate.
            # Validate before create_project so invalid requests leave no partial directories.
            speech_rate = (
                None
                if req.speech_rate_units_per_second is None
                else _validated_speech_rate(req.speech_rate_units_per_second, _t)
            )
            # Likewise validate episode targets before create_project to avoid partial directories.
            episode_target_duration = (
                None
                if req.episode_target_duration is None
                else _validated_episode_target_duration(req.episode_target_duration, _t)
            )

            try:
                manager.create_project(project_name, content_mode=req.content_mode or "narration")
            except FileExistsError as exc:
                raise HTTPException(status_code=400, detail=_t("project_exists", name=project_name)) from exc
            extras = {field: value for field in _PROJECT_BACKEND_FIELDS if (value := getattr(req, field))}
            if req.model_settings is not None:
                extras["model_settings"] = req.model_settings
            # Write generation mode/grid flags once via extras, avoiding postcreate load/save RMW;
            # always persist explicit values, including grid_storyboard false, giving new projects complete v5 shape.
            extras["generation_mode"] = req.generation_mode
            extras["grid_storyboard"] = req.grid_storyboard
            if speech_rate is not None:
                extras[SPEECH_RATE_FIELD] = speech_rate
            with project_change_source("webui"):
                project = manager.create_project_metadata(
                    project_name,
                    title or manual_name,
                    style_prompt,
                    req.content_mode,
                    aspect_ratio=req.aspect_ratio,
                    default_duration=req.default_duration,
                    episode_target_duration=episode_target_duration,
                    style_template_id=req.style_template_id,
                    extras=extras or None,
                    target_duration=req.target_duration,
                    brief=req.brief,
                    narration=narration_fields,
                )
            return {"success": True, "name": project_name, "project": project}

        return await run_sync_transaction(_sync)
    except ValueError as e:
        # Project-name/duration/brief validation failures: str(e) enters logs only.
        logger.warning("创建项目参数错误: name=%s (%s)", req.name or req.title, e)
        raise BadRequestError("project_config_invalid") from e
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/projects/{name}/video-capabilities")
async def get_video_capabilities(
    name: str,
    _t: Translator,
    video_backend: Annotated[str | None, Query()] = None,
    resolution: Annotated[str | None, Query()] = None,
    uses_reference_images: Annotated[bool | None, Query()] = None,
):
    """Resolve current-project video-model capabilities plus project preferences.

    After three-level model selection (project > system settings > system default), read model
    `supported_durations` and derive `max_duration`, also returning user preference
    `project.json.default_duration`. Both storyboard/reference_video modes reuse this.

    `video_backend` ("provider/model") selects unsaved settings-form candidates: omitted resolves stored
    configuration; supplied resolves candidate × project generation mode, making two-dimensional values
    such as voice_consistency reflect current selections rather than last saves. First check candidate
    bucket capabilities. Complete bare providers (without "/") with registry video defaults, matching
    legacy project.json overrides (see `_parse_project_provider`).

    `resolution` / `uses_reference_images` are unsaved form values. `uses_reference_images` chooses task
    bucket (default: project generation mode). Evaluate `resolution` as an override through that bucket's
    request facts, returning the narrowed result and its causes in `duration_constraints`. Omission uses saved tiers
    (dashboard); empty `resolution` explicitly means automatic, without fallback to saved tiers, resolving
    as if no project tier existed (custom providers still use model defaults).

    Project generation mode fixes capability axes throughout the project, requiring no episode number:
    mode is immutable after creation."""
    resolver = ConfigResolver(async_session_factory)
    resolution_override = None if resolution is None else ResolutionOverride(resolution or None)
    try:
        project = await asyncio.to_thread(get_project_manager().load_project, name)
        generation_type = (
            ("r2v" if uses_reference_images else "i2v")
            if uses_reference_images is not None
            else video_bucket_for_generation_mode(caps_generation_mode(project))
        )
        if video_backend:
            provider_id, model_id = split_video_backend_query(video_backend)
            project = {**project, f"video_provider_{generation_type}": f"{provider_id}/{model_id}"}
            await resolver.resolve_video_backend(project, None, generation_type=generation_type)
            caps = await resolver.video_capabilities_for_model(
                provider_id, model_id, project, generation_type=generation_type
            )
            if (caps["provider_id"], caps["model"]) != (provider_id, model_id):
                raise BadRequestError("video_capability_reference_unavailable", provider=provider_id, model=model_id)
        else:
            caps = await resolver.video_capabilities_for_project(project, generation_type=generation_type)
        request_facts = await capability_request_facts(
            project,
            generation_type=generation_type,
            config_resolver=resolver,
            resolution_override=resolution_override,
        )
        caps["duration_constraints"] = duration_constraints_payload(request_facts)
        # Content-confirmation durations select/check planning tiers, sharing planning_durations with conversion.
        caps["duration_constraints"]["planning"] = planning_durations(request_facts)
        return caps
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except VideoBucketCapabilityError as exc:
        # Bucket-gate errors carry errors-catalog keys/parameters: return structured 400 with repair guidance,
        # before generic 422 catches (ValueError subclass, caught first).
        raise BadRequestError(exc.code, **exc.params) from exc
    except VideoRequestFactsError as exc:
        # Video-request-fact codes are errors-catalog keys (ValueError subclass, caught first).
        raise UnprocessableError(exc.code, **exc.params) from exc
    except ModelNotInCatalogError:
        # Unlisted model: app handlers return model-reselection 400 (ValueError subclass, caught first).
        raise
    except ValueError as exc:
        # Original exceptions enter logs only: interpolating str(exc) would mix untranslated English technical text into en/vi UI.
        logger.warning("项目 '%s' 视频模型能力解析失败: %s", name, exc)
        raise HTTPException(
            status_code=422,
            detail=_t("video_capabilities_unresolved", name=name),
        ) from exc


@router.get("/projects/{name}/workflow-status", response_model=WorkflowStatus)
async def get_workflow_status(
    name: str,
    _t: Translator,
    episode: Annotated[int | None, Query(ge=1)] = None,
):
    """Return the authenticated, server-authoritative project workflow status."""

    try:
        manager = get_project_manager()
        status = await asyncio.to_thread(WorkflowStateService(manager).get_status, name, episode)
        project = await asyncio.to_thread(manager.load_project, name)
        return WorkflowStatus.model_validate(present_episode_diagnostics(status.model_dump(mode="json"), project, _t))
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except WorkflowRequestError as exc:
        raise BadRequestError("request_invalid") from exc


class EpisodeNextSteps(BaseModel):
    episodes: list[EpisodeNextStep]


@router.get("/projects/{name}/workflow-status/episodes", response_model=EpisodeNextSteps)
async def get_episode_next_steps(name: str, _t: Translator):
    """Suggested next steps for every episode in ledger order, for project-level episode lists."""

    try:
        manager = get_project_manager()
        steps = await asyncio.to_thread(WorkflowStateService(manager).get_episode_next_steps, name)
        if not steps:
            # Unusable whole projects have no per-episode steps and need no second project read.
            return EpisodeNextSteps(episodes=[])
        project = await asyncio.to_thread(manager.load_project, name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    return EpisodeNextSteps.model_validate(
        present_episode_diagnostics(EpisodeNextSteps(episodes=steps).model_dump(mode="json"), project, _t)
    )


@router.post("/projects/{name}/workflow-plan", response_model=WorkflowPlan)
async def get_workflow_plan(name: str, request: WorkflowPlanRequest, current_user: CurrentUser, _t: Translator):
    """Return the side-effect-free plan for one transient workflow request."""

    try:
        manager = get_project_manager()
        plan = await workflow_plan_service.get_workflow_planner(manager).get_plan(
            name,
            request,
            user_id=current_user.id,
        )
        project = await asyncio.to_thread(manager.load_project, name)
        return WorkflowPlan.model_validate(present_episode_diagnostics(plan.model_dump(mode="json"), project, _t))
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except WorkflowRequestError as exc:
        raise BadRequestError("request_invalid") from exc


@router.get("/projects/{name}")
async def get_project(
    name: str,
    _t: Translator,
    locale: Locale,
    summaries: WorkflowStateServiceDep,
    coalescer: Annotated[RequestCoalescer, Depends(get_project_detail_coalescer)],
):
    """Get project details including computed fields; coalesce concurrent same-project/same-language requests."""
    try:

        def _sync():
            manager = get_project_manager()
            if not manager.project_exists(name):
                raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))

            project = manager.load_project(name)

            # Stages, artifact counts, and episode details derive from read-time summaries, never persisted JSON.
            summary = summaries.get_project_summary(name)
            project = _merge_episode_summaries(project, summary)
            project["status"] = _project_status_payload(summary, project, _t)

            scripts = {}
            for ep in project.get("episodes", []):
                script_file = ep.get("script_file", "")
                if script_file:
                    try:
                        script = manager.load_script(name, script_file)
                        key = (
                            script_file.replace("scripts/", "", 1)
                            if script_file.startswith("scripts/")
                            else script_file
                        )
                        scripts[key] = script
                    except FileNotFoundError:
                        logger.debug("剧本文件不存在，跳过: %s/%s", name, script_file)

            # Return derived reference-used status without persistence (docs/adr/0072): scripts are already loaded above,
            # so this only scans the same data again.
            project = annotate_derivative_references(project, scripts.values())

            # Compute media fingerprints for frontend content-addressed caches.
            project_path = manager.get_project_path(name)
            fingerprints = compute_asset_fingerprints(project_path)

            return {
                "project": project,
                "scripts": scripts,
                "asset_fingerprints": fingerprints,
            }

        return await coalescer.run((name, locale), lambda: run_sync_transaction(_sync))
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/projects/{name}/agent-profile")
async def get_agent_profile_status(name: str, _t: Translator):
    """Return project-local Agent Profile customizations."""

    def _sync():
        manager = get_project_manager()
        try:
            project_dir = manager.get_project_path(name)
        except ValueError as exc:
            raise BadRequestError("invalid_project_name", name=name) from exc
        return manager.get_agent_profile_status(project_dir)

    try:
        return await asyncio.to_thread(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("读取项目 Agent profile 状态失败: project=%s", name)
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/projects/{name}/agent-profile/reset")
async def reset_agent_profile(name: str, _t: Translator):
    """Destructively restore the project Agent Profile to current built-ins."""

    def _sync():
        manager = get_project_manager()
        try:
            project_dir = manager.get_project_path(name)
        except ValueError as exc:
            raise BadRequestError("invalid_project_name", name=name) from exc
        stats = manager.force_resync_profile(project_dir)
        if stats.get("errors"):
            raise RuntimeError(f"profile reset completed with {stats['errors']} file errors")
        return {"customized": False, "customized_files": []}

    try:
        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("重置项目 Agent profile 失败: project=%s", name)
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.patch("/projects/{name}")
async def update_project(name: str, req: UpdateProjectRequest, _t: Translator):
    """Update project metadata."""
    try:

        def _sync():
            manager = get_project_manager()

            def _mutate(project: dict) -> None:
                # Entire RMW uses one _project_lock, preserving concurrent PATCH/task-writeback updates.
                is_ad = project.get("content_mode") == "ad"
                if req.title is not None:
                    project["title"] = req.title
                if req.style is not None:
                    project["style"] = req.style
                for field in _PROJECT_BACKEND_FIELDS:
                    if field in req.model_fields_set:
                        value = getattr(req, field)
                        if value:
                            validate_backend_value(value, field)
                            project[field] = value
                        else:
                            project.pop(field, None)

                if "video_generate_audio" in req.model_fields_set:
                    if req.video_generate_audio is None:
                        project.pop("video_generate_audio", None)
                    else:
                        project["video_generate_audio"] = req.video_generate_audio
                # Character voice-binding enum: null/empty clears to default soft prompt constraints.
                if "character_voice_binding" in req.model_fields_set:
                    binding = (req.character_voice_binding or "").strip()
                    if not binding:
                        project.pop(CHARACTER_VOICE_BINDING_FIELD, None)
                    elif binding in VALID_CHARACTER_VOICE_BINDINGS:
                        project[CHARACTER_VOICE_BINDING_FIELD] = binding
                    else:
                        raise HTTPException(status_code=422, detail=_t("character_voice_binding_invalid"))
                _apply_narration_patch(project, req)
                # Estimated speech rate (reading units/second): generous hard range; null clears to language defaults.
                if "speech_rate_units_per_second" in req.model_fields_set:
                    if req.speech_rate_units_per_second is None:
                        project.pop(SPEECH_RATE_FIELD, None)
                    else:
                        project[SPEECH_RATE_FIELD] = _validated_speech_rate(req.speech_rate_units_per_second, _t)
                if "aspect_ratio" in req.model_fields_set and req.aspect_ratio is not None:
                    project["aspect_ratio"] = req.aspect_ratio
                if "grid_storyboard" in req.model_fields_set:
                    if is_ad and req.grid_storyboard:
                        raise HTTPException(status_code=400, detail=_t("ad_grid_not_supported"))
                    # null and false both persist explicit false grid flags, matching creation.
                    project["grid_storyboard"] = bool(req.grid_storyboard)
                if "default_duration" in req.model_fields_set:
                    # Ad rejects field presence, including null, matching creation forbidden-field contracts
                    # and preventing deletion branches from silently returning 200.
                    if is_ad:
                        raise HTTPException(status_code=400, detail=_t("ad_no_default_duration"))
                    if req.default_duration is None:
                        project.pop("default_duration", None)
                    else:
                        project["default_duration"] = req.default_duration
                if "episode_target_duration" in req.model_fields_set:
                    # Like default_duration, ad rejects field presence including null.
                    if is_ad:
                        raise HTTPException(status_code=400, detail=_t("ad_no_episode_target_duration"))
                    if req.episode_target_duration is None:
                        project.pop(EPISODE_TARGET_DURATION_FIELD, None)
                    else:
                        project[EPISODE_TARGET_DURATION_FIELD] = _validated_episode_target_duration(
                            req.episode_target_duration, _t
                        )
                if "target_duration" in req.model_fields_set:
                    if not is_ad:
                        raise HTTPException(status_code=400, detail=_t("ad_only_field", field="target_duration"))
                    if req.target_duration is None:
                        raise HTTPException(status_code=400, detail=_t("ad_target_duration_required"))
                    project["target_duration"] = req.target_duration
                if "brief" in req.model_fields_set:
                    if not is_ad:
                        raise HTTPException(status_code=400, detail=_t("ad_only_field", field="brief"))
                    project["brief"] = req.brief if req.brief is not None else ""

                if "style_template_id" in req.model_fields_set:
                    if req.style_template_id is None:
                        # Canceling templates clears expanded style prompts too, preventing orphan text.
                        project.pop("style_template_id", None)
                        project["style"] = ""
                    else:
                        if not is_known_template(req.style_template_id):
                            raise HTTPException(
                                status_code=400,
                                detail=_t("unknown_style_template", template_id=req.style_template_id),
                            )
                        project["style_template_id"] = req.style_template_id
                        project["style"] = resolve_template_prompt(req.style_template_id)
                        # Strong exclusivity: choose templates or reference images.
                        project.pop("style_image", None)
                        project.pop("style_description", None)

                if req.clear_style_image:
                    # Explicitly clear custom references for cancel-style flows.
                    project.pop("style_image", None)
                    project.pop("style_description", None)

                if "model_settings" in req.model_fields_set:
                    if req.model_settings is None:
                        project.pop("model_settings", None)
                    else:
                        project["model_settings"] = req.model_settings

                if "episodes" in req.model_fields_set and req.episodes is not None:
                    # Merge episodes, preserving complete existing data and updating explicit request fields only.
                    # Use model_fields_set, not exclude_none, for presence so
                    # null can clear fields. EpisodePatch owns writable fields (extra="ignore");
                    # computed episode statistics cannot enter, even if sent. Title is also unwritable here:
                    # script top-level title is authoritative, mirrored one-way via _apply_episode_sync into
                    # episodes[].title; PATCH /episodes/{episode} owns title writes.
                    existing_list = project.get("episodes", [])
                    patch_map: dict[int, EpisodePatch] = {}
                    for ep in req.episodes:
                        patch_map[ep.episode] = ep  # Duplicate numbers: later wins.

                    new_episodes: list[dict] = []
                    for existing_ep in existing_list:
                        ep_num = existing_ep.get("episode")
                        patch = patch_map.pop(ep_num, None)
                        if patch is None:
                            new_episodes.append(existing_ep)
                            continue
                        updated = dict(existing_ep)
                        for field_name in patch.model_fields_set - {"episode"}:
                            value = getattr(patch, field_name)
                            if value is None:
                                updated.pop(field_name, None)
                            else:
                                updated[field_name] = value
                        new_episodes.append(updated)

                    for unknown_ep in patch_map:
                        logger.warning("Skipping patch for unknown episode %s", unknown_ep)

                    project["episodes"] = new_episodes

            with project_change_source("webui"):
                # Update fields and clear episode-binding-affected Manifest claims inside one project lock;
                # return upgraded fields without reloading project.
                project = manager.update_project_reconciling_episode_bindings(name, _mutate)
                return {"success": True, "project": project}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.delete("/projects/{name}")
async def delete_project(name: str, _t: Translator):
    """Move the entire project into deleted projects, recoverable during retention."""
    try:
        manager = get_project_manager()
        project_dir = await asyncio.to_thread(manager.get_project_path, name)
        # Finalize records/move directories in one transaction; failures restore records and original directory locations.
        # Queued tasks never start in moved directories; running tasks finish but discard persistence.
        tombstone: str | None = None
        try:
            async with retiring_project(async_session_factory, project_dir) as tombstone:
                await run_sync_transaction(deleted_projects.move_in, manager.layout, project_dir, tombstone)
        except BaseException:
            if tombstone is not None:
                await run_sync_transaction(deleted_projects.move_back, manager.layout, project_dir, tombstone)
            raise
        return {"success": True, "message": _t("project_deleted", name=name)}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/deleted-projects")
async def list_deleted_projects():
    """List deleted projects, most recently deleted first."""
    entries = await asyncio.to_thread(deleted_projects.list_entries, get_project_manager().layout)
    return {
        "projects": [entry.to_dict() for entry in entries],
        "retention_days": deleted_projects.RETENTION.days,
    }


@router.post("/deleted-projects/{deleted_id}/restore")
async def restore_deleted_project(deleted_id: str):
    """Restore the whole project: suffix occupied names, rename records to the restored name, then run startup migrations."""
    # Noninterruptible: cancellation during moves loses return locations, leaving restored directories with tombstone-named records.
    project_dir = await run_noninterruptible_async(_restore_deleted_project(get_project_manager().layout, deleted_id))
    return {"name": project_dir.name}


async def _restore_deleted_project(layout: DataRootLayout, deleted_id: str) -> Path:
    project_dir = await run_sync_transaction(deleted_projects.move_out, layout, deleted_id)
    try:
        await restore_retired_project(
            async_session_factory, deleted_id, project_dir, sdk_config_dir=default_sdk_config_dir()
        )
    except BaseException:
        await run_sync_transaction(project_dir.rename, layout.deleted_projects_dir / deleted_id)
        raise
    # Rename records first; migrations query historical episode IDs by project name.
    await run_sync_transaction(
        deleted_projects.migrate_restored,
        project_dir,
        recorded_episode_ids=recorded_episode_ids_on(asyncio.get_running_loop()),
    )
    return project_dir


@router.delete("/deleted-projects/{deleted_id}", dependencies=[Depends(reject_embedded_agent)])
async def purge_deleted_project(deleted_id: str):
    """Permanently delete already-deleted projects (creator-only, never Agent tools)."""
    await run_sync_transaction(deleted_projects.purge, get_project_manager().layout, deleted_id)
    try:
        # Permanent deletion releases formal-object references; failures are retried by maintenance after retention.
        await release_holder(deleted_id)
    except Exception:
        logger.warning("撤下已删除项目的正式对象引用失败 deleted=%s", deleted_id, exc_info=True)
    return {"success": True}


@router.get("/projects/{name}/scripts/{script_file}")
async def get_script(name: str, script_file: str, _t: Translator):
    """Get script content."""
    try:
        script = await run_sync_transaction(get_project_manager().load_script, name, script_file)
        return {"script": script, "revision": script_revision(script)}
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post(
    "/projects/{name}/script-edits",
    response_model=None,
    dependencies=[Depends(require_project_migration_ok)],
)
async def edit_script_batch(
    name: str,
    command: ScriptBatchEditCommand,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
) -> JSONResponse:
    """Execute the same revisioned script-edit command exposed to the in-process Agent."""

    manager = None
    try:
        manager = get_project_manager()
        try:
            await asyncio.to_thread(manager.get_project_path, name)
        except ValueError as exc:
            raise BadRequestError("invalid_project_name", name=name) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=name) from exc

        with project_change_source("webui"):
            result = await run_sync_transaction(make_script_batch_editor(manager).execute, name, command)
        return JSONResponse(status_code=script_batch_status(result), content=result.model_dump(mode="json"))
    except FileNotFoundError as exc:
        if manager is None or not await asyncio.to_thread(manager.project_exists, name):
            raise NotFoundError("project_not_found", name=name) from exc
        target = command.script or str(command.episode)
        raise NotFoundError("script_not_found", name=target) from exc
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get(
    "/projects/{name}/script-items/{item_id}/prompt-preview",
    dependencies=[Depends(require_project_migration_ok)],
)
async def preview_script_item_prompts(
    name: str,
    item_id: str,
    _t: Translator,
    names: DisplayNamesCatalog,
    script_file: str = Query(..., description="Script filename"),
):
    """Render this item's final prompt text currently sent to image/video models.

    Share execution renderers, matching actual outgoing prompts verbatim. Read-only: no provider requests,
    charges, or manifest writes. If one side lacks valid prompts, return its unavailable reason while
    rendering the other, showing authored halves of incomplete items.

    Apply the same migration gate as generation: product-reference assembly reads manifests and blocked
    projects cannot generate. Repair-first guidance is more accurate than text that could never be sent."""
    try:
        preview = await preview_item_prompts(name, script_file, item_id)
    except ScriptItemNotFound as exc:
        raise NotFoundError("script_item_not_found", id=item_id) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc

    def _side(rendered):
        return {
            "text": rendered.text,
            "unavailable": render_generation_input_error(rendered.unavailable, rendered.unavailable_params, _t, names)
            if rendered.unavailable
            else None,
            "is_text_form": rendered.is_text_form,
            # Rendering notices (e.g. reference truncation) share task warnings and render by request language.
            "warnings": [render_message(warning["key"], warning["params"], _t, names) for warning in rendered.warnings],
        }

    return {
        "item_id": preview.item_id,
        "content_mode": preview.content_mode,
        "storyboard_image": _side(preview.storyboard_image),
        "video": _side(preview.video),
    }


#: Storyboard shapes manually addable/removable in timelines; reference units use unit routes.
_STORYBOARD_ITEM_KINDS = frozenset({"segments", "scenes", "shots"})


def _require_storyboard_items(script: dict, item_id: str | None) -> tuple[list, str, str]:
    """Return storyboard-route script items; raise corresponding API errors for unsupported shapes or missing requested IDs."""
    items, id_field, kind = resolve_items(script)
    if kind not in _STORYBOARD_ITEM_KINDS:
        raise BadRequestError("storyboard_script_required")
    if item_id is not None and not any(isinstance(item, dict) and item.get(id_field) == item_id for item in items):
        raise NotFoundError("script_item_not_found", id=item_id)
    return items, id_field, kind


class InsertScriptItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_file: str
    #: Insert after this storyboard; omission appends, creating the first item in empty scripts.
    after_id: str | None = Field(default=None, min_length=1)
    #: Narration/commentary body is speech content and required on insertion; other shapes insert empty items and ignore it.
    novel_text: str | None = None


@router.post(
    "/projects/{name}/script-items",
    dependencies=[Depends(require_project_migration_ok)],
)
async def insert_script_item(
    name: str,
    req: InsertScriptItemRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """Insert a pending-authoring storyboard using ``insert_after`` at current script revision; server issues an episode-unused ID."""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            items, id_field, kind = _require_storyboard_items(current, req.after_id)
            last_id = next(
                (item.get(id_field) for item in reversed(items) if isinstance(item, dict)),
                None,
            )
            after_id = req.after_id if req.after_id is not None else last_id
            item = blank_item_after(current, after_id)
            if kind == "segments":
                if req.novel_text is None or not req.novel_text.strip():
                    raise UnprocessableError("narration_segment_text_required")
                item["novel_text"] = req.novel_text
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "insert_after", "after_id": after_id, "item": item}],
                    editor=make_script_batch_editor(manager),
                    current_script=current,
                )
            require_script_edit_result(result, operation_not_found=True)
            saved_items, _id_field, _kind = resolve_items(manager.load_script(name, req.script_file))
            # Concurrent removal may occur between commit/reread; item is then null.
            # Batch commits issue server IDs; blank_item_after gives only placeholders.
            inserted_id = result.affected_ids[0] if result.affected_ids else None
            inserted = next(
                (entry for entry in saved_items if isinstance(entry, dict) and entry.get(id_field) == inserted_id),
                None,
            )
            return {"success": True, "item": inserted, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post(
    "/projects/{name}/episodes/{episode}/blank-script",
    dependencies=[Depends(require_project_migration_ok)],
)
async def start_episode_blank_script(name: str, episode: int):
    """Start blank: create an empty formal script only if absent and discard unconfirmed planning."""

    def _sync():
        with project_change_source("webui"):
            return start_blank_script(get_project_manager(), name, episode)

    try:
        script_file = await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except BlankScriptError as exc:
        if exc.code == "episode_not_found":
            raise NotFoundError("episode_not_found", episode=episode) from exc
        if exc.code == "draft_agent_owned":
            raise ConflictError("draft_agent_owned") from exc
        raise ConflictError("blank_script_formal_exists").with_diagnostic(str(exc)) from exc
    return {"success": True, "script_file": script_file}


@router.delete(
    "/projects/{name}/script-items/{item_id}",
    dependencies=[Depends(require_project_migration_ok)],
)
async def remove_script_item(
    name: str,
    item_id: str,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
    script_file: str = Query(..., description="Script filename"),
):
    """Remove storyboard ``item_id`` using ``remove`` at current revision, removing its artifacts too."""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, script_file)
            _require_storyboard_items(current, item_id)
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    script_file,
                    [{"op": "remove", "id": item_id}],
                    editor=make_script_batch_editor(manager),
                    current_script=current,
                )
            require_script_edit_result(result, operation_not_found=True)
            return {"success": True, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class MoveScriptItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_file: str
    #: Move after this storyboard; null moves first.
    after_id: str | None = Field(min_length=1)


@router.post(
    "/projects/{name}/script-items/{item_id}/move",
    dependencies=[Depends(require_project_migration_ok)],
)
async def move_script_item(
    name: str,
    item_id: str,
    req: MoveScriptItemRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """Move ``item_id`` after ``after_id`` with ``move_after`` at current revision, moving artifacts with it."""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            _require_storyboard_items(current, item_id)
            _require_storyboard_items(current, req.after_id)
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "move_after", "id": item_id, "after_id": req.after_id}],
                    editor=make_script_batch_editor(manager),
                    current_script=current,
                )
            require_script_edit_result(result)
            return {"success": True, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class UpdateSceneRequest(BaseModel):
    script_file: str
    updates: dict


@router.patch("/projects/{name}/script-scenes/{scene_id}", dependencies=[Depends(require_project_migration_ok)])
async def update_scene(
    name: str,
    scene_id: str,
    req: UpdateSceneRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """Update one drama storyboard by scene_id.

    Separate paths clearly from scene-asset CRUD (``/projects/{name}/scenes/{entry_name}``), preventing
    registration-order matching from intercepting SceneCard saves and yielding two Pydantic "Field required" errors."""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            scenes = current.get("scenes")
            if not isinstance(scenes, list) or not any(
                isinstance(scene, dict) and scene.get("scene_id") == scene_id for scene in scenes
            ):
                raise HTTPException(status_code=404, detail=_t("scene_not_found", id=scene_id))
            allowed = {
                "duration_seconds",
                "image_prompt",
                "video_prompt",
                "characters_in_scene",
                "scenes",
                "props",
                "segment_break",
                "utterances",
                "note",
            }
            fields: dict[str, Any] = {}
            for key, raw_value in req.updates.items():
                if key not in allowed or (raw_value is None and key != "note"):
                    continue
                value = raw_value
                if key in {"characters_in_scene", "scenes", "props"} and isinstance(value, list):
                    value = [asset_name_comparison_key(entry) if isinstance(entry, str) else entry for entry in value]
                fields[key] = value
            if not fields:
                matched = next(scene for scene in scenes if scene.get("scene_id") == scene_id)
                return {"success": True, "scene": matched}
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "update", "id": scene_id, "fields": fields}],
                    editor=make_script_batch_editor(manager),
                )
            require_script_edit_result(
                result,
                operation_not_found=True,
            )
            saved = manager.load_script(name, req.script_file)
            matched = next(scene for scene in saved["scenes"] if scene.get("scene_id") == scene_id)
            return {"success": True, "scene": matched, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        # Structural failures, episode mismatch, and invalid names raise ValueError (including its subclass ScriptStructureValidationError);
        # map uniformly to 422 instead of 500 fallback.
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class UpdateShotRequest(BaseModel):
    script_file: str
    updates: dict


# Ad storyboard PATCH allowlist: shot_id identity/generated_assets runtime fields are immutable.
_SHOT_UPDATABLE_FIELDS = (
    "section",
    "voiceover_text",
    "duration_seconds",
    "image_prompt",
    "video_prompt",
    "characters_in_shot",
    "scenes",
    "props",
    "products_in_shot",
    "note",
)


def _require_ad_script(script: dict, _t: Translator) -> list[dict]:
    """Require ad shape (content_mode=ad with shots), returning shots.

    Like update_segment narration guards, reject other modes even if residual shots remain, preventing ad-style rewrites."""
    if script.get("content_mode") != "ad" or "shots" not in script:
        raise HTTPException(status_code=400, detail=_t("ad_mode_required"))
    shots = script.get("shots")
    # Invalid shapes fail loudly; routers map ValueError to 422.
    if not isinstance(shots, list):
        raise ValueError("ad script field 'shots' must be a list")
    if not all(isinstance(s, dict) for s in shots):
        raise ValueError("ad script field 'shots' contains non-object elements")
    # Reject missing/dirty shot_id too, preventing misleading PATCH-location 404.
    if not all(isinstance(s.get("shot_id"), str) and s["shot_id"] for s in shots):
        raise ValueError("ad script field 'shots' contains elements missing valid 'shot_id'")
    # shot_id identifies storyboards; duplicates make PATCH silently change the first match.
    shot_ids = [s["shot_id"] for s in shots]
    if len(set(shot_ids)) != len(shot_ids):
        raise ValueError("ad script field 'shots' contains duplicate 'shot_id' values")
    return shots


@router.patch("/projects/{name}/script-shots/{shot_id}", dependencies=[Depends(require_project_migration_ok)])
async def update_shot(
    name: str,
    shot_id: str,
    req: UpdateShotRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """Update one ad/short-film storyboard by shot_id.

    Align paths with ``script-scenes``; allowlist speech / section / duration / references, etc., with
    central persistence's do-not-worsen structural validation as fallback."""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            shots = _require_ad_script(current, _t)
            matched = next((shot for shot in shots if shot.get("shot_id") == shot_id), None)
            if matched is None:
                raise HTTPException(status_code=404, detail=_t("shot_not_found", id=shot_id))
            fields = {
                key: value
                for key, value in req.updates.items()
                if key in _SHOT_UPDATABLE_FIELDS and (value is not None or key == "note")
            }
            if not fields:
                return {"success": True, "shot": matched}
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "update", "id": shot_id, "fields": fields}],
                    editor=make_script_batch_editor(manager),
                )
            require_script_edit_result(
                result,
                operation_not_found=True,
            )
            saved = manager.load_script(name, req.script_file)
            matched = next(shot for shot in saved["shots"] if shot.get("shot_id") == shot_id)
            return {"success": True, "shot": matched, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        # Structural failures, episode mismatch, and invalid names raise ValueError (including its subclass ScriptStructureValidationError);
        # map uniformly to 422 instead of 500 fallback.
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class UpdateSegmentRequest(BaseModel):
    script_file: str
    duration_seconds: int | None = None
    segment_break: bool | None = None
    novel_text: str | None = None
    image_prompt: dict | str | None = None
    video_prompt: dict | str | None = None
    note: str | None = None
    characters_in_segment: list[str] | None = None
    scenes: list[str] | None = None
    props: list[str] | None = None


class UpdateOverviewRequest(BaseModel):
    synopsis: str | None = None
    genre: str | None = None
    theme: str | None = None
    world_setting: str | None = None


class UpdateEpisodeRequest(BaseModel):
    title: str


class UpdateEpisodeSourceRequest(BaseModel):
    text: str
    #: Drama episode source-file type; omission retains existing type or records novel if absent; ignored by other modes.
    source_kind: SourceKind | None = None
    #: Confirmed type changes that stale existing episode plans.
    confirm: bool = False


@router.patch("/projects/{name}/segments/{segment_id}", dependencies=[Depends(require_project_migration_ok)])
async def update_segment(
    name: str,
    segment_id: str,
    req: UpdateSegmentRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """Update a narration/commentary storyboard."""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            if current.get("content_mode") != "narration" or "segments" not in current:
                raise HTTPException(status_code=400, detail=_t("narration_mode_required"))
            segments = current.get("segments")
            if not isinstance(segments, list):
                raise ValueError("narration script field 'segments' must be a list")
            matched = next(
                (
                    segment
                    for segment in segments
                    if isinstance(segment, dict) and segment.get("segment_id") == segment_id
                ),
                None,
            )
            if matched is None:
                raise HTTPException(status_code=404, detail=_t("segment_not_found", id=segment_id))
            fields: dict[str, Any] = {}
            for field in (
                "duration_seconds",
                "segment_break",
                "novel_text",
                "image_prompt",
                "video_prompt",
            ):
                value = getattr(req, field)
                if value is not None:
                    fields[field] = value
            if "note" in req.model_fields_set:
                fields["note"] = req.note
            for field in ("characters_in_segment", "scenes", "props"):
                if field in req.model_fields_set:
                    fields[field] = [asset_name_comparison_key(value) for value in (getattr(req, field) or [])]
            if not fields:
                return {"success": True, "segment": matched}
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "update", "id": segment_id, "fields": fields}],
                    editor=make_script_batch_editor(manager),
                )
            require_script_edit_result(
                result,
                operation_not_found=True,
            )
            saved = manager.load_script(name, req.script_file)
            matched = next(segment for segment in saved["segments"] if segment.get("segment_id") == segment_id)
            return {"success": True, "segment": matched, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        # Structural failures, episode mismatch, and invalid names raise ValueError (including its subclass ScriptStructureValidationError);
        # map uniformly to 422 instead of 500 fallback.
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.patch("/projects/{name}/episodes/{episode}", dependencies=[Depends(require_project_migration_ok)])
async def update_episode(name: str, episode: int, req: UpdateEpisodeRequest, _t: Translator):
    """Update episode top-level metadata (currently title only).

    With scripts, scripts/*.json top-level title is authoritative: locked_episode_script changes it inside
    script-lock → project-lock critical sections and calls _apply_episode_sync inline to synchronize
    project.json episodes[].title, atomically without TOCTOU. PATCH /projects can no longer edit the mirror (title is
    absent from EpisodePatch), eliminating duplicate truth sources.

    Without scripts (new blank/unplanned episodes), store titles only in ledger entries; later scripts use
    them initially. Titles may be cleared; presentation derives untitled episode names by broadcast position."""
    title = req.title.strip()

    try:

        def _sync():
            manager = get_project_manager()

            def _resolve(project: dict) -> str:
                episodes = project.get("episodes") or []
                meta = next((e for e in episodes if e.get("episode") == episode), None)
                if meta is None or not meta.get("script_file"):
                    raise HTTPException(status_code=404, detail=_t("episode_not_found", episode=episode))
                return meta["script_file"]

            def _retitle_ledger_entry(project: dict) -> None:
                script_file = _resolve(project)
                script_path = (
                    manager.get_project_path(name) / "scripts" / manager.normalize_script_filename(script_file)
                )
                if script_path.is_file():
                    # A script appeared between reads: titles now belong to it; ask callers to retry.
                    raise HTTPException(status_code=409, detail=_t("ref_script_rebound"))
                meta = next(e for e in project["episodes"] if e.get("episode") == episode)
                meta["title"] = title

            with project_change_source("webui"):
                try:
                    with manager.locked_episode_script(name, _resolve) as script:
                        script["title"] = title
                except FileNotFoundError as exc:
                    if not manager.project_exists(name):
                        raise NotFoundError("project_not_found", name=name) from exc
                    # No script yet: store title in ledger entries.
                    manager.update_project(name, _retitle_ledger_entry)
                except EpisodeScriptReboundError as exc:
                    logger.info("episode script rebound during title update: %s", exc)
                    raise HTTPException(status_code=409, detail=_t("ref_script_rebound")) from exc
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422, detail=_t("script_validation_failed", details=str(exc))
                    ) from exc

            # Return just-written values (frontend refreshProject does not strictly depend on responses).
            # Avoid postlock load_project, saving I/O and preventing concurrent writers contaminating returns.
            return {"success": True, "episode": {"episode": episode, "title": title}}

        return await run_sync_transaction(_sync)
    except (HTTPException, ApiError):
        # Catch ApiError with HTTPException: _sync NotFoundError is not an
        # HTTPException subclass and otherwise falls into except Exception as 500.
        raise
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.put("/projects/{name}/episodes/{episode}/source", dependencies=[Depends(require_project_migration_ok)])
async def update_episode_source(name: str, episode: int, req: UpdateEpisodeSourceRequest, _t: Translator):
    """Enter/edit episode source on its page; filling sourceless episodes makes them source-owning. Reject cut-episode sources, derived by planning.

    If changing type would stale existing plans and ``confirm`` is false, write neither source nor type;
    return ``needs_confirmation`` and affected IDs (``affected_episodes``)."""

    def _sync() -> dict[str, Any]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        with project_change_source("webui"):
            written = set_episode_source_text(
                manager, name, episode, req.text, source_kind=req.source_kind, confirm=req.confirm
            )
        return {
            "success": True,
            "episode": episode,
            "source_origin": written.origin.value,
            "applied": written.applied,
            "needs_confirmation": not written.applied,
            "affected_episodes": written.affected_episodes,
        }

    try:
        return await run_sync_transaction(_sync)
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, episode=episode) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


# ==================== Source-file management ====================


@router.post("/projects/{name}/source", dependencies=[Depends(require_project_migration_ok)])
async def set_project_source(
    name: Annotated[str, FastAPIPath(pattern=r"^[a-zA-Z0-9_-]+$")],
    _t: Translator,
    names: DisplayNamesCatalog,
    generate_overview: Annotated[bool, Form()] = True,
    content: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
):
    """Upload novel source files or submit text, optionally triggering AI overview generation.

    Two mutually exclusive multipart/form-data inputs:
    - file: .txt/.md upload retaining its filename.
    - content: direct text automatically named novel.txt.

    Maximum 200000 characters (about 100000 Chinese characters)."""
    MAX_CHARS = 200_000
    ALLOWED_SUFFIXES = {".txt", ".md"}

    if not content and not file:
        raise HTTPException(status_code=400, detail=_t("content_or_file_required"))
    if content and file:
        raise HTTPException(status_code=400, detail=_t("one_of_content_or_file"))

    try:
        manager = get_project_manager()

        # Read uploads asynchronously.
        raw: bytes | None = None
        original_name: str = "novel.txt"
        if file:
            original_name = file.filename or "novel.txt"
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(status_code=400, detail=_t("unsupported_file_type", name=suffix))
            if file.size is not None and file.size > MAX_CHARS * 4:
                raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
            raw = await file.read()
        text_content: str = content or ""

        # Synchronous file I/O runs in threads.
        def _sync_write():
            if not manager.project_exists(name):
                raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
            with manager.locked_source_registration(name) as (source_dir, project, _undo):
                if raw is not None:
                    safe_filename = Path(original_name).name
                    if is_derived_episode_name(safe_filename):
                        raise HTTPException(status_code=400, detail=_t("source_name_reserved"))
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise HTTPException(status_code=400, detail=_t("invalid_encoding")) from exc
                    if len(text) > MAX_CHARS:
                        raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
                    (source_dir / safe_filename).write_text(text, encoding="utf-8")
                    register_whole_source_file(project, f"source/{safe_filename}")
                    return safe_filename, len(text)
                if len(text_content) > MAX_CHARS:
                    raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
                safe_filename = "novel.txt"
                (source_dir / safe_filename).write_text(text_content, encoding="utf-8")
                register_whole_source_file(project, f"source/{safe_filename}")
                return safe_filename, len(text_content)

        safe_filename, chars = await run_sync_transaction(_sync_write)

        result: dict = {"success": True, "filename": safe_filename, "chars": chars}

        if generate_overview:
            try:
                with project_change_source("webui"):
                    overview = await manager.generate_overview(name)
                result["overview"] = overview
            except ModelNotInCatalogError as ov_err:
                # Unlisted text models return model-reselection guidance, not generic generation failures.
                logger.warning("上传后概览生成失败：模型不在清单中 (%s)", ov_err)
                result["overview"] = None
                result["overview_error"] = render_message(
                    failure_message_key(ov_err.code, ov_err.params), ov_err.params, _t, names
                )
            except Exception as ov_err:
                # Overview generation is optional after upload; failures return fallback notices without undoing successful upload.
                # Bare str(ov_err) may expose server paths/internal details; return localized generic wording.
                logger.exception("上传后概览生成失败")
                result["overview"] = None
                result["overview_error"] = (
                    _t("overview_ai_response_invalid")
                    if isinstance(ov_err, PydanticValidationError)
                    else _t("overview_generation_failed")
                )

        return result
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
    finally:
        if file:
            await file.close()


# ==================== Project-overview management ====================


@router.post("/projects/{name}/generate-overview", dependencies=[Depends(require_project_migration_ok)])
async def generate_overview(name: str, _t: Translator):
    """Generate project overviews using AI."""
    try:
        await asyncio.to_thread(get_project_manager().get_project_path, name)
    except ValueError as e:
        # Reject invalid project names (path traversal, etc.) before generation, preventing inner provider-resolution
        # except ValueError from misclassifying them as unconfigured providers.
        raise BadRequestError("invalid_project_name", name=name) from e
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    def _provider_not_configured(exc: ValueError) -> BadRequestError:
        # Names already passed prechecks; errors here belong to provider resolution (unconfigured/unavailable); str(exc) logs only.
        logger.warning("生成概述配置错误: name=%s (%s)", name, exc)
        return BadRequestError("text_provider_not_configured")

    try:
        # EmptySourceError / PydanticValidationError / ModelNotInCatalogError are ValueError subclasses, passed to
        # dedicated handling below instead of domain_error_on_value_error misclassifying unconfigured providers.
        with (
            project_change_source("webui"),
            domain_error_on_value_error(
                _provider_not_configured,
                extra_passthrough=(EmptySourceError, PydanticValidationError, ModelNotInCatalogError),
            ),
        ):
            overview = await get_project_manager().generate_overview(name)
        return {"success": True, "overview": overview}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except PydanticValidationError as exc:
        # Schema-rejected outputs (last defense after backend fallback fails):
        # bare Pydantic errors include raw model-output fragments; never forward to users.
        logger.exception("概述生成响应解析失败")
        raise HTTPException(status_code=400, detail=_t("overview_ai_response_invalid")) from exc
    except TextOutputTruncatedError as exc:
        # Max-output truncation shares text-task problem tickets, guiding frontend output-length registration or model changes.
        logger.warning("概述生成输出被截断: name=%s (%s)", name, exc)
        raise UnprocessableError(
            "text_output_truncated", provider_id=exc.provider_id or exc.provider, model=exc.model
        ).with_diagnostic(truncation_problem(exc).model_dump()) from exc
    except EmptySourceError as e:
        logger.warning("生成概述参数错误: name=%s (%s)", name, e)
        raise BadRequestError("overview_source_empty") from e
    except json.JSONDecodeError as exc:
        # Provider resolution reloads project.json; corruption must not mean unconfigured providers.
        logger.exception("生成概述失败：项目数据损坏 name=%s", name)
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
    except (HTTPException, ApiError, ModelNotInCatalogError):
        # ModelNotInCatalogError: unlisted project text model; app handlers return model-reselection 400.
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.patch("/projects/{name}/overview")
async def update_overview(name: str, req: UpdateOverviewRequest, _t: Translator):
    """Update project overviews manually."""
    try:

        def _sync():
            manager = get_project_manager()
            captured: dict[str, Any] = {}

            def _mutate(project: dict) -> None:
                # Entire RMW uses one _project_lock, preserving concurrent generated-overview writebacks.
                if "overview" not in project:
                    project["overview"] = {}
                if req.synopsis is not None:
                    project["overview"]["synopsis"] = req.synopsis
                if req.genre is not None:
                    project["overview"]["genre"] = req.genre
                if req.theme is not None:
                    project["overview"]["theme"] = req.theme
                if req.world_setting is not None:
                    project["overview"]["world_setting"] = req.world_setting
                captured["overview"] = project["overview"]

            with project_change_source("webui"):
                manager.update_project(name, _mutate)
            return {"success": True, "overview": captured["overview"]}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
