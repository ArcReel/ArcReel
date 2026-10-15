"""目录查询的测试数据：在出厂快照上改写条目或换一份目录数据，再交给目录查询读取。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from arcreel_catalog_core import BuiltinProviderListing, BuiltinProviderModel, CatalogData, CatalogEntry
from lib.catalog import query


def install_catalog(monkeypatch: pytest.MonkeyPatch, data: CatalogData) -> None:
    """让目录查询在本用例内读 ``data``，代替出厂快照。"""
    monkeypatch.setattr(query, "factory_snapshot", lambda: data)


def with_entry_changes(data: CatalogData, provider_id: str, api_model_name: str, **changes: Any) -> CatalogData:
    """把供应商模型引用的条目按 ``dataclasses.replace`` 改写；改的是条目，引用它的其他供应商模型一并受影响。"""
    listing = data.builtin_providers[provider_id]
    model = next(m for m in listing.models if m.api_model_name == api_model_name)
    target = data.entry_of(model)
    assert target is not None
    entries = tuple(replace(entry, **changes) if entry.key == target.key else entry for entry in data.entries)
    return replace(data, entries=entries)


def with_entry_facts(data: CatalogData, provider_id: str, api_model_name: str, **changes: Any) -> CatalogData:
    """改写供应商模型所引用条目的模型事实（``facts`` 上的字段）。"""
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
    """由条目与「供应商 → 条目引用」构造一份目录数据；供应商模型的 API 模型名取条目的官方模型名。"""
    listings = {
        provider_id: BuiltinProviderListing(
            models=tuple(BuiltinProviderModel(api_model_name=ref.partition("/")[2], entry=ref) for ref in refs),
            defaults=(defaults or {}).get(provider_id, {}),
        )
        for provider_id, refs in providers.items()
    }
    return CatalogData(dataset_version="2026-01-01T00:00:00Z", entries=entries, builtin_providers=listings)
