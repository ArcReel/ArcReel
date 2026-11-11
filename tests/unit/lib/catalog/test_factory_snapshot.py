"""出厂快照：随代码打包、过校验器，且与注册表登记的供应商一级代码事实对得上。

出厂快照由发版 PR 刷新成目录数据仓库的最新数据，这里只核对与具体模型和取值无关的约定：快照能通过校验、
能被目录查询完整读出，快照里的内置供应商就是注册表登记的那些，供应商在某个媒体类型下有模型时，代码里登记了
这一媒体类型的调用通道。模型级内容（有哪些模型、取什么值）由目录数据仓库按 ``arcreel_catalog_core`` 的构建侧
校验把关，这里不断言。
"""

import pytest

from arcreel_catalog_core import CATALOG_FILENAME, MANIFEST_FILENAME, check_catalog_files, dump_entry, parse_entry
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.catalog import query as catalog
from lib.catalog.query import SNAPSHOT_DIR, factory_snapshot
from lib.config.registry import PROVIDER_REGISTRY


@pytest.fixture(autouse=True)
def read_factory_snapshot(monkeypatch):
    """根 conftest 让目录查询读测试目录数据；本文件换回出厂快照。模块导入早于任何替换，``factory_snapshot`` 是原函数。"""
    monkeypatch.setattr(catalog, "factory_snapshot", factory_snapshot)


def test_snapshot_passes_build_side_check():
    manifest = (SNAPSHOT_DIR / MANIFEST_FILENAME).read_bytes()
    data = (SNAPSHOT_DIR / CATALOG_FILENAME).read_bytes()

    assert check_catalog_files(manifest, data) == []
    assert catalog.factory_snapshot().dataset_version


def test_snapshot_lists_exactly_the_registry_providers():
    assert set(catalog.factory_snapshot().builtin_providers) == set(PROVIDER_REGISTRY)


@pytest.mark.parametrize("provider_id", list(PROVIDER_REGISTRY))
def test_every_listed_model_resolves_to_an_entry(provider_id: str):
    # 引用不到条目的供应商模型会在查询时被跳过，这里要求清单与查询结果一一对应。
    listing = catalog.factory_snapshot().builtin_providers[provider_id]
    assert len(catalog.list_models(provider_id)) == len(listing.models)


@pytest.mark.parametrize("provider_id", list(PROVIDER_REGISTRY))
def test_every_listed_media_type_has_a_call_channel(provider_id: str):
    for media_type in catalog.provider_media_types(provider_id):
        assert get_provider_spec(provider_id, media_type).provider_id == provider_id


def test_every_listed_model_with_a_price_table_is_priceable():
    data = catalog.factory_snapshot()
    unpriceable = [
        f"{provider_id}/{model.api_model_name}"
        for provider_id, listing in data.builtin_providers.items()
        for model in listing.models
        if (entry := data.entry_of(model)) is not None and entry.price is not None and not entry.price.is_priceable
    ]
    assert unpriceable == []


def test_every_entry_survives_a_user_copy_round_trip():
    # 用户修改存下整条副本：任何快照条目（含价目表）写回再读入都必须原样还原
    for entry in catalog.factory_snapshot().entries:
        assert parse_entry(dump_entry(entry)) == entry, entry.key


def test_reset_for_tests_reloads_snapshot_and_index():
    before = catalog.factory_snapshot()
    provider_id = next(pid for pid in PROVIDER_REGISTRY if catalog.list_models(pid))
    models = catalog.list_models(provider_id)

    catalog.reset_for_tests()

    assert catalog.factory_snapshot() is not before
    assert catalog.list_models(provider_id) == models
