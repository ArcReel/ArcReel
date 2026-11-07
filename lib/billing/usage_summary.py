"""Pure `GET /usage/summary` aggregation: projected call rows → KPIs, daily buckets, breakdowns, and attention items.

Repositories filter/fetch only; local dates, zero-filled buckets, primary currencies, and two anomaly classes belong here. No DB
or HTTP dependencies, so row lists directly exercise timezone boundaries, currency ties, Wilson lower bounds, and trailing failures.
Python aggregation also avoids SQLite/PostgreSQL date-function dialect differences.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo

from lib.backends.providers import CallStatus

# Maximum breakdown rows per dimension; remainder aggregates into unlisted other.
MAX_BREAKDOWN_ROWS = 50
# Total attention-item cap.
MAX_ATTENTION_ITEMS = 20
# Minimum failures for attention, shared by both anomaly classes.
MIN_ATTENTION_FAILED = 2
# Standard-normal quantile for two-sided 95% intervals.
WILSON_Z_95 = 1.959963984540054
# Fixed media keys for daily reference-cost breakdowns.
MEDIA_TYPES = ("image", "video", "text", "audio")
# Maximum daily buckets per response, about ten years. Caller since/until determine windows; unbounded timestamps
# millennia apart would synchronously allocate millions of buckets. All-time since uses earliest record dates, avoiding this limit.
MAX_DAILY_BUCKETS = 3660
# Amount/rate output precision matches repository write-cost precision.
_ROUND_DIGITS = 6


class UsageWindowTooWideError(ValueError):
    """Requested since/until windows exceed ``MAX_DAILY_BUCKETS`` daily buckets."""


@dataclass(frozen=True, slots=True)
class UsageSummaryRow:
    """All call fields needed for summaries; repository projections follow this shape."""

    id: int
    project_name: str
    media_type: str
    provider: str
    model: str
    status: str
    started_at: datetime
    # None means unpriced, excluded from all monetary totals.
    cost_amount: float | None
    currency: str
    segment_id: str | None
    error_code: str | None
    # Agent assistant_session records contribute totals/breakdowns, never attention.
    agent: bool = False


@dataclass(frozen=True, slots=True)
class UsageFilterOptions:
    """Filter candidates from whole-table distinct values, independent of current filters."""

    projects: list[str]
    providers: list[tuple[str, str]]
    models: list[tuple[str, str]]


def wilson_lower_bound(failures: int, total: int, z: float = WILSON_Z_95) -> float:
    """Wilson lower confidence bound for failure rates.

    More conservative than raw rates with small samples: 2 failures in 3 give only about 0.2,
    avoiding anomalies from one/two failures on newly used models.
    """
    if total <= 0:
        return 0.0
    p = failures / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


def _as_utc(value: datetime) -> datetime:
    """Interpret naive times as UTC; SQLite DateTime values lack tzinfo."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _local_date(value: datetime, tz: tzinfo) -> date:
    return _as_utc(value).astimezone(tz).date()


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).astimezone(UTC).isoformat()


@dataclass
class _StatsAccumulator:
    """Grouped counts/currency amounts; pending rows were already excluded on retrieval."""

    success: int = 0
    failed: int = 0
    cancelled: int = 0
    # Unpriced cost_amount=NULL calls count separately, outside cost, avoiding confusion with zero cost.
    unpriced: int = 0
    cost: dict[str, float] = field(default_factory=dict)

    def add(self, row: UsageSummaryRow) -> None:
        if row.status == CallStatus.SUCCESS:
            self.success += 1
        elif row.status == CallStatus.FAILED:
            self.failed += 1
        elif row.status == CallStatus.CANCELLED:
            self.cancelled += 1
        # Reference costs sum all terminal cost_amount>0 rows regardless of status: failed calls may be billed too.
        # NULL means unpriced: exclude amounts, count separately.
        if row.cost_amount is None:
            self.unpriced += 1
        elif row.cost_amount > 0:
            self.cost[row.currency] = self.cost.get(row.currency, 0.0) + row.cost_amount

    @property
    def calls(self) -> int:
        return self.success + self.failed + self.cancelled

    def to_dict(self) -> dict[str, object]:
        decided = self.success + self.failed
        return {
            "calls": self.calls,
            "success": self.success,
            "failed": self.failed,
            "cancelled": self.cancelled,
            # Success denominators count decided calls only; user cancellations are neither success nor failure.
            "success_rate": round(self.success / decided, _ROUND_DIGITS) if decided else None,
            "cost": {currency: round(amount, _ROUND_DIGITS) for currency, amount in sorted(self.cost.items())},
            "unpriced_calls": self.unpriced,
        }


