"""Generation API routes.

Handle requests for storyboard images, videos, character images, and clue images.
All generation requests enter GenerationQueue for asynchronous GenerationWorker execution.

Error handling: route bodies retain only the happy path. Application-level exception handlers map
and redact domain exceptions (``lib.infra.api_errors``) and library exceptions (``FileNotFoundError`` /
``ScriptEditError`` / ``TaskSpecValidationError`` / unexpected exceptions) into HTTP responses
(see ``server/error_handlers.py``)."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from lib.artifacts.artifact_activation import (
    active_artifact_currency_resolver,
    artifact_is_usable,
    resolve_artifact_episode,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import ConfigResolver, video_bucket_for_generation_mode
from lib.generation.generation_queue import get_generation_queue
from lib.generation.generation_queue_client import TaskSpec
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.infra.json_io import domain_error_on_value_error
from lib.infra.path_safety import safe_exists, safe_join
from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE, DerivativeSheetSource, resolve_derivative_sheet_source
from lib.project.asset_types import ASSET_SPECS, resolve_asset_key, validate_asset_name
from lib.project.project_change_hints import build_change_label, emit_project_change_batch, project_change_source
from lib.project.project_manager import get_project_manager, is_reference_video_project
from lib.script.script_editor import resolve_items
from lib.script.script_models import get_generated_assets
from lib.script.script_skeleton import resolve_script_kind
from lib.script.storyboard_sequence import (
    EndFrameImageUnavailable,
    StoryboardImageUnavailable,
    find_storyboard_item,
    get_storyboard_items,
)
from lib.speech.narration_config import NarrationConfigError, require_project_tts_generation
from lib.speech.narration_delivery import canonical_narration_text
from lib.speech.speech_composition import SpeechMode, admit_script_unit
from server.auth import CurrentUser
from server.i18n import Translator
from server.routers._validators import require_audio_switch_supported, require_video_bucket_capability
from server.services.admission.reference_admission import require_admitted_storyboard_references
from server.services.tasks.derivative_sheet_tasks import build_derivative_sheet_instruction
from server.services.tasks.generation_context import AudioLaneRequest, resolve_generation_context
from server.services.tasks.image_edit_tasks import (
    EDITABLE_RESOURCE_TYPES,
    resolve_usable_image_edit_source,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_request_artifact_episode(
    project: dict,
    script: dict,
    script_file: str,
) -> int:
    """Validate the submitted script's live project binding before enqueue."""

    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_script_file", name=script_file)):
        return resolve_artifact_episode(
            project=project,
            script=script,
            script_filename=script_file,
        )


# ==================== Request models ====================


class GenerateStoryboardRequest(BaseModel):
    prompt: str | dict
    script_file: str


class GenerateVideoRequest(BaseModel):
    # Narration delivery is project configuration and does not affect video requests; reject removed per-request delivery fields as unknown.
    model_config = ConfigDict(extra="forbid")

    prompt: str | dict
    script_file: str
    duration_seconds: int | None = Field(default=None, gt=0)
    seed: int | None = None


class GenerateTtsRequest(BaseModel):
    script_file: str


class GenerateVoiceSampleRequest(BaseModel):
    text: str
    voice: str


class ConfirmVoiceSampleRequest(BaseModel):
    task_id: str


class EditImageRequest(BaseModel):
    resource_type: str
    resource_id: str
    instruction: str
    script_file: str | None = None


# ==================== Storyboard-image generation ====================


