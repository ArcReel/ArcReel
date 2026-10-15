"""统一入口 ``calculate_cost`` 的费用对拍：金额数值与币种逐项保持迁移前不变。

底层 per-shape 方法已删除，断言改指向公共入口 ``calculate_cost(provider, call_type, ...)``，
用各模型所属的规范 provider id（veo ``-001`` 在 gemini-vertex、``-preview`` / ``-lite`` 在
gemini-aistudio）。仅在底层暴露的形态（如按张 n、纯策略 fallback）见 ``test_pricing_strategies``。
"""

import pytest

from lib.backends.providers import PROVIDER_ANTHROPIC
from lib.billing.cost_calculator import CostCalculator, cost_calculator
from lib.billing.pricing.strategies import PricingParams


def _cost(provider, call_type, **params):
    """构造 ``PricingParams`` 并调统一入口——费用对拍只在意金额/币种，用薄封装免逐行铺展。"""
    return cost_calculator.calculate_cost(provider, PricingParams(call_type=call_type, **params))


class TestImageCost:
    def test_calculate_image_cost_known_and_default(self):
        # 默认模型 (gemini-3.1-flash-image)
        assert _cost("gemini-aistudio", "image", resolution="1k") == (0.067, "USD")
        assert _cost("gemini-aistudio", "image", resolution="2K") == (0.101, "USD")
        assert _cost("gemini-aistudio", "image", resolution="4K") == (0.151, "USD")
        assert _cost("gemini-aistudio", "image", resolution="unknown") == (0.067, "USD")
        # 指定旧模型 (gemini-3-pro-image)
        assert _cost("gemini-aistudio", "image", resolution="1k", model="gemini-3-pro-image") == (
            0.134,
            "USD",
        )
        assert _cost("gemini-aistudio", "image", resolution="2K", model="gemini-3-pro-image") == (
            0.134,
            "USD",
        )


class TestVideoCost:
    def test_calculate_video_cost_known_and_default(self):

        def video(duration, resolution, audio, provider="gemini-aistudio", model=None):
            amount, _ = _cost(
                provider,
                "video",
                duration_seconds=duration,
                resolution=resolution,
                generate_audio=audio,
                model=model,
            )
            return amount

        # 默认模型 (veo-3.1-lite-generate-preview)：1080p 含音 0.08 / 无音 0.05，720p 0.05 / 0.03
        assert video(8, "1080p", True) == pytest.approx(0.64)
        assert video(8, "1080p", False) == pytest.approx(0.40)
        assert video(8, "720p", True) == pytest.approx(0.40)
        assert video(8, "720p", False) == pytest.approx(0.24)
        # 未指定分辨率时 Veo 按 720p 出片，结算跟随 720p；Lite 不支持 4K，未知分辨率同样回退 720p+audio (0.05)
        assert video(8, None, True) == pytest.approx(0.40)
        assert video(8, None, False) == pytest.approx(0.24)
        assert video(5, "unknown", True) == pytest.approx(0.25)
        # Fast 模型 (veo-3.1-fast-generate-001，在 gemini-vertex)：1080p 含音 0.12 / 无音 0.10
        fast = "veo-3.1-fast-generate-001"
        assert video(8, "1080p", True, provider="gemini-vertex", model=fast) == pytest.approx(0.96)
        assert video(8, "1080p", False, provider="gemini-vertex", model=fast) == pytest.approx(0.8)
        assert video(8, "720p", True, provider="gemini-vertex", model=fast) == pytest.approx(0.8)
        assert video(8, "720p", False, provider="gemini-vertex", model=fast) == pytest.approx(0.64)
        # Fast 模型未指定或未知分辨率应回退到自身的 720p+audio 费率 (0.10)，而非标准模型的 0.40
        assert video(8, None, True, provider="gemini-vertex", model=fast) == pytest.approx(0.8)
        assert video(5, "unknown", True, provider="gemini-vertex", model=fast) == pytest.approx(0.50)
        # 4K 档（Standard 两侧 + AI Studio Fast 支持）：含音 0.60 / 0.30，无音 0.40 / 0.25
        preview = "veo-3.1-generate-preview"
        assert video(8, "4k", True, model=preview) == pytest.approx(4.8)
        assert video(8, "4k", False, model=preview) == pytest.approx(3.2)
        fast_preview = "veo-3.1-fast-generate-preview"
        assert video(8, "4k", True, model=fast_preview) == pytest.approx(2.4)
        assert video(8, "4k", False, model=fast_preview) == pytest.approx(2.0)
        # 历史兼容：preview 模型费率与 001 相同（preview 在 gemini-aistudio）
        assert video(8, "1080p", True, model=preview) == pytest.approx(3.2)
        assert video(8, "1080p", False, model=preview) == pytest.approx(1.6)
        assert video(8, "1080p", True, model=fast_preview) == pytest.approx(0.96)

    def test_minimax_h3_resolution_omitted_settles_at_768p(self):
        # H3 项目留 Auto（resolution=None）时，_build_v2_payload 实际下发 768P；结算须跟随
        # 同一默认（0.50 元/s），而非 resolution_only 的通用 720P 回落——H3 未登记 720P 档，
        # 落空会把 resolution=None 的调用错误结算为 ¥0（见 default_resolution 声明）。
        amount, currency = _cost("minimax", "video", duration_seconds=8, resolution=None, model="MiniMax-H3")
        assert currency == "CNY"
        assert amount == pytest.approx(4.0)
        amount_2k, _ = _cost("minimax", "video", duration_seconds=8, resolution="2k", model="MiniMax-H3")
        assert amount_2k == pytest.approx(6.4)

    def test_singleton_instance(self):
        assert isinstance(cost_calculator, CostCalculator)


