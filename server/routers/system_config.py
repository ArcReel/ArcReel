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
from lib.config.service import ConfigService
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
        "video": ["veo-3.1-generate-001", "veo-3.1-fast-generate-001"],
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
        "default_video_backend": all_s.get("default_video_backend") or None,
        "default_image_backend": all_s.get("default_image_backend") or None,
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
    if "default_video_backend" in patch and patch["default_video_backend"]:
        value = str(patch["default_video_backend"]).strip()
        if "/" not in value:
            raise HTTPException(
                status_code=400,
                detail="default_video_backend 格式应为 provider/model",
            )
        await svc.set_setting("default_video_backend", value)

    if "default_image_backend" in patch and patch["default_image_backend"]:
        value = str(patch["default_image_backend"]).strip()
        if "/" not in value:
            raise HTTPException(
                status_code=400,
                detail="default_image_backend 格式应为 provider/model",
            )
        await svc.set_setting("default_image_backend", value)

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

    # Return updated config
    return await get_system_config(_user=_user, svc=svc)