@router.post("/projects/{project_name}/generate/storyboard/{segment_id}")
async def generate_storyboard(
    project_name: str,
    segment_id: str,
    req: GenerateStoryboardRequest,
    user: CurrentUser,
    _t: Translator,
):
    """Queue storyboard-image generation and immediately return task_id.

    GenerationWorker executes asynchronously; SSE pushes status."""

    def _sync():
        pm_local = get_project_manager()
        project = pm_local.load_project(project_name)
        script = pm_local.load_script(project_name, req.script_file)
        _resolve_request_artifact_episode(project, script, req.script_file)
        items, id_field, _, _, _ = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, segment_id)
        if resolved is None:
            raise NotFoundError("segment_not_found", id=segment_id)
        # Formal script prompts alone ground generation: reject pending prompts; request-body prompt cannot replace them.
        if resolved[0].get("image_prompt") is None:
            raise ConflictError("script_prompt_pending", segment_id=segment_id)
        require_admitted_storyboard_references(project, [resolved[0]])

    await run_sync_transaction(_sync)

    # Structural validation/construction share one guard with SDK enqueue, without divergent rules;
    # TaskSpecValidationError maps to 400 through application handlers.
    spec = TaskSpec.from_request(
        task_type="storyboard",
        media_type="image",
        resource_id=segment_id,
        prompt=req.prompt,
        script_file=req.script_file,
    )

    # Enqueue.
    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        script_file=spec.script_file,
        payload=spec.payload,
        source="webui",
        user_id=user.id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("storyboard_task_submitted", segment_id=segment_id),
    }


# ==================== Video generation ====================


@router.post("/projects/{project_name}/generate/video/{segment_id}")
async def generate_video(
    project_name: str,
    segment_id: str,
    req: GenerateVideoRequest,
    user: CurrentUser,
    _t: Translator,
):
    """Queue video generation and immediately return task_id.

    Requires a storyboard image as start frame. GenerationWorker executes asynchronously.

    Only for storyboard image-to-video: reference-to-video has no storyboard-image step and is rejected with guidance to the appropriate entry point."""

    def _sync() -> dict:
        pm_local = get_project_manager()
        project = pm_local.load_project(project_name)
        project_path = pm_local.get_project_path(project_name)

        # Check generation mode before storyboard-image existence: reference-to-video has no storyboard step, otherwise users get
        # misleading generate-storyboard-first guidance. Existing storyboard files inapplicable to the mode do not participate;
        # project.json alone determines generation mode, keeping execution and estimation on the same axis.
        if is_reference_video_project(project):
            raise ConflictError("video_route_is_reference_video")

        # Like workers, recognize only explicit generated_assets.storyboard_image bindings,
        # never same-name file inference. Grid bindings to scene_{id}_first.png still resolve normally.
        # Missing scripts (FileNotFoundError) and dirty storyboard-array keys (ScriptEditError) both
        # fail fast, never silently default: an existing default file would cause successful submission
        # followed by certain worker failure on script parsing, violating expectations. Application handlers
        # map both to redacted 404 / 400 responses.
        script = pm_local.load_script(project_name, req.script_file)
        artifact_episode = _resolve_request_artifact_episode(project, script, req.script_file)
        items, id_field, _, _, _ = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, segment_id)
        if resolved is None:
            raise NotFoundError("segment_not_found", id=segment_id)
        require_admitted_storyboard_references(project, [resolved[0]])
        # Workers use current script video_prompt at execution, ignoring request prompts in enqueue payloads;
        # speech admission therefore follows on-disk units.
        admission = admit_script_unit(resolve_script_kind(script), resolved[0])
        if not admission.allowed:
            raise HTTPException(status_code=409, detail=admission.to_dict())
        # Like storyboard-image endpoints, determine pending authoring by formal video_prompt; request-body prompt cannot substitute.
        if resolved[0].get("video_prompt") is None:
            raise ConflictError("script_prompt_pending", segment_id=segment_id)
        # On-disk JSON fields are untrusted; path validation and explicit-binding requirements after schema activation
        # share the worker/current-baseline reconstruction resolver.
        try:
            resolve_usable_storyboard_video_inputs(
                project_path=project_path,
                project=project,
                episode=artifact_episode,
                resource_id=segment_id,
                item=resolved[0],
            )
        except EndFrameImageUnavailable:
            raise BadRequestError("invalid_end_frame_image_path", segment_id=segment_id) from None
        except StoryboardImageUnavailable:
            raise BadRequestError("generate_storyboard_first", segment_id=segment_id) from None
        except ValueError:
            raise BadRequestError("invalid_storyboard_image_path", segment_id=segment_id) from None
        return project

    project = await run_sync_transaction(_sync)

    # Evaluate buckets by project generation mode (docs/adr/0054), sharing execution lane declarations without separately hardcoding i2v.
    # The mode check above excludes reference-to-video, so reachable projects are always i2v. Resolution preflight exposes missing capabilities/
    # dangling references with repair guidance at submission rather than asynchronous task-panel failure.
    _video_bucket = video_bucket_for_generation_mode(project.get("generation_mode"))
    await require_video_bucket_capability(project, _video_bucket)
    await require_audio_switch_supported(project, _video_bucket)

    # Structural validation/construction share one guard with SDK enqueue, without divergent rules.
    # Duration is a capability dimension, validated by execution after provider resolution (ADR-0001).
    extra_payload: dict[str, object] = {
        "seed": req.seed,
        "duration_seconds": req.duration_seconds,
    }
    spec = TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id=segment_id,
        prompt=req.prompt,
        script_file=req.script_file,
        extra_payload=extra_payload,
    )

    # Enqueue (services resolve providers automatically from configuration; callers need not supply one).
    result = await get_generation_queue().enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        script_file=spec.script_file,
        payload=spec.payload,
        source="webui",
        user_id=user.id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("video_task_submitted", segment_id=segment_id),
    }


