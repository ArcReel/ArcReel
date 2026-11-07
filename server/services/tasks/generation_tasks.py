"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    assert_current_artifact_input_claims_usable,
    bind_artifact_input_claims_to_content_digests,
    bind_artifact_input_claims_to_frozen_visuals,
    resolve_usable_episode_script_input,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifacts.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    compose_video_artifact_basis,
)
from lib.artifacts.generation_input import (
    AssetSheetInput,
    FrozenGenerationInput,
    StoryboardImageInput,
    asset_sheet_input,
    grid_references,
    project_input_observation,
    storyboard_image_input,
)
from lib.artifacts.image_reference_snapshot import FrozenImageReferences, freeze_image_references
from lib.artifacts.version_manager import PaidVersionCommit
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.video_visual_provenance import build_storyboard_video_visual_basis
from lib.artifacts.visual_artifact_provenance import (
    GridStoryboardVisual,
    build_grid_composite_visual_basis,
    build_storyboard_video_artifact_visual_basis,
    project_basis_records_asset_names,
    project_basis_style_description,
)
from lib.config.resolver import video_bucket_for_generation_mode
from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
from lib.db.base import DEFAULT_USER_ID
from lib.generation.deleted_target import GenerationTargetDeletedError
from lib.generation.generation_queue import (
    DispatchProviderChanged,
    ensure_dispatch_provider,
    get_generation_queue,
    without_video_execution_identity,
)
from lib.generation.render_lane import is_render_task_type
from lib.generation.video_request_facts import (
    DEFAULT_PLANNED_DURATION_SECONDS,
    VideoRequestFactsError,
    audio_switch_conflict,
    require_video_request_facts,
)
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.path_safety import safe_join, try_safe_join
from lib.infra.thumbnail import extract_video_thumbnail
from lib.material.formal_objects import formal_commit
from lib.material.work_files import local_work_files
from lib.project.asset_derivatives import DERIVATIVE_ASSET_TYPE, DERIVATIVE_TASK_TYPE
from lib.project.asset_types import (
    ASSET_SPECS,
    resolve_asset_key,
    validate_asset_name,
)
from lib.project.project_change_hints import build_change_label, emit_project_change_batch, project_change_source
from lib.project.project_manager import (
    EpisodeScriptReboundError,
    ProjectManager,
    get_project_manager,
    is_reference_video_project,
    resolve_episode_script_binding,
)
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_relative_path
from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.prompt_utils import render_storyboard_video_prompt
from lib.prompts.reference_image_numbering import clamp_reference_images
from lib.script.reference_video.execution_checkpoint import (
    ProviderMediaInput,
    StagedProviderMedia,
    StoryboardSubmissionCheckpoint,
    checkpoint_version_metadata,
    cleanup_staged_provider_media,
    stage_provider_media_for_task,
)
from lib.script.script_editor import resolve_items
from lib.script.script_models import resolve_content_mode
from lib.script.script_skeleton import SKELETON_ENTITY_TYPES, SKELETON_ITEM_LABEL_KEYS, resolve_script_kind
from lib.script.storyboard_sequence import (
    find_storyboard_item,
    get_storyboard_items,
)
from lib.speech.audio_utils import (
    AUDIO_REFERENCE_MAX_BYTES,
    AUDIO_REFERENCE_MAX_SECONDS,
    AUDIO_REFERENCE_MIN_SECONDS,
    probe_audio_duration_seconds,
    probe_existing_audio_duration_seconds,
)
from lib.speech.narration_config import (
    NarrationConfigError,
    project_tts_settings,
    require_project_tts_generation,
)
from lib.speech.narration_delivery import (
    build_narration_audio_basis,
    canonical_narration_text,
    register_narration_audio_transactionally,
)
from lib.speech.speech_artifact_provenance import build_video_duration_basis
from lib.speech.speech_composition import SpeechAdmissionError, admit_script_unit
from server.services.admission.reference_admission import input_refusal_error
from server.services.currency.video_artifact_currency import (
    VideoArtifactCommitter,
    complete_video_artifact_commit,
    freeze_video_speech_facts,
)
from server.services.tasks.derivative_sheet_tasks import execute_character_derivative_task
from server.services.tasks.formal_image_commit import (
    FormalImageCommitOutcome,
    FormalImagePlan,
    StagedImageCommit,
    frozen_generation_input,
    get_aspect_ratio,
    grid_formal_image_callback,
    run_asset_sheet_image_task,
    run_formal_image_task,
    storyboard_formal_image_callback,
)
from server.services.tasks.generation_context import (
    AudioLaneRequest,
    ImageLaneRequest,
    VideoLaneRequest,
    resolve_generation_context,
)
from server.services.tasks.image_edit_tasks import execute_image_edit_task
from server.services.tasks.reference_video_tasks import execute_reference_video_task
from server.services.tasks.render_tasks import execute_render_task

logger = logging.getLogger(__name__)


def assert_duration_supported(duration: int | float | str, supported_durations: list[int]) -> None:
    """Execution capability guard: duration must belong to the resolved model's supported_durations.

    This is the sole authoritative `duration ↔ supported_durations` validation point. Providers resolve at
    execution (ADR-0001), so capability validation must follow provider resolution. Empty
    ``supported_durations`` occurs only for endpoint-fixed duration (``docs/adr/0082``), which is allowed:
    unresolved capabilities already block when obtaining facts.

    Duration may come from external configuration (payload / project.json), so parse strings/floats safely:
    normalize and compare integer seconds such as ``"6"`` / ``6.0``. Always **reject** noninteger seconds
    such as ``4.5`` rather than truncating (which silently corrects values that should be refused).

    Raise :class:`VideoCapabilityError` with a stable code on failure, symmetrical with ImageCapabilityError.
    Workers persist code + params in task.error_message; read-side Translator renders wording."""
    if not supported_durations:
        return
    try:
        numeric = float(duration)
    except (TypeError, ValueError) as exc:
        raise VideoCapabilityError("video_duration_invalid", duration=duration) from exc
    if not numeric.is_integer():
        raise VideoCapabilityError("video_duration_invalid", duration=duration)
    seconds = int(numeric)
    if seconds not in supported_durations:
        raise VideoCapabilityError(
            "video_duration_not_supported",
            duration=seconds,
            supported=", ".join(str(d) for d in supported_durations),
        )


def _episode_from_script(script: dict[str, Any] | None) -> int | None:
    if not isinstance(script, dict):
        return None
    episode = script.get("episode")
    if isinstance(episode, int):
        return episode
    return None


def compute_affected_fingerprints(project_name: str, task_type: str, resource_id: str) -> dict[str, int]:
    """Compute mtime fingerprints for affected files."""
    try:
        project_path = get_project_manager().get_project_path(project_name)
    except Exception:
        return {}

    paths: list[tuple[str, Path]] = []

    if task_type == "storyboard":
        paths.append(
            (
                f"storyboards/scene_{resource_id}.png",
                project_path / "storyboards" / f"scene_{resource_id}.png",
            )
        )
    elif task_type == "video":
        paths.append(
            (
                f"videos/scene_{resource_id}.mp4",
                project_path / "videos" / f"scene_{resource_id}.mp4",
            )
        )
        paths.append(
            (
                f"thumbnails/scene_{resource_id}.jpg",
                project_path / "thumbnails" / f"scene_{resource_id}.jpg",
            )
        )
    elif task_type == "character":
        paths.append(
            (
                f"characters/{resource_id}.png",
                project_path / "characters" / f"{resource_id}.png",
            )
        )
    elif task_type == "scene":
        paths.append(
            (
                f"scenes/{resource_id}.png",
                project_path / "scenes" / f"{resource_id}.png",
            )
        )
    elif task_type == "prop":
        paths.append(
            (
                f"props/{resource_id}.png",
                project_path / "props" / f"{resource_id}.png",
            )
        )
    elif task_type == "product":
        paths.append(
            (
                f"products/{resource_id}.png",
                project_path / "products" / f"{resource_id}.png",
            )
        )
    elif task_type in ("grid", "grid_split"):
        paths.append(
            (
                f"grids/{resource_id}.png",
                project_path / "grids" / f"{resource_id}.png",
            )
        )
        # Grid splitting overwrites multiple canonical storyboard images; actual written cell paths persist in
        # grid frame_chain records. Include their fingerprints for frontend cache-busting;
        # missing/corrupt records fall back to the main grid image only. Generation events ("grid") also include cell fingerprints:
        # generation no longer touches storyboard cells, so unchanged mtimes match frontend values without side effects.
        try:
            from lib.script.grid.grid_manager import GridManager

            grid = GridManager(project_path).get(resource_id)
        except Exception:
            grid = None
        if grid is not None:
            # Records are on-disk JSON; do not trust image_path: absolute paths override the left operand,
            # and ../ escapes the project, exposing arbitrary server-file existence/mtime to the frontend.
            project_root = project_path.resolve()
            for frame in grid.frame_chain:
                if not frame.image_path:
                    continue
                candidate = try_safe_join(project_root, frame.image_path)
                if candidate is None:
                    logger.warning("跳过越出项目目录的宫格 cell 路径: %s", frame.image_path)
                    continue
                # Fingerprint keys use normalized project-relative paths: raw project-internal absolute paths
                # leak server paths to the frontend and fail to match frontend resource keys.
                rel = candidate.relative_to(project_root).as_posix()
                paths.append((rel, candidate))
    elif task_type == "reference_video":
        paths.append(
            (
                f"reference_videos/{resource_id}.mp4",
                project_path / "reference_videos" / f"{resource_id}.mp4",
            )
        )
        paths.append(
            (
                f"reference_videos/thumbnails/{resource_id}.jpg",
                project_path / "reference_videos" / "thumbnails" / f"{resource_id}.jpg",
            )
        )
    elif task_type == "tts":
        audio_rel = resource_relative_path("audio", resource_id)
        paths.append((audio_rel, project_path / audio_rel))
    elif task_type == DERIVATIVE_TASK_TYPE:
        # Derivative resource_id is `本体名/衍生名`; preserve its two segments as path levels.
        sheet_rel = resource_relative_path(CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_id)
        paths.append((sheet_rel, project_path / sheet_rel))

    result: dict[str, int] = {}
    for rel, abs_path in paths:
        if abs_path.exists():
            result[rel] = abs_path.stat().st_mtime_ns

    return result


