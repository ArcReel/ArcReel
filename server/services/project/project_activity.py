"""
项目的最近活动时间（读时计算）。

项目大厅按它把最近在做的项目排在前面。取项目内任何内容最近一次被修改的时刻，来源有两类：

1. 账本里的业务时间戳：`project.json` 与各集剧本的 `metadata.updated_at`。业务写入会刷新它们，
   项目结构迁移改写这些 JSON 时保留原值，所以不用这些 JSON 文件的修改时间。
2. 其余内容文件的修改时间：原文、草稿、生成的图片与视频等。

不计入的文件不代表创作者的活动，却会在启动或后台流程中被统一改写，计入会让所有项目同时「刚刚更新」：

- 名字以 `.` 开头的文件与目录：锁、产物清单、迁移报告、Agent 配置副本等。
- 所有 `.json` 文件：账本与派生数据，迁移会改写它们；业务改动已由第 1 类或同时写下的媒体文件反映。
- 迁移备份（文件名含 `.bak`）与锁文件（`.lock` 结尾）。
- 项目根目录的 `CLAUDE.md`：内嵌 Agent 的配置由启动时的配置同步改写。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROFILE_SYNCED_ROOT_FILES = frozenset({"CLAUDE.md"})


def project_last_activity_at(
    project_dir: Path,
    project: Mapping[str, Any],
    scripts: Iterable[Mapping[str, Any]],
) -> datetime | None:
    """项目内容最近一次修改的时刻（UTC）；没有任何可用的时间时为 None。"""
    candidates = [stamp for stamp in (_metadata_updated_at(doc) for doc in (project, *scripts)) if stamp is not None]
    content_mtime = _latest_content_mtime(project_dir)
    if content_mtime is not None:
        candidates.append(datetime.fromtimestamp(content_mtime, UTC))
    return max(candidates, default=None)


def _metadata_updated_at(doc: Mapping[str, Any]) -> datetime | None:
    metadata = doc.get("metadata")
    raw = metadata.get("updated_at") if isinstance(metadata, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    # 早期版本写的是不带时区的本机时间。
    return parsed.astimezone(UTC)


def _latest_content_mtime(project_dir: Path) -> float | None:
    latest: float | None = None
    pending = [(project_dir, True)]
    while pending:
        directory, is_root = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            logger.debug("读取项目目录失败 dir=%s err=%s", directory, exc)
            continue
        for entry in entries:
            if not _counts_as_content(entry.name, is_root=is_root):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), False))
                elif entry.is_file(follow_symlinks=False):
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                    latest = mtime if latest is None else max(latest, mtime)
            except OSError:
                continue
    return latest


def _counts_as_content(name: str, *, is_root: bool) -> bool:
    if name.startswith("."):
        return False
    if name.endswith((".json", ".lock")) or ".bak" in name:
        return False
    return not (is_root and name in _PROFILE_SYNCED_ROOT_FILES)
