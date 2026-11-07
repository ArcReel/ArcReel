"""Video-generation preflight: project whole-request admission into per-unit quotes without creating tasks.

Quotes and formal submission share admission: reference-video tiers/costs come from admission tickets; storyboard-video
quotes use the same video-request facts as admission and script-planned durations. After user tier confirmation,
submit the quote’s ``confirmed_request_durations`` to avoid reconfirming the same tiers. If projection changes between
calls, such as a model or planned-duration change, admission requests confirmation again normally."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from lib.db import async_session_factory
from lib.generation.batch_admission import DURATION_CONFIRMATION_CODE, BatchAdmission, UnitAdmissionTicket
from lib.generation.generation_result import GenerationProblem
from lib.generation.video_request_facts import VideoRequestCostFacts, VideoRequestFacts, VideoRequestFactsFailure
from server.services.admission.cost_estimation import quote_video_request


class VideoQuoteOutcome(StrEnum):
    """What formal submission will do with this unit."""

    #: Enqueues generation, after supplying confirmed tiers for units with tier changes.
    GENERATE = "generate"
    #: Existing usable video is reused unchanged by formal submission, without charges.
    REUSE = "reuse"
    #: Gaps beyond tier confirmation prevent enqueueing the whole batch until repaired.
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class VideoUnitQuote:
    """Preflight outcome for one video unit."""

    unit_id: str
    outcome: VideoQuoteOutcome
    script_duration_seconds: int | None = None
    request_duration_seconds: int | None = None
    cost: Mapping[str, object] | None = None
    problems: tuple[GenerationProblem, ...] = ()

    @property
    def priced_cost(self) -> tuple[float, str] | None:
        """Quote ``(amount, currency)``; ``None`` when no quote is available or an unpriced quote has a ``None`` amount."""

        amount = self.cost.get("amount") if self.cost is not None else None
        currency = self.cost.get("currency") if self.cost is not None else None
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not isinstance(currency, str):
            return None
        return float(amount), currency

    @property
    def unpriced(self) -> bool:
        """Eligible to enqueue but unpriced: the Agent must tell the user this unit’s cost is unknown, never treat it as 0."""

        return self.outcome is VideoQuoteOutcome.GENERATE and self.priced_cost is None

    @property
    def tier_changed(self) -> bool:
        """Requested tier differs from script-planned duration: output is longer or shorter, priced by the requested tier."""

        return (
            self.script_duration_seconds is not None
            and self.request_duration_seconds is not None
            and self.request_duration_seconds != self.script_duration_seconds
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "outcome": self.outcome.value,
            "script_duration_seconds": self.script_duration_seconds,
            "request_duration_seconds": self.request_duration_seconds,
            "tier_changed": self.tier_changed,
            "unpriced": self.unpriced,
            "estimated_cost": dict(self.cost) if self.cost is not None else None,
            "problems": [problem.model_dump(mode="json") for problem in self.problems],
        }


@dataclass(frozen=True, slots=True)
class VideoQuoteSheet:
    """Quote sheet for one ``generate_videos`` preflight."""

    units: tuple[VideoUnitQuote, ...] = field(default_factory=tuple)

    @property
    def submittable(self) -> bool:
        """Whether formal submission with confirmed tiers can enqueue the whole batch."""

        return all(unit.outcome is not VideoQuoteOutcome.BLOCKED for unit in self.units)

    @property
    def confirmed_request_durations(self) -> dict[str, int]:
        """Tier confirmations copied unchanged into formal submission, including only enqueueable units with changed tiers."""

        return {
            unit.unit_id: unit.request_duration_seconds
            for unit in self.units
            if unit.outcome is VideoQuoteOutcome.GENERATE and unit.tier_changed and unit.request_duration_seconds
        }

    @property
    def unpriced_count(self) -> int:
        """Count of enqueueable units without a price."""

        return sum(1 for unit in self.units if unit.unpriced)

    @property
    def estimated_total(self) -> dict[str, float] | None:
        """Priced costs among enqueueable units, totaled separately by currency.

        If some units are unpriced, totals contain only priced units; :attr:`unpriced_count` gives the rest. With no priced units,
        return ``None`` because no total is available, distinct from an empty total when no units require charges."""

        total: dict[str, float] = {}
        for unit in self.units:
            if unit.outcome is not VideoQuoteOutcome.GENERATE or unit.priced_cost is None:
                continue
            amount, currency = unit.priced_cost
            total[currency] = round(total.get(currency, 0.0) + amount, 6)
        if not total and self.unpriced_count:
            return None
        return total

    def to_payload(self) -> dict[str, object]:
        return {
            "submittable": self.submittable,
            "units": [unit.to_payload() for unit in self.units],
            "estimated_total": self.estimated_total,
            "unpriced_count": self.unpriced_count,
            "confirmed_request_durations": self.confirmed_request_durations,
        }

    def summary(self, log: Sequence[str] = ()) -> str:
        lines = [*log, "预检完成，未入队任何任务。", *(f"- {unit.unit_id}：{_unit_line(unit)}" for unit in self.units)]
        total = self.estimated_total
        unpriced = self.unpriced_count
        if total is None:
            lines.append(f"预计合计：{unpriced} 个单元未定价，无法给出合计，须如实告知用户费用未知。")
        else:
            if total:
                amounts = "、".join(f"{amount:g} {currency}" for currency, amount in total.items())
                lines.append(f"预计合计：{amounts}" + ("（仅含已定价的单元）" if unpriced else ""))
            if unpriced:
                lines.append(f"另有 {unpriced} 个单元未定价，上面的合计不含它们的费用，须如实告知用户。")
        if not self.submittable:
            lines.append("有单元受阻：按各单元的 problems 修复后再预检或提交。")
        elif self.confirmed_request_durations:
            lines.append(
                "档位有变化的单元经用户确认后，正式提交时带上 confirmed_request_durations="
                f"{json.dumps(self.confirmed_request_durations, ensure_ascii=False)}，不会再要求确认档位。"
            )
        return "\n".join(lines)


def _unit_line(unit: VideoUnitQuote) -> str:
    if unit.outcome is VideoQuoteOutcome.REUSE:
        return "已有可用视频，复用，不收费"
    if unit.outcome is VideoQuoteOutcome.BLOCKED:
        return "受阻：" + "；".join(f"{problem.code}（{problem.detail}）" for problem in unit.problems)
    request = f"{unit.request_duration_seconds}s" if unit.request_duration_seconds is not None else "档位待定"
    script = f"{unit.script_duration_seconds}s" if unit.script_duration_seconds is not None else "未写"
    tier = f"申请 {request}（编排 {script}，档位有变化）" if unit.tier_changed else f"申请 {request}"
    cost = unit.cost
    if cost is None:
        price = "未定价（无法为这次请求报价）"
    elif cost.get("amount") is None:
        price = f"未定价（原因：{cost.get('unpriced_reason')}）"
    else:
        price = f"预计 {cost.get('amount')} {cost.get('currency')}"
    return f"{tier}，{price}"


def _own_problems(ticket: UnitAdmissionTicket) -> tuple[GenerationProblem, ...]:
    """Gaps other than tier confirmation, which the quote asks users to decide and does not count as blocking."""

    return tuple(problem for problem in ticket.problems if problem.code != DURATION_CONFIRMATION_CODE)


def _positive_seconds(value: object) -> int | None:
    """Seconds from scripts/projections: retain positive integers; treat absent, malformed or boolean values as unavailable."""

    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _script_duration(ticket: UnitAdmissionTicket) -> int | None:
    return _positive_seconds((ticket.projection or {}).get("planned_duration"))


def quote_sheet(
    admission: BatchAdmission | None,
    *,
    reused_ids: Sequence[str] = (),
    storyboard_durations: Mapping[str, object] | None = None,
    storyboard_costs: Mapping[str, Mapping[str, object] | None] | None = None,
) -> VideoQuoteSheet:
    """Project whole-batch admission into a quote.

    Reference-video admission tickets contain planned durations, requested tiers and costs. Storyboard-video tickets do not;
    requested tiers equal script-planned durations, with costs precomputed by :func:`quote_storyboard_video_units`."""

    units: list[VideoUnitQuote] = [VideoUnitQuote(unit_id, VideoQuoteOutcome.REUSE) for unit_id in reused_ids]
    for ticket in admission.tickets if admission is not None else ():
        problems = _own_problems(ticket)
        if storyboard_durations is not None and ticket.unit_id in storyboard_durations:
            script_duration = _positive_seconds(storyboard_durations[ticket.unit_id])
            request_duration = script_duration
            cost = (storyboard_costs or {}).get(ticket.unit_id)
        else:
            script_duration = _script_duration(ticket)
            request_duration = ticket.request_duration_seconds
            cost = ticket.request_cost
        units.append(
            VideoUnitQuote(
                unit_id=ticket.unit_id,
                outcome=VideoQuoteOutcome.BLOCKED if problems else VideoQuoteOutcome.GENERATE,
                script_duration_seconds=script_duration,
                request_duration_seconds=request_duration,
                cost=cost,
                problems=problems,
            )
        )
    return VideoQuoteSheet(units=tuple(units))


async def quote_storyboard_video_units(
    facts: VideoRequestFacts | VideoRequestFactsFailure | None,
    durations: Mapping[str, object],
    *,
    aspect_ratio: str,
) -> dict[str, dict[str, object] | None]:
    """Quote storyboard-video units using script-planned durations and the same video-request facts as admission.

    ``aspect_ratio`` is the ratio sent in video requests. Invalid facts or missing planned durations yield ``None``.
    When the price list cannot price a unit, its quote amount is ``None`` with an unpriced reason."""

    costs: dict[str, dict[str, object] | None] = {}
    for unit_id, value in durations.items():
        duration = _positive_seconds(value)
        if not isinstance(facts, VideoRequestFacts) or duration is None:
            costs[unit_id] = None
            continue
        quote = await quote_video_request(
            VideoRequestCostFacts(facts, duration, aspect_ratio=aspect_ratio, has_first_frame=True),
            async_session_factory,
        )
        costs[unit_id] = quote.to_payload() if quote is not None else None
    return costs


__all__ = [
    "VideoQuoteOutcome",
    "VideoQuoteSheet",
    "VideoUnitQuote",
    "quote_sheet",
    "quote_storyboard_video_units",
]
