"""供应商倍率：用户为一个供应商设置的价格系数，乘在这个供应商全部调用的参考费用上（docs/adr/0100）。

供应商倍率是用户偏好，所有供应商（内置与自定义）都可以设置，不随官方同步下发。没有设置时默认为 1，按订阅计费的
``ark-agent-plan`` 与 ComfyUI 协议的自定义供应商默认为 0。值存在 ``provider_config`` 表里这个供应商名下的 :data:`PROVIDER_MULTIPLIER_KEY` 键。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from lib.backends.providers import PROVIDER_ARK_AGENT_PLAN
from lib.config.repository import ProviderConfigRepository

PROVIDER_MULTIPLIER_KEY: Final = "price_multiplier"

_DEFAULT_MULTIPLIERS: Final[Mapping[str, float]] = MappingProxyType({PROVIDER_ARK_AGENT_PLAN: 0.0})


def default_provider_multiplier(provider_id: str, *, discovery_format: str | None = None) -> float:
    """供应商没有设置倍率时使用的值。

    ``discovery_format`` 是自定义供应商的模型发现协议：ComfyUI 协议的供应商跑在用户自己的机器上，名称也基本对应
    不上模型条目，默认倍率为 0，调用记 0 而不是未定价；用户按租用算力计价时可以改成非 0。
    """
    if discovery_format is not None:
        from lib.custom_provider.discovery_formats import is_comfyui_protocol

        if is_comfyui_protocol(discovery_format):
            return 0.0
    return _DEFAULT_MULTIPLIERS.get(provider_id, 1.0)


async def get_default_provider_multiplier(session: AsyncSession, provider_id: str) -> float:
    """供应商的默认倍率；自定义供应商按它的模型发现协议定，见 :func:`default_provider_multiplier`。"""
    from lib.custom_provider import is_custom_provider, parse_provider_id

    discovery_format: str | None = None
    if is_custom_provider(provider_id):
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        try:
            provider = await CustomProviderRepository(session).get_provider(parse_provider_id(provider_id))
        except ValueError:
            provider = None
        discovery_format = provider.discovery_format if provider is not None else None
    return default_provider_multiplier(provider_id, discovery_format=discovery_format)


def parse_multiplier(raw: str) -> float | None:
    """把存储或提交的倍率文本解析为非负有限数；不合法时返回 ``None``。"""
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


async def get_provider_multiplier(session: AsyncSession, provider_id: str) -> float:
    """供应商当前生效的倍率：用户设置过的值，否则为默认值。"""
    raw = (await ProviderConfigRepository(session).get_all(provider_id)).get(PROVIDER_MULTIPLIER_KEY)
    parsed = parse_multiplier(raw) if raw is not None else None
    return parsed if parsed is not None else await get_default_provider_multiplier(session, provider_id)


async def set_provider_multiplier(session: AsyncSession, provider_id: str, value: float | None) -> None:
    """设置供应商倍率；``None`` 表示恢复默认值。调用方负责校验取值与提交事务。"""
    repo = ProviderConfigRepository(session)
    if value is None:
        await repo.delete(provider_id, PROVIDER_MULTIPLIER_KEY)
    else:
        await repo.set(provider_id, PROVIDER_MULTIPLIER_KEY, repr(float(value)))
