"""项目活动账本：业务写入出口在写盘后记下当时的时刻。

项目大厅的最近活动时间读时取账本、`metadata.updated_at` 与内容文件修改时间中最晚的一个
（`server.services.project.project_activity`）。账本补的是这两类来源看不见的写入：
草稿与脚本规划等 JSON 的改写（JSON 的修改时间分不清业务写入与迁移改写，不计入），
以及删除（删掉最新的文件后修改时间会回退）。

账本是项目根目录的 `.last_activity`，内容只有一个带时区的 ISO 8601 时刻。点开头的文件不进
内容扫描与归档导出；项目结构迁移、产物补录与配置同步不调用记账，所以不会推进活动时间。

记账失败只记日志：业务写入已经落盘，活动时间写不进去不应让这次写入报错。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from lib.infra.json_io import atomic_write_bytes

logger = logging.getLogger(__name__)

ACTIVITY_FILENAME = ".last_activity"


def record_project_activity(project_dir: Path) -> None:
    """把当前时刻记为项目的最近活动时间（原子整份覆盖）。"""
    stamp = datetime.now(UTC).isoformat()
    try:
        atomic_write_bytes(project_dir / ACTIVITY_FILENAME, stamp.encode("utf-8"))
    except OSError as exc:
        logger.warning("记录项目活动时间失败 dir=%s err=%s", project_dir, exc)


def recorded_project_activity(project_dir: Path) -> datetime | None:
    """账本里的最近活动时间（UTC）；没有账本或内容无法解析时为 None。"""
    try:
        raw = (project_dir / ACTIVITY_FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


__all__ = ["ACTIVITY_FILENAME", "record_project_activity", "recorded_project_activity"]
