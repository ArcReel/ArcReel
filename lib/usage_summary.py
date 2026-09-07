"""`GET /usage/summary` 的纯聚合：轻投影调用行 → KPI、日桶、构成与需要关注。

仓储只负责按筛选取回行，切天、补零桶、主币种与两类异常判定都在这里完成——聚合不碰数据库
也不碰 HTTP，因此边界（时区跨日、并列币种、Wilson 下界、末尾连续失败）可直接以行列表驱动。
在 Python 侧聚合还回避了 SQLite 与 PostgreSQL 日期函数的方言差异。
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo

from lib.providers import CallStatus

# 构成表每维最多列出的行数，余量并入列表外的 other。
MAX_BREAKDOWN_ROWS = 50
# 需要关注列表的总条数上限。
MAX_ATTENTION_ITEMS = 20
# 进入需要关注所需的最少失败次数（两类异常同一门槛）。
MIN_ATTENTION_FAILED = 2
# 95% 双侧置信区间的标准正态分位点。
WILSON_Z_95 = 1.959963984540054
# 日桶里按媒体类型分列参考费用的固定键集。
MEDIA_TYPES = ("image", "video", "text", "audio")
# 一次响应最多铺的日桶数（约十年）。窗口由调用方的 since / until 决定，不设上界时一对
# 跨越数千年的时刻会让服务端同步构造几百万个桶；「全部」范围的 since 取最早记录日，不受此限。
MAX_DAILY_BUCKETS = 3660
# 金额与比率的输出精度，与仓储写侧的费用精度一致。
_ROUND_DIGITS = 6


class UsageWindowTooWideError(ValueError):
    """请求的 since / until 展开的日桶数超过 ``MAX_DAILY_BUCKETS``。"""


@dataclass(frozen=True, slots=True)
class UsageSummaryRow:
    """一次调用在汇总里用得到的全部字段；仓储的轻投影按此形状取行。"""

    id: int
    project_name: str
    media_type: str
    provider: str
    model: str
    status: str
    started_at: datetime
    cost_amount: float
    currency: str
    segment_id: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class UsageFilterOptions:
    """筛选候选值：取全表 distinct，不随本次筛选变化。"""

    projects: list[str]
    providers: list[tuple[str, str]]
    models: list[tuple[str, str]]


def wilson_lower_bound(failures: int, total: int, z: float = WILSON_Z_95) -> float:
    """失败率的 Wilson 置信下界。

    小样本上比裸失败率保守得多——3 次里失败 2 次的下界只有 0.2 左右，不会因为一两次失败就
    把一个刚开始用的模型报成异常。
    """
    if total <= 0:
        return 0.0
    p = failures / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


def _as_utc(value: datetime) -> datetime:
    """无时区的时刻按 UTC 解释：SQLite 取回的 DateTime 列不带 tzinfo。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _local_date(value: datetime, tz: tzinfo) -> date:
    return _as_utc(value).astimezone(tz).date()


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).astimezone(UTC).isoformat()


@dataclass
class _StatsAccumulator:
    """一个分组的计数与分币种金额；pending 行在取行时已被排除，这里不再判。"""

    success: int = 0
    failed: int = 0
    cancelled: int = 0
    cost: dict[str, float] = field(default_factory=dict)

    def add(self, row: UsageSummaryRow) -> None:
        if row.status == CallStatus.SUCCESS:
            self.success += 1
        elif row.status == CallStatus.FAILED:
            self.failed += 1
        elif row.status == CallStatus.CANCELLED:
            self.cancelled += 1
        # 参考费用汇总所有 cost_amount > 0 的终态行，不看状态——失败的调用一样可能被计费。
        if row.cost_amount > 0:
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
            # 成功率的分母只数已判定的调用；用户取消既不算成功也不算失败。
            "success_rate": round(self.success / decided, _ROUND_DIGITS) if decided else None,
            "cost": {currency: round(amount, _ROUND_DIGITS) for currency, amount in sorted(self.cost.items())},
        }


def _primary_currency(cost: dict[str, float]) -> str | None:
    """汇总金额最大的币种；并列时优先 USD，再按字母序。"""
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
    """本地日窗口 [首日, 末日]（两端含）；期间内没有可聚合的行时为 None。

    窗口超过 ``MAX_DAILY_BUCKETS`` 天抛 ``UsageWindowTooWideError``：桶数由调用方的两个时刻
    直接决定，不能让它无上界地摊开。两端都显式给定时先于行校验，结果不随数据有无而变。
    """
    since_day = _local_date(since, tz) if since else None
    # until 是半开区间的右端：正好落在本地日零点时不产生当天的桶。
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
        # 堆叠柱只能落在一种币种上，取主币种；其余币种的金额不混进来，也不做汇率折算。
        if row.cost_amount > 0 and row.currency == primary_currency and row.media_type in costs[day]:
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

    # 调用数降序；并列按分组键排序，让分页边界与 other 的取舍稳定可复现。
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
            for currency, amount in stats.cost.items():
                merged.cost[currency] = merged.cost.get(currency, 0.0) + amount
        result["other"] = {"groups": len(overflow), **merged.to_dict()}
    else:
        result["other"] = None
    return result


def _breakdown(rows: list[UsageSummaryRow]) -> dict[str, object]:
    return {
        "project": _breakdown_dimension(
            rows,
            key_of=lambda row: (row.project_name,),
            fields_of=lambda key: {"project_name": key[0]},
        ),
        "provider": _breakdown_dimension(
            rows,
            key_of=lambda row: (row.provider,),
            fields_of=lambda key: {"provider": key[0]},
        ),
        "model": _breakdown_dimension(
            rows,
            key_of=lambda row: (row.provider, row.model),
            fields_of=lambda key: {"provider": key[0], "model": key[1]},
        ),
    }


def _failure_rate_attention(rows: list[UsageSummaryRow]) -> list[dict[str, object]]:
    """失败率偏高：分组失败率的 Wilson 95% 下界高于总体失败率，且分组失败 ≥ 2 次。"""
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
        # 供应商已经报了就不再报它下面的模型：同一件事说一次。
        for (provider, model), (ok, bad) in sorted(by_model.items())
        if provider not in flagged_providers and _triggered(ok, bad)
    )
    return items


def _consecutive_failure_attention(rows: list[UsageSummaryRow]) -> list[dict[str, object]]:
    """同一目标连续失败：期间内末尾连续失败 ≥ 2 次且之后没有成功。

    目标键是 (项目, 媒体类型, 分镜)，没有分镜的调用不参与——「同一个分镜反复生成不出来」才是
    用户要看的信号。已取消的调用既不计数也不打断连续段：那是用户自己的操作，不是失败证据。
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
    items = _failure_rate_attention(rows) + _consecutive_failure_attention(rows)

    # 两类异常按各自的失败次数一起排序；并列时按条目内容定序，保证同一批数据的输出稳定。
    return sorted(items, key=lambda item: (-_failure_count(item), repr(item)))[:MAX_ATTENTION_ITEMS]


def build_summary(
    rows: list[UsageSummaryRow],
    *,
    tz: tzinfo,
    since: datetime | None,
    until: datetime | None,
    filter_options: UsageFilterOptions,
) -> dict[str, object]:
    """把期间内的轻投影行聚成一份 `/usage/summary` 响应体。

    ``rows`` 已按筛选与期间取好且不含 pending；``filter_options`` 取自全表，不随筛选变化。
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