# ==================== Narration audio (TTS) generation ====================


async def _require_audio_provider_configured(project: dict) -> str:
    """Return 400 immediately without a configured audio provider, giving clear guidance at generation entry.

    Resolution failure (no global default and auto-resolution finds no ready audio provider) means
    unconfigured. Successful resolution followed by invalid credentials or other runtime errors belongs
    to workers/task panels, as with images/videos. Return resolved provider_id for enqueue reuse,
    avoiding repeated resolution per segment."""
    from lib.db import async_session_factory

    try:
        resolved = await ConfigResolver(async_session_factory).resolve_audio_backend(project, None)
    except ModelNotInCatalogError:
        # Model absent from provider lists: app-level handlers return model-reselection 400 (ValueError subclass, caught before ValueError).
        raise
    except ValueError as exc:
        raise BadRequestError("audio_provider_not_configured") from exc
    return resolved.provider_id


def _require_project_tts(project: dict) -> str:
    """Generate narration only for TTS-selected projects with complete snapshots; return snapshot provider_id for enqueue reuse."""

    try:
        return require_project_tts_generation(project).provider_id
    except NarrationConfigError as exc:
        raise BadRequestError(exc.code) from exc


async def _enqueue_tts_segment(
    *,
    project_name: str,
    segment_id: str,
    script_file: str,
    user_id: str,
    provider_id: str | None,
) -> dict:
    spec = TaskSpec.from_request(
        task_type="tts",
        media_type="audio",
        resource_id=segment_id,
        script_file=script_file,
    )

    queue = get_generation_queue()
    return await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        script_file=spec.script_file,
        payload=spec.payload,
        source="webui",
        user_id=user_id,
        provider_id=provider_id,
    )