class TestAnthropicTextCost:
    def test_calculate_anthropic_text_cost(self):
        amount, currency = _cost(
            PROVIDER_ANTHROPIC,
            "text",
            input_tokens=100_000,
            output_tokens=50_000,
            model="claude-sonnet-4",
        )
        assert currency == "USD"
        assert amount == pytest.approx(1.05)

    def test_unknown_anthropic_model_uses_default(self):
        amount, currency = _cost(
            PROVIDER_ANTHROPIC,
            "text",
            input_tokens=100_000,
            output_tokens=50_000,
            model="unknown-claude",
        )
        assert currency == "USD"
        assert amount == pytest.approx(1.05)

    def test_calculate_anthropic_haiku_text_cost(self):
        amount, currency = _cost(
            PROVIDER_ANTHROPIC,
            "text",
            input_tokens=100_000,
            output_tokens=50_000,
            model="claude-haiku-4-5",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.35)


class TestKlingVideoCost:
    """可灵 per_second_tiered 经统一入口 calculate_cost 的金额对拍（CNY）。"""

    def test_turbo_std_silent(self):
        amount, currency = _cost(
            "kling", "video", model="kling-v2-5-turbo", service_tier="std", generate_audio=False, duration_seconds=5
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.6 * 5)

    def test_turbo_pro_with_audio(self):
        amount, currency = _cost(
            "kling", "video", model="kling-v2-5-turbo", service_tier="pro", generate_audio=True, duration_seconds=5
        )
        assert currency == "CNY"
        assert amount == pytest.approx(1.0 * 5)

    def test_turbo_default_tier_maps_to_std(self):
        # service_tier 缺省 "default" → std 无声
        amount, _ = _cost("kling", "video", model="kling-v2-5-turbo", generate_audio=False, duration_seconds=10)
        assert amount == pytest.approx(0.6 * 10)

    def test_unknown_kling_model_falls_back_to_turbo(self):
        # 未知 model 回落到 kling 默认视频模型（turbo）费率，不串到 Gemini 表
        amount, currency = _cost(
            "kling", "video", model="kling-mystery", service_tier="pro", generate_audio=False, duration_seconds=5
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.8 * 5)


