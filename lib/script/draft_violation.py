"""Output-contract violation types and reporting shared by all three script_plan routes.

Violations represent machine output failing content constraints: ``code`` (machine-readable category),
``label`` (location prefix, such as ``unit E1U02`` / ``segment E1S03``), an Agent-facing message, and optional
line/speech-admission locations. Reference-video, drama, and narration decide differently but share the same
entry shape and collect-many-then-render reporting, centralized here at the route-neutral ``lib`` layer.

Draft envelopes (``lib.script.draft_quarantine``) are route-independent but persist/render violations; putting
types in reference-video would make them depend on a particular route. Define types here for all route validators
and envelopes; ``lib.script.reference_video.draft_validation`` re-exports them unchanged, retaining existing
reference-video import paths.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from pydantic import ValidationError


class DraftViolation(ValueError):
    """Draft-output contract violation. Reference-syntax misuse is only one category: source anchors, speech quantity,
    fidelity, and additional generation checks use this type too. Messages carry locations and repair guidance,
    returned unchanged to Agents through tool error envelopes.

    ``code`` is machine-readable category; ``label`` locates items (such as ``unit E1U02`` / ``segment E1S03``).
    Agent-facing wording may change; report grouping and category assertions must not depend on it. Both may be
    empty when constructed outside validators (such as additional generation checks), supplying only a message.

    ``line`` is a zero-based original body line (``text.splitlines()``, matching frontend ``toScriptLines``
    ``sourceLine``), meaningful only for line-specific checks such as syntax misuse. Unit-level violations
    without a natural line (speech overload, unregistered references) leave it empty, letting presentation
    distinguish inline anchors from card-level aggregation.

    ``item_index`` is the zero-based draft-array index (``units`` / ``scenes`` / ``segments``); ``item_id``
    is its ID (``unit_id``, ``segment_id``, etc.). Presentation attaches violations by index, never parses
    ``label``. Both empty means episode-level violations.

    Nonempty ``params`` means ``code`` is a localized problem key (such as request-fact configuration problems);
    presentation localizes code/parameters rather than showing Agent-facing messages.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        label: str = "",
        line: int | None = None,
        locations: tuple[dict[str, object], ...] = (),
        reason: str | None = None,
        action: str | None = None,
        item_index: int | None = None,
        item_id: str | None = None,
        params: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.label = label
        self.line = line
        self.locations = locations
        self.reason = reason
        self.action = action
        self.item_index = item_index
        self.item_id = item_id
        self.params = params


def schema_violations(
    exc: ValidationError, content: dict[str, Any], root: str, id_field: str | None = None
) -> list[DraftViolation]:
    """Locate schema errors on original entries; retain top-level/whole-array errors as episode-level violations."""
    items = content.get(root)
    violations: list[DraftViolation] = []
    for error in exc.errors():
        loc = error["loc"]
        index = loc[1] if len(loc) > 1 and loc[0] == root and type(loc[1]) is int else None
        item = items[index] if isinstance(items, list) and index is not None and index < len(items) else None
        item_id = item.get(id_field) if isinstance(item, dict) and id_field is not None else None
        value = error.get("input")
        shown = (
            f"（当前值：{json.dumps(value, ensure_ascii=False)}）"
            if error["type"] != "missing" and isinstance(value, (bool, int, float, str)) and len(str(value)) <= 40
            else ""
        )
        violations.append(
            DraftViolation(
                f"草稿的 content.{'.'.join(str(part) for part in loc)} 不符合产出结构：{error['msg']}{shown}",
                code="schema_invalid",
                item_index=index,
                item_id=item_id if isinstance(item_id, str) else None,
            )
        )
    return violations


class DraftViolations(DraftViolation):
    """Multiple violations collected in one validation. Message is the report; ``items`` retains structured entries.

    Inherit :class:`DraftViolation` rather than creating another type: existing callers catch/assert ``DraftViolation``;
    aggregates must share the same branch rather than diverging single/multiple handling.
    """

    def __init__(self, items: Sequence[DraftViolation]):
        super().__init__(render_violation_report(items), code="multiple", label="")
        self.items: list[DraftViolation] = list(items)


def violation_items(exc: DraftViolation) -> list[DraftViolation]:
    """Flatten individual/aggregate violations into entries for report rendering and repair-draft persistence."""
    return list(exc.items) if isinstance(exc, DraftViolations) else [exc]


def collect_violations(
    checks: Iterable[Callable[[], Any]], *, item_index: int | None = None, item_id: str | None = None
) -> list[DraftViolation]:
    """Run checks in order, collecting :class:`DraftViolation` rather than stopping at the first violation.

    Individual checks still raise on first failure (shared traversal, later checks depend on earlier conclusions),
    contributing at most one violation per call. Pass each unit's separate checks so reports cover every unit
    instead of stopping at the first bad one, showing Agents all needed fixes in one round.

    Catch only ``DraftViolation``; parser errors and malformed-data type failures propagate normally,
    never disguised as content violations.

    Supplied ``item_index`` / ``item_id`` locates this batch's violations, preserving existing locations.
    """
    found: list[DraftViolation] = []
    for check in checks:
        try:
            check()
        except DraftViolation as exc:
            found.extend(violation_items(exc))
    if item_index is not None or item_id is not None:
        locate_violations(found, item_index=item_index, item_id=item_id)
    return found


def locate_violations(
    violations: Iterable[DraftViolation], *, item_index: int | None = None, item_id: str | None = None
) -> None:
    """Fill absent item locations on violations, preserving existing values."""
    for violation in violations:
        if violation.item_index is None and item_index is not None:
            violation.item_index = item_index
        if violation.item_id is None and item_id is not None:
            violation.item_id = item_id


def locate_violations_by_id(violations: Iterable[DraftViolation], item_ids: Sequence[str]) -> None:
    """Fill ``item_index`` from ``item_id`` positions in an item-ID sequence."""
    positions = {item_id: index for index, item_id in enumerate(item_ids)}
    for violation in violations:
        if violation.item_index is None and violation.item_id in positions:
            violation.item_index = positions[violation.item_id]


def render_violation_report(violations: Sequence[DraftViolation]) -> str:
    """Render numbered report text, one violation per line with category annotation."""
    lines: list[str] = []
    for index, violation in enumerate(violations, start=1):
        suffix = f"[{violation.code}] " if violation.code else ""
        lines.append(f"{index}. {suffix}{violation}")
    return "\n".join(lines)


__all__ = [
    "DraftViolation",
    "DraftViolations",
    "collect_violations",
    "locate_violations",
    "locate_violations_by_id",
    "render_violation_report",
    "violation_items",
]
