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


def _with_listing_models(*extra: dict, defaults: dict | None = None) -> dict:
    raw = {**_CATALOG, "entries": [*_CATALOG["entries"], _entry("next", "text", {})]}
    acme = raw["builtin_providers"]["acme"]
    raw["builtin_providers"] = {
        **raw["builtin_providers"],
        "acme": {"models": [*acme["models"], *extra], "defaults": {**acme["defaults"], **(defaults or {})}},
    }
    return raw


def test_min_app_version_hides_model_and_its_default(monkeypatch):
    raw = _with_listing_models(
        {"api_model_name": "next", "entry": "acme/next", "min_app_version": "999.0.0"},
        defaults={"text": "next"},
    )
    install_catalog(monkeypatch, parse_catalog(raw))

    # 低于最低应用版本：不展示、不可解析；被指定为默认时按没有默认处理
    assert catalog.get_model("acme", "next") is None
    assert "next" not in catalog.list_models("acme")
    assert "next" not in {view.model_id for view in catalog.provider_models("acme")}
    assert catalog.default_model("acme", "text") is None


def test_min_app_version_met_keeps_model(monkeypatch):
    raw = _with_listing_models(
        {"api_model_name": "next", "entry": "acme/next", "min_app_version": "0.1.0"},
        defaults={"text": "next"},
    )
    install_catalog(monkeypatch, parse_catalog(raw))

    assert catalog.get_model("acme", "next") is not None
    assert catalog.default_model("acme", "text") == "next"


@pytest.fixture
def user_added_models(acme_catalog):
    catalog.install_user_added_models(
        [
            catalog.UserAddedModel("acme", "chat-pro", "text", multiplier=0.5),
            catalog.UserAddedModel("acme", "vendor/chat", "text", multiplier=2.0),
            catalog.UserAddedModel("acme", "clip-x", "video", fallback_durations=(4, 8)),
            # 官方清单里已有同一模型键：换成官方版本
            catalog.UserAddedModel("acme", "img-new", "image", multiplier=3.0),
        ]
    )
    yield
    catalog.install_user_added_models(())


@pytest.mark.usefixtures("user_added_models")
class TestUserAddedModels:
    def test_unmatched_model_is_resolvable_and_unpriced(self):
        assert catalog.get_model("acme", "chat-pro") == CatalogModel(
            display_name="chat-pro", media_type="text", capabilities=["text_generation"]
        )
        assert catalog.provider_model_price("acme", "chat-pro", "text") == catalog.ModelPrice(
            price=None, multiplier=0.5, entry_found=False
        )
        video = catalog.get_model("acme", "clip-x", media_type="video")
        assert video is not None
        assert video.supported_durations == [4, 8]

    def test_name_matched_model_takes_entry_facts_and_own_multiplier(self):
        model = catalog.get_model("acme", "vendor/chat")
        assert model is not None
        assert (model.display_name, model.max_output_tokens) == ("Chat", 8192)
        price = catalog.provider_model_price("acme", "vendor/chat", "text")
        assert price is not None
        assert (price.multiplier, price.entry_found) == (2.0, True)

    def test_official_listing_wins_over_same_key(self):
        views = catalog.provider_models("acme")
        assert [(v.model_id, v.official) for v in views if v.model_id == "img-new"] == [("img-new", True)]
        assert [v.model_id for v in views if not v.official] == ["chat-pro", "vendor/chat", "clip-x"]
        price = catalog.provider_model_price("acme", "img-new", "image")
        assert price is not None
        assert price.multiplier == 1.0
