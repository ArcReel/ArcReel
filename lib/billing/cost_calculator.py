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

        内置供应商与自定义供应商的调用按模型目录的价目表计价（``lib.billing.catalog_pricing``），这里只给没有申报
        用量的其余调用计价（Agent 会话的回填、供应商标识已不在注册表里的存量行）。算不出时抛 ``ValueError``
        （如按秒计价的视频缺时长），不补近似值。
        """
        # 文本无 token 数据时无从计费，保留早返回。
        if params.call_type == "text" and params.input_tokens is None:
            return 0.0, "USD"

        pricing = lookup_pricing(provider, params.model, params.call_type)
        return calculate_pricing(pricing, params)


# 单例实例，方便使用
cost_calculator = CostCalculator()
