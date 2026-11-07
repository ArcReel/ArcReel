"""Market API: register, order and refresh sources, and browse entries.

Frontend-only with existing session authentication. Adding fetches immediately; fetch failure or an invalid index returns
422 without persistence. Official sources can be disabled, reordered and renamed but deletion returns 409. Refresh
failure is not request failure; record it in source ``status`` / ``last_error``. Lists/details read only cached snapshots;
fetch raw definitions and icons on demand through the fetch boundary, returning 502 for inaccessible or unusable upstream content."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_market_core.endpoint_definition import meets_min_app_version, validate_definition
from arcreel_market_core.market import ENDPOINT_ENTRY_TYPE, check_entry_definition
from arcreel_market_core.market.address import SourceAddressError
from arcreel_market_core.market.fetch import MarketFetchError
from arcreel_market_core.market.index import MarketIndexEntry
from arcreel_market_core.market.issues import MarketIssue, MarketIssueCode
from lib.db import get_async_session
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import OFFICIAL_KIND, MarketSourceRepository
from lib.infra.api_errors import BadGatewayError, BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.market.entries import (
    MarketAssetFetchError,
    MarketAssetInvalidError,
    MarketEntryNotFoundError,
    MarketEntryService,
    MarketSourceDisabledError,
    MissingTarget,
    SourcedEntry,
    find_entry,
    get_market_entry_service,
    merge_entries,
)
from lib.market.installations import definition_digest, write_installation
from lib.market.official_service import EntryRef, OfficialServiceGateway, get_official_service_gateway
from lib.market.sources import DuplicateSourceError, MarketSourceService, get_market_source_service
from server.i18n import Translator
from server.routers._market_installations import (
    EntryInstallationResponse,
    endpoint_installation,
    entry_installation,
    entry_installations,
)
from server.routers._reorder import full_permutation_error
from server.routers.custom_endpoints import CustomEndpointResponse, endpoint_response
from server.routers.system_config import get_app_version_reader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["Market"])

Service = Annotated[MarketSourceService, Depends(get_market_source_service)]
EntryService = Annotated[MarketEntryService, Depends(get_market_entry_service)]
AppVersionReader = Annotated[Callable[[], str], Depends(get_app_version_reader)]
OfficialService = Annotated[OfficialServiceGateway, Depends(get_official_service_gateway)]

#: Frontend icon URLs include the entry version as a query parameter, changing with versions and allowing long browser caching.
ICON_CACHE_CONTROL = "private, max-age=86400"
#: SVG may embed scripts; prevent execution and external resource loading even when icon URLs open directly.
ICON_CONTENT_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'; sandbox"

_MISSING_KEYS = {
    MissingTarget.SOURCE: "market_source_not_found",
    MissingTarget.ENTRY: "market_entry_not_found",
    MissingTarget.ICON: "market_entry_icon_not_found",
}

_ORDER_ERROR_KEYS = {
    "length": "market_source_order_length_mismatch",
    "duplicate": "market_source_order_duplicate_ids",
    "mismatch": "market_source_order_ids_mismatch",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MarketIndexSummary(BaseModel):
    name: str
    description: str | None = None
    homepage: str | None = None


class MarketSourceResponse(BaseModel):
    id: int
    kind: str
    display_name: str
    address: str
    index_url: str
    canonical_key: str
    is_enabled: bool
    position: int
    status: str
    last_error: str | None
    fetched_at: str | None
    created_at: str | None
    updated_at: str | None
    #: Snapshot entries recognized by this client, excluding unknown ``type`` values.
    entry_count: int
    #: Top-level snapshot-index information; null before any successful fetch.
    index: MarketIndexSummary | None


class MarketSourceListResponse(BaseModel):
    sources: list[MarketSourceResponse]


class MarketEntryResponse(BaseModel):
    source_id: int
    source_display_name: str
    type: str
    slug: str
    path: str
    name: str
    author: str
    version: str
    media_type: str
    description: str | None
    homepage: str | None
    #: Relative index icon path; non-null values load through ``/market/sources/{id}/entries/{slug}/icon``.
    icon: str | None
    min_app_version: str | None
    #: Whether current app version meets ``min_app_version``; true without a requirement or readable app version.
    min_app_version_satisfied: bool
    installation: EntryInstallationResponse | None = None


class MarketEntryListResponse(BaseModel):
    entries: list[MarketEntryResponse]
    #: Current app version; null when unreadable.
    app_version: str | None


class MarketEntrySourceSummary(BaseModel):
    id: int
    kind: str
    display_name: str
    canonical_key: str
    is_enabled: bool
    status: str
    fetched_at: str | None
    index: MarketIndexSummary | None


class MarketEntryAggregateResponse(BaseModel):
    source_id: int
    slug: str
    #: Rounded display value from the official service, shown unchanged.
    installs: int
    rating_count: int
    #: Null below the official rating-count threshold; show only the count.
    rating_average: float | None


class MarketEntryAggregateListResponse(BaseModel):
    items: list[MarketEntryAggregateResponse]


class RateMarketEntryRequest(BaseModel):
    stars: int = Field(ge=1, le=5, strict=True)


class MarketEntryDetailResponse(BaseModel):
    entry: MarketEntryResponse
    source: MarketEntrySourceSummary
    app_version: str | None


class MarketEntryDefinitionResponse(BaseModel):
    #: Raw market-source definition parsed as JSON, without definition validation.
    definition: Any
    entry_matches_definition: bool
    #: Digest for JSON-object definitions; return unchanged at installation to prove this exact definition was confirmed.
    definition_digest: str | None


class AddMarketSourceRequest(BaseModel):
    address: str = Field(max_length=2048)
    display_name: str | None = Field(default=None, max_length=128)


class UpdateMarketSourceRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    is_enabled: bool | None = None


class ReorderMarketSourcesRequest(BaseModel):
    ids: list[int]


def _to_response(source: MarketSource) -> MarketSourceResponse:
    document: Any = source.cached_index
    summary = _index_summary(source)
    entry_count = 0
    if isinstance(document, dict):
        entries = document.get("entries")
        if isinstance(entries, list):
            entry_count = sum(1 for e in entries if isinstance(e, dict) and e.get("type") == ENDPOINT_ENTRY_TYPE)
    return MarketSourceResponse(
        id=source.id,
        kind=source.kind,
        display_name=source.display_name,
        address=source.address,
        index_url=source.index_url,
        canonical_key=source.canonical_key,
        is_enabled=source.is_enabled,
        position=source.position,
        status=source.status,
        last_error=source.last_error,
        fetched_at=_iso_utc(source.fetched_at),
        created_at=_iso_utc(source.created_at),
        updated_at=_iso_utc(source.updated_at),
        entry_count=entry_count,
        index=summary,
    )


def _index_summary(source: MarketSource) -> MarketIndexSummary | None:
    document: Any = source.cached_index
    if not isinstance(document, dict):
        return None
    return MarketIndexSummary(
        name=document.get("name", ""), description=document.get("description"), homepage=document.get("homepage")
    )


def _entry_response(
    sourced: SourcedEntry, app_version: str | None, installation: EntryInstallationResponse | None = None
) -> MarketEntryResponse:
    entry: MarketIndexEntry = sourced.entry
    satisfied = (
        entry.min_app_version is None
        or app_version is None
        or meets_min_app_version(entry.min_app_version, app_version)
    )
    return MarketEntryResponse(
        installation=installation,
        source_id=sourced.source.id,
        source_display_name=sourced.source.display_name,
        type=entry.type,
        slug=entry.slug,
        path=entry.path,
        name=entry.name,
        author=entry.author,
        version=entry.version,
        media_type=entry.media_type,
        description=entry.description,
        homepage=entry.homepage,
        icon=entry.icon,
        min_app_version=entry.min_app_version,
        min_app_version_satisfied=satisfied,
    )


def _app_version(read_app_version: Callable[[], str]) -> str | None:
    try:
        return read_app_version()
    except Exception:
        logger.exception("Failed to read app version")
        return None


def _entry_api_error(
    exc: MarketEntryNotFoundError | MarketSourceDisabledError | MarketAssetFetchError | MarketAssetInvalidError,
) -> NotFoundError | ConflictError | BadGatewayError:
    if isinstance(exc, MarketEntryNotFoundError):
        return NotFoundError(_MISSING_KEYS[exc.target])
    if isinstance(exc, MarketSourceDisabledError):
        return ConflictError("market_source_disabled")
    key = "market_entry_fetch_failed" if isinstance(exc, MarketAssetFetchError) else "market_entry_asset_invalid"
    return BadGatewayError(key, reason=str(exc)).with_diagnostic({"reason": str(exc)})


def _iso_utc(value: datetime | None) -> str | None:
    """SQLite returns timezone-naive timestamps; store all times as UTC and restore the timezone when serializing."""
    if value is None:
        return None
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).isoformat()


async def _require_source(repo: MarketSourceRepository, source_id: int) -> MarketSource:
    source = await repo.get(source_id)
    if source is None:
        raise NotFoundError("market_source_not_found")
    return source


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/sources", response_model=MarketSourceListResponse)
async def list_sources(session: AsyncSession = Depends(get_async_session)) -> MarketSourceListResponse:
    sources = await MarketSourceRepository(session).list_ordered()
    return MarketSourceListResponse(sources=[_to_response(s) for s in sources])


@router.post("/sources", status_code=201, response_model=MarketSourceResponse)
async def add_source(body: AddMarketSourceRequest, service: Service, _t: Translator) -> MarketSourceResponse:
    try:
        source = await service.add_source(body.address, body.display_name)
    except SourceAddressError as exc:
        raise UnprocessableError(f"market_source_address_{exc.code.value}") from exc
    except DuplicateSourceError as exc:
        raise ConflictError("market_source_duplicate") from exc
    except MarketFetchError as exc:
        raise UnprocessableError(
            "market_source_fetch_failed", status=_t(f"market_source_status_{exc.status.value}"), reason=exc.detail
        ).with_diagnostic({"status": exc.status.value, "reason": exc.detail}) from exc
    return _to_response(source)


@router.put("/sources/order", response_model=MarketSourceListResponse)
async def reorder_sources(
    body: ReorderMarketSourcesRequest, session: AsyncSession = Depends(get_async_session)
) -> MarketSourceListResponse:
    repo = MarketSourceRepository(session)
    existing_ids = [source.id for source in await repo.list_ordered()]
    error_kind = full_permutation_error(existing_ids, body.ids)
    if error_kind is not None:
        raise BadRequestError(_ORDER_ERROR_KEYS[error_kind])
    await repo.apply_order(body.ids)
    await session.commit()
    return MarketSourceListResponse(sources=[_to_response(s) for s in await repo.list_ordered()])


@router.patch("/sources/{source_id}", response_model=MarketSourceResponse)
async def update_source(
    source_id: int, body: UpdateMarketSourceRequest, session: AsyncSession = Depends(get_async_session)
) -> MarketSourceResponse:
    repo = MarketSourceRepository(session)
    source = await _require_source(repo, source_id)
    if body.display_name is not None:
        display_name = body.display_name.strip()
        if not display_name:
            raise UnprocessableError("market_source_display_name_required")
        source.display_name = display_name
    if body.is_enabled is not None:
        source.is_enabled = body.is_enabled
    await session.commit()
    await session.refresh(source)
    return _to_response(source)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, session: AsyncSession = Depends(get_async_session)) -> Response:
    repo = MarketSourceRepository(session)
    source = await _require_source(repo, source_id)
    if source.kind == OFFICIAL_KIND:
        raise ConflictError("market_source_official_undeletable")
    await repo.delete(source_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/sources/{source_id}/refresh", response_model=MarketSourceResponse)
async def refresh_source(source_id: int, service: Service) -> MarketSourceResponse:
    source = await service.refresh_source(source_id, manual=True)
    if source is None:
        raise NotFoundError("market_source_not_found")
    return _to_response(source)


@router.post("/refresh", response_model=MarketSourceListResponse)
async def refresh_sources(
    service: Service,
    stale_only: Annotated[
        bool,
        Query(
            description="Refresh only enabled sources whose last successful refresh was over 1 hour ago (automatic refresh on opening the market page)"
        ),
    ] = False,
) -> MarketSourceListResponse:
    """Refresh all enabled sources and return updated rows per source; neither refresh nor include disabled sources."""
    sources = await service.refresh_all(stale_only=stale_only)
    return MarketSourceListResponse(sources=[_to_response(s) for s in sources])


@router.get("/entries", response_model=MarketEntryListResponse)
async def list_entries(
    read_app_version: AppVersionReader,
    session: AsyncSession = Depends(get_async_session),
    entry_type: Annotated[
        str, Query(alias="type", description="Entry type; only endpoint is available initially")
    ] = ENDPOINT_ENTRY_TYPE,
) -> MarketEntryListResponse:
    """Entries from enabled sources’ cached snapshots, ordered by source then by name within each source; no pagination or outbound requests."""
    sources = await MarketSourceRepository(session).list_ordered()
    app_version = _app_version(read_app_version)
    merged = merge_entries(sources, entry_type=entry_type)
    installations = await entry_installations(session, merged)
    return MarketEntryListResponse(
        entries=[
            _entry_response(item, app_version, installations.get((item.source.canonical_key, item.entry.slug)))
            for item in merged
        ],
        app_version=app_version,
    )


@router.get("/entries/aggregates", response_model=MarketEntryAggregateListResponse)
async def list_entry_aggregates(
    official: OfficialService,
    session: AsyncSession = Depends(get_async_session),
    entry_type: Annotated[
        str, Query(alias="type", description="Entry type; only endpoint is available initially")
    ] = ENDPOINT_ENTRY_TYPE,
) -> MarketEntryAggregateListResponse:
    """Official-service install counts and ratings for enabled official-source entries; never send third-party entries to the official service. Return 409 when the official service is disabled."""
    client = await official.require()
    sources = [
        source for source in await MarketSourceRepository(session).list_ordered() if source.kind == OFFICIAL_KIND
    ]
    merged = merge_entries(sources, entry_type=entry_type)
    aggregates = await client.aggregates(
        [EntryRef(type=item.entry.type, source=item.source.canonical_key, slug=item.entry.slug) for item in merged]
    )
    return MarketEntryAggregateListResponse(
        items=[
            MarketEntryAggregateResponse(
                source_id=item.source.id,
                slug=item.entry.slug,
                installs=aggregate.installs,
                rating_count=aggregate.rating_count,
                rating_average=aggregate.rating_average,
            )
            for item, aggregate in zip(merged, aggregates, strict=True)
        ]
    )


@router.get("/sources/{source_id}/entries/{slug}", response_model=MarketEntryDetailResponse)
async def get_entry(
    source_id: int,
    slug: str,
    read_app_version: AppVersionReader,
    session: AsyncSession = Depends(get_async_session),
) -> MarketEntryDetailResponse:
    """Index entry and source summary from cached snapshots only, without fetching definitions; disabled-source entries remain queryable."""
    source = await _require_source(MarketSourceRepository(session), source_id)
    entry = find_entry(source, slug)
    if entry is None:
        raise NotFoundError("market_entry_not_found")
    app_version = _app_version(read_app_version)
    sourced = SourcedEntry(source=source, entry=entry)
    return MarketEntryDetailResponse(
        entry=_entry_response(sourced, app_version, await entry_installation(session, sourced)),
        source=MarketEntrySourceSummary(
            id=source.id,
            kind=source.kind,
            display_name=source.display_name,
            canonical_key=source.canonical_key,
            is_enabled=source.is_enabled,
            status=source.status,
            fetched_at=_iso_utc(source.fetched_at),
            index=_index_summary(source),
        ),
        app_version=app_version,
    )


@dataclass(frozen=True)
class _CheckedDefinition:
    source: MarketSource
    entry: MarketIndexEntry
    definition: Any
    issues: list[MarketIssue]

    @property
    def matches_entry(self) -> bool:
        return not any(issue.code == MarketIssueCode.PROJECTION_MISMATCH for issue in self.issues)

    @property
    def digest(self) -> str | None:
        return definition_digest(self.definition) if isinstance(self.definition, dict) else None


async def _checked_definition(
    service: MarketEntryService, session: AsyncSession, source_id: int, slug: str
) -> _CheckedDefinition:
    """After fetching a definition, reread the current snapshot for rules ④⑤. Return 409 if refresh changed the entry/index address during fetch; never validate old-address content against a new entry."""
    try:
        fetched = await service.fetch_definition(source_id, slug)
    except (MarketEntryNotFoundError, MarketSourceDisabledError, MarketAssetFetchError, MarketAssetInvalidError) as exc:
        raise _entry_api_error(exc) from exc
    source = await _require_source(MarketSourceRepository(session), source_id)
    entry = find_entry(source, slug)
    if entry is None:
        raise NotFoundError("market_entry_not_found")
    if entry != fetched.entry or source.index_url != fetched.index_url:
        raise ConflictError("market_entry_changed_during_fetch")
    return _CheckedDefinition(
        source=source,
        entry=entry,
        definition=fetched.definition,
        issues=check_entry_definition(entry, fetched.definition, definition_file=entry.path),
    )


@router.get("/sources/{source_id}/entries/{slug}/definition", response_model=MarketEntryDefinitionResponse)
async def get_entry_definition(
    source_id: int, slug: str, service: EntryService, session: AsyncSession = Depends(get_async_session)
) -> MarketEntryDefinitionResponse:
    checked = await _checked_definition(service, session, source_id, slug)
    return MarketEntryDefinitionResponse(
        definition=checked.definition,
        entry_matches_definition=checked.matches_entry,
        definition_digest=checked.digest,
    )


@router.get("/sources/{source_id}/entries/{slug}/icon", response_class=Response)
async def get_entry_icon(source_id: int, slug: str, service: EntryService) -> Response:
    try:
        icon = await service.fetch_icon(source_id, slug)
    except (MarketEntryNotFoundError, MarketSourceDisabledError, MarketAssetFetchError, MarketAssetInvalidError) as exc:
        raise _entry_api_error(exc) from exc
    return Response(
        content=icon.content,
        media_type=icon.media_type,
        headers={
            "Cache-Control": ICON_CACHE_CONTROL,
            "Content-Security-Policy": ICON_CONTENT_SECURITY_POLICY,
            "X-Content-Type-Options": "nosniff",
        },
    )


class InstallMarketEntryRequest(BaseModel):
    #: Definition digest shown at confirmation; refetch on installation and reject content differing from what the user reviewed.
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    overwrite_endpoint_id: int | None = Field(default=None, gt=0)


class InstallMarketEntryResponse(BaseModel):
    endpoint: CustomEndpointResponse
    installation: EntryInstallationResponse


@router.post("/sources/{source_id}/entries/{slug}/install", response_model=InstallMarketEntryResponse)
async def install_entry(
    source_id: int,
    slug: str,
    body: InstallMarketEntryRequest,
    service: EntryService,
    official: OfficialService,
    background_tasks: BackgroundTasks,
    read_app_version: AppVersionReader,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> InstallMarketEntryResponse:
    """Report first official-source installs through a background task after commit; do not report updates with existing installation records or third-party sources."""
    checked = await _checked_definition(service, session, source_id, slug)
    source, entry, definition = checked.source, checked.entry, checked.definition
    if not source.is_enabled:
        raise ConflictError("market_source_disabled")
    if checked.digest is not None and checked.digest != body.definition_digest:
        raise ConflictError("market_entry_definition_changed")
    if not checked.matches_entry:
        raise UnprocessableError("market_entry_definition_mismatch")
    if entry.min_app_version is not None and not meets_min_app_version(
        entry.min_app_version, _app_version(read_app_version) or ""
    ):
        raise UnprocessableError("market_entry_requires_newer_app", version=entry.min_app_version)
    if checked.issues or not isinstance(definition, dict):
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(
            validate_definition(definition).to_payload(_t)
        )
    first_install = await entry_installation(session, SourcedEntry(source=source, entry=entry)) is None
    try:
        endpoint = await write_installation(
            session,
            source_key=source.canonical_key,
            slug=slug,
            definition=definition,
            overwrite_endpoint_id=body.overwrite_endpoint_id,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("market_entry_already_installed") from exc
    from server.services.tasks.generation_context import invalidate_backend_cache

    invalidate_backend_cache()
    await session.refresh(endpoint)
    installation = await entry_installation(session, SourcedEntry(source=source, entry=entry))
    if installation is None:
        raise NotFoundError("custom_endpoint_not_found")
    app_version = _app_version(read_app_version)
    if first_install and source.kind == OFFICIAL_KIND and app_version:
        background_tasks.add_task(
            official.report_install,
            EntryRef(type=entry.type, source=source.canonical_key, slug=entry.slug),
            version=entry.version,
            app_version=app_version,
        )
    return InstallMarketEntryResponse(
        endpoint=endpoint_response(endpoint, await endpoint_installation(session, endpoint.id)),
        installation=installation,
    )


@router.put("/sources/{source_id}/entries/{slug}/rating", status_code=204)
async def rate_entry(
    source_id: int,
    slug: str,
    body: RateMarketEntryRequest,
    official: OfficialService,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Submit/update ratings through the official service for official-source entries only; preserve official error/status codes in local responses."""
    source = await _require_source(MarketSourceRepository(session), source_id)
    entry = find_entry(source, slug)
    if entry is None:
        raise NotFoundError("market_entry_not_found")
    if source.kind != OFFICIAL_KIND:
        raise ConflictError("market_entry_rating_unofficial")
    if await entry_installation(session, SourcedEntry(source=source, entry=entry)) is None:
        raise ConflictError("market_entry_rating_not_installed")
    client = await official.require()
    await client.rate(EntryRef(type=entry.type, source=source.canonical_key, slug=entry.slug), body.stars)
    return Response(status_code=204)
