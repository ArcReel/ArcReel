"""Video-channel entry binding and name matching.

Selected endpoints determine request structure, metering, and input routes; model facts come from assembly-selected entries.
DashScope prompt limits match official entry names and aliases, falling back to API model names without matching entries.
Use caller-provided entries directly, otherwise look up by API model name now (docs/adr/0105).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Self

from lib.catalog import query as catalog

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry


def capability_names(model: str, entry: CatalogEntry | None = None) -> tuple[str, ...]:
    """Names compared for prompt-limit matching: official entry names/model aliases, or API names without matching entries.

    ``entry`` is the caller-resolved corresponding entry (binding takes precedence, ``docs/adr/0105``); otherwise look up by API model name now.
    """
    if entry is None:
        entry = catalog.find_entry(model, media_type="video")
    if entry is None:
        return (model,)
    return (entry.name, *entry.aliases)


def match_by_names[T](model: str, match: Callable[[str], T | None], *, entry: CatalogEntry | None = None) -> T | None:
    """Apply ``match`` to :func:`capability_names` names in sequence; return the first non-``None`` result, or ``None`` if none match."""
    for name in capability_names(model, entry):
        found = match(name)
        if found is not None:
            return found
    return None


class EntryBoundCapabilities:
    """Shared channel-instance state holding corresponding model entries (docs/adr/0104, 0105).

    Assembly supplies entries through :meth:`with_catalog_entry` (bindings first, otherwise current name lookup). Without one,
    ``_catalog_entry`` is ``None`` and API model names drive current lookup. Entries supply model facts/prompt-limit matches; requests still send API model names.
    """

    _catalog_entry: CatalogEntry | None = None

    @property
    def catalog_entry(self) -> CatalogEntry | None:
        """Assembly-supplied entry: settlement, capabilities, and request construction use it to identify the same model (:func:`catalog_entry_of`)."""
        return self._catalog_entry

    def with_catalog_entry(self, entry: CatalogEntry | None) -> Self:
        """Return a copy determining request shape from ``entry``; share clients/credentials/models and other state, without mutating this instance."""
        clone = copy.copy(self)
        clone._catalog_entry = entry
        clone._on_catalog_entry_changed()
        return clone

    def _on_catalog_entry_changed(self) -> None:
        """Recompute model-name-derived construction state for new entries here; no override needed without such state."""


def catalog_entry_of(backend: object) -> CatalogEntry | None:
    """Entry defining a channel instance's model: assembly-supplied entry, or ``None`` for nonentry-based channels (declarative, ComfyUI).

    Settlement and request construction read model facts from the same entry; presubmission estimates resolve entries identically (built-ins ``provider_model_entry``, custom entry bindings).
    """
    return getattr(backend, "catalog_entry", None)
