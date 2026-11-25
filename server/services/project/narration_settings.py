"""项目旁白交付与配音设置。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from lib.backends.audio_profiles import AudioProfile
from lib.catalog import query as catalog
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.custom_provider import is_custom_provider
from lib.speech.narration_config import (
    NARRATION_DELIVERY_FIELD,
    POST_PRODUCTION,
    TTS_BACKEND_FIELD,
    TTS_GROUP_FIELDS,
    TTS_SPEED_FIELD,
    TTS_VOICE_FIELD,
    NarrationConfigError,
    NarrationDelivery,
    parse_tts_backend,
    parse_tts_speed,
)


@dataclass(frozen=True, slots=True)
class NarrationSettingsInput:
    """建项请求里的旁白配置。``provided`` 列出请求显式给出的 TTS 字段。"""

    delivery: NarrationDelivery = POST_PRODUCTION
    audio_backend: str | None = None
    narration_voice: str | None = None
    narration_speed: float | None = None
    provided: frozenset[str] = frozenset()


def validate_tts_backend(value: str) -> str:
    """TTS 模型必须是完整的 ``provider/model``，供应商已知，且注册表登记的模型是音频模型。"""

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


async def new_project_narration_fields(value: NarrationSettingsInput, *, resolver: ConfigResolver) -> dict[str, object]:
    fields: dict[str, object] = {NARRATION_DELIVERY_FIELD: value.delivery}
    patch = {key: getattr(value, key) for key in value.provided}
    backend = patch.get(TTS_BACKEND_FIELD)
    profile = AudioProfile()
    if backend:
        provider, model = validate_tts_backend(backend).split("/", 1)
        profile = await resolver.audio_model_profile(provider, model)
    apply_project_tts_patch(fields, patch, lambda _provider, _model: profile)
    return fields


def apply_project_tts_patch(
    project: dict, patch: Mapping[str, object], profile_for: Callable[[str, str], AudioProfile]
) -> None:
    if not any(k in patch for k in TTS_GROUP_FIELDS):
        return
    previous = project.get(TTS_BACKEND_FIELD)
    raw_backend = patch.get(TTS_BACKEND_FIELD, previous)
    if raw_backend is not None and not isinstance(raw_backend, str):
        raise NarrationConfigError("narration_tts_model_invalid")
    backend = (raw_backend or "").strip()
    if not backend:
        if TTS_BACKEND_FIELD not in patch:
            raise NarrationConfigError("narration_tts_model_required")
        raw_voice = patch.get(TTS_VOICE_FIELD)
        has_voice = bool(raw_voice.strip()) if isinstance(raw_voice, str) else raw_voice is not None
        if has_voice or patch.get(TTS_SPEED_FIELD) is not None:
            raise NarrationConfigError("narration_tts_clear_conflict")
        for key in TTS_GROUP_FIELDS:
            project.pop(key, None)
        return
    normalized = validate_tts_backend(backend)
    provider, model = normalized.split("/", 1)
    profile = profile_for(provider, model)
    same = previous == normalized
    raw_voice = patch.get(TTS_VOICE_FIELD, project.get(TTS_VOICE_FIELD) if same else profile.first_voice)
    raw_speed = patch.get(TTS_SPEED_FIELD, project.get(TTS_SPEED_FIELD) if same else None)
    voice = raw_voice.strip() if isinstance(raw_voice, str) else ""
    speed = parse_tts_speed(raw_speed)
    if raw_speed is not None and speed is None:
        raise NarrationConfigError("narration_tts_speed_invalid")
    profile.validate(voice, speed)
    project.update({TTS_BACKEND_FIELD: normalized, TTS_VOICE_FIELD: voice})
    if speed is None:
        project.pop(TTS_SPEED_FIELD, None)
    else:
        project[TTS_SPEED_FIELD] = speed


__all__ = [
    "NarrationSettingsInput",
    "apply_project_tts_patch",
    "new_project_narration_fields",
    "validate_tts_backend",
]
