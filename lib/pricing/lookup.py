"""按 ``(provider, model, media_type)`` 查出该调用应使用的 ``Pricing`` 声明。

回落次序复刻历史计费行为：未知 provider / 未知 model / 无声明定价均回落到「该 provider 该
媒体类型默认模型」的定价，再回落到 Gemini 默认费率——保证迁移前后金额一致。
"""

from __future__ import annotations

import logging

from lib.pricing.types import PerToken, Pricing, ViduDelegate
from lib.providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_ARK,
    PROVIDER_DASHSCOPE,
    PROVIDER_GROK,
    PROVIDER_KLING,
    PROVIDER_MINIMAX,
    PROVIDER_OPENAI,
    PROVIDER_VIDU,
)

logger = logging.getLogger(__name__)

# 这些 provider 各有独立费率表：未知 model 回落到「该 provider 的默认模型」。其余 provider
# （gemini-aistudio/vertex、裸 gemini、ark-agent-plan、未知）一律回落 Gemini 家族通用默认费率，
# 复刻历史「仅 ark/grok/openai 走专属表、其余皆走全局 Gemini 表」的路由。
_OWN_TABLE_PROVIDERS = frozenset(
    {PROVIDER_ARK, PROVIDER_GROK, PROVIDER_OPENAI, PROVIDER_DASHSCOPE, PROVIDER_MINIMAX, PROVIDER_KLING}
)

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


def _gemini_default_pricing_for(media_type: str, model: str | None = None) -> Pricing:
    """非 ark/grok/openai/vidu/anthropic 的 provider（含裸 ``gemini`` / 未知 provider /
    Agent Plan / gemini-aistudio / gemini-vertex）统一走 Gemini 家族费率：先按 model 在
    aistudio + vertex 命中（复刻历史「全局 Gemini 费率表按 model 命中」，覆盖如裸 ``gemini`` +
    ``veo-3.1-generate-001`` 这类历史调用），未命中再回落 aistudio 该媒体类型的默认模型——
    历史上所有 Gemini 家族 provider 共用同一通用默认（如视频 veo-3.1-lite），故按 aistudio
    的 ``default=True`` 取，避免按各 provider 自身默认（如 vertex 视频默认是 fast-001）算错价。"""
    # registry 导入放函数内：lib.config 包初始化会拉起 resolver→usage_repo→cost_calculator，
    # 与本模块（被 cost_calculator 导入）构成导入环；延迟到调用时导入即可避开。
    from lib.config.registry import PROVIDER_REGISTRY, default_model_for_provider

    if model is not None:
        for provider_id in ("gemini-aistudio", "gemini-vertex"):
            info = PROVIDER_REGISTRY[provider_id].models.get(model)
            if info is not None and info.pricing is not None and info.media_type == media_type:
                return info.pricing

    meta = PROVIDER_REGISTRY["gemini-aistudio"]
    # 默认模型取 registry 的 default=True 单一真相源，避免与硬编码模型名漂移。
    for fallback_media in (media_type, "text"):
        model_id = default_model_for_provider("gemini-aistudio", fallback_media)
        if model_id is not None:
            info = meta.models.get(model_id)
            if info is not None and info.pricing is not None:
                return info.pricing
    raise RuntimeError("gemini-aistudio 缺少可用于兜底的默认模型定价声明")


def lookup_pricing(provider: str, model: str | None, media_type: str) -> Pricing:
    """返回该调用的定价声明。``media_type`` 即 call_type（``text`` / ``image`` / ``video`` / ``audio``）。"""
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
    # 必须媒体类型匹配才命中：历史按 call_type 分表，模型名只在本模态费率表内查。跨模态的
    # (model, call_type) 组合（如 video 调用带着图像模型名）应回落到当前媒体类型的默认模型，
    # 而非按该模型自身的异模态费率计价。
    if info is not None and info.media_type == media_type and info.pricing is not None:
        return info.pricing

    # 未知 model 名很可能是配置/调用错误，告警；媒体类型与调用不符同样告警（将按默认模型计价）；
    # 已知 model 但定价为 None（如 Agent Plan 套餐）是预期的 Gemini 兜底，降级到 debug 避免噪声。
    if model is not None and info is None:
        logger.warning("pricing lookup: provider=%s model=%s 未在 registry，回落到默认模型费率", provider, model)
    elif info is not None and info.media_type != media_type:
        logger.warning(
            "pricing lookup: provider=%s model=%s 媒体类型(%s)与调用(%s)不符，回落到默认模型费率",
            provider,
            model,
            info.media_type,
            media_type,
        )
    elif info is not None and info.pricing is None:
        logger.debug("pricing lookup: provider=%s model=%s 未声明定价，回落到默认模型费率", provider, model)

    # ark/grok/openai 各有专属费率表 → 未知 model 回落到该 provider 的默认模型；
    # 其余（gemini 家族等）回落 Gemini 通用默认费率，与历史路由一致。
    if provider in _OWN_TABLE_PROVIDERS:
        default_model = default_model_for_provider(provider, media_type)
        if default_model is not None:
            default_info = meta.models.get(default_model)
            if default_info is not None and default_info.pricing is not None:
                return default_info.pricing
    return _gemini_default_pricing_for(media_type, model)
