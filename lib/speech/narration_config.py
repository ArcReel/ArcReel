"""项目旁白交付方式与可选的整组配音覆盖。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

POST_PRODUCTION = "post_production"
USE_TTS = "use_tts"
NarrationDelivery = Literal["post_production", "use_tts"]

NARRATION_DELIVERY_FIELD = "narration_delivery"
TTS_BACKEND_FIELD = "audio_backend"
TTS_VOICE_FIELD = "narration_voice"
TTS_SPEED_FIELD = "narration_speed"
TTS_GROUP_FIELDS = (TTS_BACKEND_FIELD, TTS_VOICE_FIELD, TTS_SPEED_FIELD)
NARRATION_CONFIG_FIELDS = (NARRATION_DELIVERY_FIELD, *TTS_GROUP_FIELDS)


@dataclass(frozen=True, slots=True)
class TtsSynthesisSettings:
    """Model and parameters used by a single narration synthesis."""

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
    """项目旁白配置不满足约束；``code`` 是 errors 目录的 key。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_tts_backend(raw: object) -> tuple[str, str] | None:
    """``"provider/model"`` → (provider, model)；裸供应商或缺一段的值不是完整快照。"""

    if not isinstance(raw, str) or "/" not in raw:
        return None
    provider, model = (part.strip() for part in raw.split("/", 1))
    if not provider or not model:
        return None
    return provider, model


def parse_tts_speed(raw: object) -> float | None:
    """正的有限数值（数字或数字字符串）→ float，其余返回 None。"""

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
    """项目的旁白交付方式；缺失或取值不认识时按后期配音处理。"""

    return USE_TTS if project.get(NARRATION_DELIVERY_FIELD) == USE_TTS else POST_PRODUCTION


def project_tts_settings(project: Mapping[str, object]) -> TtsSynthesisSettings | None:
    """读取项目配音覆盖；无覆盖或字段不完整时返回 None。"""

    backend = parse_tts_backend(project.get(TTS_BACKEND_FIELD))
    voice = project.get(TTS_VOICE_FIELD)
    if backend is None or not isinstance(voice, str) or not voice.strip():
        return None
    raw_speed = project.get(TTS_SPEED_FIELD)
    speed = parse_tts_speed(raw_speed)
    if raw_speed is not None and speed is None:
        return None
    return TtsSynthesisSettings(provider_id=backend[0], model_id=backend[1], voice=voice.strip(), speed=speed)


def tts_group_fields(settings: TtsSynthesisSettings) -> dict[str, object]:
    """把执行组编码成项目配音字段；``narration_speed`` 为 None 时调用方删除该键。"""

    return {
        TTS_BACKEND_FIELD: f"{settings.provider_id}/{settings.model_id}",
        TTS_VOICE_FIELD: settings.voice,
        TTS_SPEED_FIELD: settings.speed,
    }


def _invalid_tts_group_code(project: Mapping[str, object]) -> str:
    if parse_tts_backend(project.get(TTS_BACKEND_FIELD)) is None:
        return "narration_tts_model_required"
    voice = project.get(TTS_VOICE_FIELD)
    if not isinstance(voice, str) or not voice.strip():
        return "narration_tts_voice_required"
    return "narration_tts_speed_invalid"


def validate_project_narration_config(project: Mapping[str, object]) -> None:
    """项目可继承全局配音设置。显式覆盖时须同时指定模型、音色。"""

    if any(k in project for k in TTS_GROUP_FIELDS) and project_tts_settings(project) is None:
        raise NarrationConfigError(_invalid_tts_group_code(project))


def require_project_tts_generation(project: Mapping[str, object]) -> TtsSynthesisSettings:
    """生成旁白配音的前提：项目选了 TTS 配音且快照完整。返回这份快照。"""

    if project_narration_delivery(project) != USE_TTS:
        raise NarrationConfigError("narration_delivery_post_production")
    settings = project_tts_settings(project)
    if settings is None:
        raise NarrationConfigError(_invalid_tts_group_code(project))
    return settings


__all__ = [
    "NARRATION_CONFIG_FIELDS",
    "NARRATION_DELIVERY_FIELD",
    "POST_PRODUCTION",
    "TTS_BACKEND_FIELD",
    "TTS_GROUP_FIELDS",
    "TTS_SPEED_FIELD",
    "TTS_VOICE_FIELD",
    "USE_TTS",
    "NarrationConfigError",
    "NarrationDelivery",
    "TtsSynthesisSettings",
    "parse_tts_backend",
    "parse_tts_speed",
    "project_narration_delivery",
    "project_tts_settings",
    "require_project_tts_generation",
    "tts_group_fields",
    "validate_project_narration_config",
]
