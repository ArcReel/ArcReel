"""供应商调用按模型目录计价：结算与提交前预估共用的入口。

价目表取供应商模型所对应条目的价目表，倍率取供应商倍率（用户偏好，由调用方经
:func:`lib.billing.multipliers.get_provider_multiplier` 读出）与供应商模型倍率之积，再交
:func:`lib.billing.metering.price_requests` 计算（docs/adr/0100）。

- 内置供应商：供应商模型引用清单里的条目，供应商模型倍率随目录数据下发。
- 自定义供应商：供应商模型按条目绑定或名字对应条目（docs/adr/0105），供应商模型倍率由用户填写；对应不上条目时
  记为未定价（docs/adr/0106）。
- Agent 会话：模型名按 Agent 凭证档位的条目绑定或名字对应条目，倍率只有 Agent 凭证倍率一级（docs/adr/0102）。
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.billing.metering import Cost, Usage, price_requests


def is_builtin_provider(provider: str) -> bool:
    """注册表里的内置供应商；其余（自定义供应商、Agent 凭证）不经清单查条目。"""
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


def price_custom_call(
    *,
    model: str,
    media_type: str,
    entry_binding: str | None,
    requests: Sequence[Usage],
    provider_multiplier: float,
    model_multiplier: float,
    estimate: bool = False,
) -> Cost:
    """一次自定义供应商调用的参考费用。

    ``model`` 是 API 模型名，``entry_binding`` 是模型行上的条目绑定（「模型厂商/官方模型名」），``media_type``
    取这次调用的媒体类型；对应条目的规则见 :func:`lib.catalog.query.find_entry`。两级倍率之积为 0 时直接记 0。
    其余参数同 :func:`price_builtin_call`。
    """
    from lib.catalog import query as catalog

    entry = catalog.find_entry(model, media_type=media_type, binding=catalog.binding_key(entry_binding, media_type))
    return price_requests(
        entry.price if entry is not None else None,
        requests,
        multiplier=provider_multiplier * model_multiplier,
        entry_found=entry is not None,
        estimate=estimate,
    )


def price_agent_call(
    *,
    model: str,
    entry_binding: str | None,
    requests: Sequence[Usage],
    multiplier: float,
) -> Cost:
    """Agent 会话里一个模型一轮的参考费用（docs/adr/0102）。

    ``model`` 是 API 模型名，``entry_binding`` 是 Agent 凭证上这个模型所在档位的条目绑定，对应条目的规则见
    :func:`lib.catalog.query.find_entry`；``multiplier`` 是 Agent 凭证倍率，只此一级，为 0 时直接记 0。
    ``requests`` 是这一轮各次请求的用量，各自落上下文档、单独计价后相加。
    """
    from lib.catalog import query as catalog

    entry = catalog.find_entry(model, media_type="text", binding=catalog.binding_key(entry_binding, "text"))
    return price_requests(
        entry.price if entry is not None else None,
        requests,
        multiplier=multiplier,
        entry_found=entry is not None,
    )
