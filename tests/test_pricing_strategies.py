"""按 kind 的计费策略纯函数测试：canonical 输入 + 各 fallback 分支。"""

from __future__ import annotations

import pytest

from lib.pricing.strategies import PricingParams, calculate_pricing
from lib.pricing.types import (
    PerImageByResolution,
    PerImageFlat,
    PerImageOpenAIToken,
    PerSecondMatrix,
    PerToken,
    PerTokenVideo,
    ViduDelegate,
)


class TestPerToken:
    pricing = PerToken(
        rates={"m1": {"input": 0.50, "output": 3.00}},
        default_model="m1",
        currency="USD",
    )

    def test_known_model(self):
        amount, currency = calculate_pricing(
            self.pricing, PricingParams(call_type="text", model="m1", input_tokens=1000, output_tokens=500)
        )
        assert currency == "USD"
        assert amount == pytest.approx((1000 * 0.50 + 500 * 3.00) / 1_000_000)

    def test_unknown_model_falls_back_to_default(self):
        amount, _ = calculate_pricing(
            self.pricing, PricingParams(call_type="text", model="unknown", input_tokens=1000, output_tokens=500)
        )
        assert amount == pytest.approx((1000 * 0.50 + 500 * 3.00) / 1_000_000)

    def test_none_model_uses_default(self):
        amount, _ = calculate_pricing(self.pricing, PricingParams(call_type="text", input_tokens=1000, output_tokens=0))
        assert amount == pytest.approx(1000 * 0.50 / 1_000_000)


class TestPerImageFlat:
    pricing = PerImageFlat(rates={"m1": 0.22}, default_model="m1", currency="CNY")

    def test_single_image(self):
        amount, currency = calculate_pricing(self.pricing, PricingParams(call_type="image", model="m1"))
        assert currency == "CNY"
        assert amount == pytest.approx(0.22)

    def test_n_images(self):
        amount, _ = calculate_pricing(self.pricing, PricingParams(call_type="image", model="m1", n=3))
        assert amount == pytest.approx(0.22 * 3)

    def test_unknown_model_falls_back(self):
        amount, _ = calculate_pricing(self.pricing, PricingParams(call_type="image", model="unknown", n=4))
        assert amount == pytest.approx(0.22 * 4)


class TestPerImageByResolution:
    pricing = PerImageByResolution(
        rates={"m1": {"512PX": 0.045, "1K": 0.067, "2K": 0.101, "4K": 0.151}},
        default_model="m1",
        currency="USD",
    )

    def test_known_resolution_uppercased(self):
        amount, currency = calculate_pricing(
            self.pricing, PricingParams(call_type="image", model="m1", resolution="1k")
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.067)

    def test_high_resolution(self):
        amount, _ = calculate_pricing(self.pricing, PricingParams(call_type="image", model="m1", resolution="4K"))
        assert amount == pytest.approx(0.151)

    def test_unknown_resolution_falls_back_to_1k(self):
        amount, _ = calculate_pricing(self.pricing, PricingParams(call_type="image", model="m1", resolution="unknown"))
        assert amount == pytest.approx(0.067)

    def test_none_resolution_defaults_to_1k(self):
        amount, _ = calculate_pricing(self.pricing, PricingParams(call_type="image", model="m1"))
        assert amount == pytest.approx(0.067)

    def test_n_images(self):
        amount, _ = calculate_pricing(self.pricing, PricingParams(call_type="image", model="m1", resolution="1K", n=3))
        assert amount == pytest.approx(0.067 * 3)

    def test_zero_1k_rate_not_treated_as_missing(self):
        # free 模型 1K 显式 0.0，未知分辨率回落 default_cost 时应保留自身 0.0，不误用 paid 费率。
        pricing = PerImageByResolution(
            rates={"free": {"1K": 0.0}, "paid": {"1K": 0.067}},
            default_model="paid",
            currency="USD",
        )
        amount, _ = calculate_pricing(pricing, PricingParams(call_type="image", model="free", resolution="4K"))
        assert amount == pytest.approx(0.0)