# (entity_type, action, label_key, include_script_episode)
# label_key is a stable event-payload identifier; the UI renders it in the user language using lib/i18n
# ``event_label_*``. Specs for three project-level assets (character / scene / prop) derive from
# lib.project.asset_types.ASSET_SPECS.
# storyboard / video / reference_video are absent here: derive entity_type and item nouns dynamically by script skeleton (segments/scenes/shots/
# video_units), via _SKELETON_DRIVEN_TASK_ACTIONS, avoiding fixed
# ``segment``/storyboard nouns inconsistent with storyboard-level events (project_state_projection.py).
_TASK_CHANGE_SPECS: dict[str, tuple] = {
    "grid": ("grid", "grid_ready", "grid", True),
    "grid_split": ("grid", "grid_split_done", "grid_split", True),
    "voice_sample": ("character", "voice_sample_ready", "voice_sample", False),
    **{atype: (atype, "updated", f"asset_image_{atype}", False) for atype in ASSET_SPECS},
    # Derivative asset images live under the base entity (entity_id is `本体名/衍生名`), with a separate item noun:
    # reusing asset_image_character would read the compound ID as a character name.
    DERIVATIVE_TASK_TYPE: (DERIVATIVE_ASSET_TYPE, "updated", "asset_image_character_derivative", False),
}

# Skeleton-driven task type → completion action. Resolve entity_type/item nouns dynamically by current project script skeleton
# (resolve_script_kind, same criterion as storyboard-level events), without constant task_type-based hardcoding.
_SKELETON_DRIVEN_TASK_ACTIONS: dict[str, str] = {
    "storyboard": "storyboard_ready",
    "video": "video_ready",
    "reference_video": "reference_video_ready",
    "tts": "tts_ready",
}

# Task types with their own item labels: tts produces narration audio, distinct from skeleton nouns. reference_video explicitly
# denotes video units, matching reference-project skeleton terminology. storyboard/video fall back to skeleton-derived
# label_key (storyboard), consistent with storyboard-level events in the same project.
_SKELETON_TASK_LABEL_KEYS: dict[str, str] = {
    "reference_video": "skeleton_video_units",
    "tts": "narration_audio",
}


def _load_event_script(project_name: str, script_file: str | None) -> dict[str, Any] | None:
    """Load the completion event's script once, sharing it for skeleton kind and episode; return None if missing/corrupted.

    Callers apply their own None fallbacks (skeleton kind ``"segments"``, episode ``None``), preventing
    script-loading failures from interrupting notifications."""
    if not script_file:
        return None
    try:
        return get_project_manager().load_script(project_name, script_file)
    except Exception:
        return None


def emit_generation_success_batch(
    *,
    task_type: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
) -> dict[str, int]:
    """Emit project changes for generation/upload completion; return affected-file fingerprints for callers to reuse without recalculation.

    The project_change_source contextvar determines event source (worker / webui callers wrap their own context)."""
    if task_type == "image_edit":
        # Edit completion events share the generation-completion shape for the same resource. Dispatch payload.resource_type to
        # existing spec tables (skeleton-driven storyboard, ASSET_SPECS-derived four asset types), preserving entity/action/
        # fingerprints so existing frontend SSE fingerprint refresh covers edits without changes.
        task_type = str(payload.get("resource_type") or "")

    script_file = str(payload.get("script_file") or "") or None
    # Load the script once for both skeleton kind and episode, avoiding duplicate parsing of one script_file.
    script = _load_event_script(project_name, script_file)

    action = _SKELETON_DRIVEN_TASK_ACTIONS.get(task_type)
    if action is not None:
        reference_route_task = task_type == "reference_video"
        if task_type == "tts":
            try:
                reference_route_task = is_reference_video_project(get_project_manager().load_project(project_name))
            except Exception:
                reference_route_task = False
        if reference_route_task:
            # Reference-to-video resource identity is always video unit; generation mode comes from immutable-after-creation project.json,
            # preventing residual ad shots[] from misclassifying E1U* events as shots after TTS success.
            kind = "video_units"
        else:
            kind = resolve_script_kind(script) if isinstance(script, dict) else "segments"
        entity_type = SKELETON_ENTITY_TYPES.get(kind, "segment")
        label_key = _SKELETON_TASK_LABEL_KEYS.get(task_type) or SKELETON_ITEM_LABEL_KEYS.get(kind, "skeleton_segments")
        include_script_episode = True
    else:
        spec = _TASK_CHANGE_SPECS.get(task_type)
        if spec is None:
            return {}
        entity_type, action, label_key, include_script_episode = spec

    asset_fingerprints = compute_affected_fingerprints(project_name, task_type, resource_id)

    change: dict[str, Any] = {
        "entity_type": entity_type,
        "action": action,
        "entity_id": resource_id,
        **build_change_label(label_key, id=resource_id),
        "focus": None,
        "important": True,
        "asset_fingerprints": asset_fingerprints,
    }
    if include_script_episode:
        change["script_file"] = script_file
        change["episode"] = _episode_from_script(script)

    try:
        emit_project_change_batch(project_name, [change])
    except Exception:
        logger.exception(
            "发送生成完成项目事件失败 project=%s task_type=%s resource_id=%s",
            project_name,
            task_type,
            resource_id,
        )
    return asset_fingerprints


@dataclass(frozen=True, slots=True)
class _StoryboardImageInputs:
    """Storyboard-image inputs available before image-lane resolution: project snapshot, script claim, and valid generation inputs."""

    project: dict[str, Any]
    project_path: Path
    currency_resolver: ArtifactCurrencyResolver
    script_claim: ArtifactInputClaim
    generation_input: StoryboardImageInput