def _primary_currency(cost: dict[str, float]) -> str | None:
    """Currency with largest total; ties prefer USD, then alphabetical order."""
    if not cost:
        return None
    top = max(cost.values())
    tied = sorted(currency for currency, amount in cost.items() if amount == top)
    return "USD" if "USD" in tied else tied[0]


def _window(
    rows: list[UsageSummaryRow],
    *,
    tz: tzinfo,
    since: datetime | None,
    until: datetime | None,
) -> tuple[date, date] | None:
    """Inclusive local-date [first, last] windows; None without aggregatable rows.

    Beyond ``MAX_DAILY_BUCKETS`` days raises ``UsageWindowTooWideError``: caller timestamps determine bucket counts,
    requiring bounds. With both endpoints explicit, validate before rows, independently of data presence.
    """
    since_day = _local_date(since, tz) if since else None
    # until is half-open: exactly local midnight creates no bucket for that date.
    until_day = _local_date(until - timedelta(microseconds=1), tz) if until else None
    if since_day and until_day:
        _check_bucket_count(since_day, until_day)
    if not rows:
        return None
    local_dates = [_local_date(row.started_at, tz) for row in rows]
    first = since_day or min(local_dates)
    last = until_day or max(local_dates)
    if first > last:
        return None
    _check_bucket_count(first, last)
    return first, last


def _check_bucket_count(first: date, last: date) -> None:
    if (last - first).days + 1 > MAX_DAILY_BUCKETS:
        raise UsageWindowTooWideError(f"窗口 {first} ~ {last} 超过 {MAX_DAILY_BUCKETS} 个日桶")


def _daily_buckets(
    rows: list[UsageSummaryRow],
    *,
    tz: tzinfo,
    window: tuple[date, date],
    primary_currency: str | None,
) -> list[dict[str, object]]:
    counts: dict[date, dict[str, int]] = defaultdict(lambda: {"success": 0, "failed": 0, "cancelled": 0})
    costs: dict[date, dict[str, float]] = defaultdict(lambda: dict.fromkeys(MEDIA_TYPES, 0.0))
    for row in rows:
        day = _local_date(row.started_at, tz)
        if row.status in counts[day]:
            counts[day][row.status] += 1
        # Stacked bars require one primary currency; never mix/convert other currencies.
        if (
            row.cost_amount is not None
            and row.cost_amount > 0
            and row.currency == primary_currency
            and row.media_type in costs[day]
        ):
            costs[day][row.media_type] += row.cost_amount

    first, last = window
    buckets: list[dict[str, object]] = []
    day = first
    while day <= last:
        bucket_counts = counts.get(day, {"success": 0, "failed": 0, "cancelled": 0})
        bucket_costs = costs.get(day, dict.fromkeys(MEDIA_TYPES, 0.0))
        buckets.append(
            {
                "date": day.isoformat(),
                "success": bucket_counts["success"],
                "failed": bucket_counts["failed"],
                "cancelled": bucket_counts["cancelled"],
                "cost_by_media_type": {
                    media_type: round(bucket_costs[media_type], _ROUND_DIGITS) for media_type in MEDIA_TYPES
                },
            }
        )
        day += timedelta(days=1)
    return buckets


