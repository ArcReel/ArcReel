"""Catalog query test data: rewrite entries in the factory snapshot or replace catalog data for queries; price one built-in-provider call from catalog rates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from arcreel_catalog_core import BuiltinProviderListing, BuiltinProviderModel, CatalogData, CatalogEntry
from lib.billing.catalog_pricing import price_builtin_call
from lib.billing.metering import Priced, Usage
from lib.catalog import lifecycle, query


def install_catalog(monkeypatch: pytest.MonkeyPatch, data: CatalogData) -> None:
    """Make catalog queries read ``data`` instead of the factory snapshot for this case."""
    monkeypatch.setattr(query, "factory_snapshot", lambda: data)


def set_catalog_today(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    """Make the catalog read model treat ``day`` as today (UTC) for lifecycle checks; call again mid-case to cross days."""
    monkeypatch.setattr(lifecycle, "utc_today", lambda: day)


def with_entry_changes(data: CatalogData, provider_id: str, api_model_name: str, **changes: Any) -> CatalogData:
    """Rewrite the entry referenced by a provider model through ``dataclasses.replace``; other provider models sharing it also change."""
    listing = data.builtin_providers[provider_id]
    model = next(m for m in listing.models if m.api_model_name == api_model_name)
    target = data.entry_of(model)
    assert target is not None
    entries = tuple(replace(entry, **changes) if entry.key == target.key else entry for entry in data.entries)
    return replace(data, entries=entries)


def with_min_app_version(data: CatalogData, provider_id: str, api_model_name: str, version: str) -> CatalogData:
    """Set a minimum application version on one officially cataloged built-in-provider model."""
    listing = data.builtin_providers[provider_id]
    models = tuple(
        replace(model, min_app_version=version) if model.api_model_name == api_model_name else model
        for model in listing.models
    )
    providers = {**data.builtin_providers, provider_id: replace(listing, models=models)}
    return replace(data, builtin_providers=providers)


def with_entry_facts(data: CatalogData, provider_id: str, api_model_name: str, **changes: Any) -> CatalogData:
    """Rewrite model facts (fields on ``facts``) of the entry referenced by a provider model."""
    listing = data.builtin_providers[provider_id]
    model = next(m for m in listing.models if m.api_model_name == api_model_name)
    target = data.entry_of(model)
    assert target is not None
    return with_entry_changes(data, provider_id, api_model_name, facts=replace(target.facts, **changes))


def catalog_of(
    entries: tuple[CatalogEntry, ...],
    providers: Mapping[str, tuple[str, ...]],
    defaults: Mapping[str, dict[str, str]] | None = None,
) -> CatalogData:
    """Build catalog data from entries and provider-to-entry references; provider API model names use official entry model names."""
    listings = {
        provider_id: BuiltinProviderListing(
            models=tuple(BuiltinProviderModel(api_model_name=ref.partition("/")[2], entry=ref) for ref in refs),
            defaults=(defaults or {}).get(provider_id, {}),
        )
        for provider_id, refs in providers.items()
    }
    return CatalogData(dataset_version="2026-01-01T00:00:00Z", entries=entries, builtin_providers=listings)


def builtin_price(
    provider_id: str,
    model: str,
    media_type: str,
    quantities: Mapping[str, float],
    dimensions: Mapping[str, object] | None = None,
) -> tuple[float, str]:
    """Reference cost ``(金额, 币种)`` of one single-request built-in-provider call at multiplier 1; assert if unavailable."""
    cost = price_builtin_call(
        provider=provider_id,
        model=model,
        media_type=media_type,
        requests=(Usage(quantities=quantities, dimensions=dimensions or {}),),
        provider_multiplier=1.0,
    )
    assert isinstance(cost, Priced), cost
    return cost.amount, cost.currency
