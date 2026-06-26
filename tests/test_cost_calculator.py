"""统一入口 ``calculate_cost`` 的费用对拍：金额数值与币种逐项保持迁移前不变。

底层 per-shape 方法已删除，断言改指向公共入口 ``calculate_cost(provider, call_type, ...)``，
用各模型所属的规范 provider id（veo ``-001`` 在 gemini-vertex、``-preview`` / ``-lite`` 在
gemini-aistudio）。仅在底层暴露的形态（如按张 n、纯策略 fallback）见 ``test_pricing_strategies``。
"""

import pytest

from lib.cost_calculator import CostCalculator, cost_calculator
from lib.providers import PROVIDER_ANTHROPIC


class TestImageCost:
    def test_calculate_image_cost_known_and_default(self):
        calc = CostCalculator()
        # 默认模型 (gemini-3.1-flash-image-preview)
        assert calc.calculate_cost("gemini-aistudio", "image", resolution="1k") == (0.067, "USD")
        assert calc.calculate_cost("gemini-aistudio", "image", resolution="2K") == (0.101, "USD")
        assert calc.calculate_cost("gemini-aistudio", "image", resolution="4K") == (0.151, "USD")
        assert calc.calculate_cost("gemini-aistudio", "image", resolution="unknown") == (0.067, "USD")
        # 指定旧模型 (gemini-3-pro-image-preview)
        assert calc.calculate_cost("gemini-aistudio", "image", resolution="1k", model="gemini-3-pro-image-preview") == (
            0.134,
            "USD",
        )
        assert calc.calculate_cost("gemini-aistudio", "image", resolution="2K", model="gemini-3-pro-image-preview") == (
            0.134,
            "USD",
        )


class TestVideoCost:
    def test_calculate_video_cost_known_and_default(self):
        calc = CostCalculator()

        def video(duration, resolution, audio, provider="gemini-aistudio", model=None):
            amount, _ = calc.calculate_cost(
                provider,
                "video",
                duration_seconds=duration,
                resolution=resolution,
                generate_audio=audio,
                model=model,
            )
            return amount

        # 默认模型 (veo-3.1-lite-generate-preview)
        assert video(8, "1080p", True) == pytest.approx(0.64)
        assert video(8, "1080p", False) == pytest.approx(0.64)
        assert video(8, "720p", True) == pytest.approx(0.40)
        assert video(8, "720p", False) == pytest.approx(0.40)
        # Lite 不支持 4K，未知分辨率回退到 1080p+audio 费率 (0.08)
        assert video(5, "unknown", True) == pytest.approx(0.40)
        # Fast 模型 (veo-3.1-fast-generate-001，在 gemini-vertex)
        fast = "veo-3.1-fast-generate-001"
        assert video(8, "1080p", True, provider="gemini-vertex", model=fast) == pytest.approx(1.2)
        assert video(8, "1080p", False, provider="gemini-vertex", model=fast) == pytest.approx(0.8)
        assert video(6, "4k", True, provider="gemini-vertex", model=fast) == pytest.approx(2.1)
        assert video(6, "4k", False, provider="gemini-vertex", model=fast) == pytest.approx(1.8)
        # Fast 模型未知分辨率应回退到自身的 1080p+audio 费率 (0.15)，而非标准模型的 0.40
        assert video(5, "unknown", True, provider="gemini-vertex", model=fast) == pytest.approx(0.75)
        # 历史兼容：preview 模型费率与 001 相同（preview 在 gemini-aistudio）
        preview = "veo-3.1-generate-preview"
        assert video(8, "1080p", True, model=preview) == pytest.approx(3.2)
        assert video(8, "1080p", False, model=preview) == pytest.approx(1.6)
        fast_preview = "veo-3.1-fast-generate-preview"
        assert video(8, "1080p", True, model=fast_preview) == pytest.approx(1.2)

    def test_singleton_instance(self):
        assert isinstance(cost_calculator, CostCalculator)


