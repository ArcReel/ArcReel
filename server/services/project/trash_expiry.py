"""项目回收站的过期清理：应用启动时清一次，之后定期清理。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from lib.infra.data_root_layout import list_project_dirs
from lib.project import project_trash

logger = logging.getLogger(__name__)

#: 保留期按天计，每 6 小时清一次足够及时。
SWEEP_INTERVAL_SECONDS = 6 * 3600


def sweep_expired_trash(projects_dir: Path) -> int:
    """清除全部项目里过期的删除记录，返回清除数。单个项目失败只记日志，不影响其余项目。"""
    total = 0
    for project_dir in list_project_dirs(projects_dir):
        try:
            total += project_trash.purge_expired(project_dir)
        except Exception:
            logger.warning("清理项目回收站过期记录失败 project=%s", project_dir.name, exc_info=True)
    return total


async def _sweep_loop(projects_dir: Path) -> None:
    while True:
        try:
            purged = await asyncio.to_thread(sweep_expired_trash, projects_dir)
            if purged:
                logger.info("项目回收站清除过期记录 %d 条", purged)
        except Exception:
            logger.warning("项目回收站过期清理异常，等下一轮", exc_info=True)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


def start_trash_expiry(projects_dir: Path) -> asyncio.Task[None]:
    """启动后台清理循环：立即清一次，之后每隔 :data:`SWEEP_INTERVAL_SECONDS` 一次。"""
    return asyncio.get_running_loop().create_task(_sweep_loop(projects_dir), name="project-trash-expiry")


async def stop_trash_expiry(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