class TestPerImageOpenAIToken:
    pricing = PerImageOpenAIToken(
        token_rates={
            "gpt-image-2": {
                "image_in": 8.0,
                "image_cached_in": 2.0,
                "image_out": 30.0,
                "text_in": 5.0,
                "text_cached_in": 1.25,
                "text_out": 0.0,
            }
        },
        fallback_rates={
            "gpt-image-2": {
                ("low", "1024x1024"): 0.006,
                ("medium", "1024x1024"): 0.053,
                ("medium", "1024x1792"): 0.106,
                ("high", "1024x1024"): 0.211,
                ("high", "1024x1792"): 0.317,
                ("high", "1792x1024"): 0.317,
            }
        },
        default_model="gpt-image-2",
        currency="USD",
    )

    def test_token_path(self):
        amount, currency = calculate_pricing(
            self.pricing,
            PricingParams(
                call_type="image",
                model="gpt-image-2",
                image_input_tokens=10_000,
                image_output_tokens=2_000,
                text_input_tokens=500,
                text_output_tokens=100,
            ),
        )
        assert currency == "USD"
        assert amount == pytest.approx((10_000 * 8 + 2_000 * 30 + 500 * 5 + 100 * 0) / 1_000_000)

    def test_zero_tokens_still_token_path(self):
        amount, _ = calculate_pricing(
            self.pricing,
            PricingParams(
                call_type="image",
                model="gpt-image-2",
                image_input_tokens=0,
                image_output_tokens=0,
                text_input_tokens=0,
                text_output_tokens=0,
            ),
        )
        assert amount == pytest.approx(0.0)

    def test_fallback_no_size_defaults_to_1024_square(self):
        # size=None, resolution=None, aspect_ratio=None → 不反查 → 1024x1024
        amount, _ = calculate_pricing(
            self.pricing, PricingParams(call_type="image", model="gpt-image-2", quality="medium")
        )
        assert amount == pytest.approx(0.053)

    def test_fallback_no_size_falls_to_default_square(self):
        # 计费与输出尺寸解耦（adr 0011）：size 反查已删，即便有 resolution+aspect_ratio，
        # 无显式 size 也落默认 1024x1024 档（接受兜底丧失按尺寸区分成本；主路径走 token 不受影响）
        amount, _ = calculate_pricing(
            self.pricing,
            PricingParams(call_type="image", model="gpt-image-2", quality="high", resolution="1K", aspect_ratio="9:16"),
        )
        assert amount == pytest.approx(0.211)  # ("high","1024x1024")，非旧反查的 0.317

    def test_fallback_explicit_size_overrides(self):
        amount, _ = calculate_pricing(
            self.pricing,
            PricingParams(
                call_type="image",
                model="gpt-image-2",
                quality="medium",
                resolution="1K",
                aspect_ratio="1:1",
                size="1024x1792",
            ),
        )
        assert amount == pytest.approx(0.106)

    def test_fallback_unknown_quality_size_three_level(self):
        # (quality, size) 与 (quality, 1024x1024) 都缺 → ("medium","1024x1024") 0.053
        amount, _ = calculate_pricing(
            self.pricing,
            PricingParams(call_type="image", model="gpt-image-2", quality="ultra", size="9999x9999"),
        )
        assert amount == pytest.approx(0.053)

    def test_fallback_n_images(self):
        # 兜底路径同样按 n 缩放（与 token 路径无关）
        amount, _ = calculate_pricing(
            self.pricing,
            PricingParams(call_type="image", model="gpt-image-2", quality="medium", n=2),
        )
        assert amount == pytest.approx(0.053 * 2)


