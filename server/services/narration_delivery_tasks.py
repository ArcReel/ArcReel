"""Current narration-delivery materialization and paid-output validation.

Transport entry points share this service for active-TTS observation, storyboard
duration projection, and post-generation media checks.  Durable request facts
remain in :mod:`lib.narration_delivery`; this module only adapts server state.
"""

from __future__ import annotations

import asyncio
import filecmp
import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from lib.audio_utils import probe_existing_media_duration_seconds
from lib.config.resolver import ConfigResolver, VideoCapability
from lib.db import async_session_factory
from lib.generation_queue import GenerationQueue, get_generation_queue
from lib.narration_delivery import (
    NarratedVideoDurationBlockedError,
    NarratedVideoDurationPreparation,
    NarrationDeliveryPreparation,
    TtsSettingsResolver,
    TtsSynthesisSettings,
    VideoRequestCostFacts,
    prepare_current_narration_delivery,
    prepare_narrated_video_duration,
    prepare_narrated_video_output,
)
from lib.path_safety import try_safe_join
from lib.project_manager import ProjectManager
from lib.reference_video.request_projection import (
    USE_TTS,
    ConfigReferenceCapabilityProjection,
    ReferenceRequestOptions,
    materialize_current_reference_request_options,
)
from lib.resource_paths import resource_relative_path
from lib.script_skeleton import resolve_script_kind
from lib.speech_composition import admit_script_unit
from lib.version_manager import VersionManager
from server.services.generation_context import AudioLaneRequest, AudioLaneResult, resolve_generation_context


@dataclass(frozen=True, slots=True)
class ResolvedTtsSettingsResolver:
    """Serve one audio-lane snapshot to current-state delivery projection."""

    settings: TtsSynthesisSettings

    @classmethod
    def from_audio_lane(cls, audio: AudioLaneResult) -> ResolvedTtsSettingsResolver:
        return cls(
            TtsSynthesisSettings(
                provider_id=audio.provider_model.provider_id,
                model_id=audio.backend_model,
                voice=audio.narration_voice,
                speed=audio.narration_speed,
            )
        )

    async def resolve_tts_synthesis_settings(self, project: dict) -> TtsSynthesisSettings:
        del project
        return self.settings


class CurrentTtsSettingsResolver:
    """Resolve freshness inputs through the same assembled audio lane as synthesis."""

    def __init__(self, project_name: str) -> None:
        self._project_name = project_name

    async def resolve_tts_synthesis_settings(self, project: dict) -> TtsSynthesisSettings:
        ctx = await resolve_generation_context(
            self._project_name,
            None,
            project=project,
            audio=AudioLaneRequest(),
        )
        return ResolvedTtsSettingsResolver.from_audio_lane(ctx.audio).settings


def _selected_current_video_record(
    *,
    project_path: Path,
    versions: VersionManager,
    item: dict[str, Any],
    resource_type: str,
    resource_id: str,
) -> tuple[str, Path, dict[str, Any], int] | None:
    if item.get("stale"):
        return None
    assets = item.get("generated_assets")
    if not isinstance(assets, dict) or assets.get("status") != "completed":
        return None
    canonical_rel = resource_relative_path(resource_type, resource_id)
    if assets.get("video_clip") != canonical_rel:
        return None

    formal_file = try_safe_join(project_path, canonical_rel, require_file=True)
    if formal_file is None:
        return None
    history = versions.get_versions(resource_type, resource_id)
    current_version = history.get("current_version")
    if not isinstance(current_version, int) or isinstance(current_version, bool) or current_version <= 0:
        return None
    records = history.get("versions")
    if not isinstance(records, list):
        return None
    current_record = next(
        (
            record
            for record in records
            if isinstance(record, dict)
            and record.get("version") == current_version
            and record.get("is_current") is True
        ),
        None,
    )
    if current_record is None:
        return None
    snapshot_rel = current_record.get("file")
    if not isinstance(snapshot_rel, str):
        return None
    snapshot_file = try_safe_join(project_path, snapshot_rel, require_file=True)
    if snapshot_file is None:
        return None
    try:
        if not filecmp.cmp(formal_file, snapshot_file, shallow=False):
            return None
    except OSError:
        return None

    recorded_tiers: list[int] = []
    for key in ("request_duration_seconds", "effective_duration_seconds", "duration_seconds"):
        value = current_record.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return None
        recorded_tiers.append(value)
    if not recorded_tiers or len(set(recorded_tiers)) != 1:
        return None

    return canonical_rel, formal_file, current_record, recorded_tiers[0]