@router.post("/projects/{project_name}/generate/tts/{segment_id}")
async def generate_tts(
    project_name: str,
    segment_id: str,
    req: GenerateTtsRequest,
    user: CurrentUser,
    _t: Translator,
):
    """Explicitly enqueue narration audio generation for one narrator-owned unit."""

    def _sync() -> tuple[dict, dict]:
        pm_local = get_project_manager()
        _project = pm_local.load_project(project_name)
        script = pm_local.load_script(project_name, req.script_file)
        _resolve_request_artifact_episode(_project, script, req.script_file)
        items, id_field, kind = resolve_items(script)
        resolved = find_storyboard_item(items, id_field, segment_id)
        if resolved is None:
            raise NotFoundError("segment_not_found", id=segment_id)
        item = resolved[0]
        admission = admit_script_unit(kind, item)
        narration_text = canonical_narration_text(admission.preparation)
        if not admission.allowed:
            if not narration_text:
                raise BadRequestError("tts_narration_text_missing", segment_id=segment_id)
            raise HTTPException(status_code=409, detail=admission.to_dict())
        if admission.mode is not SpeechMode.NARRATOR_VOICEOVER or not narration_text:
            raise BadRequestError("tts_not_applicable", segment_id=segment_id)
        return _project, item

    project, _segment = await run_sync_transaction(_sync)

    provider_id = _require_project_tts(project)

    result = await _enqueue_tts_segment(
        project_name=project_name,
        segment_id=segment_id,
        script_file=req.script_file,
        user_id=user.id,
        provider_id=provider_id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("tts_task_submitted", segment_id=segment_id),
    }


@router.post("/projects/{project_name}/generate/tts")
async def generate_tts_batch(
    project_name: str,
    req: GenerateTtsRequest,
    user: CurrentUser,
    _t: Translator,
):
    """Queue narration audio in batch: only narrator-owned units missing narration_audio."""

    def _sync() -> tuple[dict, list[str]]:
        pm_local = get_project_manager()
        _project = pm_local.load_project(project_name)
        script = pm_local.load_script(project_name, req.script_file)
        items, id_field, kind = resolve_items(script)
        episode = _resolve_request_artifact_episode(_project, script, req.script_file)
        currency = active_artifact_currency_resolver(pm_local.get_project_path(project_name), _project)
        missing: list[str] = []
        for item in items:
            admission = admit_script_unit(kind, item)
            if not admission.allowed or admission.mode is not SpeechMode.NARRATOR_VOICEOVER:
                continue
            if not canonical_narration_text(admission.preparation):
                continue
            seg_id = item.get(id_field)
            if seg_id and not artifact_is_usable(
                currency,
                ArtifactKey.episode_audio(episode, str(seg_id)),
                get_generated_assets(item).get("narration_audio"),
            ):
                missing.append(str(seg_id))
        return _project, missing

    project, missing_ids = await run_sync_transaction(_sync)
    provider_id = _require_project_tts(project)

    if not missing_ids:
        return {
            "success": True,
            "task_ids": [],
            "task_ids_by_segment": {},
            "deduped": False,
            "message": _t("tts_batch_none_missing"),
        }

    task_ids: list[str] = []
    # Return each segment's own task rows: optimistic occupancy markers must await their own persistence, not the whole batch.
    # Skipped segments are absent from the mapping so callers leave them unmarked.
    task_ids_by_segment: dict[str, str] = {}
    deduped_flags: list[bool] = []
    for seg_id in missing_ids:
        result = await _enqueue_tts_segment(
            project_name=project_name,
            segment_id=seg_id,
            script_file=req.script_file,
            user_id=user.id,
            provider_id=provider_id,
        )
        task_ids.append(result["task_id"])
        task_ids_by_segment[seg_id] = result["task_id"]
        deduped_flags.append(bool(result.get("deduped", False)))

    message = _t("tts_batch_submitted", count=len(task_ids)) if task_ids else _t("tts_batch_none_missing")
    # Batch deduplication: deduped only if every enqueue matched existing tasks and no new task was created.
    return {
        "success": True,
        "task_ids": task_ids,
        "task_ids_by_segment": task_ids_by_segment,
        "deduped": bool(task_ids) and all(deduped_flags),
        "message": message,
    }


# ==================== Character reference audio: TTS preview samples ====================

# Coarse preview-text length guard (same as frontend VOICE_SAMPLE_TEXT_MAX_LENGTH). Reference audio must be 2-10 seconds,
# but text-length-to-duration varies by language/voice and cannot be predicted precisely before submission. Block only pasted paragraphs
# certain to fail and waste billing; actual duration validation remains post-persistence in execution.
VOICE_SAMPLE_TEXT_MAX_LENGTH = 200


