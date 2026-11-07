"""Custom calls use matched-entry price lists (docs/adr/0100, 0105, 0106)."""

from __future__ import annotations

from pathlib import Path

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.billing.catalog_pricing import price_custom_call
from lib.billing.metering import Priced, Unpriced, UnpricedReason, Usage, token_usage
from lib.custom_provider.metering import generic_video_usage

_GPT_USAGE = (Usage(quantities=token_usage(input_tokens=200_000, output_tokens=100_000)),)


def _price(model: str, *, binding: str | None = None, provider: float = 1.0, model_multiplier: float = 1.0):
    return price_custom_call(
        model=model,
        media_type="text",
        entry_binding=binding,
        requests=_GPT_USAGE,
        provider_multiplier=provider,
        model_multiplier=model_multiplier,
    )


def test_a_relay_name_that_normalizes_to_an_entry_is_charged_at_the_entry_price_times_both_multipliers():
    # gpt-5.4 standard, below 272k context: input 2.5/output 15 per million → 0.5 + 1.5 = 2.0 USD, then ×0.8 ×1.5.
    cost = _price("openai/GPT-5.4", provider=0.8, model_multiplier=1.5)

    assert isinstance(cost, Priced)
    assert cost.currency == "USD"
    assert cost.amount == pytest.approx(2.4)


def test_a_model_that_maps_to_no_entry_is_unpriced_with_reason_no_entry():
    assert _price("my-private-llm") == Unpriced(UnpricedReason.NO_ENTRY)


def test_an_entry_binding_wins_over_the_name():
    cost = _price("relay-renamed-model", binding="openai/gpt-5.4")

    assert isinstance(cost, Priced)
    assert cost.amount == pytest.approx(2.0)


def test_a_binding_to_an_entry_no_longer_in_the_catalog_falls_back_to_the_name():
    cost = _price("gpt-5.4", binding="openai/gone-model")

    assert isinstance(cost, Priced)
    assert cost.amount == pytest.approx(2.0)


def test_a_zero_multiplier_records_zero_without_looking_up_a_price():
    cost = _price("my-private-llm", provider=1.0, model_multiplier=0.0)

    assert isinstance(cost, Priced)
    assert cost.amount == 0


def test_a_video_call_on_a_custom_channel_is_charged_per_second_at_the_sent_resolution():
    request = VideoGenerationRequest(
        prompt="p", output_path=Path("v.mp4"), duration_seconds=5, resolution="1080p", generate_audio=False
    )

    cost = price_custom_call(
        model="kling-v2-6",
        media_type="video",
        entry_binding=None,
        requests=(generic_video_usage(request),),
        provider_multiplier=1.0,
        model_multiplier=1.0,
    )

    # Kling 2.6: 1080p silent 0.5 CNY/second × 5 seconds.
    assert cost == Priced(pytest.approx(2.5), "CNY")


def test_custom_video_quote_carries_overridden_capabilities():
    """Estimates without capabilities add system facts plus user overrides: always-audio Kling wrappers meter audio."""
    from lib.custom_provider.endpoints import get_endpoint_spec
    from lib.custom_provider.metering import CustomPriceSource
    from lib.db.repositories.custom_provider_repo import CustomModelPricing

    request = VideoGenerationRequest(
        prompt="p", output_path=Path("o.mp4"), duration_seconds=5, generate_audio=False, service_tier="pro"
    )
    spec = get_endpoint_spec("kling-video-pro-silent")
    plain = CustomPriceSource(CustomModelPricing(endpoint="kling-video-pro-silent"), spec)
    overridden = CustomPriceSource(
        CustomModelPricing(endpoint="kling-video-pro-silent", capability_overrides={"audio_track": "always_on"}), spec
    )

    assert plain.video_usage("kling-v3", request).dimensions["audio"] is False
    assert overridden.video_usage("kling-v3", request).dimensions["audio"] is True


def test_custom_image_quote_estimates_output_tokens_from_the_bound_entry(monkeypatch: pytest.MonkeyPatch):
    """Opaque IDs bound to entries estimate output tokens from entry quality bases, matching built-ins on the same entry."""
    from lib.backends.image_backends.base import ImageRequestFacts
    from lib.catalog import query
    from lib.custom_provider.endpoints import get_endpoint_spec
    from lib.custom_provider.metering import CustomPriceSource
    from lib.db.repositories.custom_provider_repo import CustomModelPricing
    from tests.catalog_support import install_catalog, with_entry_facts

    bases = {"low": 16, "medium": 48, "high": 96}
    install_catalog(
        monkeypatch, with_entry_facts(query.factory_snapshot(), "openai", "gpt-image-2", output_token_bases=bases)
    )
    facts = ImageRequestFacts(model="relay-image-xyz", aspect_ratio="1:1", image_size="1K", reference_count=0)
    spec = get_endpoint_spec("openai-images")

    bound = CustomPriceSource(CustomModelPricing(endpoint="openai-images", entry_binding="openai/gpt-image-2"), spec)
    unbound = CustomPriceSource(CustomModelPricing(endpoint="openai-images"), spec)

    assert bound.image_usage(facts).quantities["image_output_tokens"] == 1756
    assert "image_output_tokens" not in unbound.image_usage(facts).quantities