class TestPerSecondMatrix:
    audio = PerSecondMatrix(
        rates={
            "veo": {
                ("720p", True): 0.05,
                ("720p", False): 0.05,
                ("1080p", True): 0.08,
                ("1080p", False): 0.08,
            }
        },
        default_model="veo",
        dimensions="resolution_audio",
        currency="USD",
    )
    res_only = PerSecondMatrix(
        rates={"sora": {("720p", None): 0.30, ("1024p", None): 0.50, ("1080p", None): 0.70}},
        default_model="sora",
        dimensions="resolution_only",
        currency="USD",
    )
    flat = PerSecondMatrix(
        rates={"grok": {("", None): 0.050}},
        default_model="grok",
        dimensions="flat",
        currency="USD",
    )

    def test_resolution_audio_known(self):
        amount, currency = calculate_pricing(
            self.audio,
            PricingParams(call_type="video", model="veo", duration_seconds=8, resolution="1080p", generate_audio=True),
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.64)

    def test_resolution_audio_lowercased_and_unknown_falls_back_to_1080p_true(self):
        # resolution="UNKNOWN" → .lower() 后查不到 → fallback ("1080p", True) = 0.08
        amount, _ = calculate_pricing(
            self.audio,
            PricingParams(
                call_type="video", model="veo", duration_seconds=5, resolution="UNKNOWN", generate_audio=True
            ),
        )
        assert amount == pytest.approx(0.40)

    def test_resolution_only_known(self):
        amount, _ = calculate_pricing(
            self.res_only,
            PricingParams(call_type="video", model="sora", duration_seconds=4, resolution="1080p"),
        )
        assert amount == pytest.approx(2.80)

    def test_resolution_only_defaults_to_720p(self):
        amount, _ = calculate_pricing(self.res_only, PricingParams(call_type="video", model="sora", duration_seconds=8))
        assert amount == pytest.approx(2.40)

    def test_resolution_only_uppercased(self):
        # 大写分辨率经 .lower() 归一后命中小写费率键，不再回落 720p
        amount, _ = calculate_pricing(
            self.res_only,
            PricingParams(call_type="video", model="sora", duration_seconds=4, resolution="1080P"),
        )
        assert amount == pytest.approx(2.80)

    def test_flat_ignores_resolution(self):
        amount, _ = calculate_pricing(
            self.flat,
            PricingParams(call_type="video", model="grok", duration_seconds=10, resolution="480p"),
        )
        assert amount == pytest.approx(0.50)

    def test_duration_defaults_to_8(self):
        amount, _ = calculate_pricing(self.flat, PricingParams(call_type="video", model="grok"))
        assert amount == pytest.approx(0.40)

    def test_resolution_audio_zero_fallback_rate_not_treated_as_missing(self):
        # free 模型 (1080p,True) 显式 0.0，未知分辨率回落 fallback 时应保留自身 0.0，不误用 paid 费率。
        pricing = PerSecondMatrix(
            rates={"free": {("1080p", True): 0.0}, "paid": {("1080p", True): 0.08}},
            default_model="paid",
            dimensions="resolution_audio",
            currency="USD",
        )
        amount, _ = calculate_pricing(
            pricing,
            PricingParams(
                call_type="video", model="free", duration_seconds=10, resolution="UNKNOWN", generate_audio=True
            ),
        )
        assert amount == pytest.approx(0.0)


class TestPerTokenVideo:
    pricing = PerTokenVideo(
        rates={
            "seedance": {
                ("default", True): 16.00,
                ("default", False): 8.00,
                ("flex", True): 8.00,
                ("flex", False): 4.00,
            }
        },
        default_model="seedance",
    )

    def test_default_currency_is_cny(self):
        _, currency = calculate_pricing(
            self.pricing, PricingParams(call_type="video", model="seedance", usage_tokens=0)
        )
        assert currency == "CNY"

    def test_tier_audio_matrix(self):
        amount, _ = calculate_pricing(
            self.pricing,
            PricingParams(
                call_type="video", model="seedance", usage_tokens=1_000_000, service_tier="flex", generate_audio=False
            ),
        )
        assert amount == pytest.approx(4.00)

    def test_unknown_key_falls_back_to_default_true(self):
        # service_tier 未在表中 → fallback ("default", True) = 16.00
        amount, _ = calculate_pricing(
            self.pricing,
            PricingParams(call_type="video", model="seedance", usage_tokens=1_000_000, service_tier="ultra"),
        )
        assert amount == pytest.approx(16.00)

    def test_unknown_model_falls_back_to_default(self):
        amount, _ = calculate_pricing(
            self.pricing,
            PricingParams(call_type="video", model="unknown", usage_tokens=1_000_000, generate_audio=True),
        )
        assert amount == pytest.approx(16.00)


class TestViduDelegate:
    def test_credits_path(self):
        # usage_tokens (credits) 给定 → credits × 0.03125 CNY
        amount, currency = calculate_pricing(
            ViduDelegate(),
            PricingParams(call_type="video", model="viduq3-turbo", usage_tokens=100),
        )
        assert currency == "CNY"
        assert amount == pytest.approx(100 * 0.03125)

    def test_fallback_passes_original_model(self):
        # 无 credits → 按表估算，model 透传（viduq3-turbo 720p = 12 credits/s）
        amount, currency = calculate_pricing(
            ViduDelegate(),
            PricingParams(call_type="video", model="viduq3-turbo", resolution="720p", duration_seconds=5),
        )
        assert currency == "CNY"
        assert amount == pytest.approx(5 * 12 * 0.03125)
