"""
Custom provider management API.

Provides endpoints for custom provider CRUD, model management, model discovery, and connection testing.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.custom_provider import make_provider_id
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-providers", tags=["Custom Providers"])

_CONNECTION_TEST_TIMEOUT = 15  # seconds

_BACKEND_SETTING_KEYS = (
    "default_video_backend",
    "default_image_backend",
    "default_text_backend",
    "text_backend_script",
    "text_backend_overview",
    "text_backend_style",
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ModelInput(BaseModel):
    model_id: str
    display_name: str
    media_type: str  # "text" | "image" | "video"
    is_default: bool = False
    is_enabled: bool = True
    price_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None
    supported_durations: list[int] | None = None

    @model_validator(mode="after")
    def _check_price_consistency(self):
        if self.price_output is not None and self.price_input is None:
            raise ValueError("price_input must also be set when price_output is provided")
        return self

    def to_db_dict(self) -> dict:
        """Return a dict suitable for writing to the database (supported_durations serialized as a JSON string)."""
        d = self.model_dump()
        d["supported_durations"] = (
            json.dumps(self.supported_durations) if self.supported_durations is not None else None
        )
        return d


class CreateProviderRequest(BaseModel):
    display_name: str
    api_format: str  # "openai" or "google"
    base_url: str
    api_key: str
    models: list[ModelInput] = []


class UpdateProviderRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class FullUpdateProviderRequest(BaseModel):
    """PUT full update: provider metadata + model list in the same transaction."""

    display_name: str
    base_url: str
    api_key: str | None = None  # None = do not modify
    models: list[ModelInput]


class ProviderConnectionRequest(BaseModel):
    api_format: str
    base_url: str
    api_key: str


class ReplaceModelsRequest(BaseModel):
    models: list[ModelInput]


class ModelResponse(BaseModel):
    id: int
    model_id: str
    display_name: str
    media_type: str
    is_default: bool
    is_enabled: bool
    price_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None
    supported_durations: list[int] | None = None


class ProviderResponse(BaseModel):
    id: int
    display_name: str
    api_format: str
    base_url: str
    api_key_masked: str
    models: list[ModelResponse]
    created_at: str | None = None


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class DiscoverResponse(BaseModel):
    models: list[dict]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _model_to_response(m) -> ModelResponse:
    durations = json.loads(m.supported_durations) if m.supported_durations else None
    return ModelResponse(
        id=m.id,
        model_id=m.model_id,
        display_name=m.display_name,
        media_type=m.media_type,
        is_default=m.is_default,
        is_enabled=m.is_enabled,
        price_unit=m.price_unit,
        price_input=m.price_input,
        price_output=m.price_output,
        currency=m.currency,
        supported_durations=durations,
    )


def _provider_to_response(provider, models) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        display_name=provider.display_name,
        api_format=provider.api_format,
        base_url=provider.base_url,
        api_key_masked=mask_secret(provider.api_key),
        models=[_model_to_response(m) for m in models],
        created_at=dt_to_iso(provider.created_at),
    )


def _cleanup_project_refs(prefix: str, setting_keys: tuple[str, ...]) -> None:
    """After deleting a provider, clean up dangling references in all project.json files."""
    from lib.config.resolver import get_project_manager

    pm = get_project_manager()
    for proj_name in pm.list_projects():
        try:

            def _mutate(p: dict, _prefix=prefix, _keys=setting_keys) -> None:
                for key in _keys:
                    val = p.get(key, "")
                    if isinstance(val, str) and val.startswith(_prefix):
                        p.pop(key, None)

            pm.update_project(proj_name, _mutate)
        except Exception:
            pass  # Read failed or project is not writable; skip (non-fatal)


def _check_duplicate_model_ids(models: list[ModelInput]) -> None:
    """Validate that the model list has no duplicate model_ids and that enabled models have a valid model_id."""
    seen: set[str] = set()
    for m in models:
        if m.is_enabled and not m.model_id.strip():
            raise HTTPException(status_code=422, detail="Enabled models must have a model_id")
        if m.model_id in seen:
            raise HTTPException(status_code=422, detail=f"Duplicate model_id: {m.model_id}")
        if m.model_id:
            seen.add(m.model_id)


def _check_unique_defaults(models: list[ModelInput]) -> None:
    """Validate that at most one model per media_type has is_default=True."""
    defaults_by_type: dict[str, list[str]] = {}
    for m in models:
        if m.is_default:
            defaults_by_type.setdefault(m.media_type, []).append(m.model_id)
    duplicates = {mt: ids for mt, ids in defaults_by_type.items() if len(ids) > 1}
    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=f"Each media_type can have at most one default model; conflicts: {'; '.join(parts)}",
        )


async def _invalidate_caches(request: Request) -> None:
    """Flush backend instance cache and reload worker rate-limit config."""
    from server.services.generation_tasks import invalidate_backend_cache

    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_providers(
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """List all custom providers (including model lists)."""
    repo = CustomProviderRepository(session)
    pairs = await repo.list_providers_with_models()
    return {"providers": [_provider_to_response(p, models) for p, models in pairs]}


@router.post("", status_code=201)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """Create a custom provider, optionally creating a model list at the same time."""
    if body.models:
        _check_duplicate_model_ids(body.models)
        _check_unique_defaults(body.models)
    repo = CustomProviderRepository(session)
    model_dicts = [m.to_db_dict() for m in body.models] if body.models else None
    provider = await repo.create_provider(
        display_name=body.display_name,
        api_format=body.api_format,
        base_url=body.base_url,
        api_key=body.api_key,
        models=model_dicts,
    )
    await session.commit()
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider.id)
    return _provider_to_response(provider, models)


@router.get("/{provider_id}")
async def get_provider(
    provider_id: int,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """Get details for a single custom provider."""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider does not exist")
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models)


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int,
    body: UpdateProviderRequest,
    request: Request,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """Update custom provider configuration."""
    repo = CustomProviderRepository(session)
    kwargs = {}
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if body.base_url is not None:
        kwargs["base_url"] = body.base_url
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key

    if not kwargs:
        raise HTTPException(status_code=400, detail="At least one update field must be provided")

    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider does not exist")

    await session.commit()
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models)


@router.put("/{provider_id}")
async def full_update_provider(
    provider_id: int,
    body: FullUpdateProviderRequest,
    request: Request,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """Atomically update provider metadata + model list in a single transaction."""
    _check_duplicate_model_ids(body.models)
    _check_unique_defaults(body.models)
    repo = CustomProviderRepository(session)
    kwargs: dict = {"display_name": body.display_name, "base_url": body.base_url}
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider does not exist")
    model_dicts = [m.to_db_dict() for m in body.models]
    await repo.replace_models(provider_id, model_dicts)
    await session.commit()
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    request: Request,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a custom provider (cascades to models, cleans up dangling default config)."""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider does not exist")
    prefix = f"{make_provider_id(provider_id)}/"
    await repo.delete_provider(provider_id)
    # Clean up global default backend config that references this provider
    from lib.config.service import ConfigService

    svc = ConfigService(session)
    for key in _BACKEND_SETTING_KEYS:
        val = await svc.get_setting(key, "")
        if val and val.startswith(prefix):
            await svc.set_setting(key, "")
    await session.commit()
    await _invalidate_caches(request)
    # Clean up project-level config that references this provider (sync file I/O; offloaded to thread pool to avoid blocking the event loop)
    await asyncio.to_thread(_cleanup_project_refs, prefix, _BACKEND_SETTING_KEYS)


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------


