"""Narration-delivery configuration for new projects (docs/adr/0089).

Shared by the Web creation wizard and Agent project-creation tool: fill omitted TTS model, voice and speech rate
from global defaults. Once written to project.json, they form a project snapshot unaffected by later global changes."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from lib.catalog import query as catalog
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.custom_provider import is_custom_provider
from lib.speech.narration_config import (
    NARRATION_DELIVERY_FIELD,
    POST_PRODUCTION,
    TTS_BACKEND_FIELD,
    TTS_SPEED_FIELD,
    TTS_VOICE_FIELD,
    USE_TTS,
    NarrationConfigError,
    NarrationDelivery,
    parse_tts_backend,
    parse_tts_speed,
    validate_project_narration_config,
)
from server.services.tasks.generation_context import audio_backend_voice_ids

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NarrationSettingsInput:
    """Narration configuration in a project-creation request. ``provided`` lists explicitly supplied TTS fields; fill others from global defaults."""

    delivery: NarrationDelivery = POST_PRODUCTION
    audio_backend: str | None = None
    narration_voice: str | None = None
    narration_speed: float | None = None
    provided: frozenset[str] = frozenset()


def validate_tts_backend(value: str) -> str:
    """The TTS model must be a complete ``provider/model`` with a known provider and an audio-model registry entry."""

    pair = parse_tts_backend(value)
    if pair is None:
        raise NarrationConfigError("narration_tts_model_required")
    provider_id, model_id = pair
    if provider_id not in PROVIDER_REGISTRY:
        if not is_custom_provider(provider_id):
            raise NarrationConfigError("narration_tts_model_invalid")
    else:
        model = catalog.get_model(provider_id, model_id)
        if model is not None and model.media_type != "audio":
            raise NarrationConfigError("narration_tts_model_invalid")
    return f"{provider_id}/{model_id}"


def validate_tts_speed(value: float) -> float:
    speed = parse_tts_speed(value)
    if speed is None:
        raise NarrationConfigError("narration_tts_speed_invalid")
    return speed


async def _voice_for_backend(resolver: ConfigResolver, provider_id: str, model_id: str, voice: str) -> str:
    """Match the global voice to the TTS model actually used by the project.

    There is only one global voice, but registry order may infer another provider as the default audio model
    (the default Cherry voice belongs only to DashScope). If a voice is absent from the selected built-in model’s
    voice list, use the first listed voice. Custom-provider lists may be incomplete, so retain their voices unchanged;
    also retain the voice if its list cannot be obtained, allowing synthesis to report errors normally."""
    if is_custom_provider(provider_id):
        return voice
    try:
        voice_ids = await audio_backend_voice_ids(provider_id, model_id, resolver)
    except Exception as exc:
        # Prefilling is best effort: missing API keys, unreadable Vertex credentials or other construction failures must not block project creation.
        logger.warning("读取音色表失败，旁白音色按全局设置预填 backend=%s/%s：%s", provider_id, model_id, exc)
        return voice
    if voice_ids and voice not in voice_ids:
        return voice_ids[0]
    return voice


async def default_narration_tts(resolver: ConfigResolver) -> tuple[ProviderModel, str, float | None]:
    """Prefill a new TTS project with the global default audio model, a matching narration voice, and speech rate.

    Raise ValueError when no audio provider is configured."""
    backend, voice, speed = await resolver.default_narration_tts()
    return backend, await _voice_for_backend(resolver, backend.provider_id, backend.model_id, voice), speed


async def new_project_narration_fields(
    value: NarrationSettingsInput,
    *,
    resolver: ConfigResolver,
) -> dict[str, object]:
    """Compute narration configuration fields to write into the new project’s project.json."""

    fields: dict[str, object] = {NARRATION_DELIVERY_FIELD: value.delivery}
    backend = value.audio_backend if TTS_BACKEND_FIELD in value.provided else None
    voice = value.narration_voice if TTS_VOICE_FIELD in value.provided else None
    speed = value.narration_speed if TTS_SPEED_FIELD in value.provided else None

    if value.delivery == USE_TTS:
        omitted = {TTS_BACKEND_FIELD, TTS_VOICE_FIELD, TTS_SPEED_FIELD} - value.provided
        if omitted:
            try:
                default_backend, default_voice, default_speed = await resolver.default_narration_tts()
            except ValueError:
                default_backend, default_voice, default_speed = None, None, None
            if TTS_BACKEND_FIELD in omitted and default_backend is not None:
                backend = f"{default_backend.provider_id}/{default_backend.model_id}"
            if TTS_VOICE_FIELD in omitted:
                voice = default_voice
            if TTS_SPEED_FIELD in omitted:
                speed = default_speed
        if not backend:
            raise NarrationConfigError("narration_tts_model_required")

    if backend:
        backend = fields[TTS_BACKEND_FIELD] = validate_tts_backend(backend)
        if TTS_VOICE_FIELD not in value.provided and voice is not None:
            # Match the voice to the actual persisted model; an explicit model may use a different provider from the global default.
            provider_id, model_id = backend.split("/", 1)
            voice = await _voice_for_backend(resolver, provider_id, model_id, voice)
    if voice is not None and voice.strip():
        fields[TTS_VOICE_FIELD] = voice.strip()
    if speed is not None:
        fields[TTS_SPEED_FIELD] = validate_tts_speed(speed)
    validate_project_narration_config(fields)
    return fields


__all__ = [
    "NarrationSettingsInput",
    "new_project_narration_fields",
    "validate_tts_backend",
    "validate_tts_speed",
]
