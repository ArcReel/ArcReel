"""出厂快照：随代码打包、过校验器，且与注册表登记的供应商一级代码事实对得上。

模型清单、模型事实、默认模型与价目表只在快照里有一份，模型级内容的登记见 ``test_builtin_provider_models.py``。
这里核对快照与代码之间的约定：快照里的内置供应商就是注册表登记的那些，供应商在某个媒体类型下有模型时，
代码里登记了这一媒体类型的调用通道。
"""

import pytest

from arcreel_catalog_core import CATALOG_FILENAME, MANIFEST_FILENAME, check_catalog_files, dump_entry, parse_entry
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.catalog import query as catalog
from lib.catalog.query import SNAPSHOT_DIR
from lib.config.registry import PROVIDER_REGISTRY


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


def test_agent_plan_model_sharing_an_entry_takes_the_maker_facts():
    # 方舟 Agent Plan 的 minimax-m2.7 与 MiniMax 供应商的 MiniMax-M2.7 名字归一化后相同，按目录格式只能共用
    # 一个条目，事实取 MiniMax 官方的登记。
    plan = catalog.get_model("ark-agent-plan", "minimax-m2.7")
    maker = catalog.get_model("minimax", "MiniMax-M2.7")

    assert plan is not None
    assert maker is not None
    assert (plan.display_name, plan.capabilities, plan.max_output_tokens) == (
        maker.display_name,
        maker.capabilities,
        maker.max_output_tokens,
    )


def test_amphibious_legacy_key_resolves_to_image_entry():
    model = catalog.get_model("kling", "kling-v3-omni-image")

    assert model is not None
    assert (model.media_type, model.api_model_name) == ("image", "kling-v3-omni")


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
