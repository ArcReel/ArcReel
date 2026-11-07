"""Project narration delivery configuration (``docs/adr/0089``).

Narration delivery is required: TTS narration or post-production narration. TTS
projects store a model, voice and speech-rate snapshot in ``project.json``.
Creation prefills global defaults; later changes do not inherit those defaults.

The snapshot and delivery mode are independent. Switching to post-production
preserves the snapshot, so existing narration provenance can still be recomputed
without changing currency. Only generating narration requires current TTS delivery.
Currency uses the current snapshot; changing it makes audio generated with the
old settings stale.

This module uses only the standard library, so lower layers such as lib.project
can import it directly.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

POST_PRODUCTION = "post_production"
USE_TTS = "use_tts"
NarrationDelivery = Literal["post_production", "use_tts"]

NARRATION_DELIVERY_FIELD = "narration_delivery"
#: Three project.json TTS snapshot fields. ``audio_backend`` must be a complete ``provider/model``;
#: absent ``narration_speed`` means no speech rate is sent to the provider.
TTS_BACKEND_FIELD = "audio_backend"
TTS_VOICE_FIELD = "narration_voice"
TTS_SPEED_FIELD = "narration_speed"
NARRATION_CONFIG_FIELDS = (NARRATION_DELIVERY_FIELD, TTS_BACKEND_FIELD, TTS_VOICE_FIELD, TTS_SPEED_FIELD)
#: From this project schema onward, narration currency includes the TTS snapshot; earlier schemas have none.
PROJECT_TTS_SNAPSHOT_SCHEMA_VERSION = 16


@dataclass(frozen=True, slots=True)
class TtsSynthesisSettings:
    """Resolved paid-synthesis inputs that participate in TTS currency."""

    provider_id: str
    model_id: str
    voice: str
    speed: float | None

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if not self.voice.strip():
            raise ValueError("voice must be non-empty")
        if self.speed is not None and (not math.isfinite(self.speed) or self.speed <= 0):
            raise ValueError("speed must be positive and finite or null")


class NarrationConfigError(ValueError):
    """The project narration configuration violates a constraint; ``code`` is an errors-catalog key."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_tts_backend(raw: object) -> tuple[str, str] | None:
    """Parse ``"provider/model"`` into (provider, model); a bare provider or missing part is not a complete snapshot."""

    if not isinstance(raw, str) or "/" not in raw:
        return None
    provider, model = (part.strip() for part in raw.split("/", 1))
    if not provider or not model:
        return None
    return provider, model


def parse_tts_speed(raw: object) -> float | None:
    """Convert a positive finite number or numeric string to float; otherwise return None."""

    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        try:
            raw = float(raw.strip())
        except ValueError:
            return None
    if not isinstance(raw, int | float):
        return None
    try:
        speed = float(raw)
    except OverflowError:
        return None
    return speed if math.isfinite(speed) and speed > 0 else None


def project_narration_delivery(project: Mapping[str, object]) -> NarrationDelivery:
    """Return project delivery; missing or unrecognized values mean post-production."""

    return USE_TTS if project.get(NARRATION_DELIVERY_FIELD) == USE_TTS else POST_PRODUCTION


def project_tts_settings(project: Mapping[str, object]) -> TtsSynthesisSettings | None:
    """Return the TTS snapshot, or None for missing model/voice or invalid speed; do not read delivery mode."""

    backend = parse_tts_backend(project.get(TTS_BACKEND_FIELD))
    voice = project.get(TTS_VOICE_FIELD)
    if backend is None or not isinstance(voice, str) or not voice.strip():
        return None
    raw_speed = project.get(TTS_SPEED_FIELD)
    speed = parse_tts_speed(raw_speed)
    if raw_speed is not None and speed is None:
        return None
    return TtsSynthesisSettings(provider_id=backend[0], model_id=backend[1], voice=voice.strip(), speed=speed)


def tts_snapshot_fields(settings: TtsSynthesisSettings) -> dict[str, object]:
    """Serialize TTS settings into project.json snapshot fields; callers remove ``narration_speed`` when None."""

    return {
        TTS_BACKEND_FIELD: f"{settings.provider_id}/{settings.model_id}",
        TTS_VOICE_FIELD: settings.voice,
        TTS_SPEED_FIELD: settings.speed,
    }


def _missing_snapshot_code(project: Mapping[str, object]) -> str:
    if parse_tts_backend(project.get(TTS_BACKEND_FIELD)) is None:
        return "narration_tts_model_required"
    voice = project.get(TTS_VOICE_FIELD)
    if not isinstance(voice, str) or not voice.strip():
        return "narration_tts_voice_required"
    return "narration_tts_speed_invalid"


def validate_project_narration_config(project: Mapping[str, object]) -> None:
    """Require a complete TTS snapshot for projects using TTS narration."""

    if project_narration_delivery(project) == USE_TTS and project_tts_settings(project) is None:
        raise NarrationConfigError(_missing_snapshot_code(project))


def require_project_tts_generation(project: Mapping[str, object]) -> TtsSynthesisSettings:
    """Require TTS delivery and a complete snapshot before generating narration; return the snapshot."""

    if project_narration_delivery(project) != USE_TTS:
        raise NarrationConfigError("narration_delivery_post_production")
    settings = project_tts_settings(project)
    if settings is None:
        raise NarrationConfigError(_missing_snapshot_code(project))
    return settings


class ProjectTtsSettingsResolver:
    """Resolve TTS settings from the project snapshot; incomplete snapshots raise ValueError and count as unconfigured on reads."""

    async def resolve_tts_synthesis_settings(self, project: dict) -> TtsSynthesisSettings:
        settings = project_tts_settings(project)
        if settings is None:
            raise ValueError("project has no complete TTS snapshot")
        return settings


__all__ = [
    "NARRATION_CONFIG_FIELDS",
    "NARRATION_DELIVERY_FIELD",
    "POST_PRODUCTION",
    "PROJECT_TTS_SNAPSHOT_SCHEMA_VERSION",
    "TTS_BACKEND_FIELD",
    "TTS_SPEED_FIELD",
    "TTS_VOICE_FIELD",
    "USE_TTS",
    "NarrationConfigError",
    "NarrationDelivery",
    "ProjectTtsSettingsResolver",
    "TtsSynthesisSettings",
    "parse_tts_backend",
    "parse_tts_speed",
    "project_narration_delivery",
    "project_tts_settings",
    "require_project_tts_generation",
    "tts_snapshot_fields",
    "validate_project_narration_config",
]
