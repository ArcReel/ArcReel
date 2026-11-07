"""Capacity-based request-duration tier selection and shared projection for both routes.

Model ``supported_durations`` are discrete; request baselines rarely match exactly. Interpret tiers as
**capacity**: choose the smallest legal tier containing the baseline. Never trim output; delivered duration
is the tier duration. Baselines beyond the maximum request the maximum (output shorter than baseline).

Pure functions, no I/O. Both reference/storyboard quoting, preflight, and execution resolve current provider/model
and tiers, then share :func:`project_request_duration`, preventing divergent decisions for identical state.
Each route only maps returned :data:`DurationProblem` categories into its own problem envelopes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# Tier offset direction relative to request baseline, matching frontend `types/reference-video.ts` literals.
# Literal instead of str lets typing check branch comparisons in `warning()` and preflight responses.
Adjustment = Literal["exact", "up", "down", "unconstrained"]

EXACT: Adjustment = "exact"
"""The requested-duration basis is itself a tier member; requested value equals the basis."""
UP: Adjustment = "up"
"""Round up to a tier: output is longer than the requested-duration basis."""
DOWN: Adjustment = "down"
"""The requested-duration basis exceeds the maximum tier; request the maximum, yielding output shorter than the basis."""
UNCONSTRAINED: Adjustment = "unconstrained"
"""ArcReel does not select a duration tier: request the planned value; the endpoint determines output length."""


@dataclass(frozen=True)
class DurationSlot:
    """Tier result: ``seconds`` is backend-requested duration; ``total_seconds`` is the request baseline."""

    seconds: int
    total_seconds: int | float
    adjustment: Adjustment

    @property
    def needs_confirmation(self) -> bool:
        """Require user confirmation when request seconds differ from the baseline."""
        return self.adjustment in (UP, DOWN)

    def warning(self, *, provider: str, model: str) -> dict | None:
        """Task warning (i18n key + parameters) for a tier offset from baseline, or None without offset."""
        if not self.needs_confirmation:
            return None
        key = "ref_duration_rounded_up" if self.adjustment == UP else "ref_duration_exceeded"
        return {
            "key": key,
            "params": {"total": self.total_seconds, "duration": self.seconds, "provider": provider, "model": model},
        }


def resolve_duration_slot(total_seconds: int | float, supported_durations: Sequence[int]) -> DurationSlot:
    """Select request tiers by capacity semantics.

    Empty tier sets pass total duration through unchanged. Executable reference requests must not rely
    on this branch: ``ReferenceUnitRequestProjector`` first blocks missing/empty/invalid tiers structurally.
    Nonempty tiers need not be sorted and may duplicate values.

    Fractional seconds (such as 4.5) also select the smallest containing tier ≥ baseline; never truncate
    normalization, which silently shortens durations that should round up.
    """
    slots = sorted({int(d) for d in supported_durations})
    if not slots:
        return DurationSlot(seconds=int(total_seconds), total_seconds=total_seconds, adjustment=UNCONSTRAINED)
    fitting = [d for d in slots if d >= total_seconds]
    adjustment: Adjustment
    if fitting:
        chosen = fitting[0]
        adjustment = EXACT if chosen == total_seconds else UP
    else:
        chosen = slots[-1]
        adjustment = DOWN
    return DurationSlot(seconds=chosen, total_seconds=total_seconds, adjustment=adjustment)


#: Shared duration-projection problem categories. Callers map them to their own envelopes' problem codes:
#: categories express decisions; codes/copy belong to each route's external contract.
DurationProblem = Literal[
    "supported_durations_missing",
    "needs_replan",
    "confirmation_required",
]


@dataclass(frozen=True)
class RequestDurationProjection:
    """One request duration conclusion: selected tier, offset, and sole blocking category.

    ``slot`` None means missing tiers prevent request-second resolution; ``problem`` must then be nonempty.
    True ``endpoint_fixed`` means ArcReel does not control this dimension, and ``slot.seconds`` passes
    planned seconds through unchanged rather than using a tier member.
    """

    duration_input: int
    slot: DurationSlot | None
    endpoint_fixed: bool
    problem: DurationProblem | None


def project_request_duration(
    *,
    planned_duration_seconds: int,
    supported_durations: Sequence[int],
    duration_endpoint_fixed: bool = False,
    confirmed_request_duration_seconds: int | None = None,
    confirmation_waived: bool = False,
) -> RequestDurationProjection:
    """Project planned seconds and model tiers into request duration; planned duration is the baseline.

    With true ``duration_endpoint_fixed``, empty tiers are valid (endpoint-fixed duration,
    ``docs/adr/0082``). Never narrow or request confirmation; pass planned seconds unchanged, while endpoint workflow controls output.

    Empty sets without ``duration_endpoint_fixed`` mean missing tier declarations (``docs/adr/0018``),
    still blocking as ``supported_durations_missing``.
    """

    if isinstance(planned_duration_seconds, bool) or planned_duration_seconds <= 0:
        raise ValueError("planned_duration_seconds must be a positive integer")

    if duration_endpoint_fixed:
        return RequestDurationProjection(
            duration_input=planned_duration_seconds,
            slot=DurationSlot(
                seconds=planned_duration_seconds,
                total_seconds=planned_duration_seconds,
                adjustment=UNCONSTRAINED,
            ),
            endpoint_fixed=True,
            problem=None,
        )

    durations = tuple(
        sorted({duration for duration in supported_durations if not isinstance(duration, bool) and duration > 0})
    )
    if not durations:
        return RequestDurationProjection(
            duration_input=planned_duration_seconds,
            slot=None,
            endpoint_fixed=False,
            problem="supported_durations_missing",
        )

    slot = resolve_duration_slot(planned_duration_seconds, durations)
    problem: DurationProblem | None = None
    if slot.adjustment == DOWN:
        problem = "needs_replan"
    elif (
        slot.seconds != planned_duration_seconds
        and not confirmation_waived
        and confirmed_request_duration_seconds != slot.seconds
    ):
        problem = "confirmation_required"
    return RequestDurationProjection(
        duration_input=planned_duration_seconds,
        slot=slot,
        endpoint_fixed=False,
        problem=problem,
    )
