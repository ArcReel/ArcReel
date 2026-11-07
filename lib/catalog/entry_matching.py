"""Model-name entry matching (docs/adr/0105): find corresponding entries by name.

Name-level rules only: neither store mappings nor modify entries. Callers query current catalogs each time. Normalization uses
``arcreel_catalog_core.normalize_model_name``, shared with build-side name-conflict detection.
"""

from __future__ import annotations

from collections.abc import Iterable

from arcreel_catalog_core import CatalogEntry, EntryKey, normalize_model_name

# Exact before normalized matches; official names before aliases within equal tiers.
_EXACT_NAME, _EXACT_ALIAS, _NORMALIZED_NAME, _NORMALIZED_ALIAS = range(4)


def _hit_rank(model_name: str, normalized: str, entry: CatalogEntry) -> int | None:
    """Best matching tier for this entry/model name; ``None`` without matches."""
    if model_name == entry.name:
        return _EXACT_NAME
    if model_name in entry.aliases:
        return _EXACT_ALIAS
    if normalized == normalize_model_name(entry.name):
        return _NORMALIZED_NAME
    if any(normalized == normalize_model_name(alias) for alias in entry.aliases):
        return _NORMALIZED_ALIAS
    return None


def match_entry(
    entries: Iterable[CatalogEntry],
    model_name: str,
    *,
    media_type: str | None = None,
    binding: EntryKey | None = None,
) -> CatalogEntry | None:
    """Corresponding entries by model name; ``None`` when absent or not uniquely determinable.

    ``binding`` is user-selected: use directly when present in ``entries`` and matching ``media_type``;
    absent/type-mismatched bindings do not apply, continuing name lookup.

    Name lookup considers matching ``media_type`` only, unrestricted if omitted; prefer exact official names,
    exact aliases, normalized official names, then normalized aliases. Multiple same-media matches choose first catalog order;
    cross-media matches (dual-modality models) without ``media_type`` cannot choose, returning
    ``None`` for callers to determine media first. Discontinued entries still match.
    """
    candidates = [entry for entry in entries if media_type in (None, entry.media_type)]
    if binding is not None:
        bound = next((entry for entry in candidates if entry.key == binding), None)
        if bound is not None:
            return bound
    normalized = normalize_model_name(model_name)
    hits = [(rank, entry) for entry in candidates if (rank := _hit_rank(model_name, normalized, entry)) is not None]
    if not hits:
        return None
    best = min(rank for rank, _ in hits)
    top = [entry for rank, entry in hits if rank == best]
    if len({entry.media_type for entry in top}) > 1:
        return None
    return top[0]
