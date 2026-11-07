"""Model-catalog API: search by name/media type, manually create entries, and restore user-modified entries to official data.
User-created entries adopting official data after inclusion use the same endpoint."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import CatalogEntry, EntryKey, MediaType
from lib.catalog import query as catalog
from lib.catalog.lifecycle import entry_lifecycle
from lib.catalog.user_entries import (
    EntryEditRejected,
    create_user_entry,
    discard_user_entry,
    new_entry,
    reload_user_entries,
)
from lib.custom_provider.duration_presets import infer_supported_durations
from lib.db import get_async_session
from server.i18n import Translator
from server.routers.custom_providers import invalidate_provider_caches

router = APIRouter(prefix="/catalog", tags=["Catalog"])


class CatalogEntryHit(BaseModel):
    """A matching model entry, identified jointly by ``maker``, ``name`` and ``media_type``; the UI shows only its display name.

    ``discontinued`` means the entry is offline as of today’s UTC date, including entries whose shutdown date has arrived."""

    maker: str
    name: str
    media_type: MediaType
    display_name: str
    discontinued: bool


class CatalogSearchResponse(BaseModel):
    entries: list[CatalogEntryHit]


@router.get("/entries", response_model=CatalogSearchResponse)
async def search_catalog_entries(
    q: Annotated[str, Query(max_length=200, description="Search by name; leave empty to list all entries")] = "",
    media_type: Annotated[MediaType | None, Query(description="List entries of this media type only")] = None,
) -> CatalogSearchResponse:
    hits = catalog.search_entries(q, media_type)
    return CatalogSearchResponse(entries=[CatalogEntryHit.model_validate(vars(hit)) for hit in hits])


class NewEntryHints(BaseModel):
    """Name-based hints for manual model entry: existing catalog matches, which should be selected directly, and optional
    video durations prefilled from name-based presets; other media types use ``null``."""

    existing: CatalogEntryHit | None
    durations: list[int] | None


@router.get("/new-entry-hints", response_model=NewEntryHints)
async def get_new_entry_hints(
    name: Annotated[str, Query(max_length=255, description="Model name to enter")],
    media_type: Annotated[MediaType, Query(description="Media type to enter")],
) -> NewEntryHints:
    entry = catalog.find_entry(name.strip(), media_type=media_type) if name.strip() else None
    hit = (
        CatalogEntryHit(
            maker=entry.maker,
            name=entry.name,
            media_type=entry.media_type,
            display_name=entry.display_name,
            discontinued=entry_lifecycle(entry).discontinued,
        )
        if entry is not None
        else None
    )
    durations = infer_supported_durations(name.strip()) if media_type == "video" and name.strip() else None
    return NewEntryHints(existing=hit, durations=durations)


class CatalogEntryKey(BaseModel):
    """Entry identity: model maker/official model name, additionally distinguished by media type for dual-media models."""

    maker: str
    name: str
    media_type: MediaType


class NewEntryBody(CatalogEntryKey):
    """A manually entered model. Omitted ``display_name`` uses the model name; omitted ``facts`` fields use media-type defaults.
    ``price`` is a price list in catalog-data format; omission records call costs as unpriced."""

    display_name: str | None = None
    facts: dict[str, Any] | None = None
    price: dict[str, Any] | None = None


def build_new_entry(body: NewEntryBody, _t: Translator) -> CatalogEntry:
    """Validate and construct a manually entered model; invalid entries or existing catalog names return 422."""
    try:
        return new_entry(
            maker=body.maker,
            name=body.name,
            media_type=body.media_type,
            display_name=body.display_name,
            facts=body.facts,
            price=body.price,
        )
    except EntryEditRejected as exc:
        raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc


@router.post("/entries", response_model=CatalogEntryHit, status_code=201)
async def create_catalog_entry(
    body: NewEntryBody,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CatalogEntryHit:
    """Create a user-entered model absent from the catalog; new entries do not follow official data.

    Sheet manual entry does not use this endpoint: new entries persist with Sheet saves through ``new_entry`` in
    ``PATCH /providers/{id}/models/{model}``; cancellation leaves no entry. This endpoint supports non-Sheet entry points
    such as the add-model dialog.

    Existing provider models may now match the entry by name and change capabilities, so clear backend caches as for official-data restoration."""
    entry = build_new_entry(body, _t)
    await create_user_entry(session, entry)
    await session.commit()
    await reload_user_entries(session)
    await invalidate_provider_caches(request)
    return CatalogEntryHit(
        maker=entry.maker,
        name=entry.name,
        media_type=entry.media_type,
        display_name=entry.display_name,
        discontinued=entry_lifecycle(entry).discontinued,
    )


@router.post("/entries/restore-official", status_code=204)
async def restore_official_entry(
    body: CatalogEntryKey,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Restore official data: discard the user copy and resume following official data, affecting every provider using the entry. Irreversible.

    For modified official entries this is “Restore official data”; user-created entries with a matching identity later included
    in official data use “Adopt official data”, through the same endpoint. Do nothing without a user copy; reject if official
    data does not yet contain the entry."""
    try:
        await discard_user_entry(session, EntryKey(body.maker, body.name, body.media_type))
    except EntryEditRejected as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=_t(exc.code, **exc.params)) from exc
    await session.commit()
    await reload_user_entries(session)
    await invalidate_provider_caches(request)
    return Response(status_code=204)
