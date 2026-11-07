"""Catalog-based provider pricing shared by settlement/presubmission estimates.

Use corresponding entry price tables and provider multipliers (user preferences read by callers through
:func:`lib.billing.multipliers.get_provider_multiplier`) × provider-model multipliers, calculated through
:func:`lib.billing.metering.price_requests` (docs/adr/0100).

- Built-ins: models reference manifest entries; official catalog data supplies provider-model multipliers.
- Custom: models match bindings/names (docs/adr/0105), with user-supplied model multipliers; unmatched entries
  remain unpriced (docs/adr/0106).
- Agent sessions: names match credential-tier bindings/names; only Agent-credential multipliers apply (docs/adr/0102).
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.billing.metering import Cost, Usage, price_requests
from lib.catalog import query as catalog


def is_builtin_provider(provider: str) -> bool:
    """Registry built-ins; custom providers/Agent credentials bypass manifest entry lookup."""
    # Deferred registry imports avoid registry → dashscope_shared → usage_repo → this-module cycles.
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
    """Reference cost for one built-in call.

    ``requests`` contains each provider request's usage, individually context-tiered/priced before summing; ``estimate`` follows
    :func:`lib.billing.metering.price_usage`.
    """
    target = catalog.provider_model_price(provider, model, media_type)
    multiplier = provider_multiplier * (target.multiplier if target is not None else 1.0)
    return price_requests(
        target.price if target is not None else None,
        requests,
        multiplier=multiplier,
        entry_found=target is not None and target.entry_found,
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
    """Reference cost for one custom-provider call.

    ``model`` is API names, ``entry_binding`` model-row vendor/official-name bindings, ``media_type`` this call's media;
    matching rules: :func:`lib.catalog.query.find_entry`. Zero product of two multipliers directly records zero.
    Other parameters match :func:`price_builtin_call`.
    """
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
    """Reference cost for one model/turn in Agent sessions (docs/adr/0102).

    ``model`` is API names; ``entry_binding`` belongs to that model's credential tier, matching through
    :func:`lib.catalog.query.find_entry`; ``multiplier`` is the sole Agent credential factor, directly zero-priced when zero.
    ``requests`` contains each turn request's usage, individually context-tiered/priced before summing.
    """
    entry = catalog.find_entry(model, media_type="text", binding=catalog.binding_key(entry_binding, "text"))
    return price_requests(
        entry.price if entry is not None else None,
        requests,
        multiplier=multiplier,
        entry_found=entry is not None,
    )
