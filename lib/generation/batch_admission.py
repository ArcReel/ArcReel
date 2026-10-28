"""All-or-nothing pre-request admission for batch video generation.

A batch video request evaluates every target once — speech composition, the
optional TTS choice, the request projection and its quote — and then either
creates the whole task set or none of it.  This module owns the transport
neutral part of that: the per-unit verdict, the folding rule, the aggregate
confirmation summary, and how a refusal is written into the
``requested / succeeded / failed / blocked`` contract.

Collecting the current project, TTS, provider and queue state is the adapter's
job (:mod:`server.services.admission.video_batch_admission`); nothing here reads the
filesystem, the database or the queue.

The decision is a pre-request gate and says nothing about execution outcomes:
once a batch is admitted, individual tasks may still succeed or fail, and that
is reported through :class:`lib.generation.generation_result.GenerationBatchResult` as
usual.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from lib.generation.generation_result import (
    GenerationAction,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
)

DURATION_CONFIRMATION_CODE = "reference_duration_confirmation_required"
"""The one blocker a user can clear by agreeing to the request tier and cost."""


class BatchAdmissionDecision(StrEnum):
    """Whether this request may create its task set."""

    ADMITTED = "admitted"
    CONFIRMATION_REQUIRED = "confirmation_required"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class UnitAdmissionTicket:
    """One target's verdict, carrying every reason it has rather than the first.

    ``problems`` is empty exactly when this unit could be requested on its own.
    ``projection`` keeps the serialized current-state facts the entry already
    shows next to the verdict, so no caller re-derives tier or provider identity
    from the problem params.
    """

    unit_id: str
    problems: tuple[GenerationProblem, ...] = ()
    request_duration_seconds: int | None = None
    request_cost: Mapping[str, object] | None = None
    projection: Mapping[str, object] | None = None

    @property
    def admitted(self) -> bool:
        return not self.problems

    @property
    def confirmation_only(self) -> bool:
        """True when the only thing standing in the way is the user's consent."""

        return bool(self.problems) and all(problem.code == DURATION_CONFIRMATION_CODE for problem in self.problems)

    def to_payload(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "admitted": self.admitted,
            "request_duration_seconds": self.request_duration_seconds,
            "request_cost": dict(self.request_cost) if self.request_cost is not None else None,
            "problems": [problem.model_dump(mode="json") for problem in self.problems],
            "projection": dict(self.projection) if self.projection is not None else None,
        }


@dataclass(frozen=True, slots=True)
class BatchConfirmationTier:
    """One request tier the user is asked to accept, with its count and cost."""

    request_duration_seconds: int | None
    unit_ids: tuple[str, ...]
    cost_amount: float | None
    cost_currency: str | None
    unpriced_count: int = 0

    @property
    def unit_count(self) -> int:
        return len(self.unit_ids)

    def to_payload(self) -> dict[str, object]:
        return {
            "request_duration_seconds": self.request_duration_seconds,
            "unit_count": self.unit_count,
            "unit_ids": list(self.unit_ids),
            "cost_amount": self.cost_amount,
            "cost_currency": self.cost_currency,
            "unpriced_count": self.unpriced_count,
        }