@router.get("/projects/{project_name}/audio-backend/voices")
async def get_audio_backend_voices(project_name: str, _t: Translator):
    """Return voices from the current project's actual effective audio backend for TTS preview selection.

    Without an audio provider, return configured=false + empty list, not 400: frontend disables generation
    rather than reporting an error, matching character-card behavior when audio_backend is unconfigured."""
    project = await asyncio.to_thread(get_project_manager().load_project, project_name)
    try:
        await _require_audio_provider_configured(project)
    except BadRequestError:
        return {"configured": False, "provider_id": None, "model": None, "voices": []}

    ctx = await resolve_generation_context(
        project_name,
        None,
        project=project,
        audio=AudioLaneRequest(),
    )
    audio = ctx.audio
    # Backend VoiceOption.label for providers with descriptive wording (e.g. DashScope) stores lib/i18n
    # translation keys, not direct wording. Render by request locale; providers lacking descriptions whose labels equal IDs
    # (e.g. OpenAI) fall back to original strings when _t() finds no key, without side effects.
    return {
        "configured": True,
        "provider_id": audio.provider_model.provider_id,
        "model": audio.backend_model,
        "voices": [{"id": v.id, "label": _t(v.label)} for v in audio.voices],
    }


@router.post("/projects/{project_name}/characters/{name}/voice-sample")
async def generate_character_voice_sample(
    project_name: str,
    name: str,
    req: GenerateVoiceSampleRequest,
    user: CurrentUser,
    _t: Translator,
):
    """Queue character TTS preview generation with explicit text/voice, never falling back to global narration settings.

    Artifacts are previews, promoted to character reference_audio only explicitly through confirm. This
    endpoint only enqueues, using the existing audio lane with identical concurrency/rate/accounting to narration."""
    text = req.text.strip()
    voice = req.voice.strip()
    if not text:
        raise BadRequestError("prompt_text_empty")
    if len(text) > VOICE_SAMPLE_TEXT_MAX_LENGTH:
        raise BadRequestError("voice_sample_text_too_long", max_length=VOICE_SAMPLE_TEXT_MAX_LENGTH)
    if not voice:
        raise BadRequestError("voice_sample_voice_required")

    def _sync() -> tuple[dict, str]:
        pm_local = get_project_manager()
        project = pm_local.load_project(project_name)
        try:
            char_name = validate_asset_name(name)
        except ValueError as exc:
            raise BadRequestError("asset_invalid_name", name=name) from exc
        # Existing character keys may use NFD; resolve existence in the coordinate system.
        if resolve_asset_key(project.get("characters"), char_name) is None:
            raise NotFoundError("character_not_found", name=char_name)
        return project, char_name

    project, char_name = await asyncio.to_thread(_sync)
    provider_id = await _require_audio_provider_configured(project)

    spec = TaskSpec.from_request(
        task_type="voice_sample",
        media_type="audio",
        resource_id=char_name,
        prompt=text,
        extra_payload={"voice": voice},
        source="webui",
    )
    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        source="webui",
        user_id=user.id,
        provider_id=provider_id,
    )
    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("voice_sample_task_submitted", name=char_name),
    }


