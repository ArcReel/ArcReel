"""模型条目的生命周期：按当天日期（UTC）推出可用、即将下线、已下线（docs/adr/0103）。

生命周期记在条目上，表示模型厂商层面的退役，内置供应商与自定义供应商共用：凡是能对应到条目的供应商模型，都按
:func:`entry_lifecycle` 判定。状态每次现算，不进只随目录数据变化而重建的缓存：同一进程跨过关停日期后，即使没有
同步，候选与调用也立即随之变化。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from arcreel_catalog_core import CatalogEntry

#: ``available`` 可用；``retiring`` 即将下线（关停日期在未来）；``discontinued`` 已下线。
LifecycleStatus = Literal["available", "retiring", "discontinued"]


@dataclass(frozen=True)
class Lifecycle:
    """条目的生命周期状态，连同关停日期与官方后继（「模型厂商/官方模型名」，媒体类型与原条目相同）。

    关停日期与后继在任何状态下都原样带出：只登记了后继、没有关停日期的条目仍是可用的。
    """

    status: LifecycleStatus
    shutdown_date: date | None = None
    successor: str | None = None

    @property
    def discontinued(self) -> bool:
        return self.status == "discontinued"


#: 对应不上条目的模型没有生命周期信息，按可用处理。
AVAILABLE = Lifecycle("available")


def utc_today() -> date:
    """判定生命周期用的「今天」：当前的 UTC 日期。测试替换本函数注入时钟。"""
    return datetime.now(UTC).date()


def entry_lifecycle(entry: CatalogEntry | None, today: date | None = None) -> Lifecycle:
    """条目在 ``today``（缺省为 :func:`utc_today`）的生命周期。

    关停日期不晚于当天或 ``discontinued`` 为真即为已下线（关停当天即下线）；关停日期在未来为即将下线；其余为可用。
    ``entry`` 为 ``None``（对应不上条目）时为可用。
    """
    if entry is None:
        return AVAILABLE
    shutdown = entry.shutdown_date
    if entry.discontinued or (shutdown is not None and shutdown <= (today or utc_today())):
        status: LifecycleStatus = "discontinued"
    elif shutdown is not None:
        status = "retiring"
    else:
        status = "available"
    return Lifecycle(status, shutdown, entry.successor)