async def execute_storyboard_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for storyboard task")

    if payload.get("prompt") is None:
        raise ValueError("prompt is required for storyboard task")

    def _load() -> _StoryboardImageInputs:
        _project = get_project_manager().load_project(project_name)
        _project_path = get_project_manager().get_project_path(project_name)
        _script = get_project_manager().load_script(project_name, script_file)
        _script_input = resolve_usable_episode_script_input(
            project_path=_project_path,
            project=_project,
            script=_script,
            script_filename=str(script_file),
        )
        _currency_resolver = active_artifact_currency_resolver(_project_path, _project)
        _generation_input = storyboard_image_input(
            _project,
            _script,
            episode=_script_input.episode,
            resource_id=resource_id,
            observation=project_input_observation(_project_path),
        )
        # Invalid generation inputs fail before provider-channel resolution and billing, reporting all gaps together.
        if (_refusal := _generation_input.refusal) is not None:
            raise input_refusal_error(_refusal)
        return _StoryboardImageInputs(
            project=_project,
            project_path=_project_path,
            currency_resolver=_currency_resolver,
            script_claim=_script_input.claim,
            generation_input=_generation_input,
        )

    inputs = await run_sync_transaction(_load)
    project, project_path = inputs.project, inputs.project_path
    # Finalize references before prompts: backend caps determine outgoing image counts, so render numbering only after truncation;
    # resolve the image lane once here and reuse through submission, avoiding a different-cap backend on re-resolution.
    context = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        project_path=project_path,
        user_id=user_id,
        image=ImageLaneRequest(generation_type="i2i" if inputs.generation_input.references else "t2i"),
    )
    ensure_dispatch_provider(claimed_provider_id, context.image.provider_model.provider_id)

    def _bind_claims(
        claims: Sequence[ArtifactInputClaim], content_digests: Mapping[str, str]
    ) -> tuple[ArtifactInputClaim, ...]:
        return bind_artifact_input_claims_to_content_digests(
            resolver=inputs.currency_resolver,
            claims=claims,
            content_digests=content_digests,
        )

    def _freeze() -> FrozenGenerationInput:
        return inputs.generation_input.freeze(
            max_reference_images=context.image.max_reference_images,
            model=context.image.backend_model,
            bind_claims=_bind_claims,
        )

    artifact_path = f"storyboards/scene_{resource_id}.png"
    async with frozen_generation_input(
        _freeze,
        project_path=project_path,
        sources=[reference.visual.path for reference in inputs.generation_input.references],
    ) as frozen:
        # Put the script claim first, followed by reference-image claims bound to frozen bytes.
        formal_claims = (inputs.script_claim, *frozen.claims)

        async def _assert_claims_usable() -> None:
            await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

        async def _pre_submit(_generator: Any) -> None:
            await _assert_claims_usable()

        def _build_commit(generator: Any, outcome_box: list[FormalImageCommitOutcome]) -> StagedImageCommit:
            return storyboard_formal_image_callback(
                project_name=project_name,
                script_file=str(script_file),
                resource_id=resource_id,
                artifact_path=artifact_path,
                prompt=frozen.prompt,
                versions=generator.versions,
                task_id=task_id,
                basis=frozen.basis,
                outcome_box=outcome_box,
                project_manager=get_project_manager(),
            )

        return await run_formal_image_task(
            project_name=project_name,
            payload=payload,
            project=project,
            user_id=user_id,
            task_id=task_id,
            frozen_references=frozen.references,
            context=context,
            plan=FormalImagePlan(
                resource_type="storyboards",
                resource_id=resource_id,
                artifact_path=artifact_path,
                prompt=frozen.prompt,
                aspect_ratio=inputs.generation_input.canvas_ratio,
                build_commit_callback=_build_commit,
                pre_submit=_pre_submit,
                before_submit=_assert_claims_usable,
                warnings=frozen.warnings,
            ),
        )


def _resolve_tts_task_items(
    script: dict[str, Any],
    *,
    reference_video_route: bool,
) -> tuple[list[Any], str, str]:
    """Resolve TTS units from the generation route fixed at task start."""

    if not reference_video_route:
        return resolve_items(script)
    # Reference-to-video skeleton kind is fixed by generation mode at task start, so supply it directly; forensic resolution serves callers with unknown mode only.
    return resolve_items(script, kind="video_units")


async def execute_tts_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    """Synthesize independent narration audio for one narrator-owned script unit."""
    script_file = payload.get("script_file")

    def _prepare() -> tuple[dict, Path, str, Any | None, int | None, bool, tuple[ArtifactInputClaim, ...]]:
        pm = get_project_manager()
        current_project = pm.load_project(project_name)
        reference_video_route = is_reference_video_project(current_project)
        project_path = pm.get_project_path(project_name)
        if script_file:
            script = pm.load_script(project_name, script_file)
            script_input = resolve_usable_episode_script_input(
                project_path=project_path,
                project=current_project,
                script=script,
                script_filename=str(script_file),
            )
            episode = script_input.episode
            items, id_field, kind = _resolve_tts_task_items(
                script,
                reference_video_route=reference_video_route,
            )
            item = next(
                (
                    candidate
                    for candidate in items
                    if isinstance(candidate, dict) and str(candidate.get(id_field)) == str(resource_id)
                ),
                None,
            )
            if item is None:
                raise ValueError(f"segment not found: {resource_id}")
            admission = admit_script_unit(kind, item)
            text = canonical_narration_text(admission.preparation)
            if not admission.allowed:
                if not text:
                    raise ValueError(f"segment {resource_id} 无可合成的旁白文本")
                raise SpeechAdmissionError(admission)
            if not text:
                raise ValueError(f"segment {resource_id} 无可合成的旁白文本")
            return (
                current_project,
                project_path,
                text,
                admission.preparation,
                episode,
                reference_video_route,
                (script_input.claim,),
            )

        legacy_text = payload.get("text") or payload.get("prompt")
        if not isinstance(legacy_text, str) or not legacy_text.strip():
            raise ValueError("tts task 需要 payload.text 或 payload.script_file 之一")
        return current_project, project_path, legacy_text.strip(), None, None, reference_video_route, ()

    (
        project,
        project_path,
        text,
        preparation,
        episode,
        reference_video_route,
        formal_input_claims,
    ) = await run_sync_transaction(_prepare)

    # Synthesize narration only from the project TTS snapshot: reject if project switches to postproduction dubbing or the snapshot is incomplete.
    settings = require_project_tts_generation(project)
    voice = settings.voice
    speed = settings.speed
    # The snapshot has full provider/model, so project-level audio-lane resolution selects it. Pass no payload:
    # historical-task providers must not override the project snapshot.
    ctx = await resolve_generation_context(
        project_name,
        None,
        project=project,
        user_id=user_id,
        audio=AudioLaneRequest(),
    )
    ensure_dispatch_provider(claimed_provider_id, ctx.audio.provider_model.provider_id)
    # Custom backends may fall back to defaults; narration snapshots forbid synthesis with another model recorded as the selected one.
    if ctx.audio.backend_model != settings.model_id:
        raise NarrationConfigError("narration_tts_model_invalid")
    generator = ctx.generator
    basis = build_narration_audio_basis(preparation, settings) if preparation is not None else None

    audio_rel = resource_relative_path("audio", resource_id)
    duration_seconds: float | None = None
    # Selection-basis resolution failure occurs in the callback and is consumed outside; pass through a single-element mailbox, not a nonlocal sentinel.
    tts_selection_errors: list[BaseException] = []
    selected_current = True

    class _TtsSelectionResolutionFailed(RuntimeError):
        pass

    async def _measure_staged(staged_path: Path) -> None:
        nonlocal duration_seconds
        try:
            measured_duration = await probe_existing_audio_duration_seconds(staged_path)
        except (Exception, asyncio.CancelledError) as exc:
            tts_selection_errors.append(exc)
            return
        if measured_duration is None or not math.isfinite(measured_duration) or measured_duration <= 0:
            tts_selection_errors.append(RuntimeError("generated narration audio duration is unavailable"))
            return
        duration_seconds = float(measured_duration)

    def _commit_staged(staged_path: Path, output_path: Path) -> int | PaidVersionCommit:
        nonlocal selected_current
        if script_file is None or preparation is None or episode is None or basis is None:
            selected_current = False
            return generator.versions.commit_staged_paid_version(
                resource_type="audio",
                resource_id=resource_id,
                prompt=text,
                staged_file=staged_path,
                current_file=output_path,
                select_current=False,
                tts_provider_id=settings.provider_id,
                tts_model_id=settings.model_id,
                tts_voice=settings.voice,
                tts_speed=settings.speed,
                tts_basis_digest=None,
                tts_actual_duration_seconds=duration_seconds,
            )

        committed_preparation = preparation
        committed_episode = episode
        committed_basis = basis
        pm = get_project_manager()
        committed_outcome: list[PaidVersionCommit] = []
        should_select = False
        guarded_project: list[dict[str, Any]] = []

        version_metadata = {
            "tts_provider_id": settings.provider_id,
            "tts_model_id": settings.model_id,
            "tts_voice": settings.voice,
            "tts_speed": settings.speed,
            "tts_basis_digest": committed_basis.digest,
            "artifact_episode": committed_episode,
            "artifact_audio_basis": ArtifactBasisDescriptor.from_basis(committed_basis).to_dict(),
            "tts_actual_duration_seconds": duration_seconds,
            "execution_script_file": str(script_file),
        }

        def _archive_paid_history() -> PaidVersionCommit:
            return generator.versions.commit_staged_paid_version(
                resource_type="audio",
                resource_id=resource_id,
                prompt=text,
                staged_file=staged_path,
                current_file=output_path,
                select_current=False,
                **version_metadata,
            )

        if tts_selection_errors:
            committed_outcome.append(_archive_paid_history())
            selected_current = False
            return committed_outcome[-1]

        def _register_basis() -> None:
            register_narration_audio_transactionally(
                project_path=project_path,
                episode=committed_episode,
                preparation=committed_preparation,
                settings=settings,
            )

        def _activate(_script_path: Path) -> None:
            committed_outcome.append(
                generator.versions.commit_staged_paid_version(
                    resource_type="audio",
                    resource_id=resource_id,
                    prompt=text,
                    staged_file=staged_path,
                    current_file=output_path,
                    select_current=lambda: should_select,
                    on_select=_register_basis,
                    **version_metadata,
                )
            )

        def _same_script(_project: dict) -> str:
            guarded_project.append(_project)
            current_binding = resolve_episode_script_binding(_project, committed_episode, str(script_file))
            if current_binding is None:
                raise EpisodeScriptReboundError(f"episode {committed_episode} script binding changed before TTS commit")
            return current_binding

        try:
            with pm.locked_episode_script(
                project_name,
                _same_script,
                validate=False,
                on_commit=_activate,
            ) as current_script:
                if not guarded_project:
                    raise RuntimeError("TTS commit guard did not expose the current project")
                current_commit_settings = project_tts_settings(guarded_project[-1])
                if current_commit_settings is None:
                    tts_selection_errors.append(ValueError("project TTS snapshot is no longer complete"))
                    raise _TtsSelectionResolutionFailed
                items, id_field, current_kind = _resolve_tts_task_items(
                    current_script,
                    reference_video_route=reference_video_route,
                )
                item = next(
                    (
                        candidate
                        for candidate in items
                        if isinstance(candidate, dict) and str(candidate.get(id_field)) == str(resource_id)
                    ),
                    None,
                )
                current_basis = None
                if not tts_selection_errors and item is not None:
                    current_admission = admit_script_unit(current_kind, item)
                    try:
                        current_basis = build_narration_audio_basis(
                            current_admission.preparation,
                            current_commit_settings,
                        )
                    except ValueError:
                        current_basis = None
                should_select = current_basis is not None and current_basis.digest == committed_basis.digest
                if should_select:
                    assert item is not None
                    assets = item.get("generated_assets")
                    if not isinstance(assets, dict):
                        assets = ProjectManager.create_generated_assets(
                            str(current_script.get("content_mode") or "narration")
                        )
                        item["generated_assets"] = assets
                    assets["narration_audio"] = audio_rel
                    pm.update_scene_status(item)
        except (EpisodeScriptReboundError, _TtsSelectionResolutionFailed):
            committed_outcome.append(_archive_paid_history())
        except BaseException as failure:
            if staged_path.is_file():
                try:
                    _archive_paid_history()
                except BaseException as archive_failure:
                    failure.add_note(f"paid TTS history archival also failed: {archive_failure}")
            raise

        if not committed_outcome:
            raise RuntimeError("paid TTS commit produced no version record")
        selected_current = committed_outcome[-1].selected
        return committed_outcome[-1]

    async def _before_submit() -> None:
        await asyncio.to_thread(
            assert_current_artifact_input_claims_usable,
            project_path,
            formal_input_claims,
        )

    _output_path, version = await generator.generate_audio_async(
        text=text,
        resource_id=resource_id,
        voice=voice,
        speed=speed,
        task_id=task_id,
        before_submit=_before_submit,
        before_commit=_measure_staged,
        commit_staged=_commit_staged,
        tts_provider_id=settings.provider_id,
        tts_model_id=settings.model_id,
        tts_voice=settings.voice,
        tts_speed=settings.speed,
        tts_basis_digest=basis.digest if basis is not None else None,
    )

    if tts_selection_errors:
        raise tts_selection_errors[-1]

    version_record: dict[str, Any] | None = None
    try:
        # Audio committed: version-record reads must not be interrupted; cancellation must not reclassify paid success as interrupted.
        records = (await run_noninterruptible_sync(generator.versions.get_versions, "audio", resource_id))["versions"]
        version_record = next(
            (record for record in reversed(records) if record.get("version") == version),
            None,
        )
        created_at = next(
            (record.get("created_at") for record in reversed(records) if record.get("version") == version),
            records[-1].get("created_at") if records else None,
        )
    except Exception:
        logger.warning("读取 TTS 版本入库时间失败 resource_id=%s", resource_id, exc_info=True)
        created_at = None

    return {
        "version": version,
        "file_path": (
            audio_rel if selected_current else version_record.get("file") if isinstance(version_record, dict) else None
        ),
        "created_at": created_at,
        "resource_type": "audio",
        "resource_id": resource_id,
        "duration_seconds": duration_seconds,
        "tts_basis_digest": basis.digest if basis is not None else None,
        "selected_current": selected_current,
    }


