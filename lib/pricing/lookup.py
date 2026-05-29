"""按 ``(provider, model, media_type)`` 查出该调用应使用的 ``Pricing`` 声明。

回落次序复刻历史计费行为：未知 provider / 未知 model / 无声明定价均回落到「该 provider 该
媒体类型默认模型」的定价，再回落到 Gemini 默认费率——保证迁移前后金额一致。
"""

from __future__ import annotations

import logging

from lib.pricing.types import PerToken, Pricing, ViduDelegate
from lib.providers import PROVIDER_ANTHROPIC, PROVIDER_VIDU

logger = logging.getLogger(__name__)

# Anthropic 不在 PROVIDER_REGISTRY（无 ModelInfo 落点），文本定价作为 registry-external 例外。
# 助手主链路优先使用 SDK 回报的实际费用；此表仅在只拿到 token 数时兜底。费率为美元/百万 token。
_ANTHROPIC_PRICING = PerToken(
    rates={
        "claude-sonnet-4": {"input": 3.00, "output": 15.00},
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
        "claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
        "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
        "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
        "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
        "claude-haiku-4-20250514": {"input": 1.00, "output": 5.00},
        "claude-opus-4": {"input": 15.00, "output": 75.00},
        "claude-opus-4-6": {"input": 15.00, "output": 75.00},
        "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    },
    default_model="claude-sonnet-4",
    currency="USD",
)

# 未知 provider（``gemini`` / ``unknown`` / 缺省）回落到的 Gemini 默认模型，按媒体类型。
_GEMINI_DEFAULT_MODELS: dict[str, str] = {
    "text": "gemini-3-flash-preview",
    "image": "gemini-3.1-flash-image-preview",
    "video": "veo-3.1-lite-generate-preview",
}


def _gemini_default_pricing_for(media_type: str, model: str | None = None) -> Pricing:
    """非 ark/grok/openai/vidu/anthropic 的 provider（含裸 ``gemini`` / 未知 provider /
    Agent Plan）统一走 Gemini 家族费率：先按 model 在 aistudio + vertex 命中（复刻历史「全局
    Gemini 费率表按 model 命中」，覆盖如裸 ``gemini`` + ``veo-3.1-generate-001`` 这类历史调用），
    未命中再回落该媒体类型的默认模型。"""
    # registry 导入放函数内：lib.config 包初始化会拉起 resolver→usage_repo→cost_calculator，
    # 与本模块（被 cost_calculator 导入）构成导入环；延迟到调用时导入即可避开。
    from lib.config.registry import PROVIDER_REGISTRY

    if model is not None:
        for provider_id in ("gemini-aistudio", "gemini-vertex"):
            info = PROVIDER_REGISTRY[provider_id].models.get(model)
            if info is not None and info.pricing is not None and info.media_type == media_type:
                return info.pricing

    meta = PROVIDER_REGISTRY["gemini-aistudio"]
    model_id = _GEMINI_DEFAULT_MODELS.get(media_type, "gemini-3-flash-preview")
    info = meta.models.get(model_id)
    if info is not None and info.pricing is not None:
        return info.pricing
    text_info = meta.models["gemini-3-flash-preview"]
    assert text_info.pricing is not None
    return text_info.pricing


def lookup_pricing(provider: str, model: str | None, media_type: str) -> Pricing:
    """返回该调用的定价声明。``media_type`` 即 call_type（``text`` / ``image`` / ``video``）。"""
    if provider == PROVIDER_ANTHROPIC:
        return _ANTHROPIC_PRICING
    if provider == PROVIDER_VIDU:
        # provider 级判定：不经 model→pricing 回落，确保策略拿到原始 model 透传给 vidu 计费。
        return ViduDelegate()

    from lib.config.registry import PROVIDER_REGISTRY, default_model_for_provider

    meta = PROVIDER_REGISTRY.get(provider)
    if meta is None:
        return _gemini_default_pricing_for(media_type, model)

    info = meta.models.get(model) if model is not None else None
    if info is not None and info.pricing is not None:
        return info.pricing

    # 未知 model 名很可能是配置/调用错误，告警；已知 model 但定价为 None（如 Agent Plan 套餐）
    # 是预期的 Gemini 兜底，降级到 debug 避免噪声。
    if model is not None and info is None:
        logger.warning("pricing lookup: provider=%s model=%s 未在 registry，回落到默认模型费率", provider, model)
    elif info is not None and info.pricing is None:
        logger.debug("pricing lookup: provider=%s model=%s 未声明定价，回落到默认模型费率", provider, model)

    default_model = default_model_for_provider(provider, media_type)
    if default_model is not None:
        default_info = meta.models.get(default_model)
        if default_info is not None and default_info.pricing is not None:
            return default_info.pricing
    return _gemini_default_pricing_for(media_type, model)