@router.post("/projects/{project_name}/characters/{name}/voice-sample/confirm")
async def confirm_character_voice_sample(
    project_name: str,
    name: str,
    req: ConfirmVoiceSampleRequest,
    _t: Translator,
):
    """Promote a generated, auditioned TTS sample to character reference_audio; without confirmation, do not persist it to assets.

    Trust only voice_sample results identified by task_id, never client-supplied paths. Execution already
    validated format/duration/size under upload rules (see ``execute_character_voice_sample_task``).
    Check only successful-task ownership by this character, then persist unchanged without repeating validation."""
    try:
        char_name = validate_asset_name(name)
    except ValueError as exc:
        raise BadRequestError("asset_invalid_name", name=name) from exc
    queue = get_generation_queue()
    task = await queue.get_task(req.task_id)
    if (
        task is None
        or task.get("project_name") != project_name
        or task.get("task_type") != "voice_sample"
        or task.get("resource_id") != char_name
    ):
        raise NotFoundError("task_not_found", id=req.task_id)
    if task.get("status") != "succeeded":
        raise BadRequestError("voice_sample_not_ready")

    result = task.get("result") or {}
    sample_rel = result.get("file_path")
    if not isinstance(sample_rel, str) or not sample_rel:
        raise BadRequestError("voice_sample_not_ready")

    def _sync() -> dict:
        pm_local = get_project_manager()
        project_dir = pm_local.get_project_path(project_name)
        if not safe_exists(project_dir, sample_rel):
            raise NotFoundError("voice_sample_file_missing")
        sample_abs = safe_join(project_dir, sample_rel)
        content = sample_abs.read_bytes()

        filename = f"{char_name}.wav"
        ref_audio_rel = f"characters/refs_audio/{filename}"
        target_path = project_dir / ref_audio_rel
        try:
            with project_change_source("webui"):
                pm_local.install_character_reference_audio(project_name, char_name, ref_audio_rel, content)
        except KeyError as exc:
            raise NotFoundError("character_not_found", name=char_name) from exc

        # Fixed target name {char_name}.wav means reference_audio stays unchanged after regeneration/reconfirmation
        # (same path string). project.json field diffs cannot detect changes or automatically broadcast refreshes,
        # although persisted bytes changed. Emit character:updated explicitly with new mtime fingerprints
        # so other open clients cache-bust the audio without waiting for an unrelated
        # project refresh to synchronize by chance.
        try:
            emit_project_change_batch(
                project_name,
                [
                    {
                        "entity_type": "character",
                        "action": "updated",
                        "entity_id": char_name,
                        **build_change_label("character_reference_audio", id=char_name),
                        "focus": None,
                        "important": False,
                        "asset_fingerprints": {ref_audio_rel: target_path.stat().st_mtime_ns},
                    }
                ],
                source="webui",
            )
        except Exception:
            logger.exception(
                "发送试听样本确认项目事件失败 project=%s character=%s",
                project_name,
                char_name,
            )

        return {"path": ref_audio_rel}

    saved = await run_sync_transaction(_sync)
    return {
        "success": True,
        "path": saved["path"],
        "url": f"/api/v1/files/{project_name}/{saved['path']}",
    }


# ==================== Asset-sheet generation (shared by character / scene / prop / product) ====================


# i18n naming exception: scenes retain historical "project_scene_*" prefixes.
_ASSET_GENERATE_I18N: dict[str, dict[str, str]] = {
    "character": {"not_found": "character_not_found", "submitted": "character_task_submitted"},
    "scene": {"not_found": "project_scene_not_found", "submitted": "scene_task_submitted"},
    "prop": {"not_found": "prop_not_found", "submitted": "prop_task_submitted"},
    "product": {"not_found": "product_not_found", "submitted": "product_task_submitted"},
}


async def _enqueue_asset_generation(
    *,
    asset_type: str,
    project_name: str,
    resource_name: str,
    user_id: str,
    _t: Translator,
) -> dict:
    """Shared enqueue logic for project-level asset sheets (character / scene / prop / product).

    No request-body prompt: descriptions come only from stored project entries, reread from current state at execution."""
    spec = ASSET_SPECS[asset_type]
    keys = _ASSET_GENERATE_I18N[asset_type]

    def _sync() -> str:
        project = get_project_manager().load_project(project_name)
        # Existing keys may use NFD; resolve existence and actual persisted keys in the coordinate system;
        # resolve_asset_key treats malformed nondict / explicit-null buckets as empty.
        resolved = resolve_asset_key(project.get(spec.bucket_key), resource_name)
        if resolved is None:
            raise NotFoundError(keys["not_found"], name=resource_name)
        entry = project[spec.bucket_key][resolved]
        description = entry.get("description") if isinstance(entry, dict) else None
        if not isinstance(description, str) or not description.strip():
            raise BadRequestError("asset_description_required", name=resolved)
        return resolved

    resource_key = await asyncio.to_thread(_sync)

    task_spec = TaskSpec.from_request(
        task_type=asset_type,
        media_type="image",
        resource_id=resource_key,
    )

    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=task_spec.task_type,
        media_type=task_spec.media_type,
        resource_id=task_spec.resource_id,
        payload=task_spec.payload,
        source="webui",
        user_id=user_id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t(keys["submitted"], name=resource_name),
    }


