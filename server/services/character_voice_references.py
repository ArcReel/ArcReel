"""Shared character voice-reference operations for REST and Agent entry points."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from lib.api_errors import BadRequestError, NotFoundError
from lib.asset_types import (
    GLOBAL_ASSET_ID_FIELD,
    GLOBAL_ASSET_VOICE_SOURCE_FIELD,
    resolve_asset_key,
    validate_asset_name,
)
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.generation_queue import get_generation_queue
from lib.generation_queue_client import TaskSpec
from lib.path_safety import safe_exists, safe_join
from lib.project_change_hints import (
    ProjectChangeSource,
    build_change_label,
    emit_project_change_batch,
    project_change_source,
)
from lib.project_manager import get_project_manager
from server.services.generation_context import VideoLaneRequest, resolve_generation_context

VoiceReferenceStrategy = Literal["video", "tts"]
VoiceReferenceTaskSource = Literal["webui", "agent"]
logger = logging.getLogger(__name__)
VOICE_SAMPLE_TEXT_MAX_LENGTH = 200
VOICE_SAMPLE_CANDIDATE_STATUSES = ("queued", "running", "cancelling", "succeeded")


def default_character_monologue(name: str, project: dict[str, Any]) -> str:
    """Return a short self-introduction intended to yield roughly ten seconds."""
    language = str(project.get("source_language") or project.get("language") or "zh").lower()
    if language.startswith("en"):
        return f"Hello, I am {name}. This is my natural speaking voice. Please remember how I sound; I am glad to meet you."
    if language.startswith("vi"):
        return (
            f"Xin chào, tôi là {name}. Đây là giọng nói tự nhiên của tôi. Hãy nhớ cách tôi nói; rất vui được gặp bạn."
        )
    return f"大家好，我是{name}。这是我自然说话的声音。请记住我说话的方式，很高兴在接下来的故事里认识你。"


def character_has_effective_voice(entry: dict[str, Any]) -> bool:
    """Whether automation must leave this character's voice untouched."""
    if any(isinstance(entry.get(field), str) and entry[field].strip() for field in ("reference_audio", "voice_id")):
        return True
    voice_source = entry.get(GLOBAL_ASSET_VOICE_SOURCE_FIELD)
    linked_id = entry.get(GLOBAL_ASSET_ID_FIELD)
    return voice_source in {"reference_audio", "voice_id"} and isinstance(linked_id, str) and bool(linked_id.strip())


def _character(project: dict[str, Any], requested_name: str) -> tuple[str, dict[str, Any]]:
    try:
        normalized = validate_asset_name(requested_name)
    except ValueError:
        raise BadRequestError("asset_invalid_name", name=requested_name)
    key = resolve_asset_key(project.get("characters"), normalized)
    if key is None:
        raise NotFoundError("character_not_found", name=normalized)
    entry = project["characters"][key]
    if not isinstance(entry, dict):
        raise NotFoundError("character_not_found", name=normalized)
    return key, entry


async def latest_character_voice_candidate(
    project_name: str,
    name: str,
    *,
    manager=None,
    queue=None,
) -> dict[str, Any] | None:
    pm = manager or get_project_manager()
    task_queue = queue or get_generation_queue()
    project = await asyncio.to_thread(pm.load_project, project_name)
    character_name, _entry = _character(project, name)
    task = await task_queue.get_latest_task_for_resource(
        project_name=project_name,
        task_type="voice_sample",
        resource_id=character_name,
        statuses=VOICE_SAMPLE_CANDIDATE_STATUSES,
    )
    if task is None:
        return None
    result = task.get("result") or {}
    sample_rel = result.get("file_path")
    if task.get("status") == "succeeded" and (
        not isinstance(sample_rel, str) or not safe_exists(pm.get_project_path(project_name), sample_rel)
    ):
        return None
    return task


