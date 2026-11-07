"""`lib.billing.usage_summary` boundaries: Wilson lower bound, right-open buckets, attention ordering/limits.

Contract tests use the router seam (`tests/unit/server/routers/test_usage_router_summary.py`);
these cover expensive-to-build HTTP scenarios and precise numeric anchors.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from lib.backends.providers import CallStatus
from lib.billing.usage_summary import (
    MAX_ATTENTION_ITEMS,
    MAX_BREAKDOWN_ROWS,
    MAX_DAILY_BUCKETS,
    UsageFilterOptions,
    UsageSummaryRow,
    UsageWindowTooWideError,
    build_summary,
    wilson_lower_bound,
)

NO_OPTIONS = UsageFilterOptions(projects=[], providers=[], models=[])
BASE = datetime(2026, 3, 1, 6, 0, tzinfo=UTC)


def row(
    *,
    index: int = 0,
    status: CallStatus = CallStatus.SUCCESS,
    started_at: datetime = BASE,
    project_name: str = "alpha",
    media_type: str = "image",
    provider: str = "gemini",
    model: str = "flash",
    cost_amount: float | None = 0.0,
    currency: str = "USD",
    segment_id: str | None = None,
) -> UsageSummaryRow:
    return UsageSummaryRow(
        id=index,
        project_name=project_name,
        media_type=media_type,
        provider=provider,
        model=model,
        status=status,
        started_at=started_at,
        cost_amount=cost_amount,
        currency=currency,
        segment_id=segment_id,
        error_code=None,
    )


def summarize(rows, *, tz=UTC, since=None, until=None) -> dict[str, Any]:
    return build_summary(rows, tz=tz, since=since, until=until, filter_options=NO_OPTIONS)


class TestWilsonLowerBound:
    @pytest.mark.parametrize(
        ("failures", "total", "expected"),
        [(0, 0, 0.0), (0, 10, 0.0), (5, 5, 0.5655), (8, 10, 0.4902), (1, 2, 0.0945), (50, 100, 0.4038)],
    )
    def test_known_anchors(self, failures, total, expected):
        """Small-sample lower bounds sit far below raw failure rates, converging as samples grow."""
        assert wilson_lower_bound(failures, total) == pytest.approx(expected, abs=5e-4)


class TestWindow:
    def test_until_at_local_midnight_excludes_that_day(self):
        """Right-open windows exclude a bucket for a day beginning exactly at the endpoint."""
        body = summarize([row(started_at=BASE)], until=datetime(2026, 3, 2, tzinfo=UTC))
        assert body["range"] == {"since": "2026-03-01", "until": "2026-03-01"}

    def test_until_inside_a_day_keeps_that_day(self):
        body = summarize([row(started_at=BASE)], until=datetime(2026, 3, 2, 9, 0, tzinfo=UTC))
        assert body["range"] == {"since": "2026-03-01", "until": "2026-03-02"}

    def test_since_is_cut_in_the_requested_timezone(self):
        """since and rows both use tz-local days; UTC 06:00 is still the previous day in New York."""
        body = summarize(
            [row(started_at=BASE)],
            tz=ZoneInfo("America/New_York"),
            since=datetime(2026, 2, 28, 12, 0, tzinfo=UTC),
        )
        assert body["range"] == {"since": "2026-02-28", "until": "2026-03-01"}
        assert [bucket["date"] for bucket in body["daily"]] == ["2026-02-28", "2026-03-01"]

    def test_naive_started_at_is_read_as_utc(self):
        """SQLite timestamps without tzinfo are interpreted as UTC, not local clock time."""
        body = summarize([row(started_at=datetime(2026, 3, 1, 23, 30))], tz=ZoneInfo("Asia/Shanghai"))
        assert body["range"] == {"since": "2026-03-02", "until": "2026-03-02"}

    def test_window_at_the_bucket_cap_is_still_served(self):
        since = BASE - timedelta(days=MAX_DAILY_BUCKETS - 1)
        body = summarize([row(started_at=BASE)], since=since)
        assert len(body["daily"]) == MAX_DAILY_BUCKETS

    def test_window_beyond_the_bucket_cap_is_rejected(self):
        """Explicit endpoints determine bucket count; reject oversized windows instead of synchronously creating huge zero buckets."""
        since = BASE - timedelta(days=MAX_DAILY_BUCKETS)
        with pytest.raises(UsageWindowTooWideError):
            summarize([row(started_at=BASE)], since=since)

    def test_explicit_window_beyond_the_cap_is_rejected_even_without_rows(self):
        """Explicitly oversized two-ended windows reject regardless of data, including empty datasets."""
        with pytest.raises(UsageWindowTooWideError):
            summarize([], since=datetime(1900, 1, 1, tzinfo=UTC), until=datetime(2100, 1, 1, tzinfo=UTC))

    def test_half_open_window_without_rows_is_empty(self):
        """Single-ended windows derive the other endpoint from data; no rows means no window and null range."""
        body = summarize([], since=datetime(1900, 1, 1, tzinfo=UTC))
        assert body["range"] is None
        assert body["daily"] == []


class TestAttentionOrdering:
    def test_capped_at_twenty_by_failure_count(self):
        """Attention items truncate by descending failure count, retaining the most severe."""
        rows = [
            row(
                index=segment * 100 + occurrence,
                status=CallStatus.FAILED,
                segment_id=f"seg-{segment:02d}",
                started_at=BASE + timedelta(minutes=occurrence),
            )
            for segment in range(25)
            for occurrence in range(2 + segment % 5)
        ]
        attention = summarize(rows)["attention"]
        assert len(attention) == MAX_ATTENTION_ITEMS
        counts = [item["count"] for item in attention if item["type"] == "consecutive_failures"]
        assert counts == sorted(counts, reverse=True)
        assert min(counts) >= 3

    def test_cost_is_summed_across_statuses_and_rounded(self):
        """Any priced terminal row contributes reference cost; zero-cost cancellations leave totals unchanged."""
        rows = [
            row(index=1, status=CallStatus.SUCCESS, cost_amount=0.1234567),
            row(index=2, status=CallStatus.FAILED, cost_amount=0.0000004),
            row(index=3, status=CallStatus.CANCELLED, cost_amount=0.0),
        ]
        assert summarize(rows)["kpi"]["cost"] == {"USD": 0.123457}

    def test_unpriced_rows_stay_out_of_every_cost_total(self):
        """NULL unpriced costs neither error nor enter totals/composition/daily amounts; mix NULL, 0, and positives."""
        rows = [
            row(index=1, cost_amount=None, provider="ark", model="a"),
            row(index=2, cost_amount=0.0, provider="ark", model="a"),
            row(index=3, cost_amount=0.5, provider="ark", model="a"),
            row(index=4, cost_amount=None, provider="gemini", model="b"),
        ]
        body = summarize(rows)

        assert body["kpi"]["calls"] == 4
        assert body["kpi"]["cost"] == {"USD": 0.5}
        assert body["primary_currency"] == "USD"
        assert body["daily"][0]["cost_by_media_type"]["image"] == 0.5
        by_provider = {item["provider"]: item for item in body["breakdown"]["provider"]["rows"]}
        assert by_provider["ark"]["cost"] == {"USD": 0.5}
        assert by_provider["gemini"]["calls"] == 1
        assert by_provider["gemini"]["cost"] == {}

    def test_unpriced_calls_are_counted_apart_from_zero_cost_calls(self):
        """Count unpriced calls separately: zero-cost calls are priced and excluded; composition and outside-list remainder agree."""
        rows = [
            row(index=1, cost_amount=None, provider="ark", model="a"),
            row(index=2, cost_amount=None, provider="ark", model="a", status=CallStatus.FAILED),
            row(index=3, cost_amount=0.0, provider="ark", model="a"),
            row(index=4, cost_amount=0.5, provider="gemini", model="b"),
        ]
        body = summarize(rows)

        assert body["kpi"]["unpriced_calls"] == 2
        by_provider = {item["provider"]: item for item in body["breakdown"]["provider"]["rows"]}
        assert by_provider["ark"]["unpriced_calls"] == 2
        assert by_provider["gemini"]["unpriced_calls"] == 0

    def test_overflow_group_carries_the_unpriced_count(self):
        rows = [
            row(index=index, project_name=f"p{index:03d}", cost_amount=None if index % 2 else 1.0)
            for index in range(MAX_BREAKDOWN_ROWS + 4)
        ]
        other = summarize(rows)["breakdown"]["project"]["other"]

        assert other["groups"] == 4
        # Remainder contains the last four projects (indexes 50..53), two unpriced.
        assert other["unpriced_calls"] == 2

    def test_only_unpriced_rows_have_no_primary_currency(self):
        body = summarize([row(index=1, cost_amount=None), row(index=2, cost_amount=None)])
        assert body["primary_currency"] is None
        assert body["kpi"]["cost"] == {}
