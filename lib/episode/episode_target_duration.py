"""Sole target episode-seconds authority: field names/hard ranges/read parsing.

Optional ``project.json`` top-level settings express **whole-episode expected volume**, distinct from
``default_duration`` per-storyboard/unit seconds. Former guides unit counts,
latter individual lengths; both soft preferences overridable by content, never hard blockers (rationale:
``.out-of-scope/product-enforced-creative-limits.md``).

Ad projects lack this field: ``target_duration`` budgets/mix tables already govern whole lengths, and concurrent
sources would conflict in planning. Rejection matches ad ``default_duration``:
REST PATCH/``patch_project`` reject field presence including explicit null, meaning clear;
creation null means unprovided, identical to omission, neither persisted nor rejected.

Write models/create/PATCH/``patch_project`` allowlists and reads share ``is_valid_episode_target_duration``;
consumers use ``project_episode_target_duration`` only, never direct field reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Episode-seconds top-level ``project.json`` field.
EPISODE_TARGET_DURATION_FIELD: str = "episode_target_duration"

#: Inclusive hard seconds range, broad enough to reject obvious mistakes such as milliseconds/minutes/whole-drama
#: lengths only; no preferences within ranges, which are creative decisions.
#: Minimum 10s fits shortest complete units plus dialogue; shorter whole episodes imply input mistakes.
#: Maximum 600s, already beyond short-video episodes; longer needs should split episodes, not enlarge them.
MIN_EPISODE_TARGET_DURATION: int = 10
MAX_EPISODE_TARGET_DURATION: int = 600


def is_valid_episode_target_duration(value: Any) -> bool:
    """Whether integer seconds lie in hard ranges (``10 <= value <= 600``).

    Shared frontend/request validators, ``patch_project`` coercion, and persisted-read guards.
    Accept ``int`` only; floating seconds are unavailable, never silently rounded into settings
    differing from displayed choices. Explicitly exclude ``bool``, an ``int`` subclass, preventing JSON ``true``
    becoming one second.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return MIN_EPISODE_TARGET_DURATION <= value <= MAX_EPISODE_TARGET_DURATION


def project_episode_target_duration(project: Mapping[str, Any] | None) -> int | None:
    """Read project.json targets; absent/dirty/out-of-range → ``None``.

    ``None`` means no targets, no soft prompt constraints or review comparisons.
    Writes already reject invalid ranges; read guards cover manual edits/historical corruption,
    which must not crash already-paid generation for one bad preference.
    """
    if not isinstance(project, Mapping):
        return None
    raw = project.get(EPISODE_TARGET_DURATION_FIELD)
    return raw if is_valid_episode_target_duration(raw) else None
