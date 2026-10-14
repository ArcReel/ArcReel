"""内置供应商调用按模型目录计价：结算与提交前预估共用的入口。

价目表取供应商模型所引用条目的价目表，倍率取供应商倍率（用户偏好，由调用方经
:func:`lib.billing.multipliers.get_provider_multiplier` 读出）与供应商模型倍率（随目录数据）之积，
再交 :func:`lib.billing.metering.price_requests` 计算。
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.billing.metering import Cost, Usage, price_requests


def is_builtin_provider(provider: str) -> bool:
    """按模型目录计价的是内置供应商；自定义供应商暂走旧的自定义计价。"""
    # 注册表导入放函数内，理由同 price_builtin_call 里的目录查询导入。
    from lib.config.registry import PROVIDER_REGISTRY

    return provider in PROVIDER_REGISTRY


def price_builtin_call(
    *,
    provider: str,
    model: str,
    media_type: str,
    requests: Sequence[Usage],
    provider_multiplier: float,
    estimate: bool = False,
) -> Cost:
    """一次内置供应商调用的参考费用。

    ``requests`` 是这次调用所含各次供应商请求的用量，各自落上下文档、单独计价后相加；``estimate`` 的含义见
    :func:`lib.billing.metering.price_usage`。
    """
    # 目录查询导入放函数内：它经注册表拉起 dashscope_shared→usage_repo→本模块，模块级导入会成环。
    from lib.catalog import query as catalog

    target = catalog.provider_model_price(provider, model, media_type)
    multiplier = provider_multiplier * (target.multiplier if target is not None else 1.0)
    return price_requests(
        target.price if target is not None else None,
        requests,
        multiplier=multiplier,
        entry_found=target is not None,
        estimate=estimate,
    )