async def enqueue_character_voice_reference(
    project_name: str,
    name: str,
    *,
    strategy: VoiceReferenceStrategy = "video",
    text: str | None = None,
    voice: str | None = None,
    source: VoiceReferenceTaskSource = "webui",
    user_id: str = DEFAULT_USER_ID,
    skip_existing_voice: bool = True,
    reuse_candidate: bool = True,
    manager=None,
    queue=None,
) -> dict[str, Any]:
    """Create or reuse a preview candidate without changing ``reference_audio``."""
    pm = manager or get_project_manager()
    task_queue = queue or get_generation_queue()
    project = await asyncio.to_thread(pm.load_project, project_name)
    character_name, entry = _character(project, name)
    if skip_existing_voice and character_has_effective_voice(entry):
        return {"task_id": None, "status": "skipped", "reason": "voice_exists", "deduped": False}

    if reuse_candidate:
        existing = await latest_character_voice_candidate(
            project_name,
            character_name,
            manager=pm,
            queue=task_queue,
        )
        if existing is not None:
            return {
                "task_id": existing["task_id"],
                "status": existing["status"],
                "reason": "candidate_exists",
                "deduped": True,
            }

    monologue = (text or default_character_monologue(character_name, project)).strip()
    if not monologue:
        raise BadRequestError("prompt_text_empty")
    if len(monologue) > VOICE_SAMPLE_TEXT_MAX_LENGTH:
        raise BadRequestError("voice_sample_text_too_long", max_length=VOICE_SAMPLE_TEXT_MAX_LENGTH)

    if strategy == "video":
        ctx = await resolve_generation_context(
            project_name,
            None,
            project=project,
            user_id=user_id,
            video=VideoLaneRequest(capability="i2v"),
        )
        if ctx.video.voice_consistency == "none":
            raise BadRequestError("voice_sample_video_audio_unavailable")
        legal = [value for value in ctx.video.supported_durations if 2 <= value <= 10]
        if not legal:
            raise BadRequestError("voice_sample_video_duration_unavailable")
        duration_seconds = max(legal)
        description = str(entry.get("description") or "").strip()
        voice_style = str(entry.get("voice_style") or "").strip()
        prompt = (
            f"Single character monologue. Character: {character_name}. "
            f"Appearance and identity: {description or 'preserve the named character identity'}. "
            f"Voice: {voice_style or 'natural, clear, and consistent with the character'}. "
            f'Spoken line exactly: "{monologue}". '
            "One speaker only, clean quiet background, close framing, clear dry dialogue, "
            "no music, no ambience, no sound effects, no other voices, no overlapping speech."
        )
        spec = TaskSpec.from_request(
            task_type="voice_sample",
            media_type="video",
            resource_id=character_name,
            prompt=prompt,
            extra_payload={
                "strategy": "video",
                "monologue": monologue,
                "duration_seconds": duration_seconds,
                "voice_style": voice_style,
            },
            source=source,
        )
        provider_id = ctx.video.provider_model.provider_id
    elif strategy == "tts":
        selected_voice = (voice or "").strip()
        if not selected_voice:
            raise BadRequestError("voice_sample_voice_required")
        try:
            resolved = await ConfigResolver(async_session_factory).resolve_audio_backend(project, None)
        except ValueError:
            raise BadRequestError("audio_provider_not_configured")
        spec = TaskSpec.from_request(
            task_type="voice_sample",
            media_type="audio",
            resource_id=character_name,
            prompt=monologue,
            extra_payload={"strategy": "tts", "voice": selected_voice},
            source=source,
        )
        provider_id = resolved.provider_id
    else:
        raise BadRequestError("voice_sample_strategy_invalid")

    result = await task_queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        source=source,
        user_id=user_id,
        provider_id=provider_id,
    )
    return {**result, "reason": None}


async def confirm_character_voice_reference(
    project_name: str,
    name: str,
    task_id: str,
    *,
    source: ProjectChangeSource = "webui",
    manager=None,
    queue=None,
) -> dict[str, str]:
    """Promote one succeeded preview task to the character reference audio."""
    try:
        character_name = validate_asset_name(name)
    except ValueError:
        raise BadRequestError("asset_invalid_name", name=name)
    task_queue = queue or get_generation_queue()
    task = await task_queue.get_task(task_id)
    if (
        task is None
        or task.get("project_name") != project_name
        or task.get("task_type") != "voice_sample"
        or task.get("resource_id") != character_name
    ):
        raise NotFoundError("task_not_found", id=task_id)
    if task.get("status") != "succeeded":
        raise BadRequestError("voice_sample_not_ready")
    sample_rel = (task.get("result") or {}).get("file_path")
    if not isinstance(sample_rel, str) or not sample_rel:
        raise BadRequestError("voice_sample_not_ready")

    def _sync() -> dict[str, str]:
        pm = manager or get_project_manager()
        project_dir = pm.get_project_path(project_name)
        if not safe_exists(project_dir, sample_rel):
            raise NotFoundError("voice_sample_file_missing")
        content = safe_join(project_dir, sample_rel).read_bytes()
        ref_audio_rel = f"characters/refs_audio/{character_name}.wav"
        target_path = project_dir / ref_audio_rel
        try:
            with project_change_source(source):
                pm.install_character_reference_audio(project_name, character_name, ref_audio_rel, content)
        except KeyError:
            raise NotFoundError("character_not_found", name=character_name)
        try:
            emit_project_change_batch(
                project_name,
                [
                    {
                        "entity_type": "character",
                        "action": "updated",
                        "entity_id": character_name,
                        **build_change_label("character_reference_audio", id=character_name),
                        "focus": None,
                        "important": False,
                        "asset_fingerprints": {ref_audio_rel: target_path.stat().st_mtime_ns},
                    }
                ],
                source=source,
            )
        except Exception:
            logger.exception("failed to publish character reference-audio change")
        return {"path": ref_audio_rel}

    return await asyncio.to_thread(_sync)


__all__ = [
    "VOICE_SAMPLE_TEXT_MAX_LENGTH",
    "VoiceReferenceStrategy",
    "character_has_effective_voice",
    "confirm_character_voice_reference",
    "default_character_monologue",
    "enqueue_character_voice_reference",
    "latest_character_voice_candidate",
]