class TestArkVideoCost:
    def test_zero_tokens(self):
        amount, currency = _cost("ark", "video", usage_tokens=0, service_tier="default", generate_audio=True)
        assert amount == pytest.approx(0.0)
        assert currency == "CNY"

    def test_unknown_model_uses_default(self):
        # 未知模型回落到 ark 默认视频模型（mini）的费率
        amount, currency = _cost(
            "ark", "video", usage_tokens=1_000_000, service_tier="default", generate_audio=True, model="unknown-model"
        )
        assert currency == "CNY"
        assert amount == pytest.approx(23.0)

    def test_seedance_2_cost(self):
        amount, currency = _cost(
            "ark",
            "video",
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=True,
            model="doubao-seedance-2-0-260128",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(46.00)

    def test_seedance_2_cost_no_audio_same_price(self):
        amount, _ = _cost(
            "ark",
            "video",
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=False,
            model="doubao-seedance-2-0-260128",
        )
        assert amount == pytest.approx(46.00)

    def test_seedance_2_fast_cost(self):
        amount, currency = _cost(
            "ark",
            "video",
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=True,
            model="doubao-seedance-2-0-fast-260128",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(37.00)

    def test_seedance_2_mini_cost(self):
        amount, currency = _cost(
            "ark",
            "video",
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=True,
            model="doubao-seedance-2-0-mini-260615",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(23.00)

    def test_seedance_2_mini_cost_no_audio_same_price(self):
        amount, _ = _cost(
            "ark",
            "video",
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=False,
            model="doubao-seedance-2-0-mini-260615",
        )
        assert amount == pytest.approx(23.00)


class TestGrokVideoCost:
    def test_default_model_per_second(self):
        cost, currency = _cost("grok", "video", duration_seconds=10, model="grok-imagine-video")
        assert cost == pytest.approx(0.50)
        assert currency == "USD"

    @pytest.mark.parametrize(
        ("resolution", "expected"),
        [
            ("480p", 0.50),
            ("720p", 0.70),
            # 未显式指定分辨率时 xAI 按 480p 出片，结算跟随同一默认档
            (None, 0.50),
        ],
    )
    def test_per_second_rate_follows_resolution(self, resolution, expected):
        cost, currency = _cost("grok", "video", duration_seconds=10, resolution=resolution, model="grok-imagine-video")
        assert cost == pytest.approx(expected)
        assert currency == "USD"

    @pytest.mark.parametrize("generate_audio", [True, False])
    def test_audio_track_does_not_change_rate(self, generate_audio):
        cost, _ = _cost(
            "grok",
            "video",
            duration_seconds=10,
            resolution="720p",
            generate_audio=generate_audio,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.70)

    @pytest.mark.parametrize(
        ("model", "resolution", "expected"),
        [
            # https://docs.x.ai/developers/models/grok-imagine-video-1.5
            ("grok-imagine-video-1.5", "480p", 0.80),
            ("grok-imagine-video-1.5", "720p", 1.40),
            ("grok-imagine-video-1.5", "1080p", 2.50),
            ("grok-imagine-video-1.5", None, 0.80),
            # https://docs.x.ai/developers/models/grok-imagine-video-1.5-lite
            ("grok-imagine-video-1.5-lite", "480p", 0.20),
            ("grok-imagine-video-1.5-lite", "720p", 0.30),
            ("grok-imagine-video-1.5-lite", "1080p", 1.40),
            ("grok-imagine-video-1.5-lite", None, 0.20),
        ],
    )
    def test_1_5_family_per_second_rate_follows_resolution(self, model, resolution, expected):
        cost, currency = _cost("grok", "video", duration_seconds=10, resolution=resolution, model=model)
        assert cost == pytest.approx(expected)
        assert currency == "USD"

    def test_short_video(self):
        cost, currency = _cost("grok", "video", duration_seconds=1, model="grok-imagine-video")
        assert cost == pytest.approx(0.050)
        assert currency == "USD"

    def test_max_duration(self):
        cost, _ = _cost("grok", "video", duration_seconds=15, model="grok-imagine-video")
        assert cost == pytest.approx(0.75)

    def test_zero_duration_is_not_replaced_by_a_default(self):
        # 缺秒数不再按 8 秒兜底：0 秒就是 0 秒
        cost, _ = _cost("grok", "video", duration_seconds=0, model="grok-imagine-video")
        assert cost == 0

    def test_unknown_model_uses_default(self):
        cost, _ = _cost("grok", "video", duration_seconds=10, model="unknown-grok-model")
        assert cost == pytest.approx(0.50)


class TestArkImageCost:
    def test_ark_image_cost_default(self):
        cost, currency = _cost("ark", "image")
        assert currency == "CNY"
        assert cost == pytest.approx(0.22)

    def test_ark_image_cost_by_model(self):
        cost, _ = _cost("ark", "image", model="doubao-seedream-4-5-251128")
        assert cost == pytest.approx(0.25)

    def test_ark_image_cost_unknown_model(self):
        cost, currency = _cost("ark", "image", model="unknown-model")
        assert currency == "CNY"
        assert cost == pytest.approx(0.22)


class TestGrokImageCost:
    def test_grok_image_cost_default(self):
        cost, currency = _cost("grok", "image")
        assert cost == pytest.approx(0.02)
        assert currency == "USD"

    def test_grok_image_cost_unknown_model(self):
        cost, currency = _cost("grok", "image", model="unknown-model")
        assert cost == pytest.approx(0.02)
        assert currency == "USD"


class TestOpenAICost:
    def test_openai_text_cost(self):
        amount, currency = _cost(
            "openai", "text", input_tokens=1_000_000, output_tokens=1_000_000, model="gpt-5.4-mini"
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.75 + 4.50)

    def test_openai_text_cost_default_model(self):
        amount, currency = _cost("openai", "text", input_tokens=1_000_000, output_tokens=0)
        assert currency == "USD"
        assert amount == pytest.approx(0.75)

    def test_openai_text_cost_5_5(self):
        amount, currency = _cost("openai", "text", input_tokens=1_000_000, output_tokens=1_000_000, model="gpt-5.5")
        assert currency == "USD"
        assert amount == pytest.approx(5.00 + 30.00)

    def test_unified_entry_openai(self):
        amount, _ = _cost("openai", "text", input_tokens=500_000, output_tokens=100_000)
        assert amount == pytest.approx(0.375 + 0.45)


class TestOpenAIImageTokenCost:
    """按 token 计价；没有 usage 时不再按 (quality, size) 兜底。"""

    def test_without_usage_no_fallback(self):
        amount, _ = _cost("openai", "image", model="gpt-image-2", quality="high", size="1024x1792")
        assert amount == pytest.approx(0.0)

    def test_token_cost_gpt_image_2(self):
        # image_in × 8 + image_out × 30 + text_in × 5 + text_out × 0
        amount, currency = _cost(
            "openai",
            "image",
            model="gpt-image-2",
            image_input_tokens=10_000,
            image_output_tokens=2_000,
            text_input_tokens=500,
            text_output_tokens=100,  # gpt-image-2 text_out 费率 = 0
        )
        assert currency == "USD"
        assert amount == pytest.approx((10_000 * 8 + 2_000 * 30 + 500 * 5 + 100 * 0) / 1_000_000)

    def test_zero_tokens_still_uses_token_path(self):
        # 所有 token 至少有一个非 None 时走 token 主路径，即使全为 0。
        amount, _ = _cost(
            "openai",
            "image",
            model="gpt-image-2",
            image_input_tokens=0,
            image_output_tokens=0,
            text_input_tokens=0,
            text_output_tokens=0,
        )
        assert amount == pytest.approx(0.0)

    def test_unified_entry_token_path(self):
        amount, currency = _cost(
            "openai",
            "image",
            model="gpt-image-2",
            image_output_tokens=2_200,
            text_input_tokens=350,
        )
        assert currency == "USD"
        assert amount == pytest.approx((2_200 * 30 + 350 * 5) / 1_000_000)
