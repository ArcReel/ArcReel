"""Provider multipliers: user-configured price factors applied to all provider-call reference costs (docs/adr/0100).

Multipliers are user preferences for built-in/custom providers, never distributed by official synchronization. Default 1 unless configured;
subscription ``ark-agent-plan`` and ComfyUI custom providers default to 0. Stored under provider ``provider_config`` :data:`PROVIDER_MULTIPLIER_KEY`.
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
    """Default without configured provider multipliers.

    ``discovery_format`` is custom model-discovery protocol. ComfyUI providers run on user machines and names rarely match
    model entries, so default 0 records zero cost rather than unpriced. Users pricing rented compute can choose nonzero values.
    """
    if discovery_format is not None:
        from lib.custom_provider.discovery_formats import is_comfyui_protocol

        if is_comfyui_protocol(discovery_format):
            return 0.0
    return _DEFAULT_MULTIPLIERS.get(provider_id, 1.0)


async def get_default_provider_multiplier(session: AsyncSession, provider_id: str) -> float:
    """Provider defaults, based on discovery protocols for custom providers; :func:`default_provider_multiplier`."""
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
    """Parse stored/submitted multiplier text as nonnegative finite numbers; ``None`` if invalid."""
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


async def get_provider_multiplier(session: AsyncSession, provider_id: str) -> float:
    """Effective provider multipliers: user-configured values, otherwise defaults."""
    raw = (await ProviderConfigRepository(session).get_all(provider_id)).get(PROVIDER_MULTIPLIER_KEY)
    parsed = parse_multiplier(raw) if raw is not None else None
    return parsed if parsed is not None else await get_default_provider_multiplier(session, provider_id)


async def set_provider_multiplier(session: AsyncSession, provider_id: str, value: float | None) -> None:
    """Set provider multipliers; ``None`` restores defaults. Callers validate values and commit transactions."""
    repo = ProviderConfigRepository(session)
    if value is None:
        await repo.delete(provider_id, PROVIDER_MULTIPLIER_KEY)
    else:
        await repo.set(provider_id, PROVIDER_MULTIPLIER_KEY, repr(float(value)))
