"""目录查询：按供应商取模型、列模型、取默认模型与价格的读契约。"""

import pytest

from lib.billing.pricing.types import PerImageFlat
from lib.catalog import query as catalog
from lib.config.registry import ModelInfo, ProviderMeta

_IMAGE_PRICING = PerImageFlat(rates={"img-new": 0.04}, default_model="img-new", currency="USD")


@pytest.fixture
def fake_registry(monkeypatch):
    registry = {
        "acme": ProviderMeta(
            display_name="Acme",
            description="",
            required_keys=["api_key"],
            models={
                "chat": ModelInfo(display_name="Chat", media_type="text", capabilities=["text_generation"]),
                "img-old": ModelInfo(
                    display_name="Img Old", media_type="image", capabilities=["text_to_image"], hidden=True
                ),
                "img-new": ModelInfo(
                    display_name="Img New",
                    media_type="image",
                    capabilities=["text_to_image", "image_to_image"],
                    default=True,
                    resolutions=["1K", "2K"],
                    pricing=_IMAGE_PRICING,
                ),
            },
        ),
    }
    monkeypatch.setattr("lib.config.registry.PROVIDER_REGISTRY", registry)


@pytest.mark.usefixtures("fake_registry")
class TestCatalogQuery:
    def test_get_model_returns_entry_facts_and_none_when_unknown(self):
        model = catalog.get_model("acme", "img-new")

        assert model is not None
        assert model.display_name == "Img New"
        assert model.media_type == "image"
        assert model.capabilities == ["text_to_image", "image_to_image"]
        assert model.resolutions == ["1K", "2K"]
        assert catalog.get_model("acme", "missing") is None
        assert catalog.get_model("unknown-provider", "img-new") is None

    def test_list_models_keeps_registration_order_and_marks_hidden(self):
        models = catalog.list_models("acme")

        assert list(models) == ["chat", "img-old", "img-new"]
        assert [mid for mid, m in models.items() if m.hidden] == ["img-old"]
        assert catalog.list_models("unknown-provider") == {}

    def test_default_model_is_per_media_type(self):
        assert catalog.default_model("acme", "image") == "img-new"
        assert catalog.default_model("acme", "text") is None
        assert catalog.default_model("unknown-provider", "image") is None

    def test_model_pricing_returns_declared_pricing(self):
        assert catalog.model_pricing("acme", "img-new") == _IMAGE_PRICING
        assert catalog.model_pricing("acme", "chat") is None
        assert catalog.model_pricing("acme", "missing") is None

    def test_provider_aggregates_cover_all_models(self):
        assert catalog.provider_media_types("acme") == ["image", "text"]
        assert catalog.provider_capabilities("acme") == ["image_to_image", "text_generation", "text_to_image"]
        assert catalog.provider_media_types("unknown-provider") == []