# validate_asset_name checks character_name characters, not length; task_id is uuid4().hex
# (32 ASCII bytes). Without truncation, long names + prefix/separators/task_id build resource_id,
# then VersionManager.add_version adds "_v{n}_{timestamp}{ext}";
# this can fail persistence/version creation on 255-byte NAME_MAX filesystems. Truncate character names with sufficient headroom.
# resource_id needs no human recovery of character names from filenames (internal concatenation only, no parser), so truncation preserves correctness;
# task_id alone ensures uniqueness.
_SAMPLE_ID_NAME_MAX_BYTES = 80


def _truncate_name_bytes(name: str, max_bytes: int) -> str:
    """Truncate by UTF-8 byte count, dropping partial multibyte characters at the boundary instead of producing invalid encoding."""
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def voice_sample_resource_id(character_name: str, task_id: str) -> str:
    """Resource ID for a character TTS preview sample in ``audio/`` (separate from narration segment-ID namespace).

    Generated artifacts are previews awaiting confirmation. They share narration's ``audio/`` directory
    but use a fixed prefix, avoiding narration/commentary segment-ID collisions. Only confirm promotes
    the audio into character reference_audio.

    Include ``task_id``, not only character name: regeneration while an earlier successful sample awaits
    confirmation creates a new task. Character-only IDs would overwrite the earlier task's referenced
    file. Its ``result.file_path`` stays unchanged while physical contents become the new task's bytes
    (even bytes written before validation fails). If another holder of the earlier task_id (e.g. another
    browser tab) confirms, wrong content becomes character reference audio. Task-specific filenames
    for every generation prevent cross-task overwrites."""
    safe_name = _truncate_name_bytes(character_name, _SAMPLE_ID_NAME_MAX_BYTES)
    return f"voice_sample__{safe_name}__{task_id}"


