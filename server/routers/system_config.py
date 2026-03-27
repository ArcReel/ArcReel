"""
System configuration APIs.

Handles non-provider global settings: default backends, audio, anthropic config.
Provider-specific configuration (API keys, rate limits, credentials, connection test)
is managed by the providers router.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import (
    ConfigService,
    sync_anthropic_env,
    _DEFAULT_IMAGE_BACKEND,
    _DEFAULT_VIDEO_BACKEND,
    _DEFAULT_TEXT_BACKEND,
)
from lib.db import get_async_session
from server.auth import CurrentUser
from server.dependencies import get_config_service

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _build_options(svc: ConfigService) -> dict[str, list[str]]:
    """Compute available backends from ready providers."""
    statuses = await svc.get_all_providers_status()
    ready_providers = {s.name for s in statuses if s.status == "ready"}

    video_backends: list[str] = []
    image_backends: list[str] = []
    text_backends: list[str] = []
    for provider_id, meta in PROVIDER_REGISTRY.items():
        if provider_id not in ready_providers:
            continue
        for model_id, model_info in meta.models.items():
            full = f"{provider_id}/{model_id}"
            if model_info.media_type == "video":
                video_backends.append(full)
            elif model_info.media_type == "image":
                image_backends.append(full)
            elif model_info.media_type == "text":
                text_backends.append(full)

    return {
        "video_backends": video_backends,
        "image_backends": image_backends,
        "text_backends": text_backends,
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SystemConfigPatchRequest(BaseModel):
    default_video_backend: Optional[str] = None
    default_image_backend: Optional[str] = None
    default_text_backend: Optional[str] = None
    video_generate_audio: Optional[bool] = None
    anthropic_api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None
    anthropic_model: Optional[str] = None
    anthropic_default_haiku_model: Optional[str] = None
    anthropic_default_opus_model: Optional[str] = None
    anthropic_default_sonnet_model: Optional[str] = None
    claude_code_subagent_model: Optional[str] = None
    agent_session_cleanup_delay_seconds: Optional[int] = None
    agent_max_concurrent_sessions: Optional[int] = None
    text_backend_script: Optional[str] = None
    text_backend_overview: Optional[str] = None
    text_backend_style: Optional[str] = None


# Setting keys that map directly to string DB settings
_STRING_SETTINGS = (
    "anthropic_base_url",
    "anthropic_model",
    "anthropic_default_haiku_model",
    "anthropic_default_opus_model",
    "anthropic_default_sonnet_model",
    "claude_code_subagent_model",
    "text_backend_script",
    "text_backend_overview",
    "text_backend_style",
)


# ---------------------------------------------------------------------------
# GET /system/config
# ---------------------------------------------------------------------------


@router.get("/system/config")
async def get_system_config(
    _user: CurrentUser,
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> dict[str, Any]:
    # Read all settings in a single query
    all_s = await svc.get_all_settings()
    video_generate_audio_raw = all_s.get("video_generate_audio", "false")
    video_generate_audio = video_generate_audio_raw.lower() in ("true", "1", "yes")
    anthropic_key = all_s.get("anthropic_api_key", "")

    settings: dict[str, Any] = {
        "default_video_backend": all_s.get("default_video_backend") or _DEFAULT_VIDEO_BACKEND,
        "default_image_backend": all_s.get("default_image_backend") or _DEFAULT_IMAGE_BACKEND,
        "default_text_backend": all_s.get("default_text_backend") or _DEFAULT_TEXT_BACKEND,
        "video_generate_audio": video_generate_audio,
        "anthropic_api_key": {
            "is_set": bool(anthropic_key),
            "masked": mask_secret(anthropic_key) if anthropic_key else None,
        },
        "anthropic_base_url": all_s.get("anthropic_base_url") or None,
        "anthropic_model": all_s.get("anthropic_model") or None,
        "anthropic_default_haiku_model": all_s.get("anthropic_default_haiku_model") or None,
        "anthropic_default_opus_model": all_s.get("anthropic_default_opus_model") or None,
        "anthropic_default_sonnet_model": all_s.get("anthropic_default_sonnet_model") or None,
        "claude_code_subagent_model": all_s.get("claude_code_subagent_model") or None,
        "agent_session_cleanup_delay_seconds": int(all_s.get("agent_session_cleanup_delay_seconds") or "300"),
        "agent_max_concurrent_sessions": int(all_s.get("agent_max_concurrent_sessions") or "5"),
        "text_backend_script": all_s.get("text_backend_script") or "",
        "text_backend_overview": all_s.get("text_backend_overview") or "",
        "text_backend_style": all_s.get("text_backend_style") or "",
    }

    options = await _build_options(svc)

    return {"settings": settings, "options": options}


# ---------------------------------------------------------------------------
# PATCH /system/config
# ---------------------------------------------------------------------------


@router.patch("/system/config")
async def patch_system_config(
    req: SystemConfigPatchRequest,
    _user: CurrentUser,
    svc: Annotated[ConfigService, Depends(get_config_service)],
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for field_name in req.model_fields_set:
        patch[field_name] = getattr(req, field_name)

    # Validate backend references
    for backend_key in ("default_video_backend", "default_image_backend", "default_text_backend"):
        if backend_key in patch and patch[backend_key]:
            value = str(patch[backend_key]).strip()
            if "/" not in value:
                raise HTTPException(
                    status_code=400,
                    detail=f"{backend_key} 格式应为 provider/model",
                )
            provider_id = value.split("/", 1)[0]
            if provider_id not in PROVIDER_REGISTRY:
                raise HTTPException(
                    status_code=400,
                    detail=f"未知供应商: {provider_id}",
                )
            await svc.set_setting(backend_key, value)

    # Boolean settings
    if "video_generate_audio" in patch and patch["video_generate_audio"] is not None:
        await svc.set_setting(
            "video_generate_audio", "true" if patch["video_generate_audio"] else "false"
        )

    # Anthropic API key (secret)
    if "anthropic_api_key" in patch:
        value = patch["anthropic_api_key"]
        if value:
            await svc.set_setting("anthropic_api_key", str(value).strip())
        else:
            await svc.set_setting("anthropic_api_key", "")

    # Integer settings with range validation
    _INT_SETTINGS_RANGES = {
        "agent_session_cleanup_delay_seconds": (10, 3600),
        "agent_max_concurrent_sessions": (1, 20),
    }
    for key, (min_val, max_val) in _INT_SETTINGS_RANGES.items():
        if key in patch and patch[key] is not None:
            value = int(patch[key])
            if not (min_val <= value <= max_val):
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} 应在 {min_val}-{max_val} 之间",
                )
            await svc.set_setting(key, str(value))

    # String settings
    for key in _STRING_SETTINGS:
        if key in patch:
            value = patch[key]
            await svc.set_setting(key, str(value).strip() if value else "")

    await session.commit()

    # Sync Anthropic settings to env vars so Claude Agent SDK picks them up
    all_settings = await svc.get_all_settings()
    sync_anthropic_env(all_settings)

    # Return updated config
    return await get_system_config(_user=_user, svc=svc)
