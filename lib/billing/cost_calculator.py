"""费用计算器。

统一入口 ``calculate_cost`` 按 ``lookup_pricing`` 查出模型定价声明（``ModelInfo.pricing``，
单一真相源），再交 ``lib.billing.pricing.strategies`` 按定价形状 ``kind`` 派发计算。新增内置模型只需在
其 ``ModelInfo.pricing`` 写一条声明并复用已有 kind，无需改动本文件。
"""

from __future__ import annotations

from lib.billing.pricing.lookup import lookup_pricing
from lib.billing.pricing.strategies import PricingParams, calculate_pricing


class CostCalculator:
    """费用计算器：按定价声明的 ``kind`` 派发，不含 provider 分支。"""

    # 外部依赖常量（lib.backends.gemini_shared / lib.backends.video_backends.gemini 直接读取）。
    DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
    DEFAULT_VIDEO_MODEL = "veo-3.1-lite-generate-preview"

    def calculate_cost(self, provider: str, params: PricingParams) -> tuple[float, str]:
        """统一费用计算入口。调用方直接构造 ``PricingParams`` 传入，返回 ``(amount, currency)``。

        内置供应商的调用已改由模型目录的价目表计价（``lib.billing.catalog_pricing``），自定义供应商同样按所对应
        条目的价目表计价；这里只剩没有申报用量的旧路径，不再为它补「缺秒数按 8 秒」「每秒 60000 token」一类的
        近似值，也不再按媒体类型硬算自定义模型的费用。
        """
        # 文本无 token 数据时无从计费，保留早返回。
        if params.call_type == "text" and params.input_tokens is None:
            return 0.0, "USD"

        pricing = lookup_pricing(provider, params.model, params.call_type)
        return calculate_pricing(pricing, params)


# 单例实例，方便使用
cost_calculator = CostCalculator()