@router.post("/projects/{project_name}/generate/character/{char_name}")
async def generate_character(
    project_name: str,
    char_name: str,
    user: CurrentUser,
    _t: Translator,
):
    """Queue character asset-sheet generation and immediately return task_id."""
    return await _enqueue_asset_generation(
        asset_type="character",
        project_name=project_name,
        resource_name=char_name,
        user_id=user.id,
        _t=_t,
    )


@router.post("/projects/{project_name}/generate/character/{char_name}/derivatives/{derivative_name}")
async def generate_character_derivative(
    project_name: str,
    char_name: str,
    derivative_name: str,
    user: CurrentUser,
    _t: Translator,
):
    """Queue character derivative asset-sheet generation and immediately return task_id.

    Derivatives edit base asset sheets using their own appearance-change descriptions plus fixed guards.
    Thus request bodies contain no prompt; enqueue/execution read current project state, avoiding separate
    instruction copies. Reject missing base sheets or unwritten derivative change descriptions here, without creating tasks."""

    def _sync() -> tuple[dict, DerivativeSheetSource]:
        project = get_project_manager().load_project(project_name)
        return project, resolve_derivative_sheet_source(project, char_name, derivative_name)

    try:
        project, source = await asyncio.to_thread(_sync)
    except KeyError as exc:
        raise NotFoundError("character_not_found", name=char_name) from exc

    provider_id = await _require_i2i_image_provider_configured(project)

    spec = TaskSpec.from_request(
        task_type=DERIVATIVE_TASK_TYPE,
        media_type="image",
        resource_id=source.target.artifact_id,
        prompt=build_derivative_sheet_instruction(source.description),
    )
    result = await get_generation_queue().enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        source="webui",
        user_id=user.id,
        provider_id=provider_id,
    )
    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("derivative_task_submitted", name=source.target.derivative_key),
    }


@router.post("/projects/{project_name}/generate/scene/{scene_name}")
async def generate_scene(
    project_name: str,
    scene_name: str,
    user: CurrentUser,
    _t: Translator,
):
    """Queue scene asset-sheet generation and immediately return task_id."""
    return await _enqueue_asset_generation(
        asset_type="scene",
        project_name=project_name,
        resource_name=scene_name,
        user_id=user.id,
        _t=_t,
    )


@router.post("/projects/{project_name}/generate/prop/{prop_name}")
async def generate_prop(
    project_name: str,
    prop_name: str,
    user: CurrentUser,
    _t: Translator,
):
    """Queue prop asset-sheet generation and immediately return task_id."""
    return await _enqueue_asset_generation(
        asset_type="prop",
        project_name=project_name,
        resource_name=prop_name,
        user_id=user.id,
        _t=_t,
    )


@router.post("/projects/{project_name}/generate/product/{product_name}")
async def generate_product(
    project_name: str,
    product_name: str,
    user: CurrentUser,
    _t: Translator,
):
    """Queue product standard-reference-image (product sheet) generation and immediately return task_id."""
    return await _enqueue_asset_generation(
        asset_type="product",
        project_name=project_name,
        resource_name=product_name,
        user_id=user.id,
        _t=_t,
    )


# ==================== Instruction-based image editing (image edit) ====================


