"""项目回收站的过期清理：应用启动时清一次，之后定期清理。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import list_project_dirs
from lib.project import project_trash

logger = logging.getLogger(__name__)

#: 保留期按天计，每 6 小时清一次足够及时。
SWEEP_INTERVAL_SECONDS = 6 * 3600


async def _sweep_loop(projects_dir: Path) -> None:
    while True:
        purged = 0
        try:
            project_dirs = await asyncio.to_thread(list_project_dirs, projects_dir)
        except Exception:
            logger.warning("项目回收站过期清理列项目失败，等下一轮", exc_info=True)
            project_dirs = []
        # 按项目各成一个事务：关停时只等正在清理的那个项目；单个项目失败不影响其余项目。
        for project_dir in project_dirs:
            try:
                purged += await run_sync_transaction(project_trash.purge_expired, project_dir)
            except Exception:
                logger.warning("清理项目回收站过期记录失败 project=%s", project_dir.name, exc_info=True)
        if purged:
            logger.info("项目回收站清除过期记录 %d 条", purged)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


def start_trash_expiry(projects_dir: Path) -> asyncio.Task[None]:
    """启动后台清理循环：立即清一次，之后每隔 :data:`SWEEP_INTERVAL_SECONDS` 一次。"""
    return asyncio.get_running_loop().create_task(_sweep_loop(projects_dir), name="project-trash-expiry")


async def stop_trash_expiry(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