async def execute_character_voice_sample_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    """Generate a TTS audition sample for candidate character reference audio (preview only, without updating character assets).

    ``resource_id`` is the character name; text and voice come explicitly from payload (``prompt`` = text
    to synthesize, ``voice`` = user-selected voice ID), never falling back to global narration settings.
    Validate generated artifacts like reference-audio uploads (wav fixed by persisted extension, 2-10
    seconds, ≤15MB). Validation failures raise and mark the task failed; never silently accept noncompliant samples."""
    character_name = validate_asset_name(resource_id)
    text = payload.get("prompt")
    voice = payload.get("voice")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("voice sample 任务需要非空 payload.prompt（待合成文本）")
    if not isinstance(voice, str) or not voice.strip():
        raise ValueError("voice sample 任务需要 payload.voice（音色 id）")
    if not task_id:
        # Workers always pass the queued task's own ID through execute_generation_task. Absence means bypassing
        # normal queue execution (e.g. erroneous direct calls); sample_id cross-task isolation depends on it, so fail fast.
        raise ValueError("voice sample 任务需要 task_id")

    project = await asyncio.to_thread(get_project_manager().load_project, project_name)
    if resolve_asset_key(project.get("characters"), character_name) is None:
        # As in execute_character_task and other executors, characters may be deleted between enqueue and worker pickup;
        # recheck existence before execution to avoid paying for ownerless orphan previews.
        raise ValueError(f"character not found: {character_name}")
    ctx = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        user_id=user_id,
        audio=AudioLaneRequest(),
    )
    ensure_dispatch_provider(claimed_provider_id, ctx.audio.provider_model.provider_id)
    generator = ctx.generator

    sample_id = voice_sample_resource_id(character_name, task_id)
    _, version = await generator.generate_audio_async(
        text=text.strip(),
        resource_id=sample_id,
        voice=voice.strip(),
        speed=None,
        task_id=task_id,
        # Preview samples belong to characters: deleting a character moves its samples to trash.
        target_resource=("characters", character_name),
    )

    audio_rel = resource_relative_path("audio", sample_id)
    audio_abs = get_project_manager().get_project_path(project_name) / audio_rel

    def _read_bytes() -> bytes:
        return audio_abs.read_bytes()

    content = await asyncio.to_thread(_read_bytes)
    if len(content) > AUDIO_REFERENCE_MAX_BYTES:
        raise ValueError(f"生成的语音样本超过 {AUDIO_REFERENCE_MAX_BYTES // (1024 * 1024)}MB 限制")

    duration = await probe_audio_duration_seconds(content, audio_abs.suffix)
    if duration is not None and not (AUDIO_REFERENCE_MIN_SECONDS <= duration <= AUDIO_REFERENCE_MAX_SECONDS):
        raise ValueError(
            f"生成的语音样本时长 {duration:.1f}s 超出 "
            f"{AUDIO_REFERENCE_MIN_SECONDS:.0f}-{AUDIO_REFERENCE_MAX_SECONDS:.0f} 秒范围"
        )

    return {
        "version": version,
        "file_path": audio_rel,
        "resource_type": "audio",
        "resource_id": sample_id,
        "character_name": character_name,
        "voice": voice.strip(),
        "duration_seconds": duration,
    }


