"""按 ``(provider, model, media_type)`` 查出该调用应使用的 ``Pricing`` 声明。

回落次序复刻历史计费行为：未知 provider / 未知 model / 无声明定价均回落到「该 provider 该
媒体类型默认模型」的定价，再回落到 Gemini 默认费率——保证迁移前后金额一致。
"""

from __future__ import annotations

import logging

from lib.backends.providers import (
    PROVIDER_AGNES,
    PROVIDER_ARK,
    PROVIDER_DASHSCOPE,
    PROVIDER_GROK,
    PROVIDER_KLING,
    PROVIDER_MINIMAX,
    PROVIDER_OPENAI,
    PROVIDER_VIDU,
)
from lib.billing.pricing.types import Pricing, ViduDelegate

logger = logging.getLogger(__name__)

# 这些 provider 各有独立费率表：未知 model 回落到「该 provider 的默认模型」。其余 provider
# （gemini-aistudio/vertex、裸 gemini、ark-agent-plan、未知）一律回落 Gemini 家族通用默认费率，
# 复刻历史「仅 ark/grok/openai 走专属表、其余皆走全局 Gemini 表」的路由。
_OWN_TABLE_PROVIDERS = frozenset(
    {PROVIDER_ARK, PROVIDER_GROK, PROVIDER_OPENAI, PROVIDER_DASHSCOPE, PROVIDER_MINIMAX, PROVIDER_KLING, PROVIDER_AGNES}
)


def _gemini_default_pricing_for(media_type: str, model: str | None = None) -> Pricing:
    """非 ark/grok/openai/vidu 的 provider（含裸 ``gemini`` / 未知 provider /
    Agent Plan / gemini-aistudio / gemini-vertex）统一走 Gemini 家族费率：先按 model 在
    aistudio + vertex 命中（复刻历史「全局 Gemini 费率表按 model 命中」，覆盖如裸 ``gemini`` +
    ``veo-3.1-generate-001`` 这类历史调用），未命中再回落 aistudio 该媒体类型的默认模型——
    历史上所有 Gemini 家族 provider 共用同一通用默认（如视频 veo-3.1-lite），故按 aistudio
    的 ``default=True`` 取，避免按各 provider 自身默认（如 vertex 视频默认是 fast-001）算错价。"""
    # 目录查询导入放函数内：它经注册表拉起 dashscope_shared→usage_repo→cost_calculator，
    # 与本模块（被 cost_calculator 导入）构成导入环；延迟到调用时导入即可避开。
    from lib.catalog import query as catalog

    if model is not None:
        for provider_id in ("gemini-aistudio", "gemini-vertex"):
            pricing = catalog.model_pricing(provider_id, model)
            if pricing is not None and catalog.get_model(provider_id, model, media_type=media_type) is not None:
                return pricing

    # 默认模型取模型目录的默认模型单一真相源，避免与硬编码模型名漂移。
    for fallback_media in (media_type, "text"):
        model_id = catalog.default_model("gemini-aistudio", fallback_media)
        if model_id is not None:
            pricing = catalog.model_pricing("gemini-aistudio", model_id)
            if pricing is not None:
                return pricing
    raise RuntimeError("gemini-aistudio 缺少可用于兜底的默认模型定价声明")


def lookup_pricing(provider: str, model: str | None, media_type: str) -> Pricing:
    """返回该调用的定价声明。``media_type`` 即 call_type（``text`` / ``image`` / ``video`` / ``audio``）。"""
    if provider == PROVIDER_VIDU:
        # provider 级判定：不经 model→pricing 回落，确保策略拿到原始 model 透传给 vidu 计费。
        return ViduDelegate()

    from lib.catalog import query as catalog
    from lib.config.registry import PROVIDER_REGISTRY

    if provider not in PROVIDER_REGISTRY:
        return _gemini_default_pricing_for(media_type, model)

    # 必须媒体类型匹配才命中：历史按 call_type 分表，模型名只在本模态费率表内查。跨模态的
    # (model, call_type) 组合（如 video 调用带着图像模型名）应回落到当前媒体类型的默认模型，
    # 而非按该模型自身的异模态费率计价。
    info = catalog.get_model(provider, model, media_type=media_type) if model is not None else None
    pricing = catalog.model_pricing(provider, model) if model is not None else None
    if info is not None and pricing is not None:
        return pricing

    # 未知 model 名或媒体类型与调用不符，很可能是配置/调用错误，告警（将按默认模型计价）；
    # 已知 model 但定价为 None（如 Agent Plan 套餐）是预期的 Gemini 兜底，降级到 debug 避免噪声。
    if model is not None and info is None:
        logger.warning(
            "pricing lookup: provider=%s model=%s 不在该供应商的 %s 模型清单中，回落到默认模型费率",
            provider,
            model,
            media_type,
        )
    elif info is not None and pricing is None:
        logger.debug("pricing lookup: provider=%s model=%s 未声明定价，回落到默认模型费率", provider, model)

    # ark/grok/openai 各有专属费率表 → 未知 model 回落到该 provider 的默认模型；
    # 其余（gemini 家族等）回落 Gemini 通用默认费率，与历史路由一致。
    if provider in _OWN_TABLE_PROVIDERS:
        default_model = catalog.default_model(provider, media_type)
        if default_model is not None:
            default_pricing = catalog.model_pricing(provider, default_model)
            if default_pricing is not None:
                return default_pricing
    return _gemini_default_pricing_for(media_type, model)
