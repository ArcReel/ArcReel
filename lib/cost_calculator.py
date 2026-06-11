"""费用计算器。

统一入口 ``calculate_cost`` 按 ``lookup_pricing`` 查出模型定价声明（``ModelInfo.pricing``，
单一真相源），再交 ``lib.pricing.strategies`` 按定价形状 ``kind`` 派发计算。新增内置模型只需在
其 ``ModelInfo.pricing`` 写一条声明并复用已有 kind，无需改动本文件。
"""

from __future__ import annotations

from lib.custom_provider import is_custom_provider
from lib.pricing.lookup import lookup_pricing
from lib.pricing.strategies import PricingParams, calculate_pricing
from lib.pricing.types import CHARACTERS_PER_PRICING_UNIT, PerSecondMatrix, PerTokenVideo
from lib.providers import CallType


class CostCalculator:
    """费用计算器：按定价声明的 ``kind`` 派发，不含 provider 分支。"""

    # 外部依赖常量（lib.gemini_shared / lib.video_backends.gemini 直接读取）。
    DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview"
    DEFAULT_VIDEO_MODEL = "veo-3.1-lite-generate-preview"

    # Ark 生成视频的 token/s 近似常量（用于参考模式成本估算，实际 token 由生成回调覆盖）。
    _ARK_TOKENS_PER_SECOND_ESTIMATE = 60_000

    def calculate_cost(
        self,
        provider: str,
        call_type: CallType,
        *,
        model: str | None = None,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
        duration_seconds: int | None = None,
        generate_audio: bool = True,
        usage_tokens: int | None = None,
        service_tier: str = "default",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        quality: str | None = None,
        size: str | None = None,
        image_input_tokens: int | None = None,
        image_output_tokens: int | None = None,
        text_input_tokens: int | None = None,
        text_output_tokens: int | None = None,
        custom_price_input: float | None = None,
        custom_price_output: float | None = None,
        custom_currency: str | None = None,
    ) -> tuple[float, str]:
        """统一费用计算入口。返回 ``(amount, currency)``。

        自定义供应商的价格信息通过 ``custom_price_*`` 参数传入（调用方需预先查询 DB）。
        """
        if is_custom_provider(provider):
            return self._calculate_custom_cost(
                call_type,
                price_input=custom_price_input,
                price_output=custom_price_output,
                currency=custom_currency,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_seconds=duration_seconds,
                usage_tokens=usage_tokens,
            )

        # 文本无 token 数据时无从计费，保留早返回。
        if call_type == "text" and input_tokens is None:
            return 0.0, "USD"

        pricing = lookup_pricing(provider, model, call_type)
        # 按秒计费的视频：单次实时调用无/0 时长时按默认 8 秒计（历史行为）。参考模式聚合走
        # estimate_reference_video_cost，传真实累计时长（可为 0），不经此默认。
        if isinstance(pricing, PerSecondMatrix):
            duration_seconds = duration_seconds or 8
        params = PricingParams(
            call_type=call_type,
            model=model,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            generate_audio=generate_audio,
            usage_tokens=usage_tokens,
            service_tier=service_tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            quality=quality,
            size=size,
            image_input_tokens=image_input_tokens,
            image_output_tokens=image_output_tokens,
            text_input_tokens=text_input_tokens,
            text_output_tokens=text_output_tokens,
        )
        return calculate_pricing(pricing, params)

    def estimate_reference_video_cost(
        self,
        *,
        unit_durations_seconds: list[int],
        provider: str,
        model: str | None = None,
        resolution: str | None = None,
        generate_audio: bool = True,
        service_tier: str = "default",
    ) -> tuple[float, str]:
        """聚合参考模式一集的视频费用：sum over units of (duration × 单价)。

        token 计费的视频（Ark）按 duration × ``_ARK_TOKENS_PER_SECOND_ESTIMATE`` 近似换算 token；
        其余按秒计费的模型直接用累计时长。空列表返回该定价声明自带的币种。
        """
        pricing = lookup_pricing(provider, model, "video")
        if not unit_durations_seconds:
            return 0.0, pricing.currency

        total_duration = sum(max(0, int(d)) for d in unit_durations_seconds)
        usage_tokens = (
            total_duration * self._ARK_TOKENS_PER_SECOND_ESTIMATE if isinstance(pricing, PerTokenVideo) else None
        )
        params = PricingParams(
            call_type="video",
            model=model,
            resolution=resolution,
            duration_seconds=total_duration,
            generate_audio=generate_audio,
            usage_tokens=usage_tokens,
            service_tier=service_tier,
        )
        return calculate_pricing(pricing, params)

    @staticmethod
    def _calculate_custom_cost(
        call_type: str,
        *,
        price_input: float | None = None,
        price_output: float | None = None,
        currency: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_seconds: int | None = None,
        usage_tokens: int | None = None,
    ) -> tuple[float, str]:
        """根据调用方预查的价格信息计算自定义供应商费用。"""
        if price_input is None:
            return 0.0, "USD"

        cur = currency or "USD"

        if call_type == "text":
            inp = (input_tokens or 0) * price_input
            out = (output_tokens or 0) * (price_output or 0)
            return (inp + out) / 1_000_000, cur
        elif call_type == "image":
            return price_input, cur
        elif call_type == "video":
            return (duration_seconds or 8) * price_input, cur
        elif call_type == "audio":
            # usage_tokens 承载合成字符数（与 _per_character 同模式）；单价口径为每万字符，
            # 与内置 per_character pricing kind 共用同一计价单位常量。
            return (usage_tokens or 0) / CHARACTERS_PER_PRICING_UNIT * price_input, cur
        return 0.0, cur


# 单例实例，方便使用
cost_calculator = CostCalculator()
