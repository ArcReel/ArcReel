"""全局旁白配置组与音频模型的离线参数声明。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from lib.backends.audio_profiles import AudioProfile, endpoint_profile
from lib.backends.builtin_endpoints import select_provider_endpoint
from lib.backends.providers import is_custom_provider, parse_provider_id
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.repository import SystemSettingRepository
from lib.config.service import ConfigService
from lib.config.url_utils import is_official_openai_base_url
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.infra.api_errors import UnprocessableError
from lib.speech.narration_config import parse_tts_backend

TTS_KEYS = ("default_audio_backend", "narration_voice", "narration_speed")


def builtin_audio_profile(provider: str, model: str) -> AudioProfile:
    _, endpoint = catalog.callable_model_view(provider, model, "audio", submitted=False)
    return endpoint_profile(select_provider_endpoint(provider, "audio", endpoint), model)


async def model_profile(session: AsyncSession, provider: str, model: str) -> AudioProfile:
    if not is_custom_provider(provider):
        return builtin_audio_profile(provider, model)
    repo = CustomProviderRepository(session)
    try:
        db_id = parse_provider_id(provider)
    except ValueError as exc:
        raise UnprocessableError("narration_tts_model_invalid") from exc
    row = await repo.get_model_by_ids(db_id, model)
    if row is None:
        return AudioProfile()
    host = await repo.get_provider(db_id) if row.endpoint == "openai-tts" else None
    return endpoint_profile(
        row.endpoint,
        model,
        custom_provider=True,
        official_host=bool(host and is_official_openai_base_url(host.base_url)),
    )


async def patch_global_tts(svc: ConfigService, session: AsyncSession, patch: dict[str, Any]) -> None:
    if not any(key in patch for key in TTS_KEYS):
        return
    await SystemSettingRepository(session).lock_audio_group()
    saved = await svc.get_all_settings()
    previous = saved.get("default_audio_backend", "")
    backend = (patch.get("default_audio_backend", previous) or "").strip()
    if not backend:
        if "default_audio_backend" not in patch:
            raise UnprocessableError("narration_tts_model_required")
        if (patch.get("narration_voice") or "").strip() or patch.get("narration_speed") is not None:
            raise UnprocessableError("narration_tts_clear_conflict")
        voice, speed = "", None
    else:
        pair = parse_tts_backend(backend)
        if pair is None:
            raise UnprocessableError("narration_tts_model_invalid")
        try:
            profile = await model_profile(session, *pair)
        except ModelNotInCatalogError:
            raise
        except ValueError as exc:
            raise UnprocessableError("narration_tts_model_invalid") from exc
        same = backend == previous
        default_voice = saved.get("narration_voice", "") if same else profile.first_voice
        voice = (patch.get("narration_voice", default_voice) or "").strip()
        speed = patch.get(
            "narration_speed", ConfigService.parse_narration_speed(saved.get("narration_speed", "")) if same else None
        )
        profile.validate(voice, speed)
    for key, value in zip(TTS_KEYS, (backend, voice, "" if speed is None else str(speed)), strict=True):
        await svc.set_setting(key, value)
