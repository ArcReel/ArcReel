"""`lib.usage_summary` 的聚合边界：Wilson 下界、桶窗口的半开右端、需要关注的排序与上限。

契约判据在路由缝（`tests/unit/server/routers/test_usage_router_summary.py`），这里只补
那些用 HTTP 造数据代价过高、或需要精确数值锚点的边界。
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from lib.providers import CallStatus
from lib.usage_summary import (
    MAX_ATTENTION_ITEMS,
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
    cost_amount: float = 0.0,
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
        """小样本上的下界远低于裸失败率，样本变大后向裸失败率收敛。"""
        assert wilson_lower_bound(failures, total) == pytest.approx(expected, abs=5e-4)


class TestWindow:
    def test_until_at_local_midnight_excludes_that_day(self):
        """右端是半开的：正好落在本地日零点时不为当天开桶。"""
        body = summarize([row(started_at=BASE)], until=datetime(2026, 3, 2, tzinfo=UTC))
        assert body["range"] == {"since": "2026-03-01", "until": "2026-03-01"}

    def test_until_inside_a_day_keeps_that_day(self):
        body = summarize([row(started_at=BASE)], until=datetime(2026, 3, 2, 9, 0, tzinfo=UTC))
        assert body["range"] == {"since": "2026-03-01", "until": "2026-03-02"}

    def test_since_is_cut_in_the_requested_timezone(self):
        """since 与行都按 tz 折成本地日：UTC 06:00 在纽约还是前一天。"""
        body = summarize(
            [row(started_at=BASE)],
            tz=ZoneInfo("America/New_York"),
            since=datetime(2026, 2, 28, 12, 0, tzinfo=UTC),
        )
        assert body["range"] == {"since": "2026-02-28", "until": "2026-03-01"}
        assert [bucket["date"] for bucket in body["daily"]] == ["2026-02-28", "2026-03-01"]

    def test_naive_started_at_is_read_as_utc(self):
        """SQLite 取回的时刻不带 tzinfo，按 UTC 解释而不是本地时钟。"""
        body = summarize([row(started_at=datetime(2026, 3, 1, 23, 30))], tz=ZoneInfo("Asia/Shanghai"))
        assert body["range"] == {"since": "2026-03-02", "until": "2026-03-02"}

    def test_window_at_the_bucket_cap_is_still_served(self):
        since = BASE - timedelta(days=MAX_DAILY_BUCKETS - 1)
        body = summarize([row(started_at=BASE)], since=since)
        assert len(body["daily"]) == MAX_DAILY_BUCKETS

    def test_window_beyond_the_bucket_cap_is_rejected(self):
        """桶数由调用方的两个时刻直接决定，超过上限拒绝而不是同步铺出海量零桶。"""
        since = BASE - timedelta(days=MAX_DAILY_BUCKETS)
        with pytest.raises(UsageWindowTooWideError):
            summarize([row(started_at=BASE)], since=since)

    def test_explicit_window_beyond_the_cap_is_rejected_even_without_rows(self):
        """两端显式给定的超宽窗口不随数据有无而变：没有行也一样拒绝。"""
        with pytest.raises(UsageWindowTooWideError):
            summarize([], since=datetime(1900, 1, 1, tzinfo=UTC), until=datetime(2100, 1, 1, tzinfo=UTC))

    def test_half_open_window_without_rows_is_empty(self):
        """只给一端时窗口由数据补齐；没有行就没有窗口，range 为 null。"""
        body = summarize([], since=datetime(1900, 1, 1, tzinfo=UTC))
        assert body["range"] is None
        assert body["daily"] == []


class TestAttentionOrdering:
    def test_capped_at_twenty_by_failure_count(self):
        """需要关注按失败次数降序截断，留下的是最严重的那些。"""
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
        """终态行只要计了费就进参考费用，取消行的零费用不改变结果。"""
        rows = [
            row(index=1, status=CallStatus.SUCCESS, cost_amount=0.1234567),
            row(index=2, status=CallStatus.FAILED, cost_amount=0.0000004),
            row(index=3, status=CallStatus.CANCELLED, cost_amount=0.0),
        ]
        assert summarize(rows)["kpi"]["cost"] == {"USD": 0.123457}
