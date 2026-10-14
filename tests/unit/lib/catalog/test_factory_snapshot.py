"""出厂快照：随代码打包、过校验器，且与注册表登记的模型保持一致。

注册表的 ``ModelInfo`` 在计费迁到价目表之前仍保留模型字段。快照由注册表一次性转换而来，这里核对两份数据
没有分叉：往注册表加模型而不同步快照，或反过来，都会在这里失败。
"""

import pytest

from arcreel_catalog_core import CATALOG_FILENAME, MANIFEST_FILENAME, check_catalog_files
from lib.billing.pricing.types import ViduDelegate
from lib.catalog import query as catalog
from lib.catalog.query import SNAPSHOT_DIR, CatalogModel
from lib.config.registry import PROVIDER_REGISTRY, ModelInfo

# 方舟 Agent Plan 的 minimax-m2.7 与 MiniMax 供应商的 MiniMax-M2.7 名字归一化后相同，按目录格式只能共用
# 一个条目，事实取 MiniMax 官方的登记（多出结构化输出、最大输出 204800）。
_SHARED_ENTRY_DEVIATIONS = {("ark-agent-plan", "minimax-m2.7")}


def _registry_view(info: ModelInfo) -> CatalogModel:
    return CatalogModel(
        display_name=info.display_name,
        media_type=info.media_type,
        capabilities=info.capabilities,
        default=info.default,
        supported_durations=info.supported_durations,
        duration_resolution_constraints=info.duration_resolution_constraints,
        reference_image_durations=info.reference_image_durations,
        resolutions=info.resolutions,
        hidden=info.hidden,
        api_model_name=info.api_model_name,
        max_output_tokens=info.max_output_tokens,
    )


def _registry_models() -> list[tuple[str, str, ModelInfo]]:
    return [
        (provider_id, model_id, info)
        for provider_id, meta in PROVIDER_REGISTRY.items()
        for model_id, info in meta.models.items()
    ]


def test_snapshot_passes_build_side_check():
    manifest = (SNAPSHOT_DIR / MANIFEST_FILENAME).read_bytes()
    data = (SNAPSHOT_DIR / CATALOG_FILENAME).read_bytes()

    assert check_catalog_files(manifest, data) == []
    assert catalog.factory_snapshot().dataset_version


@pytest.mark.parametrize("provider_id", list(PROVIDER_REGISTRY))
def test_snapshot_lists_every_registry_model_in_order(provider_id: str):
    assert list(catalog.list_models(provider_id)) == list(PROVIDER_REGISTRY[provider_id].models)


@pytest.mark.parametrize(
    ("provider_id", "model_id", "info"),
    [
        pytest.param(*row, id=f"{row[0]}/{row[1]}")
        for row in _registry_models()
        if (row[0], row[1]) not in _SHARED_ENTRY_DEVIATIONS
    ],
)
def test_snapshot_model_matches_registry(provider_id: str, model_id: str, info: ModelInfo):
    assert catalog.get_model(provider_id, model_id) == _registry_view(info)


def test_agent_plan_model_sharing_an_entry_takes_the_maker_facts():
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


@pytest.mark.parametrize(
    ("provider_id", "model_id", "info"),
    [
        pytest.param(*row, id=f"{row[0]}/{row[1]}")
        for row in _registry_models()
        if row[2].pricing is not None and not isinstance(row[2].pricing, ViduDelegate)
    ],
)
def test_priced_registry_model_has_price_table_in_same_currency(provider_id: str, model_id: str, info: ModelInfo):
    data = catalog.factory_snapshot()
    api_model_name = info.api_model_name or model_id
    listed = [m for m in data.builtin_providers[provider_id].models if m.api_model_name == api_model_name]
    entries = [entry for m in listed if (entry := data.entry_of(m)) is not None and entry.media_type == info.media_type]

    assert len(entries) == 1
    price = entries[0].price
    assert price is not None
    assert info.pricing is not None
    assert (price.currency, price.is_priceable) == (info.pricing.currency, True)
