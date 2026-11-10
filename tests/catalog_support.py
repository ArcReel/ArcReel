"""目录查询的测试数据：换一份目录数据或在测试目录数据上改写条目，再交给目录查询读取；按目录价目表给内置供应商的一次调用计价。

用例默认读测试目录数据（``tests/fixtures/catalog.json``，由根 conftest 装入），不读出厂快照：出厂快照随发版刷新成
目录数据仓库的最新数据，测试断言的模型与取值不能跟着变。这份数据归测试所有，用例需要的数据变化改这份文件，或用
本模块的 ``with_*``、``catalog_of`` 在用例内构造。只有 ``tests/unit/lib/catalog/test_factory_snapshot.py`` 读出厂快照，
核对它本身的格式与可加载性。
"""

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
    """让目录查询在本用例内读 ``data``，代替测试目录数据。"""
    monkeypatch.setattr(query, "factory_snapshot", lambda: data)


def set_catalog_today(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    """让目录读模型在本用例内把 ``day`` 当作今天（UTC 日期），判定生命周期用；可在用例中途再次调用跨日。"""
    monkeypatch.setattr(lifecycle, "utc_today", lambda: day)


def with_entry_changes(data: CatalogData, provider_id: str, api_model_name: str, **changes: Any) -> CatalogData:
    """把供应商模型引用的条目按 ``dataclasses.replace`` 改写；改的是条目，引用它的其他供应商模型一并受影响。"""
    listing = data.builtin_providers[provider_id]
    model = next(m for m in listing.models if m.api_model_name == api_model_name)
    target = data.entry_of(model)
    assert target is not None
    entries = tuple(replace(entry, **changes) if entry.key == target.key else entry for entry in data.entries)
    return replace(data, entries=entries)


def with_min_app_version(data: CatalogData, provider_id: str, api_model_name: str, version: str) -> CatalogData:
    """给内置供应商的一个官方收录模型标上最低应用版本。"""
    listing = data.builtin_providers[provider_id]
    models = tuple(
        replace(model, min_app_version=version) if model.api_model_name == api_model_name else model
        for model in listing.models
    )
    providers = {**data.builtin_providers, provider_id: replace(listing, models=models)}
    return replace(data, builtin_providers=providers)


def with_recommendation(
    data: CatalogData, provider_id: str, media_type: str, api_model_name: str | None
) -> CatalogData:
    """改写内置供应商在这个媒体类型下的推荐模型（目录里的默认模型）；``None`` 即去掉这一项。"""
    listing = data.builtin_providers[provider_id]
    defaults = {key: value for key, value in listing.defaults.items() if key != media_type}
    if api_model_name is not None:
        defaults[media_type] = api_model_name
    providers = {**data.builtin_providers, provider_id: replace(listing, defaults=defaults)}
    return replace(data, builtin_providers=providers)


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


def builtin_price(
    provider_id: str,
    model: str,
    media_type: str,
    quantities: Mapping[str, float],
    dimensions: Mapping[str, object] | None = None,
) -> tuple[float, str]:
    """内置供应商一次单请求调用在倍率 1 下的参考费用 ``(金额, 币种)``；算不出时断言失败。"""
    cost = price_builtin_call(
        provider=provider_id,
        model=model,
        media_type=media_type,
        requests=(Usage(quantities=quantities, dimensions=dimensions or {}),),
        provider_multiplier=1.0,
    )
    assert isinstance(cost, Priced), cost
    return cost.amount, cost.currency
