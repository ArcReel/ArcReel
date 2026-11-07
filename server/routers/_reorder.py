"""Full-permutation reorder validation for market-source reorder endpoints.

Return error discriminants instead of raising HTTP errors; callers map them to their domain's i18n wording,
keeping validation semantics (equal length, no duplicates, equal sets) in one place.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import Literal

PermutationError = Literal["length", "duplicate", "mismatch"]


def full_permutation_error[T: Hashable](
    existing_ids: Sequence[T], proposed_ids: Sequence[T]
) -> PermutationError | None:
    """``proposed_ids`` must be a permutation of all ``existing_ids``; return None if valid, otherwise the error category."""
    if len(proposed_ids) != len(existing_ids):
        return "length"
    if len(set(proposed_ids)) != len(proposed_ids):
        return "duplicate"
    if set(proposed_ids) != set(existing_ids):
        return "mismatch"
    return None
