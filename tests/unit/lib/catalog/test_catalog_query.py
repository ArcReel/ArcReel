"""目录查询：按供应商取模型、列模型、取默认模型与价格的读契约。"""

import pytest

from arcreel_catalog_core import parse_catalog
from lib.billing.pricing.types import PerImageFlat
from lib.catalog import query as catalog
from lib.catalog.query import CatalogModel
from lib.config.registry import ModelInfo, ProviderMeta
from tests.catalog_support import install_catalog

_IMAGE_PRICING = PerImageFlat(rates={"img-new": 0.04}, default_model="img-new", currency="USD")


def _entry(name: str, media_type: str, facts: dict, **extra) -> dict:
    return {
        "maker": "acme",
        "name": name,
        "media_type": media_type,
        "display_name": name.title(),
        "facts": facts,
        **extra,
    }


_CATALOG = {
    "dataset_version": "2026-01-01T00:00:00Z",
    "entries": [
        _entry("chat", "text", {"image_input": True, "max_output_tokens": 8192}),
        _entry("img-old", "image", {"text_to_image": True}, discontinued=True),
        _entry("img-new", "image", {"text_to_image": True, "image_to_image": True, "resolutions": ["1K", "2K"]}),
        _entry(
            "omni",
            "video",
            {"durations": [5, 10], "resolutions": ["720p"], "duration_constraints": {"with_reference_images": [5]}},
        ),
        _entry("omni", "image", {"image_to_image": True}),
        _entry("tts", "audio", {}),
    ],
    "builtin_providers": {
        "acme": {
            "models": [
                {"api_model_name": "chat", "entry": "acme/chat"},
                {"api_model_name": "img-old", "entry": "acme/img-old"},
                {"api_model_name": "img-new", "entry": "acme/img-new"},
                {"api_model_name": "ghost", "entry": "acme/ghost"},
                {"api_model_name": "tts", "entry": "acme/tts"},
            ],
            "defaults": {"image": "img-new"},
        },
        "kling": {
            "models": [
                {"api_model_name": "omni", "entry": "acme/omni", "media_type": "video"},
                {"api_model_name": "kling-v3-omni", "entry": "acme/omni", "media_type": "image"},
            ],
            "defaults": {"image": "kling-v3-omni"},
        },
    },
}


@pytest.fixture
def acme_catalog(monkeypatch):
    install_catalog(monkeypatch, parse_catalog(_CATALOG))


@pytest.mark.usefixtures("acme_catalog")
class TestCatalogQuery:
    def test_get_model_reads_entry_facts_and_none_when_unknown(self):
        assert catalog.get_model("acme", "img-new") == CatalogModel(
            display_name="Img-New",
            media_type="image",
            capabilities=["text_to_image", "image_to_image"],
            default=True,
            resolutions=["1K", "2K"],
        )
        assert catalog.get_model("acme", "missing") is None
        assert catalog.get_model("unknown-provider", "img-new") is None

    def test_capabilities_follow_entry_facts_per_media_type(self):
        chat = catalog.get_model("acme", "chat")
        tts = catalog.get_model("acme", "tts")

        assert chat is not None
        assert (chat.capabilities, chat.max_output_tokens) == (["text_generation", "vision"], 8192)
        assert tts is not None
        assert tts.capabilities == ["text_to_speech"]

    def test_list_models_keeps_listing_order_and_hides_discontinued_entries(self):
        models = catalog.list_models("acme")

        assert list(models) == ["chat", "img-old", "img-new", "tts"]
        assert [mid for mid, m in models.items() if m.hidden] == ["img-old"]
        assert catalog.list_models("unknown-provider") == {}

    def test_amphibious_image_model_keeps_its_legacy_key(self):
        models = catalog.list_models("kling")

        assert list(models) == ["omni", "kling-v3-omni-image"]
        image = models["kling-v3-omni-image"]
        assert (image.media_type, image.api_model_name, image.default) == ("image", "kling-v3-omni", True)
        video = models["omni"]
        assert (video.api_model_name, video.supported_durations, video.reference_image_durations) == (
            None,
            [5, 10],
            [5],
        )

    def test_default_model_is_per_media_type(self):
        assert catalog.default_model("acme", "image") == "img-new"
        assert catalog.default_model("acme", "text") is None
        assert catalog.default_model("kling", "image") == "kling-v3-omni-image"
        assert catalog.default_model("unknown-provider", "image") is None

    def test_provider_aggregates_cover_all_models(self):
        assert catalog.provider_media_types("acme") == ["audio", "image", "text"]
        assert catalog.provider_capabilities("acme") == [
            "image_to_image",
            "text_generation",
            "text_to_image",
            "text_to_speech",
            "vision",
        ]
        assert catalog.provider_media_types("unknown-provider") == []


def test_model_pricing_reads_registry_pricing(monkeypatch):
    registry = {
        "acme": ProviderMeta(
            display_name="Acme",
            description="",
            required_keys=["api_key"],
            models={
                "chat": ModelInfo(display_name="Chat", media_type="text", capabilities=["text_generation"]),
                "img-new": ModelInfo(
                    display_name="Img New", media_type="image", capabilities=["text_to_image"], pricing=_IMAGE_PRICING
                ),
            },
        ),
    }
    monkeypatch.setattr("lib.config.registry.PROVIDER_REGISTRY", registry)

    assert catalog.model_pricing("acme", "img-new") == _IMAGE_PRICING
    assert catalog.model_pricing("acme", "chat") is None
    assert catalog.model_pricing("acme", "missing") is None
    assert catalog.model_pricing("unknown-provider", "img-new") is None


def test_reset_for_tests_reloads_snapshot_and_index():
    before = catalog.factory_snapshot()
    model = catalog.get_model("gemini-aistudio", "gemini-3-pro-image")

    catalog.reset_for_tests()

    assert catalog.factory_snapshot() is not before
    assert catalog.get_model("gemini-aistudio", "gemini-3-pro-image") == model
