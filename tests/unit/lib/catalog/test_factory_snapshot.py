"""出厂快照：随代码打包、过校验器，且与注册表登记的供应商一级代码事实对得上。

出厂快照由发版 PR 刷新成目录数据仓库的最新数据，这里只核对与具体模型和取值无关的约定：快照能通过校验、
能被目录查询完整读出，快照里的内置供应商就是注册表登记的那些，供应商在某个媒体类型下有模型时，代码里登记了
这一媒体类型的调用通道，各推荐模型均未下线且能完成装配。模型级内容（有哪些模型、取什么值）由目录数据仓库按
``arcreel_catalog_core`` 的构建侧校验把关，这里不断言。
"""

from datetime import datetime
from pathlib import Path

import pytest

from arcreel_catalog_core import CATALOG_FILENAME, MANIFEST_FILENAME, check_catalog_files, dump_entry, parse_entry
from lib.backends.backend_assembly import assemble_backend
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.catalog import query as catalog
from lib.catalog.query import SNAPSHOT_DIR, factory_snapshot
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from tests.catalog_support import set_catalog_today
from tests.fakes import captured_vertex_credentials


@pytest.fixture(autouse=True)
def read_factory_snapshot(monkeypatch):
    """根 conftest 让目录查询读测试目录数据；本文件换回出厂快照。模块导入早于任何替换，``factory_snapshot`` 是原函数。"""
    monkeypatch.setattr(catalog, "factory_snapshot", factory_snapshot)
    set_catalog_today(monkeypatch, datetime.fromisoformat(factory_snapshot().dataset_version).date())


def test_snapshot_passes_build_side_check():
    manifest = (SNAPSHOT_DIR / MANIFEST_FILENAME).read_bytes()
    data = (SNAPSHOT_DIR / CATALOG_FILENAME).read_bytes()

    assert check_catalog_files(manifest, data) == []
    assert catalog.factory_snapshot().dataset_version


def test_snapshot_lists_exactly_the_registry_providers():
    assert set(catalog.factory_snapshot().builtin_providers) == set(PROVIDER_REGISTRY)


@pytest.mark.parametrize("provider_id", list(PROVIDER_REGISTRY))
def test_every_listed_model_resolves_to_an_entry(provider_id: str):
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
    for entry in catalog.factory_snapshot().entries:
        assert parse_entry(dump_entry(entry)) == entry, entry.key


def test_reset_for_tests_reloads_snapshot_and_index():
    before = catalog.factory_snapshot()
    provider_id = next(pid for pid in PROVIDER_REGISTRY if catalog.list_models(pid))
    models = catalog.list_models(provider_id)

    catalog.reset_for_tests()

    assert catalog.factory_snapshot() is not before
    assert catalog.list_models(provider_id) == models


async def test_every_recommended_model_is_callable_and_assembles(db_factory, tmp_path: Path):
    credentials = tmp_path / "vertex.json"
    credentials.write_text('{"project_id": "factory-project"}', encoding="utf-8")
    async with db_factory() as session:
        config = ConfigService(session)
        for provider_id in PROVIDER_REGISTRY:
            for key, value in (
                ("api_key", "test-key"),
                ("access_key", "ak-test"),
                ("secret_key", "sk-test"),
                ("credentials_path", str(credentials)),
                ("gcs_bucket", "factory-bucket"),
            ):
                await config.set_provider_config(provider_id, key, value)
        await session.commit()

    resolver = ConfigResolver(db_factory)
    checked = 0
    with captured_vertex_credentials() as load:
        load.return_value.token = "test-token"
        load.return_value.valid = True
        load.return_value.quota_project_id = None
        for provider_id in sorted(PROVIDER_REGISTRY):
            for media_type in sorted(catalog.provider_media_types(provider_id)):
                model = catalog.default_model(provider_id, media_type)
                if model is None:
                    continue
                catalog.require_callable(provider_id, model, media_type)
                backend = await assemble_backend(
                    provider_id=provider_id, media_type=media_type, model_id=model, resolver=resolver
                )
                assert backend.model == model
                checked += 1

    assert checked > 0