def _breakdown_dimension(
    rows: list[UsageSummaryRow],
    *,
    key_of: Callable[[UsageSummaryRow], tuple[str, ...]],
    fields_of: Callable[[tuple[str, ...]], dict[str, object]],
) -> dict[str, object]:
    grouped: dict[tuple[str, ...], _StatsAccumulator] = {}
    for row in rows:
        grouped.setdefault(key_of(row), _StatsAccumulator()).add(row)

    # Descending calls, ties by group keys, make pagination/other boundaries reproducible.
    ordered = sorted(grouped.items(), key=lambda item: (-item[1].calls, item[0]))
    listed = ordered[:MAX_BREAKDOWN_ROWS]
    overflow = ordered[MAX_BREAKDOWN_ROWS:]

    result: dict[str, object] = {"rows": [{**fields_of(key), **stats.to_dict()} for key, stats in listed]}
    if overflow:
        merged = _StatsAccumulator()
        for _, stats in overflow:
            merged.success += stats.success
            merged.failed += stats.failed
            merged.cancelled += stats.cancelled
            merged.unpriced += stats.unpriced
            for currency, amount in stats.cost.items():
                merged.cost[currency] = merged.cost.get(currency, 0.0) + amount
        result["other"] = {"groups": len(overflow), **merged.to_dict()}
    else:
        result["other"] = None
    return result


_AGENT_MARK = "1"
_GENERATION_MARK = "0"


def _breakdown(rows: list[UsageSummaryRow]) -> dict[str, object]:
    """Breakdowns across three dimensions.

    Provider/model dimensions each merge Agent records into one item (``agent=True``, no specific provider):
    Agent providers are ``agent-<凭证 id>`` per credential, otherwise mixed with generation providers, while this view
    concerns Agent's overall share. Per-credential inspection uses Agent-session filters.
    """
    return {
        "project": _breakdown_dimension(
            rows,
            key_of=lambda row: (row.project_name,),
            fields_of=lambda key: {"project_name": key[0]},
        ),
        "provider": _breakdown_dimension(
            rows,
            key_of=lambda row: (_AGENT_MARK, "") if row.agent else (_GENERATION_MARK, row.provider),
            fields_of=lambda key: {"provider": key[1], "agent": key[0] == _AGENT_MARK},
        ),
        "model": _breakdown_dimension(
            rows,
            key_of=lambda row: (
                (_AGENT_MARK, "", row.model) if row.agent else (_GENERATION_MARK, row.provider, row.model)
            ),
            fields_of=lambda key: {"provider": key[1], "model": key[2], "agent": key[0] == _AGENT_MARK},
        ),
    }


def _failure_rate_attention(rows: list[UsageSummaryRow]) -> list[dict[str, object]]:
    """Elevated failures: group Wilson 95% lower bounds exceed overall failure rates, with ≥2 group failures."""
    by_provider: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_model: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    total_success = 0
    total_failed = 0
    for row in rows:
        if row.status == CallStatus.SUCCESS:
            index = 0
        elif row.status == CallStatus.FAILED:
            index = 1
        else:
            continue
        by_provider[row.provider][index] += 1
        by_model[(row.provider, row.model)][index] += 1
        total_success += index == 0
        total_failed += index == 1

    decided = total_success + total_failed
    overall = total_failed / decided if decided else 0.0

    def _triggered(success: int, failed: int) -> bool:
        return failed >= MIN_ATTENTION_FAILED and wilson_lower_bound(failed, success + failed) > overall

    flagged_providers = {provider for provider, (ok, bad) in by_provider.items() if _triggered(ok, bad)}
    items: list[dict[str, object]] = [
        {
            "type": "failure_rate",
            "provider": provider,
            "model": None,
            "success": by_provider[provider][0],
            "failed": by_provider[provider][1],
            "failure_rate": round(by_provider[provider][1] / (by_provider[provider][0] + by_provider[provider][1]), 6),
            "overall_failure_rate": round(overall, 6),
        }
        for provider in sorted(flagged_providers)
    ]
    items.extend(
        {
            "type": "failure_rate",
            "provider": provider,
            "model": model,
            "success": ok,
            "failed": bad,
            "failure_rate": round(bad / (ok + bad), 6),
            "overall_failure_rate": round(overall, 6),
        }
        # Once providers are flagged, omit their models to avoid duplicate attention.
        for (provider, model), (ok, bad) in sorted(by_model.items())
        if provider not in flagged_providers and _triggered(ok, bad)
    )
    return items


