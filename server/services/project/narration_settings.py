"""新建项目的旁白交付配置（docs/adr/0089）。

Web 创建向导与 Agent 建项工具共用：TTS 配音项目省略的模型、音色与配音语速以全局默认预填，
写入 project.json 后成为项目快照，此后全局默认的变化不再影响这个项目。
"""

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
    """建项请求里的旁白配置。``provided`` 列出请求显式给出的 TTS 字段，其余字段按全局默认预填。"""

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


def validate_tts_speed(value: float) -> float:
    speed = parse_tts_speed(value)
    if speed is None:
        raise NarrationConfigError("narration_tts_speed_invalid")
    return speed


async def _voice_for_backend(resolver: ConfigResolver, provider_id: str, model_id: str, voice: str) -> str:
    """让全局音色与项目实际使用的 TTS 模型相符。

    全局音色只有一份，默认音频模型却可能按注册表顺序自动推断到另一家供应商（缺省音色 Cherry
    只属于 DashScope）。音色不在所选内置模型的音色表中时，改用表中第一个音色。自定义供应商的
    音色表不一定完整，音色原样保留；音色表取不到时同样原样保留，合成时照常报错。
    """
    if is_custom_provider(provider_id):
        return voice
    try:
        voice_ids = await audio_backend_voice_ids(provider_id, model_id, resolver)
    except Exception as exc:
        # 预填只是尽力而为：缺 API Key、Vertex 凭证文件不可读等构造失败都不能阻断建项
        logger.warning("读取音色表失败，旁白音色按全局设置预填 backend=%s/%s：%s", provider_id, model_id, exc)
        return voice
    if voice_ids and voice not in voice_ids:
        return voice_ids[0]
    return voice


async def default_narration_tts(resolver: ConfigResolver) -> tuple[ProviderModel, str, float | None]:
    """新建 TTS 项目的预填值：全局默认音频模型、与之相符的旁白音色与配音语速。

    未配置任何音频供应商时抛 ValueError。
    """
    backend, voice, speed = await resolver.default_narration_tts()
    return backend, await _voice_for_backend(resolver, backend.provider_id, backend.model_id, voice), speed


async def new_project_narration_fields(
    value: NarrationSettingsInput,
    *,
    resolver: ConfigResolver,
) -> dict[str, object]:
    """算出新项目要写入 project.json 的旁白配置字段。"""

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
            # 音色按实际写入的模型校正，显式给出的模型可能与全局默认模型不是同一家
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