@dataclass(frozen=True, slots=True)
class BatchAdmission:
    """The whole request's verdict over a resolved target set."""

    operation: str
    selection: GenerationSelectionMode
    tickets: tuple[UnitAdmissionTicket, ...]

    @property
    def decision(self) -> BatchAdmissionDecision:
        refused = [ticket for ticket in self.tickets if not ticket.admitted]
        if not refused:
            return BatchAdmissionDecision.ADMITTED
        if all(ticket.confirmation_only for ticket in refused):
            return BatchAdmissionDecision.CONFIRMATION_REQUIRED
        return BatchAdmissionDecision.BLOCKED

    @property
    def admitted(self) -> bool:
        return self.decision is BatchAdmissionDecision.ADMITTED

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(ticket.unit_id for ticket in self.tickets)

    @property
    def refused_tickets(self) -> tuple[UnitAdmissionTicket, ...]:
        return tuple(ticket for ticket in self.tickets if not ticket.admitted)

    @property
    def holding_unit_ids(self) -> tuple[str, ...]:
        """The units whose own problems kept the whole batch from being created."""

        return tuple(ticket.unit_id for ticket in self.refused_tickets)

    def withheld_problem_for(self, ticket: UnitAdmissionTicket) -> GenerationProblem | None:
        """The reason this ticket has none of its own, or ``None`` if it does.

        Both the result contract and the serialized envelope ask the same
        question of the same ticket, so they ask it here once — a unit that
        passed on its own is never described two different ways.
        """

        holding = self.holding_unit_ids
        if not holding or not ticket.admitted:
            return None
        return batch_admission_withheld_problem(holding)

    def confirmation_tiers(self) -> tuple[BatchConfirmationTier, ...]:
        """Group the units awaiting consent by the tier they would be billed at."""

        grouped: dict[int | None, list[UnitAdmissionTicket]] = {}
        for ticket in self.tickets:
            if not ticket.confirmation_only:
                continue
            grouped.setdefault(ticket.request_duration_seconds, []).append(ticket)
        tiers: list[BatchConfirmationTier] = []
        for seconds, members in sorted(grouped.items(), key=lambda pair: (pair[0] is None, pair[0])):
            quoted = [(_cost_amount(member.request_cost), _cost_currency(member.request_cost)) for member in members]
            priced = [(amount, currency) for amount, currency in quoted if amount is not None and currency is not None]
            currencies = {currency for _, currency in priced}
            # The total covers the priced members only; those the price list cannot quote
            # are counted in ``unpriced_count`` so the user sees the sum is a floor.
            # Amounts of different currencies do not add up to a number at all, so a tier
            # priced in more than one currency has no total.
            total = sum(amount for amount, _ in priced) if len(currencies) == 1 else None
            tiers.append(
                BatchConfirmationTier(
                    request_duration_seconds=seconds,
                    unit_ids=tuple(member.unit_id for member in members),
                    cost_amount=total,
                    cost_currency=next(iter(currencies)) if len(currencies) == 1 else None,
                    unpriced_count=len(quoted) - len(priced),
                )
            )
        return tuple(tiers)

    def quoted_total(self) -> tuple[dict[str, float], int]:
        """The whole request's quoted cost by currency, and how many targets have no quote.

        Each unit is quoted at the tier it would be billed at, so a batch awaiting consent
        totals what it costs once the user accepts. Currencies stay apart; units the price
        list cannot quote are counted instead, so the caller knows the total is a floor.
        """

        total: dict[str, float] = {}
        unpriced = 0
        for ticket in self.tickets:
            amount = _cost_amount(ticket.request_cost)
            currency = _cost_currency(ticket.request_cost)
            if amount is None or currency is None:
                unpriced += 1
                continue
            total[currency] = round(total.get(currency, 0.0) + amount, 6)
        return {currency: amount for currency, amount in total.items() if amount > 0}, unpriced

    def projections(self) -> list[dict[str, object]]:
        return [dict(ticket.projection) for ticket in self.tickets if ticket.projection is not None]

    def record_refusal(
        self,
        builder: GenerationResultBuilder,
        *,
        states: Mapping[str, GenerationTargetState] | None = None,
    ) -> None:
        """Report every target as blocked, because zero tasks were created.

        A unit that passed on its own still gets a machine-readable reason —
        ``BATCH_ADMISSION_WITHHELD`` naming the units that held the batch back —
        so no caller has to infer from an absent entry whether it was requested.
        """

        if self.admitted:
            raise ValueError("record_refusal requires a refused admission")
        for ticket in self.tickets:
            withheld = self.withheld_problem_for(ticket)
            problem = ticket.problems[0] if ticket.problems else withheld
            if problem is None:
                raise RuntimeError("a refused batch leaves no ticket without a reason")
            if len(ticket.problems) > 1:
                problem = problem.model_copy(
                    update={
                        "params": {
                            **problem.params,
                            "problems": [other.model_dump(mode="json") for other in ticket.problems],
                        }
                    }
                )
            state = states.get(ticket.unit_id) if states is not None else None
            builder.block(
                ticket.unit_id,
                problem=problem,
                artifact_key=state.artifact_key if state is not None else None,
                artifact_path=state.artifact_path if state is not None else None,
                artifact_status=state.status if state is not None else None,
            )

    def to_payload(self) -> dict[str, object]:
        """The envelope Web and Agent both serialize, before any localization.

        On a refused batch every unit carries a reason, including the ones that
        would have passed on their own: ``withheld`` marks them and their problem
        names the units that held the batch back. ``admitted`` stays each unit's
        own verdict, so a caller can tell "you must fix this" from "you must fix
        someone else".
        """

        units: list[dict[str, object]] = []
        for ticket in self.tickets:
            payload = ticket.to_payload()
            withheld = self.withheld_problem_for(ticket)
            payload["withheld"] = withheld is not None
            if withheld is not None:
                payload["problems"] = [withheld.model_dump(mode="json")]
            units.append(payload)
        return {
            "decision": self.decision.value,
            "operation": self.operation,
            "selection": self.selection.value,
            "units": units,
            "confirmation": {"tiers": [tier.to_payload() for tier in self.confirmation_tiers()]}
            if self.decision is BatchAdmissionDecision.CONFIRMATION_REQUIRED
            else None,
        }


def batch_admission_withheld_problem(holding_unit_ids: Sequence[str]) -> GenerationProblem:
    """本身通过准入、但因同批其它单元受阻而未创建任务的单元的结论。"""

    return GenerationProblem(
        code=GenerationProblemCode.BATCH_ADMISSION_WITHHELD,
        detail=(
            "本次批量请求未创建任何任务：同批的 "
            f"{'、'.join(holding_unit_ids)} 未通过准入。修复后重试即可连同本 unit 一起提交。"
        ),
        action=GenerationAction.RETRY,
        params={"blocked_unit_ids": list(holding_unit_ids)},
    )


def _cost_amount(cost: Mapping[str, object] | None) -> float | None:
    if cost is None:
        return None
    amount = cost.get("amount")
    return float(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None


def _cost_currency(cost: Mapping[str, object] | None) -> str | None:
    if cost is None:
        return None
    currency = cost.get("currency")
    return currency if isinstance(currency, str) and currency else None


def refused_ticket(
    unit_id: str,
    *,
    code: str,
    detail: str,
    action: GenerationAction,
    params: dict[str, object] | None = None,
) -> UnitAdmissionTicket:
    """One target refused before the request-planning modules were consulted.

    Entries use this for the gaps they detect themselves — an id that is not in
    the script, an artifact state that cannot be read — so those refusals join
    the same fold instead of being recorded on their own.
    """

    return UnitAdmissionTicket(
        unit_id=unit_id,
        problems=(GenerationProblem(code=code, detail=detail, action=action, params=params or {}),),
    )


__all__ = [
    "DURATION_CONFIRMATION_CODE",
    "BatchAdmission",
    "BatchAdmissionDecision",
    "BatchConfirmationTier",
    "UnitAdmissionTicket",
    "batch_admission_withheld_problem",
    "refused_ticket",
]
