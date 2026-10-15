"""费用计算器。

统一入口 ``calculate_cost`` 按 ``lookup_pricing`` 查出模型定价声明（``ModelInfo.pricing``，
单一真相源），再交 ``lib.billing.pricing.strategies`` 按定价形状 ``kind`` 派发计算。新增内置模型只需在
其 ``ModelInfo.pricing`` 写一条声明并复用已有 kind，无需改动本文件。
"""

from __future__ import annotations

from lib.billing.pricing.lookup import lookup_pricing
from lib.billing.pricing.strategies import PricingParams, calculate_pricing
from lib.billing.pricing.types import CHARACTERS_PER_PRICING_UNIT
from lib.custom_provider import is_custom_provider


class CostCalculator:
    """费用计算器：按定价声明的 ``kind`` 派发，不含 provider 分支。"""

    # 外部依赖常量（lib.backends.gemini_shared / lib.backends.video_backends.gemini 直接读取）。
    DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
    DEFAULT_VIDEO_MODEL = "veo-3.1-lite-generate-preview"

    def calculate_cost(
        self,
        provider: str,
        params: PricingParams,
        *,
        custom_price_input: float | None = None,
        custom_price_output: float | None = None,
        custom_currency: str | None = None,
    ) -> tuple[float, str]:
        """统一费用计算入口。调用方直接构造 ``PricingParams`` 传入，返回 ``(amount, currency)``。

        自定义供应商的价格信息通过 ``custom_price_*`` 参数传入（调用方需预先查询 DB）；
        它们是 DB 侧的价格来源、非定价形状维度，故不并入 ``PricingParams``。

        内置供应商的视频调用已改由模型目录的价目表计价（``lib.billing.catalog_pricing``），这里不再为它
        补「缺秒数按 8 秒」「每秒 60000 token」一类的近似值。
        """
        if is_custom_provider(provider):
            return self._calculate_custom_cost(
                params.call_type,
                price_input=custom_price_input,
                price_output=custom_price_output,
                currency=custom_currency,
                input_tokens=params.input_tokens,
                output_tokens=params.output_tokens,
                duration_seconds=params.duration_seconds,
                usage_tokens=params.usage_tokens,
            )

        # 文本无 token 数据时无从计费，保留早返回。
        if params.call_type == "text" and params.input_tokens is None:
            return 0.0, "USD"

        pricing = lookup_pricing(provider, params.model, params.call_type)
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
        if call_type == "image":
            return price_input, cur
        if call_type == "video":
            return (duration_seconds or 8) * price_input, cur
        if call_type == "audio":
            # usage_tokens 承载合成字符数（与 _per_character 同模式）；单价口径为每万字符，
            # 与内置 per_character pricing kind 共用同一计价单位常量。
            return (usage_tokens or 0) / CHARACTERS_PER_PRICING_UNIT * price_input, cur
        return 0.0, cur


# 单例实例，方便使用
cost_calculator = CostCalculator()