async def _selected_current_video_covering_duration(
    *,
    project_path: Path,
    versions: VersionManager,
    item: dict[str, Any],
    resource_type: str,
    resource_id: str,
    minimum_actual_duration_seconds: float,
) -> tuple[str, Path, dict[str, Any], int] | None:
    """Read one trusted selected visual whose measured media covers current TTS."""

    if (
        isinstance(minimum_actual_duration_seconds, bool)
        or not isinstance(minimum_actual_duration_seconds, (int, float))
        or not math.isfinite(minimum_actual_duration_seconds)
        or minimum_actual_duration_seconds <= 0
    ):
        raise ValueError("minimum_actual_duration_seconds must be positive")
    selected = await asyncio.to_thread(
        _selected_current_video_record,
        project_path=project_path,
        versions=versions,
        item=item,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if selected is None:
        return None
    actual_duration = await probe_existing_media_duration_seconds(selected[1])
    if (
        actual_duration is None
        or not math.isfinite(actual_duration)
        or actual_duration < minimum_actual_duration_seconds
    ):
        return None
    return selected


async def current_selected_video_tier(
    *,
    project_path: Path,
    versions: VersionManager,
    item: dict[str, Any],
    resource_type: str,
    resource_id: str,
    minimum_actual_duration_seconds: float,
) -> int | None:
    """Observe a selected visual tier only when its media can carry current TTS."""

    selected = await _selected_current_video_covering_duration(
        project_path=project_path,
        versions=versions,
        item=item,
        resource_type=resource_type,
        resource_id=resource_id,
        minimum_actual_duration_seconds=minimum_actual_duration_seconds,
    )
    return selected[3] if selected is not None else None


async def reuse_current_video_for_tier(
    *,
    project_path: Path,
    versions: VersionManager,
    item: dict[str, Any],
    resource_type: str,
    resource_id: str,
    request_duration_seconds: int,
    minimum_actual_duration_seconds: float,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return a selected visual only when its tier and measured media are reusable."""

    selected = await _selected_current_video_covering_duration(
        project_path=project_path,
        versions=versions,
        item=item,
        resource_type=resource_type,
        resource_id=resource_id,
        minimum_actual_duration_seconds=minimum_actual_duration_seconds,
    )
    if selected is None:
        return None
    canonical_rel, _formal_file, current_record, selected_tier = selected
    if selected_tier != request_duration_seconds:
        return None
    current_version = current_record["version"]
    assets = item["generated_assets"]

    result: dict[str, Any] = {
        "version": current_version,
        "file_path": canonical_rel,
        "created_at": current_record.get("created_at"),
        "resource_type": resource_type,
        "resource_id": resource_id,
        "video_uri": assets.get("video_uri") if isinstance(assets.get("video_uri"), str) else None,
        "reused_existing": True,
        "request_duration_seconds": request_duration_seconds,
    }
    if warnings is not None:
        result["warnings"] = warnings
    return result


async def active_tts_resource_ids(
    *,
    project_name: str,
    resource_ids: Iterable[str],
    script_file: str,
    queue: GenerationQueue | None = None,
) -> frozenset[str]:
    """Return units with active explicit TTS for one script's equivalent locators."""

    normalized = list(dict.fromkeys(resource_id for resource_id in resource_ids if resource_id))
    if not normalized:
        return frozenset()
    normalized_script = str(PurePosixPath(script_file.replace("\\", "/")))
    basename = PurePosixPath(normalized_script).name
    if not basename or basename == ".":
        raise ValueError("script_file must identify a script")
    locators = tuple(dict.fromkeys((normalized_script, basename, f"scripts/{basename}")))
    queue = queue or get_generation_queue()
    active_batches = await asyncio.gather(
        *(
            queue.get_active_tasks_for_resources(
                project_name=project_name,
                task_type="tts",
                resource_ids=normalized,
                script_file=locator,
            )
            for locator in locators
        )
    )
    return frozenset(str(task.get("resource_id") or "") for batch in active_batches for task in batch)


async def tts_task_in_progress(
    *,
    project_name: str,
    resource_id: str,
    script_file: str,
) -> bool:
    """Whether one unit currently has an active explicit TTS task."""

    active = await active_tts_resource_ids(
        project_name=project_name,
        resource_ids=(resource_id,),
        script_file=script_file,
    )
    return resource_id in active


async def prepare_current_storyboard_narrated_video_duration(
    *,
    project_name: str,
    project: dict[str, Any],
    project_path: Path,
    script: dict[str, Any],
    script_file: str,
    item: dict[str, Any],
    capability: VideoCapability,
    planned_duration_seconds: int | None,
    confirmed_request_duration_seconds: int | None,
    tts_in_progress: bool | None = None,
) -> NarratedVideoDurationPreparation:
    """Materialize current TTS and video-tier facts for one storyboard unit."""

    resolver = ConfigResolver(async_session_factory)
    candidate = await ConfigReferenceCapabilityProjection(resolver).resolve_candidate(project, capability)
    planned = planned_duration_seconds
    if planned is None:
        configured = project.get("default_duration")
        planned = configured if isinstance(configured, int) and not isinstance(configured, bool) else None
    if planned is None or planned <= 0:
        planned = candidate.supported_durations[0]
    preparation = admit_script_unit(resolve_script_kind(script), item).preparation
    active = tts_in_progress
    if active is None:
        active = await tts_task_in_progress(
            project_name=project_name,
            resource_id=preparation.unit_id,
            script_file=script_file,
        )
    narration = await prepare_current_narration_delivery(
        project=project,
        episode=ProjectManager.resolve_episode_from_script(script, script_file),
        preparation=preparation,
        project_path=project_path,
        delivery="use_tts",
        resolver=CurrentTtsSettingsResolver(project_name),
        tts_in_progress=active,
    )
    current_visual_duration = (
        await current_selected_video_tier(
            project_path=project_path,
            versions=VersionManager(project_path),
            item=item,
            resource_type="videos",
            resource_id=preparation.unit_id,
            minimum_actual_duration_seconds=narration.actual_duration_seconds,
        )
        if narration.actual_duration_seconds is not None
        else None
    )
    result = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=planned,
        supported_durations=candidate.supported_durations,
        confirmed_request_duration_seconds=confirmed_request_duration_seconds,
        current_visual_duration_seconds=current_visual_duration,
    )
    if result.request_duration_seconds is None:
        return result
    return replace(
        result,
        cost=VideoRequestCostFacts(
            provider_id=candidate.provider_id,
            model_id=candidate.model_id,
            resolution=candidate.resolution,
            duration_seconds=result.request_duration_seconds,
            generate_audio=candidate.generate_audio,
        ),
    )


async def prepare_current_reference_video_request_options(
    *,
    project: dict[str, Any],
    script: dict[str, Any],
    unit: dict[str, Any],
    project_path: Path,
    options: ReferenceRequestOptions,
    project_name: str,
    tts_settings_resolver: TtsSettingsResolver | None = None,
    tts_in_progress: bool = False,
) -> ReferenceRequestOptions:
    """Materialize TTS and selected-visual tier facts from one current state."""

    prepared = await materialize_current_reference_request_options(
        project=project,
        script=script,
        unit=unit,
        project_path=project_path,
        options=options,
        resolver=tts_settings_resolver or CurrentTtsSettingsResolver(project_name),
        tts_in_progress=tts_in_progress,
    )
    visual_tier = (
        await current_selected_video_tier(
            project_path=project_path,
            versions=VersionManager(project_path),
            item=unit,
            resource_type="reference_videos",
            resource_id=str(unit.get("unit_id") or ""),
            minimum_actual_duration_seconds=prepared.narration_preparation.actual_duration_seconds,
        )
        if options.narration_delivery == USE_TTS
        and prepared.narration_preparation is not None
        and prepared.narration_preparation.actual_duration_seconds is not None
        else None
    )
    return replace(prepared, current_visual_duration_seconds=visual_tier)


async def require_generated_video_covers_current_tts(
    *,
    narration: NarrationDeliveryPreparation,
    request_duration_seconds: int,
    output_path: Path,
    versions: VersionManager,
    resource_type: str,
    resource_id: str,
    version: int,
) -> None:
    """Reject a paid video as current if its actual media truncates selected TTS."""

    actual_duration = await probe_existing_media_duration_seconds(output_path)
    preparation = NarratedVideoDurationPreparation(
        narration=narration,
        planned_duration_seconds=request_duration_seconds,
        duration_input=request_duration_seconds,
        request_duration_seconds=request_duration_seconds,
        adjustment=None,
        problems=(),
    )
    checked = prepare_narrated_video_output(
        preparation,
        actual_duration_seconds=actual_duration,
    )
    if checked.allowed:
        return
    await asyncio.to_thread(
        versions.reject_current_version,
        resource_type,
        resource_id,
        rejected_version=version,
        current_file=output_path,
    )
    raise NarratedVideoDurationBlockedError(checked)


__all__ = [
    "active_tts_resource_ids",
    "current_selected_video_tier",
    "prepare_current_storyboard_narrated_video_duration",
    "prepare_current_reference_video_request_options",
    "ResolvedTtsSettingsResolver",
    "require_generated_video_covers_current_tts",
    "reuse_current_video_for_tier",
    "tts_task_in_progress",
]