async def execute_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    script_file: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
    poll_timeout_seconds: int = DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload_script_file = payload.get("script_file")
    if script_file is None and isinstance(payload_script_file, str):
        script_file = payload_script_file
    if not isinstance(script_file, str) or not script_file:
        raise ValueError("script_file is required for video task")

    def _load():
        _pm = get_project_manager()
        _project = _pm.load_project(project_name)
        _project_path = _pm.get_project_path(project_name)
        _script = _pm.load_script(project_name, script_file)
        _script_input = resolve_usable_episode_script_input(
            project_path=_project_path,
            project=_project,
            script=_script,
            script_filename=script_file,
        )
        _items, _id_field, _, _, _ = get_storyboard_items(_script)
        _resolved = find_storyboard_item(_items, _id_field, resource_id)
        _item = _resolved[0] if _resolved else {}
        return (
            _project,
            _project_path,
            _item,
            resolve_content_mode(_script, _project),
            resolve_script_kind(_script),
            _script,
            _script_input,
        )

    project, project_path, item, content_mode, script_kind, _script, script_input = await run_sync_transaction(_load)
    # Queue execution re-materializes mutable visual intent from the current script unit. Direct/internal callers
    # without a task row retain the request-prompt fallback for compatibility with synchronous service tests.
    current_prompt = item.get("video_prompt") if isinstance(item, dict) else None
    prompt = current_prompt if task_id is not None else payload.get("prompt", current_prompt)
    if prompt is None:
        raise ValueError("current script unit is missing video_prompt")
    requested_visual_prompt = copy.deepcopy(prompt)
    # Evaluate lane buckets by project generation mode, sharing submission (``generate_video``): after reference-to-video is blocked there,
    # projects reaching here are always i2v. Avoid separate hardcoded buckets that could diverge in mode interpretation.
    execution_payload = without_video_execution_identity(payload) if task_id is not None else payload
    ctx = await resolve_generation_context(
        project_name,
        execution_payload,
        project=project,
        user_id=user_id,
        video=VideoLaneRequest(
            generation_type=video_bucket_for_generation_mode(project.get("generation_mode")),
            route="storyboard",
        ),
    )
    generator = ctx.generator
    registry_provider_id = ctx.video.provider_model.provider_id
    ensure_dispatch_provider(claimed_provider_id, registry_provider_id)
    if ctx.video.request_facts is None:
        raise RuntimeError("storyboard video lane is missing its request facts")
    request_facts = require_video_request_facts(ctx.video.request_facts)
    if (conflict := audio_switch_conflict(request_facts)) is not None:
        raise VideoRequestFactsError(conflict)
    requested_generate_audio = request_facts.requested_generate_audio
    model_name = ctx.video.backend_model
    supported_durations = list(request_facts.allowed_durations)
    resolution = request_facts.resolution

    artifact_episode = script_input.episode
    formal_input_claims: list[ArtifactInputClaim] = [script_input.claim]

    def _resolve_usable_inputs() -> tuple[ArtifactCurrencyResolver, Path, Path | None]:
        # First/end-frame availability reads artifact manifests and hashes storyboard images; run together in a worker thread.
        resolver = active_artifact_currency_resolver(project_path, project)
        start, end = resolve_usable_storyboard_video_inputs(
            project_path=project_path,
            project=project,
            episode=artifact_episode,
            resource_id=resource_id,
            item=item,
            resolver=resolver,
            claims=formal_input_claims,
        )
        return resolver, start, end

    currency_resolver, storyboard_file, end_image = await asyncio.to_thread(_resolve_usable_inputs)
    aspect_ratio = get_aspect_ratio(project, "videos")
    seed = payload.get("seed")

    def _visual_basis_digest_for(storyboard_image: Path, end_frame_image: Path | None) -> str:
        return build_storyboard_video_visual_basis(
            prompt=requested_visual_prompt,
            storyboard_image=storyboard_image,
            end_frame_image=end_frame_image,
            aspect_ratio=aspect_ratio,
            provider_id=registry_provider_id,
            model_id=model_name,
            resolution=resolution,
            seed=seed,
            requested_generate_audio=requested_generate_audio,
            content_mode=content_mode,
            utterances=item.get("utterances") if content_mode == "drama" else None,
            has_utterances=content_mode == "drama" and "utterances" in item,
            voice_characters=(None if ctx.video.is_silent else project.get("characters"))
            if content_mode == "drama"
            else None,
        ).digest

    def _current_visual_basis_digest() -> str:
        return _visual_basis_digest_for(storyboard_file, end_image)

    visual_basis_digest = await asyncio.to_thread(_current_visual_basis_digest)

    # render_storyboard_video_prompt centralizes final text: voice_profiles stripping, drama lip-sync dialogue
    # injection (ordered storyboard utterances as sole source), YAML rendering, and negative suffixes. Preview
    # endpoints and batch admission share it. Silent cases (class C models produce no audio, or episode audio is disabled) pass characters=None
    # to omit Voice_Profiles; both paths use VideoLaneResult.is_silent.
    prompt_text = render_storyboard_video_prompt(
        prompt,
        item if isinstance(item, dict) else None,
        content_mode=content_mode,
        voice_characters=(None if ctx.video.is_silent else (project.get("characters") or {}))
        if content_mode == "drama"
        else None,
    )
    service_tier = payload.get("video_provider_settings", {}).get("service_tier", "default")

    # Provider / model / capabilities / resolution come from one resolved video lane. Query capabilities by actual backend identity
    # (registry provider_id + backend.model), matching the model actually called. Historical payload provider overrides
    # and disabled-custom-model fallback therefore cannot mislead duration guards
    # by applying project-default-model capabilities to the actual called model. Resolution/construction failures
    # already propagate unchanged from resolve_generation_context and fail the whole task.
    # Execution centralizes duration resolution: payload > project.default_duration > first request-fact tier.
    # Use ``is not None``, not ``or``, for payload values, preserving explicit falsy values as supplied.
    duration_seconds = (
        item.get("duration_seconds")
        if task_id is not None and isinstance(item, dict)
        else payload.get("duration_seconds")
    )
    if duration_seconds is None:
        duration_seconds = project.get("default_duration")
    if not duration_seconds:
        if request_facts.allowed_durations:
            duration_seconds = request_facts.allowed_durations[0]
        else:
            # Empty tiers mean endpoint-fixed duration only (other cases already failed request facts); without tiers, use the shared
            # planning-length default.
            duration_seconds = DEFAULT_PLANNED_DURATION_SECONDS

    if not isinstance(duration_seconds, (int, str)) or isinstance(duration_seconds, bool):
        raise ValueError("video request duration must be an integer or integer string")
    # Capability guard: sole authority after provider resolution (ADR-0001). Delegate safe parsing to the guard;
    # do not pre-truncate with int(), silently correcting noninteger seconds into accidentally valid values.
    assert_duration_supported(duration_seconds, supported_durations)
    duration_seconds = int(float(duration_seconds))

    provider_start_image = storyboard_file
    provider_end_image = end_image

    async def _admit_before_submit() -> Mapping[str, object] | None:
        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_input_claims)
        return None

    checkpoint_hook: Callable[[], Awaitable[Mapping[str, object] | None]] | None = _admit_before_submit
    staged_media: tuple[StagedProviderMedia, ...] = ()
    if task_id is not None:
        artifact_speech_preparation = admit_script_unit(script_kind, item).preparation
        artifact_speech = await asyncio.to_thread(
            freeze_video_speech_facts,
            artifact_speech_preparation,
            characters=project.get("characters"),
            include_voice_styles=not ctx.video.is_silent,
        )
        artifact_duration_basis = build_video_duration_basis(duration_seconds)
        artifact_duration_tiers = tuple(
            sorted(
                {
                    duration_seconds,
                    *request_facts.allowed_durations,
                }
            )
        )
        media_inputs = [
            ProviderMediaInput(
                path=storyboard_file,
                role="start_image",
                logical_type="storyboard",
                logical_name=resource_id,
                kind="first_frame",
            )
        ]
        if end_image is not None:
            media_inputs.append(
                ProviderMediaInput(
                    path=end_image,
                    role="end_image",
                    logical_type="storyboard",
                    logical_name=resource_id,
                    kind="last_frame",
                )
            )
        staged_media = await stage_provider_media_for_task(project_path, task_id, tuple(media_inputs))
        try:

            def _bind_staged_media() -> tuple[list[ArtifactInputClaim], Path, Path | None]:
                # Execution digest binding and staging-path verification both read disk; run together in a worker thread.
                claims = list(
                    bind_artifact_input_claims_to_content_digests(
                        resolver=currency_resolver,
                        claims=formal_input_claims,
                        content_digests={media.source_locator: media.sha256 for media in staged_media},
                    )
                )
                start = safe_join(
                    project_path,
                    next(media.staged_locator for media in staged_media if media.role == "start_image"),
                    require_file=True,
                )
                staged_end = next((media for media in staged_media if media.role == "end_image"), None)
                end = (
                    safe_join(project_path, staged_end.staged_locator, require_file=True)
                    if staged_end is not None
                    else None
                )
                return claims, start, end

            formal_input_claims, provider_start_image, provider_end_image = await asyncio.to_thread(_bind_staged_media)
            visual_basis_digest = await asyncio.to_thread(
                _visual_basis_digest_for, provider_start_image, provider_end_image
            )
            artifact_visual_basis = await asyncio.to_thread(
                lambda: build_storyboard_video_artifact_visual_basis(
                    resource_id=resource_id,
                    visual_prompt=requested_visual_prompt,
                    storyboard_image=provider_start_image,
                    end_frame_image=provider_end_image,
                    aspect_ratio=aspect_ratio,
                )
            )
            artifact_video_basis = compose_video_artifact_basis(
                visual=artifact_visual_basis,
                speech=artifact_speech.basis,
                duration=artifact_duration_basis,
            )

            def _build_checkpoint() -> StoryboardSubmissionCheckpoint:
                artifact_currency = VideoArtifactCurrencyFacts(
                    episode=artifact_episode,
                    request_duration_seconds=duration_seconds,
                    visual_basis=artifact_visual_basis,
                    speech_basis=artifact_speech.basis,
                    duration_basis=artifact_duration_basis,
                    video_basis=artifact_video_basis,
                    voice_style_speakers=artifact_speech.voice_style_speakers,
                    duration_tiers=artifact_duration_tiers,
                    reference_image_limit=None,
                    parent_version=generator.versions.get_current_version("videos", resource_id),
                )
                return StoryboardSubmissionCheckpoint.create(
                    task_id=task_id,
                    project_name=project_name,
                    script_file=script_file,
                    unit_id=resource_id,
                    generation_type="i2v",
                    provider_id=ctx.video.provider_model.provider_id,
                    provider_model_id=ctx.video.provider_model.model_id,
                    backend_model_id=ctx.video.backend_model,
                    endpoint_guard=ctx.video.endpoint,
                    prompt=prompt_text,
                    duration_seconds=duration_seconds,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    generate_audio=requested_generate_audio,
                    service_tier=service_tier,
                    seed=seed,
                    visual_basis_digest=visual_basis_digest,
                    artifact_currency=artifact_currency,
                    media=staged_media,
                    reference_audio_targets=None,
                )

            async def _checkpoint_before_submit() -> Mapping[str, object]:
                await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_input_claims)
                # Checkpoints read version records (version-management lock) and bind digests; construct in a worker thread.
                checkpoint = await asyncio.to_thread(_build_checkpoint)
                await get_generation_queue().persist_execution_checkpoint(
                    task_id,
                    checkpoint.to_json(),
                    checkpoint.provider_id,
                )
                return checkpoint_version_metadata(checkpoint)

            checkpoint_hook = _checkpoint_before_submit
        except BaseException:
            await asyncio.to_thread(cleanup_staged_provider_media, project_path, task_id)
            raise

    artifact_committer = (
        VideoArtifactCommitter(
            project_manager=get_project_manager(),
            project_name=project_name,
            project_path=project_path,
            versions=generator.versions,
            resource_type="videos",
            resource_id=resource_id,
            prompt=prompt_text,
        )
        if task_id is not None
        else None
    )
    #: Nonblocking backend execution notices (e.g. multiple ComfyUI output files), persisted with task result.
    warnings: list[dict[str, Any]] = []
    try:
        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_input_claims)
        # Direct calls send project storyboard images to providers: retrieve remote versions with cleared caches first, registering use during the call.
        async with local_work_files(project_path, [provider_start_image] if task_id is None else []):
            _output_path, version, _, video_uri = await generator.generate_video_async(
                prompt=prompt_text,
                resource_type="videos",
                resource_id=resource_id,
                start_image=provider_start_image,
                end_image=provider_end_image,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
                resolution=resolution,
                task_id=task_id,
                before_submit=checkpoint_hook,
                formal_output=task_id is not None,
                before_formal_commit=artifact_committer.prepare_selection if artifact_committer is not None else None,
                commit_formal_output=artifact_committer,
                seed=seed,
                service_tier=service_tier,
                visual_basis_digest=visual_basis_digest,
                generate_audio=requested_generate_audio,
                poll_timeout_seconds=poll_timeout_seconds,
                warnings=warnings,
            )

        async def _finalize() -> dict[str, Any]:
            return await finalize_video_task(
                project_name=project_name,
                script_file=script_file,
                project_path=project_path,
                resource_id=resource_id,
                version=version,
                video_uri=video_uri,
                generator=generator,
                warnings=warnings,
            )

        return await complete_video_artifact_commit(
            committer=artifact_committer,
            versions=generator.versions,
            resource_type="videos",
            resource_id=resource_id,
            version=version,
            video_uri=video_uri,
            finalize=_finalize,
            warnings=warnings,
        )
    finally:
        if artifact_committer is not None:
            await artifact_committer.release_admission_guard()
        if task_id is not None:
            await asyncio.to_thread(cleanup_staged_provider_media, project_path, task_id)


