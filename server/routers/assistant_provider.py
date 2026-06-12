"""Assistant provider configuration API.

Manages the assistant provider settings (Claude Agent SDK vs LiteLLM vs OpenAI-compatible).
Settings are persisted in the ``system_settings`` table.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.i18n import Translator
from server.auth import CurrentUser
from server.dependencies import get_config_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Setting keys in system_settings table
_ASSISTANT_PROVIDER_KEY = "assistant_provider"
_LITELLM_MODEL_KEY = "litellm_model"
_LITELLM_API_KEY_KEY = "litellm_api_key"
_LITELLM_BASE_URL_KEY = "litellm_base_url"
_LITELLM_MAX_TOOL_ROUNDS_KEY = "litellm_max_tool_rounds"
_OPENAI_MODEL_KEY = "openai_model"
_OPENAI_API_KEY_KEY = "openai_api_key"
_OPENAI_BASE_URL_KEY = "openai_base_url"

# Popular model presets for the frontend dropdown
LITELLM_MODEL_PRESETS: list[dict[str, str]] = [
    # OpenAI
    {"id": "openai/gpt-4o", "name": "GPT-4o", "provider": "OpenAI"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "provider": "OpenAI"},
    {"id": "openai/o3-mini", "name": "o3-mini", "provider": "OpenAI"},
    {"id": "openai/gpt-5.4", "name": "GPT-5.4", "provider": "OpenAI"},
    # Anthropic
    {"id": "anthropic/claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "provider": "Anthropic"},
    {"id": "anthropic/claude-haiku-4-5", "name": "Claude Haiku 4.5", "provider": "Anthropic"},
    {"id": "anthropic/claude-opus-4-6", "name": "Claude Opus 4.6", "provider": "Anthropic"},
    # Google
    {"id": "gemini/gemini-2.0-flash", "name": "Gemini 2.0 Flash", "provider": "Google"},
    {"id": "gemini/gemini-2.5-pro-preview-05-06", "name": "Gemini 2.5 Pro", "provider": "Google"},
    # Groq
    {"id": "groq/llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "provider": "Groq"},
    {"id": "groq/llama-4-scout-17b-16e-instruct", "name": "Llama 4 Scout", "provider": "Groq"},
    # DeepSeek
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek V3", "provider": "DeepSeek"},
    {"id": "deepseek/deepseek-reasoner", "name": "DeepSeek R1", "provider": "DeepSeek"},
    # Mistral
    {"id": "mistral/mistral-large-latest", "name": "Mistral Large", "provider": "Mistral"},
    {"id": "mistral/codestral-latest", "name": "Codestral", "provider": "Mistral"},
    # OpenRouter
    {"id": "openrouter/meta-llama/llama-4-maverick", "name": "Llama 4 Maverick", "provider": "OpenRouter"},
    {"id": "openrouter/qwen/qwen-2.5-72b-instruct", "name": "Qwen 2.5 72B", "provider": "OpenRouter"},
    # xAI
    {"id": "xai/grok-3", "name": "Grok 3", "provider": "xAI"},
    # Xiaomi MiMo
    {"id": "xiaomi-mimo/mimo-v2.5-pro", "name": "MiMo v2.5 Pro", "provider": "Xiaomi"},
    # Zhipu
    {"id": "zhipu/glm-5.1", "name": "GLM 5.1", "provider": "Zhipu"},
]


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class AssistantProviderConfig(BaseModel):
    """Current assistant provider configuration."""

    provider: Literal["claude", "litellm", "openai"] = "claude"
    litellm_model: str | None = None
    litellm_api_key_set: bool = False
    litellm_api_key_masked: str | None = None
    litellm_base_url: str | None = None
    litellm_max_tool_rounds: int = 20
    openai_model: str | None = None
    openai_api_key_set: bool = False
    openai_api_key_masked: str | None = None
    openai_base_url: str | None = None


class UpdateAssistantProviderRequest(BaseModel):
    """Request to update assistant provider settings."""

    provider: Literal["claude", "litellm", "openai"] | None = None
    litellm_model: str | None = None
    litellm_api_key: str | None = None
    litellm_base_url: str | None = None
    litellm_max_tool_rounds: int | None = None
    openai_model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None


class TestConnectionRequest(BaseModel):
    """Request to test provider connection."""

    model: str
    api_key: str | None = None
    base_url: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/assistant-provider")
async def get_assistant_provider(
    _user: CurrentUser,
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> dict[str, Any]:
    """Get current assistant provider configuration.

    Priority: DB settings > .env fallback > defaults.
    """
    settings = await svc.get_all_settings()

    provider = settings.get(_ASSISTANT_PROVIDER_KEY, "claude") or "claude"

    # LiteLLM — DB first, then .env fallback
    litellm_api_key = settings.get(_LITELLM_API_KEY_KEY, "") or os.environ.get("LITELLM_API_KEY", "")
    litellm_base_url = settings.get(_LITELLM_BASE_URL_KEY, "") or os.environ.get("LITELLM_BASE_URL", "")
    litellm_model = settings.get(_LITELLM_MODEL_KEY, "") or os.environ.get("LITELLM_MODEL", "")

    # OpenAI-compatible — DB first, then .env fallback
    openai_api_key = settings.get(_OPENAI_API_KEY_KEY, "") or os.environ.get("OPENAI_API_KEY", "")
    openai_base_url = settings.get(_OPENAI_BASE_URL_KEY, "") or os.environ.get("OPENAI_BASE_URL", "")
    openai_model = settings.get(_OPENAI_MODEL_KEY, "") or os.environ.get("OPENAI_MODEL", "")

    return {
        "provider": provider,
        # LiteLLM
        "litellm_model": litellm_model or None,
        "litellm_api_key_set": bool(litellm_api_key),
        "litellm_api_key_masked": _mask_key(litellm_api_key) if litellm_api_key else None,
        "litellm_base_url": litellm_base_url or None,
        "litellm_max_tool_rounds": int(settings.get(_LITELLM_MAX_TOOL_ROUNDS_KEY, "20") or "20"),
        # OpenAI-compatible
        "openai_model": openai_model or None,
        "openai_api_key_set": bool(openai_api_key),
        "openai_api_key_masked": _mask_key(openai_api_key) if openai_api_key else None,
        "openai_base_url": openai_base_url or None,
        # Model presets
        "model_presets": LITELLM_MODEL_PRESETS,
    }


@router.put("/assistant-provider")
async def update_assistant_provider(
    body: UpdateAssistantProviderRequest,
    _user: CurrentUser,
    svc: Annotated[ConfigService, Depends(get_config_service)],
    _t: Translator,
) -> dict[str, str]:
    """Update assistant provider settings."""
    updates: list[str] = []

    if body.provider is not None:
        await svc.set_setting(_ASSISTANT_PROVIDER_KEY, body.provider)
        updates.append(f"provider={body.provider}")

    # LiteLLM fields
    if body.litellm_model is not None:
        await svc.set_setting(_LITELLM_MODEL_KEY, body.litellm_model)
        updates.append(f"litellm_model={body.litellm_model}")

    if body.litellm_api_key is not None:
        await svc.set_setting(_LITELLM_API_KEY_KEY, body.litellm_api_key)
        updates.append("litellm_api_key=***")

    if body.litellm_base_url is not None:
        await svc.set_setting(_LITELLM_BASE_URL_KEY, body.litellm_base_url)
        updates.append(f"litellm_base_url={body.litellm_base_url}")

    if body.litellm_max_tool_rounds is not None:
        await svc.set_setting(_LITELLM_MAX_TOOL_ROUNDS_KEY, str(body.litellm_max_tool_rounds))
        updates.append(f"litellm_max_tool_rounds={body.litellm_max_tool_rounds}")

    # OpenAI-compatible fields
    if body.openai_model is not None:
        await svc.set_setting(_OPENAI_MODEL_KEY, body.openai_model)
        updates.append(f"openai_model={body.openai_model}")

    if body.openai_api_key is not None:
        await svc.set_setting(_OPENAI_API_KEY_KEY, body.openai_api_key)
        updates.append("openai_api_key=***")

    if body.openai_base_url is not None:
        await svc.set_setting(_OPENAI_BASE_URL_KEY, body.openai_base_url)
        updates.append(f"openai_base_url={body.openai_base_url}")

    if not updates:
        raise HTTPException(status_code=400, detail=_t("no_fields_to_update"))

    logger.info("Assistant provider config updated: %s", ", ".join(updates))
    return {"status": "ok", "updated": ", ".join(updates)}


@router.post("/assistant-provider/test")
async def test_connection(
    body: TestConnectionRequest,
    _user: CurrentUser,
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> dict[str, Any]:
    """Test provider connection by sending a simple completion request."""
    import litellm

    api_key = body.api_key
    if not api_key:
        settings = await svc.get_all_settings()
        api_key = settings.get(_LITELLM_API_KEY_KEY, "") or os.environ.get("LITELLM_API_KEY", "")
    # API key is optional — some servers (Ollama, local) don't require auth
    # litellm will handle empty api_key gracefully

    try:
        kwargs: dict[str, Any] = {
            "model": body.model,
            "messages": [{"role": "user", "content": "Say 'OK' in one word."}],
            "max_tokens": 10,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if body.base_url:
            kwargs["api_base"] = body.base_url

        response = await litellm.acompletion(**kwargs)
        content = response.choices[0].message.content if response.choices else ""
        return {
            "status": "ok",
            "model": body.model,
            "response": content.strip(),
        }
    except Exception as exc:
        logger.warning("Provider connection test failed: %s", exc)
        return {
            "status": "error",
            "error": str(exc)[:500],
        }


class DiscoverModelsRequest(BaseModel):
    """Request to discover models from an OpenAI-compatible endpoint."""

    base_url: str
    api_key: str | None = None


@router.post("/assistant-provider/discover")
async def discover_models(
    body: DiscoverModelsRequest,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Discover available models from an OpenAI-compatible /v1/models endpoint."""
    import httpx

    url = body.base_url.rstrip("/")
    # Avoid duplicating /v1 — if URL already ends with /v1, just append /models
    if url.endswith("/v1/models"):
        pass  # already correct
    elif url.endswith("/v1"):
        url = f"{url}/models"
    else:
        url = f"{url}/v1/models"

    try:
        headers = {}
        if body.api_key:
            headers["Authorization"] = f"Bearer {body.api_key}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        models = [m["id"] for m in data.get("data", []) if "id" in m]
        models.sort()
        return {"status": "ok", "models": models}
    except Exception as exc:
        logger.warning("Model discovery failed: %s", exc)
        return {"status": "error", "error": str(exc)[:500], "models": []}


def _mask_key(key: str) -> str:
    """Mask an API key for display."""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"