@router.put("/{provider_id}/models")
async def replace_models(
    provider_id: int,
    body: ReplaceModelsRequest,
    request: Request,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """Replace the entire model list for a provider."""
    _check_duplicate_model_ids(body.models)
    _check_unique_defaults(body.models)
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider does not exist")
    # Record old model IDs for cleaning up dangling references
    old_models = await repo.list_models(provider_id)
    old_model_ids = {m.model_id for m in old_models}
    new_model_ids = {m.model_id for m in body.models}
    deleted_model_ids = old_model_ids - new_model_ids

    model_dicts = [m.to_db_dict() for m in body.models]
    new_models = await repo.replace_models(provider_id, model_dicts)

    # Clean up global config entries referencing deleted models
    if deleted_model_ids:
        from lib.config.service import ConfigService

        svc = ConfigService(session)
        prefix = f"{make_provider_id(provider_id)}/"
        for key in _BACKEND_SETTING_KEYS:
            val = await svc.get_setting(key, "")
            if val and val.startswith(prefix):
                _, model_part = val.split("/", 1)
                if model_part in deleted_model_ids:
                    await svc.set_setting(key, "")

    await session.commit()
    await _invalidate_caches(request)
    return [_model_to_response(m) for m in new_models]


# ---------------------------------------------------------------------------
# Stateless operations
# ---------------------------------------------------------------------------


@router.post("/discover")
async def discover_models_endpoint(
    body: ProviderConnectionRequest,
    _user: CurrentUser,
):
    """Model discovery: query available models using api_format + base_url + api_key."""
    from lib.custom_provider.discovery import discover_models

    try:
        models = await discover_models(
            api_format=body.api_format,
            base_url=body.base_url or None,
            api_key=body.api_key,
        )
        return DiscoverResponse(models=models)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("Model discovery failed: %s", err_msg)
        raise HTTPException(status_code=502, detail=f"Model discovery failed: {err_msg}")


@router.post("/test")
async def test_connection(
    body: ProviderConnectionRequest,
    _user: CurrentUser,
):
    """Connection test: verify connectivity for api_format + base_url + api_key."""
    return await _run_connection_test(body.api_format, body.base_url, body.api_key)


@router.post("/{provider_id}/test")
async def test_connection_by_id(
    provider_id: int,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """Test connectivity for the specified provider using stored credentials."""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider does not exist")
    return await _run_connection_test(provider.api_format, provider.base_url, provider.api_key)


async def _run_connection_test(api_format: str, base_url: str, api_key: str) -> ConnectionTestResponse:
    """Shared connection test logic."""
    try:
        if api_format == "openai":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_openai, base_url, api_key),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        elif api_format == "google":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_google, base_url, api_key),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        else:
            return ConnectionTestResponse(
                success=False,
                message=f"Unsupported api_format: {api_format}",
            )
        return result
    except TimeoutError:
        return ConnectionTestResponse(
            success=False,
            message="Connection timed out; please check your network or API configuration",
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("Connection test failed [%s]: %s", api_format, err_msg)
        return ConnectionTestResponse(
            success=False,
            message=f"Connection failed: {err_msg}",
        )


def _test_openai(base_url: str, api_key: str) -> ConnectionTestResponse:
    """Validate an OpenAI-compatible API via models.list()."""
    from openai import OpenAI

    from lib.config.url_utils import ensure_openai_base_url

    client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
    models = client.models.list()
    count = sum(1 for _ in models)
    return ConnectionTestResponse(
        success=True,
        message="Connection successful",
        model_count=count,
    )


def _test_google(base_url: str, api_key: str) -> ConnectionTestResponse:
    """Validate a Google genai API via models.list()."""
    from google import genai

    from lib.config.url_utils import ensure_google_base_url

    effective_url = ensure_google_base_url(base_url)
    http_options = {"base_url": effective_url} if effective_url else None
    client = genai.Client(api_key=api_key, http_options=http_options)
    pager = client.models.list()
    count = sum(1 for _ in pager)
    return ConnectionTestResponse(
        success=True,
        message="Connection successful",
        model_count=count,
    )
