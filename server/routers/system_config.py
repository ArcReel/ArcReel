"""
System configuration APIs.

Handles non-provider global settings: default backends, audio, anthropic config.
Provider-specific configuration (API keys, rate limits, credentials, connection test)
is managed by the providers router.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import (
    ConfigService,
    _DEFAULT_IMAGE_BACKEND,
    _DEFAULT_VIDEO_BACKEND,
)
from lib.db import get_async_session
from server.auth import get_current_user
from server.dependencies import get_config_service

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Provider → model mapping (hardcoded for now, will be dynamic later)
# ---------------------------------------------------------------------------

_PROVIDER_MODELS: dict[str, dict[str, list[str]]] = {
    "gemini-aistudio": {
        "video": ["veo-3.1-generate-preview", "veo-3.1-fast-generate-preview"],
        "image": ["gemini-3.1-flash-image-preview"],
    },
    "gemini-vertex": {
        "video": ["veo-3.1-generate-001", "veo-3.1-fast-generate-001"],
        "image": ["gemini-3.1-flash-image-preview"],
    },
    "seedance": {
        "video": ["doubao-seedance-1-5-pro-251215"],
        "image": [],
    },
    "grok": {
        "video": ["grok-imagine-video"],
        "image": [],
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _build_options(svc: ConfigService) -> dict[str, list[str]]:
    """Compute available backends from ready providers."""
    statuses = await svc.get_all_providers_status()
    ready_providers = {s.name for s in statuses if s.status == "ready"}

    video_backends: list[str] = []
    image_backends: list[str] = []
    for provider_id, models in _PROVIDER_MODELS.items():
        if provider_id not in ready_providers:
            continue
        for model in models.get("video", []):
            video_backends.append(f"{provider_id}/{model}")
        for model in models.get("image", []):
            image_backends.append(f"{provider_id}/{model}")

    return {
        "video_backends": video_backends,
        "image_backends": image_backends,
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SystemConfigPatchRequest(BaseModel):
    default_video_backend: Optional[str] = None
    default_image_backend: Optional[str] = None
    video_generate_audio: Optional[bool] = None
    anthropic_api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None
    anthropic_model: Optional[str] = None
    anthropic_default_haiku_model: Optional[str] = None
    anthropic_default_opus_model: Optional[str] = None
    anthropic_default_sonnet_model: Optional[str] = None
    claude_code_subagent_model: Optional[str] = None


# Setting keys that map directly to string DB settings
_STRING_SETTINGS = (
    "anthropic_base_url",
    "anthropic_model",
    "anthropic_default_haiku_model",
    "anthropic_default_opus_model",
    "anthropic_default_sonnet_model",
    "claude_code_subagent_model",
)

# DB setting key → environment variable name
_ANTHROPIC_ENV_MAP: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_base_url": "ANTHROPIC_BASE_URL",
    "anthropic_model": "ANTHROPIC_MODEL",
    "anthropic_default_haiku_model": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "anthropic_default_opus_model": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "anthropic_default_sonnet_model": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "claude_code_subagent_model": "CLAUDE_CODE_SUBAGENT_MODEL",
}


def _sync_anthropic_env(all_settings: dict[str, str]) -> None:
    """Sync Anthropic-related DB settings to environment variables.

    The Claude Agent SDK reads config from os.environ, so DB values
    must be mirrored to env vars for the SDK to pick them up.
    """
    for db_key, env_key in _ANTHROPIC_ENV_MAP.items():
        value = all_settings.get(db_key, "").strip()
        if value:
            os.environ[env_key] = value
        else:
            os.environ.pop(env_key, None)

# ---------------------------------------------------------------------------
# GET /system/config
# ---------------------------------------------------------------------------


@router.get("/system/config")
async def get_system_config(
    _user: Annotated[dict, Depends(get_current_user)],
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
    }

    options = await _build_options(svc)

    return {"settings": settings, "options": options}


# ---------------------------------------------------------------------------
# PATCH /system/config
# ---------------------------------------------------------------------------


@router.patch("/system/config")
async def patch_system_config(
    req: SystemConfigPatchRequest,
    _user: Annotated[dict, Depends(get_current_user)],
    svc: Annotated[ConfigService, Depends(get_config_service)],
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for field_name in req.model_fields_set:
        patch[field_name] = getattr(req, field_name)

    # Validate backend references
    for backend_key in ("default_video_backend", "default_image_backend"):
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

    # String settings
    for key in _STRING_SETTINGS:
        if key in patch:
            value = patch[key]
            await svc.set_setting(key, str(value).strip() if value else "")

    await session.commit()

    # Sync Anthropic settings to env vars so Claude Agent SDK picks them up
    all_settings = await svc.get_all_settings()
    _sync_anthropic_env(all_settings)

    # Return updated config
    return await get_system_config(_user=_user, svc=svc)
