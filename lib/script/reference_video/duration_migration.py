"""Legacy reference-unit duration migration: consolidate per-shot durations at unit level.

Durations formerly lived on ``shots[*].duration``, with unit duration derived by summation. Unit duration
is now authoritative (``ReferenceVideoUnit.duration_seconds``), and shots no longer hold durations. Rewrite
legacy data once: sum into unit fields and strip shot durations plus retired ``duration_override``.

The migrator is pure (mutates input dict, returns changed flag + warnings). Loaders persist read-modify-write
under locks; the first migration determines durable state, with no second-load changes. Therefore every
``supported_durations`` entry point must agree. Both online draft paths (content-confirmation conversion and Web
review) use the union of complete r2v/i2v request-fact declarations, ensuring persisted tier membership.
Archive import lacks a configuration DB session and cannot evaluate request facts; union registry declarations
from archive-reported bucket identities, guaranteeing membership in one declared set only.

Two ``supported_durations`` None cases only clamp structural bounds: unresolvable/unconfigured video models,
and the synchronous episode-script ``migrate_script_unit_durations`` loading chain. Preflight/execution
``resolve_duration_slot`` still projects tiers and warns about offsets, preserving earlier semantics.

**Episode scripts never enforce tiers through writeback: a final responsibility boundary, not a missing feature**:

- Persisted durations already belong to tiers: ``ScriptGenerator._add_metadata`` checks final references per unit
  and errors on nonmembers. Offsets can only follow later model/resolution changes.
- No invalid tier reaches providers: shared ``ReferenceUnitRequestProjector`` reprojects and warns in preflight
  and execution. Only tasks actually submitted record requested seconds in execution checkpoints for resume;
  neither task payload nor script changes.
- Writeback conflicts with repository policy: nonmembers fail loudly or warn with reselection guidance, never
  silently rewriting user planning. Preflight is read-only GET; execution writes race user edits.
- Load-time writeback also cannot work: synchronous ``load_script`` lacks a DB session and tier access.
"""

from __future__ import annotations

from collections.abc import Sequence

from arcreel_market_core.validation_messages import ValidationMessage
from lib.script.reference_video.duration_slots import resolve_duration_slot
from lib.script.script_models import REFERENCE_UNIT_DURATION_RANGE

#: Shot-level fields and retired unit fields removed by migration.
_LEGACY_SHOT_FIELD = "duration"
_LEGACY_UNIT_FIELD = "duration_override"


def _positive_int(value: object) -> int | None:
    """Accept only positive integers (exclude bool despite its int subclass), matching malformed script-duration rules."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _strip_legacy_shot_durations(unit: dict) -> tuple[int | None, bool]:
    """Strip shot ``duration``, returning ``(时长之和, 是否剥掉过字段)``.

    None sum means no usable source: absent shot durations or all malformed (sum zero). Zero is invalid;
    both cases make callers fall back to the unit field rather than inventing duration.
    Return removal independently: stripping fields requires persistence even when no sum is usable.
    """
    shots = unit.get("shots")
    if not isinstance(shots, list):
        return None, False
    total = 0
    stripped = False
    for shot in shots:
        if not isinstance(shot, dict) or _LEGACY_SHOT_FIELD not in shot:
            continue
        stripped = True
        total += _positive_int(shot.pop(_LEGACY_SHOT_FIELD)) or 0
    return (total or None), stripped


def migrate_unit_durations(
    units: object,
    *,
    supported_durations: Sequence[int] | None = None,
) -> tuple[bool, list[ValidationMessage]]:
    """Consolidate per-shot durations into units in place; return ``(是否发生变更, warnings)``.

    Warnings are locale-neutral ``ValidationMessage``, rendered at consumption boundaries (request locale
    for archive import; logging only in loaders).

    Prefer existing ``duration_seconds`` (already summed or user-entered before consolidation; both represent
    actual requested seconds), falling back to shot sums for absent/malformed values. If neither exists,
    strip fields without inventing duration; already-invalid missing required fields are not migration's responsibility.

    Given ``supported_durations``, select by capacity (clamp beyond maximum) and warn on offsets;
    otherwise clamp only to structural ``REFERENCE_UNIT_DURATION_RANGE``.
    """
    if not isinstance(units, list):
        return False, []

    changed = False
    warnings: list[ValidationMessage] = []
    low, high = REFERENCE_UNIT_DURATION_RANGE

    for unit in units:
        if not isinstance(unit, dict):
            continue

        legacy_total, stripped = _strip_legacy_shot_durations(unit)
        had_override = _LEGACY_UNIT_FIELD in unit
        unit.pop(_LEGACY_UNIT_FIELD, None)
        if not stripped and not had_override:
            continue
        changed = True

        target = _positive_int(unit.get("duration_seconds")) or legacy_total
        if target is None:
            continue

        clamped = min(max(target, low), high)
        if clamped != target:
            warnings.append(
                ValidationMessage(
                    "val_unit_duration_clamped",
                    {"unit_id": unit.get("unit_id"), "target": target, "low": low, "high": high, "clamped": clamped},
                )
            )
        if supported_durations:
            slot = resolve_duration_slot(clamped, supported_durations)
            if slot.seconds != clamped:
                warnings.append(
                    ValidationMessage(
                        "val_unit_duration_slotted",
                        {
                            "unit_id": unit.get("unit_id"),
                            "duration": clamped,
                            "durations": sorted(set(supported_durations)),
                            "slot": slot.seconds,
                        },
                    )
                )
            clamped = slot.seconds
        unit["duration_seconds"] = clamped

    return changed, warnings


def migrate_script_unit_durations(script: object) -> tuple[bool, list[ValidationMessage]]:
    """Episode-script entry point applying only to reference-video skeletons containing ``video_units``.

    Inspect shape, not ``generation_mode`` stamps: malformed/incomplete scripts may omit stamps, and
    consolidation only asks whether per-shot durations need stripping. Other skeletons naturally do nothing.
    """
    if not isinstance(script, dict):
        return False, []
    return migrate_unit_durations(script.get("video_units"))
