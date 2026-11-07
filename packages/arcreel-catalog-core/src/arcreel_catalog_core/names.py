"""Model name normalization (see docs/adr/0105)."""

from __future__ import annotations


def normalize_model_name(name: str) -> str:
    """Ignore case, strip the namespace through the last ``/``, and treat dots as hyphens.

    Do not strip date suffixes or suffixes such as ``:free``, ``-thinking``, and ``-preview``: whether a dated snapshot
    belongs to the same model is expressed by model aliases in catalog data.
    """
    return name.rpartition("/")[2].lower().replace(".", "-")
