"""自定义供应商调用按所对应条目的价目表计价（docs/adr/0100、0105、0106）。"""

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
    # gpt-5.4 标准档、上下文不足 272k：输入 2.5、输出 15（每百万 token）→ 0.5 + 1.5 = 2.0 USD，再乘 0.8 × 1.5
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

    # 可灵 2.6：1080p 无声 0.5 CNY/秒 × 5 秒
    assert cost == Priced(pytest.approx(2.5), "CNY")
