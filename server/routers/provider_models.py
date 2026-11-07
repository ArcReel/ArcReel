"""Provider-model API: list by provider, read one, save provider settings, enable/disable, and remove; built-in providers also support adding by ID (docs/adr/0099).

Shared by built-in/custom providers. Reading/saving corresponds to the provider-model Sheet's
Provider settings: officially listed models expose enablement only. User-managed models (custom
providers and user-added built-in models) additionally expose model ID, provider-model multiplier,
and capability overrides. Custom models also expose default status, endpoint, request URLs, and default resolution.

The Sheet's lower Model information section is the matched model entry, shared by all providers
using it. Reads include maintainer, official values, and other sharing providers. Saves atomically
persist it with provider settings in one request, storing changes as a whole-entry user copy
(docs/adr/0099). User-managed models can also associate another model (entry binding, docs/adr/0105)
or manually describe unlisted models (create and bind a new entry)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, assert_never, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import (
    MEDIA_TYPES,
    AudioFacts,
    CatalogEntry,
    ImageFacts,
    MediaType,
    TextFacts,
    VideoFacts,
    dump_entry,
)
from lib.backends.backend_assembly.specs import PROVIDER_ONLY_ENDPOINTS, builtin_model_endpoint
from lib.billing.multipliers import get_provider_multiplier
from lib.catalog import builtin_discovery, builtin_models
from lib.catalog import query as catalog
from lib.catalog.composition import EntrySource
from lib.catalog.lifecycle import Lifecycle, LifecycleStatus, entry_lifecycle
from lib.catalog.price_summary import summarize_price
from lib.catalog.reload_order import LatestReadWins
from lib.catalog.user_entries import (
    UNSET,
    EntryEditRejected,
    create_user_entry,
    edited_entry,
    reload_user_entries,
    save_user_entry,
)
from lib.config.model_setting_keys import GLOBAL_MODEL_SETTING_KEYS
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import ConfigService
from lib.custom_provider import is_custom_provider, make_provider_id, parse_provider_id
from lib.custom_provider.capability_view import (
    CapabilityView,
    OverrideRejected,
    builtin_capability_view,
    check_user_added_overrides,
    user_added_runtime_overrides,
)
from lib.custom_provider.discovery_formats import is_comfyui_protocol
from lib.custom_provider.duration_presets import infer_supported_durations
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.endpoints import get_endpoint_spec
from lib.custom_provider.model_facts import entry_summary, model_entry
from lib.db import get_async_session
from lib.db.models.custom_provider import CustomProviderModel
from lib.db.repositories.builtin_provider_model_repo import BuiltinProviderModelRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.i18n import Translator
from server.routers.catalog import NewEntryBody, build_new_entry
from server.routers.custom_providers import (
    CUSTOM_MODEL_SETTING_FIELDS,
    EndpointRequestDescriptor,
    add_custom_model,
    cleanup_project_model_refs,
    custom_model_settings,
    discover_custom_provider_models,
    invalidate_provider_caches,
    remove_custom_model,
    rename_project_model_refs,
    update_custom_model,
)
from server.services.project.model_references import project_model_references

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["Providers"])

ModelFlag = Literal["discontinued", "retiring", "unpriced", "modified"]


class LifecycleResponse(BaseModel):
    """Matched entry lifecycle as of today (UTC date): status, shutdown date, and official successor (model vendor/official model name).

    Unmatched models are ``available`` with both date fields ``null``."""

    status: LifecycleStatus
    shutdown_date: date | None = None
    successor: str | None = None


class PriceSummaryItemResponse(BaseModel):
    """Unit-price range for one billing item; ``per`` is its counting quantity (1000000 per million tokens, 1 per image/second)."""

    meter: str
    min_price: float
    max_price: float
    per: int


class PriceSummaryResponse(BaseModel):
    currency: str
    items: list[PriceSummaryItemResponse]


class ProviderModelItem(BaseModel):
    """One row in a provider's model list.

    - ``model_id``: key referenced by settings/projects; ``api_model_name`` is sent to the provider.
    - ``display_name``: matched-entry display name (user-entered for custom providers); ``null`` falls back to model ID in the UI.
    - ``is_default``: effective default; never marked for disabled/retired models, without changing stored custom-provider defaults.
    - ``official``: officially listed built-in models allow only enabling/disabling; user-added/custom models are ``false``.
    - ``flags``: ``discontinued`` means retired; ``retiring`` means pending shutdown (date in ``lifecycle``);
      ``unpriced`` means calls are recorded as unpriced (no matched entry or no price; omit when the two-level multiplier product is 0, so no billing);
      ``modified`` means a user-edited entry no longer follows official updates.
    - ``endpoint``: custom model call-endpoint key; ``null`` for built-in providers.
    - ``lifecycle``: matched-entry lifecycle today. Retired models remain listed but are neither candidates nor callable.
    - ``min_app_version``: minimum app version for officially listed models; ``null`` if unspecified or not official.
    - ``min_app_version_satisfied``: app meets ``min_app_version``. Unsatisfied models appear last, with
      ``enabled`` and ``is_default`` false, excluded from candidates/defaults/calls. They cannot be enabled
      or have model settings read (docs/adr/0103)."""

    model_id: str
    api_model_name: str
    display_name: str | None
    media_type: MediaType
    enabled: bool
    is_default: bool
    official: bool
    flags: list[ModelFlag]
    price: PriceSummaryResponse | None
    lifecycle: LifecycleResponse
    endpoint: str | None = None
    min_app_version: str | None = None
    min_app_version_satisfied: bool = True


class ProviderModelsResponse(BaseModel):
    """``discoverable``: provider supports model discovery (not custom ComfyUI or built-ins lacking model-list APIs)."""

    models: list[ProviderModelItem]
    discoverable: bool = False


class ProviderModelEnabledUpdate(BaseModel):
    """Enable/disable one provider model; disabling custom models also clears their default status."""

    model_id: str
    enabled: bool


class AddProviderModelRequest(BaseModel):
    """Add by ID: ``media_type`` distinguishes multimodal models and specifies type for unmatched models;
    ``entry_binding`` is the user-selected entry (model vendor/official model name). ``endpoint`` is the
    new model's call endpoint, inferred by name if omitted (docs/adr/0105). Built-ins accept only their
    built-in endpoints for that media type, ignoring this field when matching an officially listed model."""

    model_id: Annotated[str, Field(min_length=1, max_length=128)]
    media_type: MediaType | None = None
    entry_binding: str | None = None
    endpoint: str | None = None


class DiscoveredModelItem(BaseModel):
    """One discovered model.

    - ``recognized``: custom providers match an entry by name; built-ins match an officially listed model
      by exact API model name. ``model_key`` is its model key, or ``null`` without a match.
    - ``media_type``: matched entry/official model type, otherwise name-inferred; used as media type when adding user models.
    - ``matched_entry``: matched-entry summary.
    - ``endpoint`` and ``endpoint_by_media_type``: custom-provider default endpoint and defaults per
      media type when switching to an entry of another type; ``null`` for built-ins."""

    model_id: str
    display_name: str
    media_type: MediaType
    recognized: bool
    model_key: str | None = None
    matched_entry: dict[str, Any] | None = None
    endpoint: str | None = None
    endpoint_by_media_type: dict[str, str] | None = None
    is_default: bool = False
    is_enabled: bool = True
    max_output_tokens: int | None = None


class DiscoverProviderModelsResponse(BaseModel):
    """Model-discovery result. ``not_applicable`` true means no model-list API; ``reason`` explains why."""

    models: list[DiscoveredModelItem] = []
    not_applicable: bool = False
    reason: str | None = None


class AddProviderModelResponse(BaseModel):
    """``official`` true means an officially listed match, enabled by adding it."""

    model: ProviderModelItem
    official: bool


class CapabilityViewResponse(BaseModel):
    """Parameters this provider supports: original values per dimension (runtime values without overrides;
    ``null`` means unlisted), effective overrides, and effective values. ``editable`` lists modifiable
    dimensions in UI order, empty for officially listed models. ``displayed`` lists every shown dimension
    in UI order; dimensions outside ``editable`` are read-only."""

    media_type: str
    editable: list[str]
    displayed: list[str]
    base: dict[str, Any]
    overrides: dict[str, Any]
    effective: dict[str, Any]


class OfficialEntryValues(BaseModel):
    """Official-data values of a user-modified entry, for per-field comparison and adoption."""

    display_name: str
    facts: dict[str, Any]
    price: dict[str, Any] | None


class SharedProviderResponse(BaseModel):
    provider_id: str
    display_name: str


class EntryInfoResponse(BaseModel):
    """Merged entry matched to the provider model, for the Sheet's lower Model information section.

    - ``discontinued``: whether retired today (UTC date), including entries whose shutdown date has arrived.
    - ``price``: price table in catalog format, directly writable as ``EntryUpdate.price``; ``null`` without one.
    - ``source``: ``official`` follows official updates; ``modified`` is user-edited and no longer follows;
      ``user_created`` is created and populated by the user.
    - ``official``: official values when user-modified entries, or user-created entries later officially
      listed, allow restoring/switching to official data; ``null`` otherwise.
    - ``shared_with``: other providers sharing this entry (configured-key built-ins and enabled custom
      provider models); edits and official restoration affect them together."""

    maker: str
    name: str
    display_name: str
    media_type: str
    aliases: list[str]
    discontinued: bool
    context_length: int | None
    facts: dict[str, Any]
    price: dict[str, Any] | None
    source: EntrySource
    official: OfficialEntryValues | None
    shared_with: list[SharedProviderResponse]


class EndpointOption(BaseModel):
    """One selectable call endpoint for user-added built-in models. ``display_name_key`` is a dashboard
    namespace i18n key; declarative endpoints use an empty string and take names from ``display_name``."""

    key: str
    display_name_key: str
    display_name: str | None = None


class ProviderModelDetail(BaseModel):
    """Provider-model Sheet content.

    - ``multiplier``: provider-model multiplier; official models receive it from official data, hidden in UI as ``null``.
    - ``provider_multiplier``: provider multiplier, used to show the product of both levels.
    - ``entry``: matched entry; ``null`` means unmatched and calls are recorded as unpriced.
    - ``entry_binding``: user-selected entry (model vendor/official model name); ``null`` for name-based
      matching without binding. Officially listed models have no binding.
    - Custom models additionally expose ``endpoint``, ``resolution``, ``path_overrides``, and ``requests``
      (endpoint requests whose paths can be overridden); empty for built-ins.
    - User-added built-in models additionally expose ``endpoint`` (attached built-in endpoint) and
      ``endpoint_options`` (this provider's selectable endpoints for that media type, in declaration order).
      Official models leave both empty and follow official endpoints."""

    model: ProviderModelItem
    multiplier: float | None
    provider_multiplier: float
    entry: EntryInfoResponse | None
    entry_binding: str | None = None
    capabilities: CapabilityViewResponse
    endpoint: str | None = None
    resolution: str | None = None
    path_overrides: dict[str, str] | None = None
    requests: list[EndpointRequestDescriptor] = []
    endpoint_options: list[EndpointOption] = []


class EntryUpdate(BaseModel):
    """Changes to the Sheet's lower Model information section. ``maker``, ``name``, and ``media_type`` are
    the entry identity read by the Sheet and must match the provider model's current entry. Omitted
    ``display_name``, ``facts``, and ``price`` stay unchanged; ``facts`` includes only model facts to change.
    ``price`` replaces the entire table (see ``EntryInfoResponse.price``); explicit ``null`` clears it,
    recording calls as unpriced."""

    maker: str
    name: str
    media_type: MediaType
    display_name: str | None = None
    facts: dict[str, Any] | None = None
    price: dict[str, Any] | None = None


class ProviderModelSettingsUpdate(BaseModel):
    """Save provider settings and model information from the provider-model Sheet; omitted fields stay unchanged.

    - All provider models: ``enabled`` and ``entry`` (edit the matched entry as a user copy, no longer following official updates).
    - User-managed models: ``model_id`` (change ID), ``endpoint`` (built-ins select only their own
      endpoints for that media type), ``multiplier``, ``capability_overrides`` (whole replacement;
      ``null`` or empty object clears), ``entry_binding`` (associate another existing same-media-type
      catalog entry; ``null`` detaches and restores name matching), ``new_entry`` (manually describe an
      unlisted model: create and bind; existing same-name catalog model yields 422; ``new_entry`` takes precedence over
      simultaneous ``entry_binding``). Save entries/bindings together; refusal of any step persists neither.
    - Custom models: ``is_default``, ``path_overrides``, ``resolution``."""

    enabled: bool | None = None
    is_default: bool | None = None
    model_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    multiplier: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    capability_overrides: dict[str, Any] | None = None
    endpoint: str | None = None
    path_overrides: dict[str, str] | None = None
    resolution: str | None = None
    entry: EntryUpdate | None = None
    entry_binding: str | None = None
    new_entry: NewEntryBody | None = None


class ProjectReference(BaseModel):
    name: str
    title: str | None


class ProviderModelReferences(BaseModel):
    """Global setting keys and projects referencing this provider model, for preremoval warnings."""

    settings: list[str]
    projects: list[ProjectReference]


_USER_ADDED_RELOADS = LatestReadWins()


async def reload_user_added_models(session: AsyncSession) -> None:
    """Load user-added models from built-in-provider model tables for catalog queries, at startup and after each committed write.

    Catalog readers (APIs, generation workers, Agent-session tools) run in the application process, so
    in-process replacement affects every read site. Concurrent loads apply in their read-start order."""
    await _USER_ADDED_RELOADS.reload(
        lambda: builtin_models.list_user_added_rows(session),
        lambda rows: catalog.install_user_added_models(
            catalog.UserAddedModel(
                provider_id=row.provider_id,
                model_id=row.model_id,
                media_type=cast("MediaType", row.media_type),
                multiplier=row.multiplier,
                entry_binding=row.entry_binding,
                fallback_durations=tuple(infer_supported_durations(row.model_id)) if row.media_type == "video" else (),
                capability_overrides=user_added_runtime_overrides(
                    row.media_type, row.capability_overrides, model_id=row.model_id
                ),
                endpoint=row.endpoint,
            )
            for row in rows
            if row.media_type in MEDIA_TYPES
        ),
    )


def _flags(entry: CatalogEntry | None, multiplier: float, state: Lifecycle) -> list[ModelFlag]:
    flags: list[ModelFlag] = []
    if state.status == "discontinued":
        flags.append("discontinued")
    elif state.status == "retiring":
        flags.append("retiring")
    priced = entry is not None and entry.price is not None and entry.price.is_priceable
    if not priced and multiplier != 0:
        flags.append("unpriced")
    if entry is not None and catalog.entry_source(entry.key) == "modified":
        flags.append("modified")
    return flags


def _lifecycle(state: Lifecycle) -> LifecycleResponse:
    return LifecycleResponse(status=state.status, shutdown_date=state.shutdown_date, successor=state.successor)


def _price(entry: CatalogEntry | None) -> PriceSummaryResponse | None:
    summary = summarize_price(entry.price, entry.media_type) if entry is not None else None
    if summary is None:
        return None
    return PriceSummaryResponse(
        currency=summary.currency,
        items=[
            PriceSummaryItemResponse(meter=i.meter, min_price=i.min_price, max_price=i.max_price, per=i.per)
            for i in summary.items
        ],
    )


def _builtin_item(item: builtin_models.BuiltinModelItem, provider_multiplier: float) -> ProviderModelItem:
    view = item.view
    return _builtin_view_item(view, provider_multiplier, enabled=item.enabled, is_default=item.is_default)


def _builtin_view_item(
    view: catalog.ProviderModelView, provider_multiplier: float, *, enabled: bool, is_default: bool
) -> ProviderModelItem:
    return ProviderModelItem(
        model_id=view.model_id,
        api_model_name=view.api_model_name,
        display_name=view.entry.display_name if view.entry is not None else None,
        media_type=cast("MediaType", view.model.media_type),
        enabled=enabled,
        is_default=is_default,
        official=view.official,
        flags=_flags(view.entry, provider_multiplier * view.multiplier, view.lifecycle),
        price=_price(view.entry),
        lifecycle=_lifecycle(view.lifecycle),
        min_app_version=view.min_app_version,
        min_app_version_satisfied=view.min_app_version_satisfied,
    )


async def _custom_items(session: AsyncSession, provider_id: str, db_id: int) -> list[ProviderModelItem]:
    provider_multiplier = await get_provider_multiplier(session, provider_id)
    endpoint_repo = CustomEndpointRepository(session)
    items: list[ProviderModelItem] = []
    for row in await CustomProviderRepository(session).list_models(db_id):
        try:
            media_type = (await resolve_endpoint_spec(row.endpoint, endpoint_repo.get)).media_type
        except ValueError:
            # Deleted call endpoint: the row is uncallable and ungroupable, so omit it from lists; repair through custom-provider forms.
            logger.warning("自定义供应商 %s 的模型 %s 的调用端点 %s 不存在", provider_id, row.model_id, row.endpoint)
            continue
        if media_type in MEDIA_TYPES:
            items.append(_custom_item(row, cast("MediaType", media_type), provider_multiplier))
    return items


def _custom_item(row: CustomProviderModel, media_type: MediaType, provider_multiplier: float) -> ProviderModelItem:
    entry = catalog.find_entry(
        row.model_id, media_type=media_type, binding=catalog.binding_key(row.entry_binding, media_type)
    )
    display_name = row.display_name.strip() if row.display_name and row.display_name.strip() != row.model_id else None
    state = entry_lifecycle(entry)
    return ProviderModelItem(
        model_id=row.model_id,
        api_model_name=row.model_id,
        display_name=display_name or (entry.display_name if entry is not None else None),
        media_type=media_type,
        enabled=row.is_enabled,
        # As with built-ins, default markers show actual effective defaults; omit disabled/retired models (default resolution skips them too).
        is_default=row.is_enabled and row.is_default and not state.discontinued,
        official=False,
        flags=_flags(entry, provider_multiplier * row.multiplier, state),
        price=_price(entry),
        lifecycle=_lifecycle(state),
        endpoint=row.endpoint,
    )


async def _custom_db_id(session: AsyncSession, provider_id: str, _t: Translator) -> int:
    try:
        db_id = parse_provider_id(provider_id)
    except ValueError:
        db_id = None
    if db_id is None or await CustomProviderRepository(session).get_provider(db_id) is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return db_id


def _require_builtin(provider_id: str, _t: Translator) -> None:
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))


def _raise_for(exc: builtin_models.BuiltinModelError, _t: Translator) -> HTTPException:
    return HTTPException(status_code=404 if exc.not_found else 422, detail=_t(exc.code, **exc.params))


async def _builtin_items(
    session: AsyncSession, provider_id: str, *, include_version_gated: bool = False
) -> list[ProviderModelItem]:
    """Built-in provider model rows. ``include_version_gated`` appends officially listed models requiring a
    newer app version: display requirements only, without enabling/default status (docs/adr/0103)."""
    provider_multiplier = await get_provider_multiplier(session, provider_id)
    items = [
        _builtin_item(item, provider_multiplier)
        for item in await builtin_models.list_builtin_models(session, provider_id)
    ]
    if include_version_gated:
        items += [
            _builtin_view_item(view, provider_multiplier, enabled=False, is_default=False)
            for view in catalog.version_gated_models(provider_id)
        ]
    return items


@router.get("/{provider_id}/models", response_model=ProviderModelsResponse)
async def list_provider_models(
    provider_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelsResponse:
    """All models for a provider, with enabled state, status flags, and price summaries."""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        provider = await CustomProviderRepository(session).get_provider(db_id)
        return ProviderModelsResponse(
            models=await _custom_items(session, provider_id, db_id),
            discoverable=provider is not None and not is_comfyui_protocol(provider.discovery_format),
        )
    _require_builtin(provider_id, _t)
    return ProviderModelsResponse(
        models=await _builtin_items(session, provider_id, include_version_gated=True),
        discoverable=builtin_discovery.is_discoverable(provider_id),
    )


@router.put("/{provider_id}/models/enabled", response_model=ProviderModelItem)
async def set_provider_model_enabled(
    provider_id: str,
    body: ProviderModelEnabledUpdate,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelItem:
    """Enable/disable a provider model immediately.

    Enabled models determine custom-provider available media types; clear caches and refresh worker
    capacity tables after changes, as with other model edits."""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        repo = CustomProviderRepository(session)
        row = await repo.get_model_by_ids(db_id, body.model_id)
        if row is None:
            raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=body.model_id))
        row.is_enabled = body.enabled
        if not body.enabled:
            row.is_default = False
        await session.commit()
        await invalidate_provider_caches(request)
        item = next((i for i in await _custom_items(session, provider_id, db_id) if i.model_id == body.model_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=body.model_id))
        return item
    _require_builtin(provider_id, _t)
    try:
        await builtin_models.set_builtin_model_enabled(session, provider_id, body.model_id, body.enabled)
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    await session.commit()
    return await _one_builtin_item(session, provider_id, body.model_id, _t)


async def _one_builtin_item(
    session: AsyncSession, provider_id: str, model_id: str, _t: Translator
) -> ProviderModelItem:
    item = next((i for i in await _builtin_items(session, provider_id) if i.model_id == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    return item


@router.post("/{provider_id}/models", response_model=AddProviderModelResponse, status_code=201)
async def add_provider_model(
    provider_id: str,
    body: AddProviderModelRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> AddProviderModelResponse:
    """Add a model by ID.

    Built-ins enable official matches, otherwise add user models. Custom providers infer endpoints by
    name when omitted; new models become default if their media type has none."""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        row = await add_custom_model(
            session,
            db_id,
            body.model_id,
            endpoint=body.endpoint,
            media_type=body.media_type,
            entry_binding=body.entry_binding,
            _t=_t,
        )
        model_id = row.model_id
        await session.commit()
        await invalidate_provider_caches(request)
        item = next((i for i in await _custom_items(session, provider_id, db_id) if i.model_id == model_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
        return AddProviderModelResponse(model=item, official=False)
    _require_builtin(provider_id, _t)
    try:
        added = await builtin_models.add_builtin_model(
            session,
            provider_id,
            body.model_id,
            media_type=body.media_type,
            entry_binding=body.entry_binding,
            inferred_media_type=builtin_discovery.name_media_type(provider_id, body.model_id.strip()),
            endpoint=body.endpoint,
        )
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    await session.commit()
    await reload_user_added_models(session)
    return AddProviderModelResponse(
        model=await _one_builtin_item(session, provider_id, added.model_id, _t), official=added.official
    )


#: Timeout waiting for built-in provider model-list APIs during discovery.
_DISCOVERY_TIMEOUT_SECONDS = 30


@router.post("/{provider_id}/models/discover", response_model=DiscoverProviderModelsResponse)
async def discover_provider_models(
    provider_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> DiscoverProviderModelsResponse:
    """Discover models through the provider's model-list API; return every ID and match, leaving exclusion of already-added models to the UI.

    Custom providers match entry names and supply default endpoints; built-ins match exact catalog API
    model names. Discovery results do not establish model availability."""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        found = await discover_custom_provider_models(session, db_id, _t)
        return DiscoverProviderModelsResponse(
            models=[DiscoveredModelItem.model_validate(m) for m in found.models],
            not_applicable=found.not_applicable,
            reason=found.reason,
        )
    _require_builtin(provider_id, _t)
    if not builtin_discovery.is_discoverable(provider_id):
        return DiscoverProviderModelsResponse(not_applicable=True, reason=_t("discovery_not_applicable_builtin"))
    config = await _builtin_discovery_config(session, provider_id)
    if config is None:
        raise HTTPException(status_code=422, detail=_t("missing_credentials"))
    try:
        model_ids = await asyncio.wait_for(
            asyncio.to_thread(builtin_discovery.list_model_ids, provider_id, config),
            timeout=_DISCOVERY_TIMEOUT_SECONDS,
        )
    except builtin_discovery.BuiltinDiscoveryConfigError as exc:
        raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc
    except Exception as exc:
        err_msg = str(exc) or type(exc).__name__
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("获取模型失败 [%s]: %s", provider_id, err_msg)
        raise HTTPException(status_code=502, detail=_t("discovery_failed", err_msg=err_msg)) from exc
    return DiscoverProviderModelsResponse(
        models=[_discovered_builtin_item(m) for m in builtin_discovery.classify_model_ids(provider_id, model_ids)]
    )


async def _builtin_discovery_config(session: AsyncSession, provider_id: str) -> dict[str, str] | None:
    """Provider configuration overlaid with active credentials; ``None`` without credentials."""
    cred = await CredentialRepository(session).get_active(provider_id)
    if cred is None:
        return None
    config = await ConfigService(session).get_provider_config(provider_id)
    # Credential overlay probes Vertex credential-file existence; offload to a thread.
    await asyncio.to_thread(cred.overlay_config, config)
    if not config.get("base_url") and (meta := PROVIDER_REGISTRY.get(provider_id)) and meta.default_base_url:
        config["base_url"] = meta.default_base_url
    return config


def _discovered_builtin_item(model: builtin_discovery.DiscoveredBuiltinModel) -> DiscoveredModelItem:
    return DiscoveredModelItem(
        model_id=model.model_id,
        display_name=model.entry.display_name if model.entry is not None else model.model_id,
        media_type=cast("MediaType", model.media_type),
        recognized=model.recognized,
        model_key=model.model_key,
        matched_entry=entry_summary(model.entry) if model.entry is not None else None,
    )


@router.delete("/{provider_id}/models/{model_id:path}", status_code=204)
async def remove_provider_model(
    provider_id: str,
    model_id: str,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Remove user-managed models: custom models (also clearing global/project references), or user-added
    built-in models. Officially listed models may only be disabled."""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        await remove_custom_model(session, db_id, model_id, _t)
        await session.commit()
        await invalidate_provider_caches(request)
        await cleanup_project_model_refs(db_id, {model_id})
        return Response(status_code=204)
    _require_builtin(provider_id, _t)
    try:
        await builtin_models.remove_builtin_model(session, provider_id, model_id)
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    await session.commit()
    await reload_user_added_models(session)
    await invalidate_provider_caches(request)
    return Response(status_code=204)


def _capabilities_response(view: CapabilityView | None, media_type: str) -> CapabilityViewResponse:
    if view is None:
        return CapabilityViewResponse(
            media_type=media_type, editable=[], displayed=[], base={}, overrides={}, effective={}
        )
    return CapabilityViewResponse(
        media_type=view.media_type,
        editable=list(view.editable),
        displayed=list(view.displayed),
        base=view.base,
        overrides=view.overrides,
        effective=view.effective,
    )


def _facts_json(entry: CatalogEntry) -> dict[str, Any]:
    facts = entry.facts
    match facts:
        case TextFacts():
            return {
                "image_input": facts.image_input,
                "structured_output": facts.structured_output,
                "max_output_tokens": facts.max_output_tokens,
            }
        case ImageFacts():
            return {
                "text_to_image": facts.text_to_image,
                "image_to_image": facts.image_to_image,
                "resolutions": list(facts.resolutions),
            }
        case VideoFacts():
            constraints = facts.duration_constraints
            return {
                "durations": list(facts.durations),
                "resolutions": list(facts.resolutions),
                "duration_constraints": {
                    "by_resolution": {res: list(allowed) for res, allowed in constraints.by_resolution.items()},
                    "with_reference_images": list(constraints.with_reference_images),
                },
            }
        case AudioFacts():
            return {}
        case _:
            assert_never(facts)


def _price_json(entry: CatalogEntry) -> dict[str, Any] | None:
    return cast("dict[str, Any] | None", dump_entry(entry).get("price"))


async def _custom_model_entry(row: CustomProviderModel, endpoint_repo: CustomEndpointRepository) -> CatalogEntry | None:
    try:
        media_type = (await resolve_endpoint_spec(row.endpoint, endpoint_repo.get)).media_type
    except ValueError:
        return None
    return model_entry(row.model_id, media_type, row.entry_binding) if media_type in MEDIA_TYPES else None


async def _providers_sharing(
    session: AsyncSession, entry: CatalogEntry, provider_id: str
) -> list[SharedProviderResponse]:
    """Other providers sharing this entry: configured-key built-ins and custom providers, counting enabled models only."""
    shared: list[SharedProviderResponse] = []
    configured = await CredentialRepository(session).get_active_credentials_bulk()
    for builtin_id, meta in PROVIDER_REGISTRY.items():
        if builtin_id == provider_id or builtin_id not in configured:
            continue
        items = await builtin_models.list_builtin_models(session, builtin_id)
        if any(i.enabled and i.view.entry is not None and i.view.entry.key == entry.key for i in items):
            shared.append(SharedProviderResponse(provider_id=builtin_id, display_name=meta.display_name))
    endpoint_repo = CustomEndpointRepository(session)
    for provider, rows in await CustomProviderRepository(session).list_providers_with_models():
        custom_id = make_provider_id(provider.id)
        if custom_id == provider_id:
            continue
        for row in rows:
            if row.is_enabled and (found := await _custom_model_entry(row, endpoint_repo)) and found.key == entry.key:
                shared.append(SharedProviderResponse(provider_id=custom_id, display_name=provider.display_name))
                break
    return shared


async def _entry_info(session: AsyncSession, entry: CatalogEntry | None, provider_id: str) -> EntryInfoResponse | None:
    if entry is None:
        return None
    source = catalog.entry_source(entry.key)
    official = catalog.official_entry(entry.key) if source != "official" else None
    return EntryInfoResponse(
        maker=entry.maker,
        name=entry.name,
        display_name=entry.display_name,
        media_type=entry.media_type,
        aliases=list(entry.aliases),
        discontinued=entry_lifecycle(entry).discontinued,
        context_length=entry.context_length,
        facts=_facts_json(entry),
        price=_price_json(entry),
        source=source,
        official=(
            OfficialEntryValues(
                display_name=official.display_name, facts=_facts_json(official), price=_price_json(official)
            )
            if official is not None
            else None
        ),
        shared_with=await _providers_sharing(session, entry, provider_id),
    )


async def _builtin_binding(session: AsyncSession, provider_id: str, model_id: str, *, official: bool) -> str | None:
    """Entry binding for user-added built-in models; absent for official models."""
    if official:
        return None
    row = await BuiltinProviderModelRepository(session).get(provider_id, model_id)
    return row.entry_binding if row is not None else None


async def _builtin_detail(
    session: AsyncSession, provider_id: str, model_id: str, _t: Translator
) -> ProviderModelDetail:
    provider_multiplier = await get_provider_multiplier(session, provider_id)
    items = await builtin_models.list_builtin_models(session, provider_id)
    item = next((i for i in items if i.view.model_id == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    view = item.view
    capabilities = builtin_capability_view(
        provider_id,
        model_id,
        base_model=view.base_model or view.model,
        official=view.official,
        overrides=view.capability_overrides,
    )
    media_type = view.model.media_type
    return ProviderModelDetail(
        model=_builtin_item(item, provider_multiplier),
        multiplier=None if view.official else view.multiplier,
        provider_multiplier=provider_multiplier,
        entry=await _entry_info(session, view.entry, provider_id),
        entry_binding=await _builtin_binding(session, provider_id, model_id, official=view.official),
        capabilities=_capabilities_response(capabilities, media_type),
        endpoint=None if view.official else _user_added_endpoint(provider_id, media_type, model_id),
        endpoint_options=(
            []
            if view.official
            else [_endpoint_option(e) for e in builtin_models.endpoint_options(provider_id, media_type)]
        ),
    )


def _user_added_endpoint(provider_id: str, media_type: str, model_id: str) -> str | None:
    try:
        return builtin_model_endpoint(provider_id, media_type, model_id)
    except ValueError:
        return None


def _endpoint_option(endpoint: str) -> EndpointOption:
    """UI name for built-in endpoints: shared custom-provider endpoints use registry wording; built-in-only endpoints use name-based i18n keys."""
    if endpoint in PROVIDER_ONLY_ENDPOINTS:
        return EndpointOption(key=endpoint, display_name_key=f"endpoint_{endpoint.replace('-', '_')}_display")
    spec = get_endpoint_spec(endpoint)
    return EndpointOption(key=endpoint, display_name_key=spec.display_name_key, display_name=spec.display_name)


async def _custom_detail(
    session: AsyncSession, provider_id: str, db_id: int, model_id: str, _t: Translator
) -> ProviderModelDetail:
    row = await CustomProviderRepository(session).get_model_by_ids(db_id, model_id)
    item = next((i for i in await _custom_items(session, provider_id, db_id) if i.model_id == model_id), None)
    if row is None or item is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    settings = await custom_model_settings(session, row)
    return ProviderModelDetail(
        model=item,
        multiplier=row.multiplier,
        provider_multiplier=await get_provider_multiplier(session, provider_id),
        entry=await _entry_info(session, model_entry(row.model_id, item.media_type, row.entry_binding), provider_id),
        entry_binding=row.entry_binding,
        capabilities=_capabilities_response(settings.capabilities, item.media_type),
        endpoint=settings.endpoint,
        resolution=settings.resolution,
        path_overrides=settings.path_overrides,
        requests=settings.requests,
    )


@router.get("/{provider_id}/models/{model_id:path}", response_model=ProviderModelDetail)
async def get_provider_model(
    provider_id: str,
    model_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelDetail:
    """Read one provider model: list row, provider settings, matched entry, and effective/original supported-parameter values."""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        return await _custom_detail(session, provider_id, db_id, model_id, _t)
    _require_builtin(provider_id, _t)
    return await _builtin_detail(session, provider_id, model_id, _t)


_BUILTIN_SETTING_FIELDS = frozenset(
    {"enabled", "model_id", "endpoint", "multiplier", "capability_overrides", "entry", "entry_binding", "new_entry"}
)


async def _current_entry(session: AsyncSession, provider_id: str, model_id: str, _t: Translator) -> CatalogEntry | None:
    """Currently matched merged entry; 404 if the provider model does not exist."""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        row = await CustomProviderRepository(session).get_model_by_ids(db_id, model_id)
        if row is None:
            raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
        return await _custom_model_entry(row, CustomEndpointRepository(session))
    items = await builtin_models.list_builtin_models(session, provider_id)
    item = next((i for i in items if i.view.model_id == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    return item.view.entry


async def _save_entry(
    session: AsyncSession, provider_id: str, model_id: str, update: EntryUpdate, _t: Translator
) -> bool:
    """Save model-information edits as an entry user copy; return whether written (identical entries are not written and keep following official updates)."""
    current = await _current_entry(session, provider_id, model_id, _t)
    if current is None or (current.maker, current.name, current.media_type) != (
        update.maker,
        update.name,
        update.media_type,
    ):
        raise HTTPException(status_code=409, detail=_t("provider_model_entry_mismatch", model=model_id))
    try:
        entry = edited_entry(
            current,
            display_name=update.display_name,
            facts=update.facts,
            price=update.price if "price" in update.model_fields_set else UNSET,
        )
    except EntryEditRejected as exc:
        raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc
    if entry == current:
        return False
    await save_user_entry(session, entry)
    return True


@dataclass(frozen=True)
class _BindingChange:
    """Entry-binding change in this save: ``ref`` is the new binding (``None`` detaches), ``created`` is its newly created entry."""

    ref: str | None
    created: CatalogEntry | None


def _plan_binding(body: ProviderModelSettingsUpdate, _t: Translator) -> _BindingChange | None:
    """Requested association or manual entry, ``None`` if omitted. Validate new entries here; :func:`_apply_binding` writes them."""
    if body.new_entry is not None:
        created = build_new_entry(body.new_entry, _t)
        return _BindingChange(created.key.ref, created)
    if "entry_binding" in body.model_fields_set:
        return _BindingChange((body.entry_binding or "").strip() or None, None)
    return None


async def _apply_binding(session: AsyncSession, change: _BindingChange, media_type: str, _t: Translator) -> None:
    """Check bindings against saved media type: existing entries must exist and match type; new entries must match type. Write new entries after acceptance."""
    if change.created is not None:
        if change.created.media_type != media_type:
            raise HTTPException(
                status_code=422, detail=_t("provider_model_binding_not_found", binding=change.created.key.ref)
            )
        await create_user_entry(session, change.created)
    elif change.ref is not None and catalog.find_bound_entry(change.ref, media_type) is None:
        raise HTTPException(status_code=422, detail=_t("provider_model_binding_not_found", binding=change.ref))


def _endpoint_in_options(provider_id: str, media_type: str, endpoint: str | None) -> str | None:
    """Endpoint switched in this save, for validating overrides against the new endpoint; saves reject nonoptions, treated here as unchanged."""
    return endpoint if endpoint in builtin_models.endpoint_options(provider_id, media_type) else None


async def _save_builtin_settings(
    session: AsyncSession,
    provider_id: str,
    model_id: str,
    body: ProviderModelSettingsUpdate,
    binding: _BindingChange | None,
    _t: Translator,
) -> str:
    fields = body.model_fields_set
    if unsupported := sorted(fields - _BUILTIN_SETTING_FIELDS):
        raise HTTPException(
            status_code=422,
            detail=_t("provider_model_setting_not_applicable", model=model_id, setting=unsupported[0]),
        )
    kwargs: dict[str, Any] = {}
    if "capability_overrides" in fields:
        item = next(
            (i for i in await builtin_models.list_builtin_models(session, provider_id) if i.view.model_id == model_id),
            None,
        )
        if item is not None and not item.view.official:
            try:
                kwargs["capability_overrides"] = check_user_added_overrides(
                    provider_id,
                    model_id,
                    item.view.model.media_type,
                    body.capability_overrides,
                    endpoint=_endpoint_in_options(provider_id, item.view.model.media_type, body.endpoint),
                )
            except OverrideRejected as exc:
                raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc
        else:
            kwargs["capability_overrides"] = body.capability_overrides
    if binding is not None:
        kwargs["entry_binding"] = binding.ref
    try:
        saved = await builtin_models.update_builtin_model(
            session,
            provider_id,
            model_id,
            enabled=body.enabled,
            new_model_id=body.model_id,
            multiplier=body.multiplier,
            endpoint=body.endpoint,
            **kwargs,
        )
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    if binding is not None:
        row = await BuiltinProviderModelRepository(session).get(provider_id, saved)
        if row is not None and row.media_type is not None:
            await _apply_binding(session, binding, row.media_type, _t)
    return saved


@router.patch("/{provider_id}/models/{model_id:path}", response_model=ProviderModelDetail)
async def update_provider_model(
    provider_id: str,
    model_id: str,
    body: ProviderModelSettingsUpdate,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelDetail:
    """Atomically save provider settings and model information in one request; return the saved Sheet content."""
    if not is_custom_provider(provider_id):
        _require_builtin(provider_id, _t)
    # Save the entry copy using its pre-save match first: the Sheet read this entry; changing model ID in the same request must not change which entry is edited.
    entry_saved = body.entry is not None and await _save_entry(session, provider_id, model_id, body.entry, _t)
    binding = _plan_binding(body, _t)
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        renames = {"enabled": "is_enabled"}
        changes = {
            renames.get(name, name): getattr(body, name)
            for name in body.model_fields_set
            if renames.get(name, name) in CUSTOM_MODEL_SETTING_FIELDS
        }
        if binding is not None:
            changes["entry_binding"] = binding.ref
        row = await update_custom_model(session, db_id, model_id, changes, _t)
        saved_model_id = row.model_id
        # Endpoint switches may change media type: validate existing bindings against the new type even when binding is unchanged.
        if binding is None and "endpoint" in changes and row.entry_binding:
            binding = _BindingChange(ref=row.entry_binding, created=None)
        if binding is not None:
            media_type = (await resolve_endpoint_spec(row.endpoint, CustomEndpointRepository(session).get)).media_type
            await _apply_binding(session, binding, media_type, _t)
        await session.commit()
        if entry_saved or (binding is not None and binding.created is not None):
            await reload_user_entries(session)
        await invalidate_provider_caches(request)
        await rename_project_model_refs(db_id, model_id, saved_model_id)
        return await _custom_detail(session, provider_id, db_id, saved_model_id, _t)
    saved_model_id = await _save_builtin_settings(session, provider_id, model_id, body, binding, _t)
    await session.commit()
    if entry_saved or (binding is not None and binding.created is not None):
        await reload_user_entries(session)
    await reload_user_added_models(session)
    await invalidate_provider_caches(request)
    return await _builtin_detail(session, provider_id, saved_model_id, _t)


def _project_references(reference: str) -> list[ProjectReference]:
    """Projects referencing ``reference`` in model-setting fields; skip unreadable projects."""
    return [
        ProjectReference(name=project.name, title=project.title)
        for project in project_model_references()
        if reference in project.references.values()
    ]


@router.get("/{provider_id}/model-references", response_model=ProviderModelReferences)
async def get_provider_model_references(
    provider_id: str,
    model_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelReferences:
    """Global settings and projects referencing this provider model, for removal-confirmation dialogs."""
    if is_custom_provider(provider_id):
        await _custom_db_id(session, provider_id, _t)
    else:
        _require_builtin(provider_id, _t)
    reference = f"{provider_id}/{model_id}"
    all_settings = await ConfigService(session).get_all_settings()
    settings = [key for key in GLOBAL_MODEL_SETTING_KEYS if all_settings.get(key) == reference]
    projects = await asyncio.to_thread(_project_references, reference)
    return ProviderModelReferences(settings=settings, projects=projects)