def _consecutive_failure_attention(rows: list[UsageSummaryRow]) -> list[dict[str, object]]:
    """Consecutive failures per target: ≥2 trailing failures during windows, without subsequent success.

    Targets are (project, media, storyboard); exclude calls without storyboards, focusing on repeated failure to generate the same
    storyboard. Canceled calls neither count nor interrupt streaks: user actions, not failure evidence.
    """
    grouped: dict[tuple[str, str, str], list[UsageSummaryRow]] = defaultdict(list)
    for row in rows:
        if row.segment_id:
            grouped[(row.project_name, row.media_type, row.segment_id)].append(row)

    items: list[dict[str, object]] = []
    for (project_name, media_type, segment_id), group in sorted(grouped.items()):
        run: list[UsageSummaryRow] = []
        for row in sorted(group, key=lambda item: (_as_utc(item.started_at), item.id), reverse=True):
            if row.status == CallStatus.CANCELLED:
                continue
            if row.status != CallStatus.FAILED:
                break
            run.append(row)
        if len(run) < MIN_ATTENTION_FAILED:
            continue
        run.reverse()
        items.append(
            {
                "type": "consecutive_failures",
                "project_name": project_name,
                "media_type": media_type,
                "segment_id": segment_id,
                "count": len(run),
                "first_failed_at": _iso_utc(run[0].started_at),
                "last_failed_at": _iso_utc(run[-1].started_at),
                "last_error_code": run[-1].error_code,
            }
        )
    return items


def _failure_count(item: dict[str, object]) -> int:
    value = item["failed"] if item["type"] == "failure_rate" else item["count"]
    return value if isinstance(value, int) else 0


def _attention(rows: list[UsageSummaryRow]) -> list[dict[str, object]]:
    # Failure-rate/streak anomalies use generation calls only; Agent turns neither raise nor lower rates.
    rows = [row for row in rows if not row.agent]
    items = _failure_rate_attention(rows) + _consecutive_failure_attention(rows)

    # Sort both anomaly classes together by failure counts, then item content for stable outputs.
    return sorted(items, key=lambda item: (-_failure_count(item), repr(item)))[:MAX_ATTENTION_ITEMS]


def build_summary(
    rows: list[UsageSummaryRow],
    *,
    tz: tzinfo,
    since: datetime | None,
    until: datetime | None,
    filter_options: UsageFilterOptions,
) -> dict[str, object]:
    """Aggregate projected period rows into `/usage/summary` responses.

    ``rows`` already match filters/windows and exclude pending; ``filter_options`` comes from whole tables, independent of filters.
    """
    kpi = _StatsAccumulator()
    for row in rows:
        kpi.add(row)
    primary_currency = _primary_currency(kpi.cost)
    window = _window(rows, tz=tz, since=since, until=until)

    return {
        "range": {"since": window[0].isoformat(), "until": window[1].isoformat()} if window else None,
        "primary_currency": primary_currency,
        "kpi": kpi.to_dict(),
        "daily": _daily_buckets(rows, tz=tz, window=window, primary_currency=primary_currency) if window else [],
        "breakdown": _breakdown(rows),
        "attention": _attention(rows),
        "filter_options": {
            "projects": filter_options.projects,
            "providers": [{"provider": provider, "label": label} for provider, label in filter_options.providers],
            "models": [{"provider": provider, "model": model} for provider, model in filter_options.models],
        },
    }