async def finalize_video_task(
    *,
    project_name: str,
    script_file: str,
    project_path: Path,
    resource_id: str,
    version: int,
    video_uri: str | None,
    generator: Any,
    warnings: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Finalize logic shared by normal generation and resume: write scene assets, extract thumbnail, return result dict.

    Omit ``warnings`` when empty: readers test key presence to determine whether a version has notices;
    always writing an empty list would add a perpetually empty shape to every version."""

    def _update_video_metadata():
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_clip",
            asset_path=f"videos/scene_{resource_id}.mp4",
        )
        if video_uri:
            get_project_manager().update_scene_asset(
                project_name=project_name,
                script_filename=script_file,
                scene_id=resource_id,
                asset_type="video_uri",
                asset_path=video_uri,
            )

    await run_noninterruptible_sync(_update_video_metadata)

    video_file = project_path / f"videos/scene_{resource_id}.mp4"
    thumbnail_file = project_path / f"thumbnails/scene_{resource_id}.jpg"
    if await extract_video_thumbnail(video_file, thumbnail_file):
        await run_noninterruptible_sync(
            get_project_manager().update_scene_asset,
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_thumbnail",
            asset_path=f"thumbnails/scene_{resource_id}.jpg",
        )
    else:
        # hard-delete: derived -- Remove video thumbnails on extraction failure; they can be regenerated from the video.
        await run_noninterruptible_sync(thumbnail_file.unlink, missing_ok=True)

    created_at = await run_noninterruptible_sync(
        lambda: generator.versions.get_versions("videos", resource_id)["versions"][-1]["created_at"]
    )

    result: dict[str, Any] = {
        "version": version,
        "file_path": f"videos/scene_{resource_id}.mp4",
        "created_at": created_at,
        "resource_type": "videos",
        "resource_id": resource_id,
        "video_uri": video_uri,
    }
    if warnings:
        result["warnings"] = list(warnings)
    return result


async def execute_asset_sheet_task(
    kind: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    """Asset sheets (characters, scenes, props, products): descriptions from stored entries, references and basis from generation inputs."""

    def _load() -> tuple[dict[str, Any], Path, AssetSheetInput]:
        _project = get_project_manager().load_project(project_name)
        _project_path = get_project_manager().get_project_path(project_name)
        _generation_input = asset_sheet_input(
            _project,
            asset_type=kind,
            name=resource_id,
            observation=project_input_observation(_project_path),
        )
        # Invalid generation inputs fail before provider-channel resolution and billing, reporting all gaps together.
        if (_refusal := _generation_input.refusal) is not None:
            raise input_refusal_error(_refusal)
        return _project, _project_path, _generation_input

    project, project_path, generation_input = await asyncio.to_thread(_load)
    context = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        project_path=project_path,
        user_id=user_id,
        image=ImageLaneRequest(generation_type="i2i" if generation_input.references else "t2i"),
    )
    ensure_dispatch_provider(claimed_provider_id, context.image.provider_model.provider_id)

    def _freeze() -> FrozenGenerationInput:
        return generation_input.freeze(
            max_reference_images=context.image.max_reference_images,
            model=context.image.backend_model,
            bind_claims=_originals_have_no_claims,
        )

    async with frozen_generation_input(
        _freeze,
        project_path=project_path,
        sources=[reference.visual.path for reference in generation_input.references],
    ) as frozen:
        return await run_asset_sheet_image_task(
            asset_type=kind,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
            user_id=user_id,
            task_id=task_id,
            project=project,
            frozen=frozen,
            context=context,
            project_manager=get_project_manager(),
        )


def _originals_have_no_claims(
    claims: Sequence[ArtifactInputClaim], _content_digests: Mapping[str, str]
) -> tuple[ArtifactInputClaim, ...]:
    """Asset-sheet references are only creator-uploaded originals, not registered artifacts, so no claims require rechecking."""

    if claims:
        raise ValueError("asset sheet references carry no artifact input claims")
    return ()


async def execute_character_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    return await execute_asset_sheet_task(
        "character",
        project_name,
        resource_id,
        payload,
        user_id=user_id,
        task_id=task_id,
        claimed_provider_id=claimed_provider_id,
    )


async def execute_scene_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    return await execute_asset_sheet_task(
        "scene",
        project_name,
        resource_id,
        payload,
        user_id=user_id,
        task_id=task_id,
        claimed_provider_id=claimed_provider_id,
    )


async def execute_prop_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    return await execute_asset_sheet_task(
        "prop",
        project_name,
        resource_id,
        payload,
        user_id=user_id,
        task_id=task_id,
        claimed_provider_id=claimed_provider_id,
    )


async def execute_product_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    return await execute_asset_sheet_task(
        "product",
        project_name,
        resource_id,
        payload,
        user_id=user_id,
        task_id=task_id,
        claimed_provider_id=claimed_provider_id,
    )


async def execute_grid_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    """Execute a grid joint-image generation task.

    resource_id is the grid_id. Steps:
    1. Load GridGeneration (an already completed record ends the task without generating),
       set status to generating
    2. Generate the joint image via MediaGenerator (versioned as resource_type "grids")
    3. Mark completed; splitting into storyboard cells is a separate, explicit step
    """
    from lib.script.grid.grid_manager import GridManager
    from lib.script.grid.grid_resolution import grid_image_size
    from lib.script.grid.layout import grid_aspect_ratio_for
    from lib.script.grid.models import GridGeneration
    from lib.script.grid.prompt_builder import build_grid_prompt

    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)

    def _load_grid() -> tuple[GridManager, GridGeneration | None]:
        manager = GridManager(project_path)
        return manager, manager.get(resource_id)

    # a) Load grid
    grid_manager, grid = await asyncio.to_thread(_load_grid)
    if grid is None:
        raise ValueError(f"grid not found: {resource_id}")
    project = await asyncio.to_thread(get_project_manager().load_project, project_name)
    if grid.status == "completed":
        # Creation/regeneration records start pending; failure retries start failed. Already-completed records can only be duplicate enqueues
        # when a previous task finished just as an in-flight grid was reused: do not generate or bill again.
        # Report success only if the combined image is registered and usable, as in cell splitting.
        grid_path = f"grids/{resource_id}.png"
        resolver = await asyncio.to_thread(active_artifact_currency_resolver, project_path, project)
        comparison = await asyncio.to_thread(
            resolver.compare, ArtifactKey.episode_grid(grid.episode, resource_id), artifact_path=grid_path
        )
        if not comparison.usable:
            raise ValueError(f"grid {resource_id} is completed but its composite is not usable")
        logger.info("宫格已完成，跳过重复入队的生成任务: grid_id=%s task_id=%s", resource_id, task_id)
        return {"file_path": grid_path, "resource_type": "grids", "resource_id": resource_id}
    script = await run_sync_transaction(get_project_manager().load_script, project_name, grid.script_file)
    script_input = await asyncio.to_thread(
        resolve_usable_episode_script_input,
        project_path=project_path,
        project=project,
        script=script,
        script_filename=grid.script_file,
    )
    artifact_episode = script_input.episode
    if artifact_episode != grid.episode:
        raise ValueError(f"grid episode {grid.episode} does not match bound script episode {artifact_episode}")

    frozen_references: FrozenImageReferences | None = None
    # Provider/claimed-slot mismatch requeues the task; restore the grid record's pre-claim state rather than leaving generating.
    status_before_claim, error_before_claim = grid.status, grid.error_message
    try:
        # b) Set status to generating
        grid.status = "generating"
        grid.error_message = None
        await run_sync_transaction(grid_manager.save, grid)

        items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
        item_by_id = {str(item.get(id_field)): item for item in items if isinstance(item, dict)}
        if len(set(grid.scene_ids)) != len(grid.scene_ids):
            raise ValueError("grid scene identities must be unique")
        missing_members = [scene_id for scene_id in grid.scene_ids if scene_id not in item_by_id]
        if missing_members:
            raise ValueError(f"grid scenes are no longer present in the bound script: {missing_members}")

        # c) Build reference images + metadata
        from lib.script.grid.models import ReferenceImage

        grid_input = await asyncio.to_thread(
            lambda: grid_references(
                project,
                script,
                member_ids=grid.scene_ids,
                observation=project_input_observation(project_path),
            )
        )
        # Invalid reference sets fail before provider-channel resolution and billing, reporting all gaps together.
        if (grid_refusal := grid_input.refusal) is not None:
            raise input_refusal_error(grid_refusal)
        assembled = grid_input.references
        visual_references = [reference.visual for reference in assembled]
        currency_resolver = await asyncio.to_thread(active_artifact_currency_resolver, project_path, project)
        # Finalize references before prompts: backend caps determine outgoing counts; cell-body 图N numbering must use
        # the truncated sequence. Resolve the image lane once before freezing and reuse through submission.
        ctx = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            project_path=project_path,
            user_id=user_id,
            image=ImageLaneRequest(generation_type="i2i" if assembled else "t2i"),
        )
        ensure_dispatch_provider(claimed_provider_id, ctx.image.provider_model.provider_id)

        def _freeze_references() -> FrozenImageReferences:
            # Register snapshots immediately after constructing in the thread: cancellation during freezing loses return values; finally cleans registered snapshot directories.
            nonlocal frozen_references
            frozen_references = freeze_image_references(
                [reference.visual.path for reference in assembled] or None,
                visual_references,
            )
            return frozen_references

        async with local_work_files(project_path, [reference.visual.path for reference in assembled]):
            frozen_references = await run_sync_transaction(_freeze_references)
        # Put the script claim first, followed by reference-image claims bound to frozen bytes.
        formal_claims = list(
            await asyncio.to_thread(
                bind_artifact_input_claims_to_frozen_visuals,
                project_path=project_path,
                resolver=currency_resolver,
                claims=[
                    script_input.claim,
                    *(reference.claim for reference in assembled if reference.claim is not None),
                ],
                source_references=visual_references,
                frozen_references=frozen_references.visual_references,
            )
        )
        # Register basis and grid records with the full assembled set; providers receive only the truncated prefix (as in storyboards);
        # return truncation warnings with task results.
        reference_clamp = clamp_reference_images(
            visual_references, ctx.image.max_reference_images, model=ctx.image.backend_model
        )
        sent_references = frozen_references.sent(reference_clamp.kept)
        reference_images = sent_references.reference_images
        # Grid currency replays this record, so project from the full reference set, matching basis references item by item.
        grid.reference_images = []
        for reference in assembled:
            visual = reference.visual
            if visual.logical_type is None or visual.logical_id is None:
                raise ValueError(f"grid reference has no asset identity: {reference.artifact_path}")
            grid.reference_images.append(
                ReferenceImage(path=reference.artifact_path, name=visual.logical_id, ref_type=visual.logical_type)
            )
        await run_sync_transaction(grid_manager.save, grid)

        # d) Generate grid image
        members = tuple(
            GridStoryboardVisual(
                resource_id=scene_id,
                image_prompt=item_by_id[scene_id].get("image_prompt"),
                video_prompt=item_by_id[scene_id].get("video_prompt"),
            )
            for scene_id in grid.scene_ids
        )
        member_aspect_ratio = grid.video_aspect_ratio or get_aspect_ratio(project, "storyboards")
        grid_aspect_ratio = grid_aspect_ratio_for(grid.rows, grid.cols, member_aspect_ratio)
        prompt_text = build_grid_prompt(
            scenes=[item_by_id[scene_id] for scene_id in grid.scene_ids],
            id_field=id_field,
            rows=grid.rows,
            cols=grid.cols,
            style=normalize_style_value(project.get("style")),
            style_description=normalize_style_value(project.get("style_description")),
            aspect_ratio=member_aspect_ratio,
            grid_aspect_ratio=grid_aspect_ratio,
            references=sent_references.visual_references,
        )
        grid_basis = build_grid_composite_visual_basis(
            group_id=grid.id,
            members=members,
            rows=grid.rows,
            columns=grid.cols,
            style=str(project.get("style") or ""),
            style_description=project_basis_style_description(project),
            grid_aspect_ratio=grid_aspect_ratio,
            references=frozen_references.visual_references,
            asset_names=project_basis_records_asset_names(project),
        )
        generator = ctx.generator
        aspect_ratio = grid_aspect_ratio

        # Backfill grid metadata: routers cannot know needs_i2i at creation/rebuild time, so fill it here.
        # Record registry provider identity for later re-resolution, and actual backend model identity
        # (disabled custom targets may fall back to a called model differing from resolved model_id).
        grid.provider = ctx.image.provider_model.provider_id
        grid.model = ctx.image.backend_model
        grid.prompt = prompt_text
        await run_sync_transaction(grid_manager.save, grid)
        # Share estimation's ``grid_image_size``; minimum tier and ``large_grid_allowed`` gating share one constant.
        image_size = grid_image_size(
            ctx.image.resolution, ctx.image.provider_model.provider_id, ctx.image.backend_model
        )
        formal_outcomes: list[FormalImageCommitOutcome] = []

        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

        async def _before_submit() -> None:
            await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

        # Write new combined images using the global new-content-version storage location (local by default).
        async with formal_commit(project_name):
            await generator.generate_image_async(
                prompt=prompt_text,
                resource_type="grids",
                resource_id=resource_id,
                reference_images=reference_images,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                before_submit=_before_submit,
                formal_output=True,
                task_id=task_id,
                commit_formal_output=grid_formal_image_callback(
                    project_path=project_path,
                    grid_manager=grid_manager,
                    grid=grid,
                    resource_id=resource_id,
                    prompt=str(prompt_text),
                    versions=generator.versions,
                    task_id=task_id,
                    basis=grid_basis,
                    outcome_box=formal_outcomes,
                ),
            )

        # With formal_output=True, generate_image_async always commits through the activation callback, which records exactly one result.
        outcome = formal_outcomes[0]

    except DispatchProviderChanged:

        def _restore_status_before_claim() -> None:
            restored = grid_manager.get(resource_id) or grid
            restored.status = status_before_claim
            restored.error_message = error_before_claim
            grid_manager.save(restored)

        await run_sync_transaction(_restore_status_before_claim)
        raise
    except GenerationTargetDeletedError:
        # Grid deleted: do not write back failure state and recreate its record.
        raise
    except Exception:
        import traceback

        # Obtain traceback only on the exception-context event-loop thread, then persist in a worker thread.
        failure_text = traceback.format_exc()

        def _record_failure() -> None:
            # The formal-write transaction restores the durable grid record on failure.
            # Reload it before recording failure so the in-memory ``grid`` never carries
            # completion fields that did not become durable.
            failed = grid_manager.get(resource_id) or grid
            failed.status = "failed"
            failed.error_message = failure_text
            grid_manager.save(failed)

        await run_sync_transaction(_record_failure)
        raise
    finally:
        if frozen_references is not None:
            await run_noninterruptible_sync(frozen_references.cleanup)

    grid_result: dict[str, Any] = {
        "version": outcome.version,
        "file_path": f"grids/{resource_id}.png",
        "created_at": outcome.created_at,
        "resource_type": "grids",
        "resource_id": resource_id,
    }
    if (clamp_warning := reference_clamp.warning()) is not None:
        grid_result["warnings"] = [clamp_warning]
    return grid_result


_TASK_EXECUTORS = {
    "storyboard": execute_storyboard_task,
    "video": execute_video_task,
    "tts": execute_tts_task,
    "voice_sample": execute_character_voice_sample_task,
    "character": execute_character_task,
    "scene": execute_scene_task,
    "prop": execute_prop_task,
    "product": execute_product_task,
    "grid": execute_grid_task,
    "reference_video": execute_reference_video_task,
    "image_edit": execute_image_edit_task,
    DERIVATIVE_TASK_TYPE: execute_character_derivative_task,
}


async def execute_generation_task(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
    task_type = task.get("task_type")
    project_name = task.get("project_name")
    resource_id = str(task.get("resource_id"))
    payload = task.get("payload") or {}
    user_id = task.get("user_id", DEFAULT_USER_ID)
    queue_task_id = task.get("task_id")
    # Workers copy current global settings into task dicts at dispatch; nonworker callers (tests / direct generation) use defaults.
    video_poll_timeout_seconds = int(task.get("video_poll_timeout_seconds", DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS))

    if not project_name:
        raise ValueError("task.project_name is required")
    if not task_type:
        raise ValueError("task.task_type is required")

    executor = _TASK_EXECUTORS.get(task_type)
    if task_type.startswith("text_"):
        from server.tool_runtime import execute_queued_text_task

        return await execute_queued_text_task(task)
    if is_render_task_type(task_type):
        return await execute_render_task(task)
    if executor is None:
        raise ValueError(f"unsupported task_type: {task_type}")

    with project_change_source("worker"):
        # Propagate capability exceptions (Image/VideoCapabilityError, ReferencePayloadFloorError) unchanged:
        # worker encode_task_failure_message persists code + params, deferring rendering to read-side
        # Translator; the same failed task displays zh/en/vi by Accept-Language.
        try:
            if task_type in ("video", "reference_video"):
                result = await executor(
                    project_name,
                    resource_id,
                    payload,
                    script_file=task.get("script_file"),
                    user_id=user_id,
                    task_id=queue_task_id,
                    claimed_provider_id=claimed_provider_id,
                    poll_timeout_seconds=video_poll_timeout_seconds,
                )
            else:
                result = await executor(
                    project_name,
                    resource_id,
                    payload,
                    user_id=user_id,
                    task_id=queue_task_id,
                    claimed_provider_id=claimed_provider_id,
                )
        except GenerationTargetDeletedError as deleted:
            # Results moved to project trash still count as success; no resource was written, so emit no generation-success event.
            return deleted.result
        # Event-emission failure does not roll back artifacts: persisted artifacts remain valid and exceptions propagate normally.
        # Events read scripts and compute fingerprints, so offload to a worker; to_thread copies context, preserving worker event source.
        # Artifacts committed: event emission must not be interrupted; cancellation must not reclassify paid success as interrupted.
        await run_noninterruptible_sync(
            emit_generation_success_batch,
            task_type=task_type,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
        )
        return result