async def _require_i2i_image_provider_configured(project: dict) -> str:
    """Return 400 without creating tasks if the project i2i slot cannot resolve an available provider.

    Image editing is necessarily i2i, known at enqueue (the sole exception; see ``docs/adr/0001`` and
    GLOSSARY.md “Image editing”), so resolve before enqueue. Execution ``generate_image_async``
    capability gating remains as fallback. Return provider_id for enqueue reuse, routing rate-limit pools
    and accounting precisely by i2i slot."""
    from lib.db import async_session_factory

    try:
        resolved = await ConfigResolver(async_session_factory).resolve_image_backend(
            project, None, generation_type="i2i"
        )
    except ModelNotInCatalogError:
        # Model absent from provider lists: app-level handlers return model-reselection 400 (ValueError subclass, caught before ValueError).
        raise
    except ValueError as exc:
        raise BadRequestError("image_edit_i2i_unavailable") from exc
    return resolved.provider_id


@router.post("/projects/{project_name}/edit/image")
async def edit_image(
    project_name: str,
    req: EditImageRequest,
    user: CurrentUser,
    _t: Translator,
):
    """Queue instruction-based image editing and immediately return task_id.

    Use i2i with the current image as sole reference and editing instructions as sole prompt. New images
    replace current, old images enter version history automatically; do not write back original image_prompt
    (semantics in ``docs/adr/0050``)."""
    if req.resource_type not in EDITABLE_RESOURCE_TYPES:
        raise BadRequestError("image_edit_resource_type_invalid", resource_type=req.resource_type)
    instruction = req.instruction.strip()
    if not instruction:
        raise BadRequestError("image_edit_instruction_required")
    is_storyboard = req.resource_type == "storyboard"
    script_file = req.script_file.strip() if req.script_file else None
    if is_storyboard and not script_file:
        raise BadRequestError("image_edit_script_file_required")

    def _sync() -> dict:
        pm_local = get_project_manager()
        project = pm_local.load_project(project_name)
        project_path = pm_local.get_project_path(project_name)
        script = pm_local.load_script(project_name, str(script_file)) if is_storyboard else None
        artifact_episode = None
        if script is not None:
            artifact_episode = _resolve_request_artifact_episode(project, script, str(script_file))
        try:
            source = resolve_usable_image_edit_source(
                project=project,
                project_path=project_path,
                resource_type=req.resource_type,
                resource_id=req.resource_id,
                script=script,
                artifact_episode=artifact_episode,
                resolver=active_artifact_currency_resolver(project_path, project),
            )
        except KeyError as exc:
            if is_storyboard:
                raise NotFoundError("segment_not_found", id=req.resource_id) from exc
            if req.resource_type == DERIVATIVE_TASK_TYPE:
                raise NotFoundError("asset_derivative_not_found", name=req.resource_id) from exc
            raise NotFoundError(_ASSET_GENERATE_I18N[req.resource_type]["not_found"], name=req.resource_id) from exc
        if source is None:
            raise BadRequestError("image_edit_no_current_image", id=req.resource_id)
        return project

    project = await run_sync_transaction(_sync)

    provider_id = await _require_i2i_image_provider_configured(project)

    # Structural validation/construction share one guard with SDK enqueue, without divergent rules.
    spec = TaskSpec.from_request(
        task_type="image_edit",
        media_type="image",
        resource_id=req.resource_id,
        prompt=instruction,
        script_file=script_file if is_storyboard else None,
        extra_payload={"resource_type": req.resource_type},
    )

    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        script_file=spec.script_file,
        # image_edit shares one task_type across four asset types + storyboard, whose resource_id namespaces
        # overlap (characters and props can share names); include resource type in dedup keys to prevent cross-type false duplicates.
        resource_type=req.resource_type,
        payload=spec.payload,
        source="webui",
        user_id=user.id,
        provider_id=provider_id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("image_edit_task_submitted", id=req.resource_id),
    }
