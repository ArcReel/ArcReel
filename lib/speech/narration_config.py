"""项目的旁白交付配置（``docs/adr/0089``）。

旁白交付方式是必填的项目配置，取值「TTS 配音」或「后期配音」。TTS 配音项目在 ``project.json``
上保存 TTS 模型、音色与配音语速的快照：创建时以全局默认值预填，之后不再继承全局默认。

快照与交付方式相互独立：改为后期配音后快照保留，已有旁白配音的生成依据照旧可以复算，时效不变；
只有生成旁白配音要求项目当前选的是 TTS 配音。旁白配音的时效按当前快照复算，修改快照后，
按旧设置生成的配音读作 stale。

本模块只依赖标准库，``lib.project`` 等底层模块可以直接引用。
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
#: TTS 快照的三个 project.json 字段。``audio_backend`` 必须是完整的 ``provider/model``；
#: ``narration_speed`` 缺省表示不向供应商传语速。
TTS_BACKEND_FIELD = "audio_backend"
TTS_VOICE_FIELD = "narration_voice"
TTS_SPEED_FIELD = "narration_speed"
NARRATION_CONFIG_FIELDS = (NARRATION_DELIVERY_FIELD, TTS_BACKEND_FIELD, TTS_VOICE_FIELD, TTS_SPEED_FIELD)
#: 自这一项目 schema 起 TTS 快照是旁白配音时效的输入；更早的 schema 没有快照。
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
    """项目的 TTS 快照；模型或音色缺失、语速损坏时返回 None。不读取交付方式。"""

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
    """把 TTS 设置写成 project.json 快照字段；``narration_speed`` 为 None 时调用方删除该键。"""

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
    """TTS 配音项目必须带完整的 TTS 快照。"""

    if project_narration_delivery(project) == USE_TTS and project_tts_settings(project) is None:
        raise NarrationConfigError(_missing_snapshot_code(project))


def require_project_tts_generation(project: Mapping[str, object]) -> TtsSynthesisSettings:
    """生成旁白配音的前提：项目选了 TTS 配音且快照完整。返回这份快照。"""

    if project_narration_delivery(project) != USE_TTS:
        raise NarrationConfigError("narration_delivery_post_production")
    settings = project_tts_settings(project)
    if settings is None:
        raise NarrationConfigError(_missing_snapshot_code(project))
    return settings


class ProjectTtsSettingsResolver:
    """按项目快照回答当前 TTS 设置；快照不完整时抛 ValueError（读侧视为未配置）。"""

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
