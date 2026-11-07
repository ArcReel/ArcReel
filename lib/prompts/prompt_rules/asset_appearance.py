"""Render the current appearance associated with a reference name in asset candidates.

Asset blocks in script_plan and prompt_authoring explain each reference name's appearance to the script model.
Characters allow asset names and ``base/derivative`` (see ``docs/adr/0072``). Derivatives register only changes
relative to the base, so appearance is **base description plus those changes**. Both builders (drama/narration
``lib.prompts.prompt_builders_script`` and reference-to-video ``lib.prompts.prompt_builders_reference``) share
this module, keeping wording in one place.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD
from lib.references.reference_catalog import derivative_reference

#: Derivative-appearance prefix on a new line after the base. Model-facing prompt text, exempt from i18n by repository rules.
CURRENT_FORM_PREFIX = "Current appearance: "


def _description(entry: object) -> str:
    value = entry.get("description") if isinstance(entry, Mapping) else None
    return value.strip() if isinstance(value, str) else ""


def iter_asset_appearances(asset_type: str, bucket: Mapping[str, Any] | None) -> Iterator[tuple[str, str]]:
    """Yield script-eligible ``(reference_name, appearance)`` pairs, each base followed by its derivatives.

    Types without derivative support (``AssetSpec.supports_derivatives``) yield base names only. Externally
    corrupted project.json entries or derivative tables may not be Mapping; treat as undescribed, without raising.
    """
    supports_derivatives = ASSET_SPECS[asset_type].supports_derivatives
    for name, entry in (bucket or {}).items():
        base_description = _description(entry)
        yield str(name), base_description
        if not supports_derivatives or not isinstance(entry, Mapping):
            continue
        derivatives = entry.get(DERIVATIVES_FIELD)
        if not isinstance(derivatives, Mapping):
            continue
        for derivative_name, derivative in derivatives.items():
            change = _description(derivative)
            parts = [part for part in (base_description, f"{CURRENT_FORM_PREFIX}{change}" if change else "") if part]
            yield derivative_reference(str(name), str(derivative_name)), "\n".join(parts)


def asset_reference_names(asset_type: str, bucket: Mapping[str, Any] | None) -> list[str]:
    """All names eligible for this type's script reference fields, each base followed by derivatives."""
    return [name for name, _appearance in iter_asset_appearances(asset_type, bucket)]
