"""Agent-credential model tiers: actual called model names and matching catalog entries (docs/adr/0102).

Tiers are the main model plus four Claude Code routes: ``haiku`` / ``sonnet`` / ``opus`` capability
levels and subagents. Environment assembly, connection-time pricing snapshots, and settings-tier
pricing status share actual tier model names here, yielding identical names for the same credential.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from arcreel_catalog_core import normalize_model_name
from lib.catalog import query as catalog

if TYPE_CHECKING:
    from lib.db.models.agent_credential import AgentAnthropicCredential

Tier = Literal["model", "haiku", "sonnet", "opus", "subagent"]

#: Fixed tier order, main model first. For repeated model names, use the first bound tier.
TIERS: tuple[Tier, ...] = ("model", "haiku", "sonnet", "opus", "subagent")

#: Official Anthropic preset: CLI resolves empty tiers; never fill them with main-model names for users.
OFFICIAL_PRESET_ID = "anthropic-official"

#: Tier field names on credentials.
_CREDENTIAL_FIELDS: Mapping[Tier, str] = {
    "model": "model",
    "haiku": "haiku_model",
    "sonnet": "sonnet_model",
    "opus": "opus_model",
    "subagent": "subagent_model",
}

#: Legacy system-setting tier keys, used when credential tiers are empty (the pre-credential configuration method).
TIER_SETTING_KEYS: Mapping[Tier, str] = {
    "model": "anthropic_model",
    "haiku": "anthropic_default_haiku_model",
    "sonnet": "anthropic_default_sonnet_model",
    "opus": "anthropic_default_opus_model",
    "subagent": "claude_code_subagent_model",
}


def tier_models(credential: AgentAnthropicCredential, settings: Mapping[str, str]) -> dict[Tier, str]:
    """Actual model names per tier; empty strings delegate decisions to CLI.

    Empty credential tiers first fall back to legacy system settings. For nonofficial-Anthropic
    presets, any still-empty four route tiers use the main model, preventing CLI fallback to
    default Claude names.
    """
    names: dict[Tier, str] = {
        tier: (getattr(credential, field) or settings.get(TIER_SETTING_KEYS[tier], "").strip())
        for tier, field in _CREDENTIAL_FIELDS.items()
    }
    main_model = names["model"]
    if main_model and credential.preset_id != OFFICIAL_PRESET_ID:
        for tier in TIERS[1:]:
            if not names[tier]:
                names[tier] = main_model
    return names


@dataclass(frozen=True)
class AgentPricing:
    """Agent-session pricing basis: environment-injected credential at connection, with its multiplier/tier bindings then (docs/adr/0102).

    Snapshot from the same credential row as environment values at connection; all turns on that
    connection use it. Changing multipliers/bindings during a session does not affect the existing
    connection; only later starts/resumptions use new values. Defaults represent no active credential,
    with environment variables falling back to system settings.
    """

    credential_id: int | None = None
    multiplier: float = 1.0
    #: Bound tiers in :data:`TIERS` order: (actual model name, entry binding “模型厂商/官方模型名”).
    tier_bindings: tuple[tuple[str, str], ...] = ()

    def entry_binding_for(self, model: str) -> str | None:
        """Entry binding for the tier containing API ``model``; compare names case-/namespace-insensitively. If several tiers
        share a model, use the first bound tier; otherwise return ``None`` and match entries by model name."""
        target = normalize_model_name(model)
        return next((ref for name, ref in self.tier_bindings if normalize_model_name(name) == target), None)


def agent_pricing(credential: AgentAnthropicCredential, settings: Mapping[str, str]) -> AgentPricing:
    """Current credential-pricing snapshot; tier model-name rules match :func:`tier_models`."""
    bindings = credential.tier_bindings or {}
    names = tier_models(credential, settings)
    return AgentPricing(
        credential_id=credential.id,
        multiplier=credential.multiplier,
        tier_bindings=tuple((names[tier], ref) for tier in TIERS if (ref := bindings.get(tier)) and names[tier]),
    )


@dataclass(frozen=True)
class TierStatus:
    """Current-catalog tier pricing status, queried rather than persisted.

    ``model_name`` ``None`` means an empty tier, letting CLI choose; only the two binding fields matter.
    ``manually_bound`` means bindings point to current catalog entries and determine correspondence;
    missing bound entries fall back to model-name matching and make it false.
    ``binding_stale`` means bindings persist but catalog entries disappeared (official removal);
    correspondence reverted to model names, but bindings remain so UI can still offer unbinding.
    ``priced`` means matched entries have price tables recognized by current code.
    """

    tier: Tier
    model_name: str | None
    entry_display_name: str | None
    manually_bound: bool
    binding_stale: bool
    priced: bool


def tier_statuses(credential: AgentAnthropicCredential, settings: Mapping[str, str]) -> list[TierStatus]:
    """Pricing status for every credential tier, in :data:`TIERS` order."""
    bindings = credential.tier_bindings or {}
    statuses: list[TierStatus] = []
    for tier, name in tier_models(credential, settings).items():
        ref = bindings.get(tier)
        bound = catalog.find_bound_entry(ref, "text") if ref else None
        stale = bool(ref) and bound is None
        if not name:
            # Empty-tier bindings are inactive but retained; report them truthfully so users can clear them.
            statuses.append(
                TierStatus(tier, None, None, manually_bound=bound is not None, binding_stale=stale, priced=False)
            )
            continue
        entry = bound or catalog.find_entry(name, media_type="text")
        statuses.append(
            TierStatus(
                tier,
                name,
                entry.display_name if entry is not None else None,
                manually_bound=bound is not None,
                binding_stale=stale,
                priced=entry is not None and entry.price is not None and entry.price.is_priceable,
            )
        )
    return statuses