class TestAnthropicTextCost:
    def test_calculate_anthropic_text_cost(self):
        amount, currency = cost_calculator.calculate_cost(
            PROVIDER_ANTHROPIC,
            "text",
            input_tokens=100_000,
            output_tokens=50_000,
            model="claude-sonnet-4",
        )
        assert currency == "USD"
        assert amount == pytest.approx(1.05)

    def test_unknown_anthropic_model_uses_default(self):
        amount, currency = cost_calculator.calculate_cost(
            PROVIDER_ANTHROPIC,
            "text",
            input_tokens=100_000,
            output_tokens=50_000,
            model="unknown-claude",
        )
        assert currency == "USD"
        assert amount == pytest.approx(1.05)

    def test_calculate_anthropic_haiku_text_cost(self):
        amount, currency = cost_calculator.calculate_cost(
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
        amount, currency = cost_calculator.calculate_cost(
            "kling", "video", model="kling-v2-5-turbo", service_tier="std", generate_audio=False, duration_seconds=5
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.6 * 5)

    def test_turbo_pro_with_audio(self):
        amount, currency = cost_calculator.calculate_cost(
            "kling", "video", model="kling-v2-5-turbo", service_tier="pro", generate_audio=True, duration_seconds=5
        )
        assert currency == "CNY"
        assert amount == pytest.approx(1.0 * 5)

    def test_turbo_default_tier_maps_to_std(self):
        # service_tier 缺省 "default" → std 无声
        amount, _ = cost_calculator.calculate_cost(
            "kling", "video", model="kling-v2-5-turbo", generate_audio=False, duration_seconds=10
        )
        assert amount == pytest.approx(0.6 * 10)

    def test_unknown_kling_model_falls_back_to_turbo(self):
        # 未知 model 回落到 kling 默认视频模型（turbo）费率，不串到 Gemini 表
        amount, currency = cost_calculator.calculate_cost(
            "kling", "video", model="kling-mystery", service_tier="pro", generate_audio=False, duration_seconds=5
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.8 * 5)


class TestArkVideoCost:
    def test_online_with_audio(self):
        amount, currency = cost_calculator.calculate_cost(
            "ark",
            "video",
            usage_tokens=246840,
            service_tier="default",
            generate_audio=True,
            model="doubao-seedance-1-5-pro-251215",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(3.9494, rel=1e-3)

    def test_online_no_audio(self):
        amount, currency = cost_calculator.calculate_cost(
            "ark",
            "video",
            usage_tokens=246840,
            service_tier="default",
            generate_audio=False,
            model="doubao-seedance-1-5-pro-251215",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(1.9747, rel=1e-3)

    def test_flex_with_audio(self):
        amount, currency = cost_calculator.calculate_cost(
            "ark",
            "video",
            usage_tokens=246840,
            service_tier="flex",
            generate_audio=True,
            model="doubao-seedance-1-5-pro-251215",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(1.9747, rel=1e-3)

    def test_flex_no_audio(self):
        amount, currency = cost_calculator.calculate_cost(
            "ark",
            "video",
            usage_tokens=246840,
            service_tier="flex",
            generate_audio=False,
            model="doubao-seedance-1-5-pro-251215",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.9874, rel=1e-3)

    def test_zero_tokens(self):
        amount, currency = cost_calculator.calculate_cost(
            "ark", "video", usage_tokens=0, service_tier="default", generate_audio=True
        )
        assert amount == pytest.approx(0.0)
        assert currency == "CNY"

    def test_unknown_model_uses_default(self):
        # 未知模型回落到 ark 默认视频模型（mini）的费率
        amount, currency = cost_calculator.calculate_cost(
            "ark", "video", usage_tokens=1_000_000, service_tier="default", generate_audio=True, model="unknown-model"
        )
        assert currency == "CNY"
        assert amount == pytest.approx(23.0)

    def test_seedance_2_cost(self):
        amount, currency = cost_calculator.calculate_cost(
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
        amount, _ = cost_calculator.calculate_cost(
            "ark",
            "video",
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=False,
            model="doubao-seedance-2-0-260128",
        )
        assert amount == pytest.approx(46.00)

    def test_seedance_2_fast_cost(self):
        amount, currency = cost_calculator.calculate_cost(
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
        amount, currency = cost_calculator.calculate_cost(
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
        amount, _ = cost_calculator.calculate_cost(
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
        cost, currency = cost_calculator.calculate_cost(
            "grok", "video", duration_seconds=10, model="grok-imagine-video"
        )
        assert cost == pytest.approx(0.50)
        assert currency == "USD"

    def test_short_video(self):
        cost, currency = cost_calculator.calculate_cost("grok", "video", duration_seconds=1, model="grok-imagine-video")
        assert cost == pytest.approx(0.050)
        assert currency == "USD"

    def test_max_duration(self):
        cost, _ = cost_calculator.calculate_cost("grok", "video", duration_seconds=15, model="grok-imagine-video")
        assert cost == pytest.approx(0.75)

    def test_zero_duration_defaults_to_8s(self):
        # 统一入口把 0/缺省时长视为默认 8 秒（与历史 calculate_cost 行为一致）：8 × 0.050 = 0.40
        cost, _ = cost_calculator.calculate_cost("grok", "video", duration_seconds=0, model="grok-imagine-video")
        assert cost == pytest.approx(0.40)

    def test_unknown_model_uses_default(self):
        cost, _ = cost_calculator.calculate_cost("grok", "video", duration_seconds=10, model="unknown-grok-model")
        assert cost == pytest.approx(0.50)


class TestArkImageCost:
    def test_ark_image_cost_default(self):
        cost, currency = cost_calculator.calculate_cost("ark", "image")
        assert currency == "CNY"
        assert cost == pytest.approx(0.22)

    def test_ark_image_cost_by_model(self):
        cost, _ = cost_calculator.calculate_cost("ark", "image", model="doubao-seedream-4-5-251128")
        assert cost == pytest.approx(0.25)

    def test_ark_image_cost_unknown_model(self):
        cost, currency = cost_calculator.calculate_cost("ark", "image", model="unknown-model")
        assert currency == "CNY"
        assert cost == pytest.approx(0.22)


class TestGrokImageCost:
    def test_grok_image_cost_default(self):
        cost, currency = cost_calculator.calculate_cost("grok", "image")
        assert cost == pytest.approx(0.02)
        assert currency == "USD"

    def test_grok_image_cost_pro(self):
        cost, currency = cost_calculator.calculate_cost("grok", "image", model="grok-imagine-image-pro")
        assert cost == pytest.approx(0.07)
        assert currency == "USD"

    def test_grok_image_cost_unknown_model(self):
        cost, currency = cost_calculator.calculate_cost("grok", "image", model="unknown-model")
        assert cost == pytest.approx(0.02)
        assert currency == "USD"


class TestOpenAICost:
    def test_openai_text_cost(self):
        amount, currency = cost_calculator.calculate_cost(
            "openai", "text", input_tokens=1_000_000, output_tokens=1_000_000, model="gpt-5.4-mini"
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.75 + 4.50)

    def test_openai_text_cost_default_model(self):
        amount, currency = cost_calculator.calculate_cost("openai", "text", input_tokens=1_000_000, output_tokens=0)
        assert currency == "USD"
        assert amount == pytest.approx(0.75)

    def test_openai_image_cost_low(self):
        amount, currency = cost_calculator.calculate_cost("openai", "image", model="gpt-image-2", quality="low")
        assert currency == "USD"
        assert amount == pytest.approx(0.006)  # 默认 1024x1024

    def test_openai_image_cost_landscape(self):
        amount, currency = cost_calculator.calculate_cost(
            "openai", "image", model="gpt-image-2", quality="high", size="1792x1024"
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.317)

    def test_openai_video_cost(self):
        amount, currency = cost_calculator.calculate_cost("openai", "video", duration_seconds=8, model="sora-2")
        assert currency == "USD"
        assert amount == pytest.approx(0.80)

    def test_openai_video_cost_pro(self):
        amount, currency = cost_calculator.calculate_cost(
            "openai", "video", duration_seconds=4, model="sora-2-pro", resolution="1080p"
        )
        assert currency == "USD"
        assert amount == pytest.approx(2.80)

    def test_openai_text_cost_5_5(self):
        amount, currency = cost_calculator.calculate_cost(
            "openai", "text", input_tokens=1_000_000, output_tokens=1_000_000, model="gpt-5.5"
        )
        assert currency == "USD"
        assert amount == pytest.approx(5.00 + 30.00)

    def test_openai_image_cost_gpt_image_2_high_square(self):
        amount, currency = cost_calculator.calculate_cost("openai", "image", model="gpt-image-2", quality="high")
        assert currency == "USD"
        assert amount == pytest.approx(0.211)

    def test_openai_image_cost_gpt_image_2_high_portrait(self):
        amount, currency = cost_calculator.calculate_cost(
            "openai", "image", model="gpt-image-2", quality="high", size="1024x1792"
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.317)

    def test_openai_image_cost_default_uses_gpt_image_2(self):
        # 不传 model → 回落 OpenAI 图片默认模型 gpt-image-2，medium 1024x1024 = 0.053
        amount, currency = cost_calculator.calculate_cost("openai", "image", quality="medium")
        assert currency == "USD"
        assert amount == pytest.approx(0.053)

    def test_unified_entry_openai(self):
        amount, _ = cost_calculator.calculate_cost("openai", "text", input_tokens=500_000, output_tokens=100_000)
        assert amount == pytest.approx(0.375 + 0.45)
        amount, _ = cost_calculator.calculate_cost("openai", "image", model="gpt-image-2", quality="high")
        assert amount == pytest.approx(0.211)  # 默认 1024x1024
        amount, _ = cost_calculator.calculate_cost(
            "openai", "image", model="gpt-image-2", quality="high", size="1024x1792"
        )
        assert amount == pytest.approx(0.317)
        amount, _ = cost_calculator.calculate_cost("openai", "video", duration_seconds=12, model="sora-2")
        assert amount == pytest.approx(1.20)


class TestOpenAIImageTokenCost:
    """token-based 主路径与 (quality, size) 兜底。计费与输出尺寸解耦（adr 0011）：
    兜底不再按 (resolution, aspect_ratio) 反查 size，无显式 size 即落默认 1024x1024 档。"""

    def test_token_cost_gpt_image_2(self):
        # image_in × 8 + image_out × 30 + text_in × 5 + text_out × 0
        amount, currency = cost_calculator.calculate_cost(
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
        amount, _ = cost_calculator.calculate_cost(
            "openai",
            "image",
            model="gpt-image-2",
            image_input_tokens=0,
            image_output_tokens=0,
            text_input_tokens=0,
            text_output_tokens=0,
        )
        assert amount == pytest.approx(0.0)

    def test_fallback_no_size_uses_default_square(self):
        # 所有 token 入参 None 时走 fallback；无显式 size → 默认 1024x1024（不再反查 resolution+aspect）。
        amount, _ = cost_calculator.calculate_cost(
            "openai", "image", model="gpt-image-2", quality="high", resolution="1K", aspect_ratio="9:16"
        )
        # high + 1024x1024 → 0.211（旧反查会得 0.317，已废弃）
        assert amount == pytest.approx(0.211)

    def test_fallback_aspect_independent(self):
        # 计费与尺寸解耦：相同 quality 下不同 aspect_ratio 的兜底金额一致（均落默认 1024x1024）。
        common = {"model": "gpt-image-2", "quality": "high", "resolution": "1K"}
        amount_1_1, _ = cost_calculator.calculate_cost("openai", "image", aspect_ratio="1:1", **common)
        amount_9_16, _ = cost_calculator.calculate_cost("openai", "image", aspect_ratio="9:16", **common)
        amount_16_9, _ = cost_calculator.calculate_cost("openai", "image", aspect_ratio="16:9", **common)
        assert amount_1_1 == pytest.approx(0.211)
        assert amount_9_16 == pytest.approx(0.211)
        assert amount_16_9 == pytest.approx(0.211)
        assert amount_1_1 == amount_9_16, "兜底计费不再随 aspect 变化（尺寸解耦）"

    def test_fallback_explicit_size_used(self):
        # 显式 size kwarg 仍被兜底计费采用（resolution/aspect_ratio 不参与）。
        amount, _ = cost_calculator.calculate_cost(
            "openai",
            "image",
            model="gpt-image-2",
            quality="medium",
            resolution="1K",
            aspect_ratio="1:1",
            size="1024x1792",  # 显式 size
        )
        assert amount == pytest.approx(0.106)  # gpt-image-2 medium 1024x1792

    def test_unified_entry_token_path(self):
        amount, currency = cost_calculator.calculate_cost(
            "openai",
            "image",
            model="gpt-image-2",
            image_output_tokens=2_200,
            text_input_tokens=350,
        )
        assert currency == "USD"
        assert amount == pytest.approx((2_200 * 30 + 350 * 5) / 1_000_000)

    def test_unified_entry_fallback_aspect_independent(self):
        # 统一入口的兜底同样与 aspect 解耦：均落默认 1024x1024 档
        amount_1_1, _ = cost_calculator.calculate_cost(
            "openai", "image", model="gpt-image-2", quality="high", resolution="1K", aspect_ratio="1:1"
        )
        amount_9_16, _ = cost_calculator.calculate_cost(
            "openai", "image", model="gpt-image-2", quality="high", resolution="1K", aspect_ratio="9:16"
        )
        assert amount_1_1 == pytest.approx(0.211)
        assert amount_9_16 == pytest.approx(0.211)
        assert amount_1_1 == amount_9_16
