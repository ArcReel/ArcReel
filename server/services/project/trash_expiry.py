"""项目回收站与已删除项目的过期清理：应用启动时清一次，之后定期清理。"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import DataRootLayout, list_project_dirs
from lib.material.formal_objects import reconcile_and_collect, release_holder
from lib.project import deleted_projects, project_trash

logger = logging.getLogger(__name__)

#: 保留期按天计，每 6 小时清一次足够及时。
SWEEP_INTERVAL_SECONDS = 6 * 3600


async def _sweep_loop(layout: DataRootLayout) -> None:
    while True:
        purged = 0
        try:
            project_dirs = await asyncio.to_thread(list_project_dirs, layout.projects_dir)
        except Exception:
            logger.warning("项目回收站过期清理列项目失败，等下一轮", exc_info=True)
            project_dirs = []
        # 按项目各成一个事务：关停时只等正在清理的那个项目；单个项目失败不影响其余项目。
        for project_dir in project_dirs:
            try:
                count = await run_sync_transaction(project_trash.purge_expired, project_dir)
                purged += count
                if count:
                    # 清除的记录不再持有正式对象；失败时维护循环会再重算一次
                    await reconcile_and_collect(project_dir.name, project_dir)
            except Exception:
                logger.warning("清理项目回收站过期记录失败 project=%s", project_dir.name, exc_info=True)
        if purged:
            logger.info("项目回收站清除过期记录 %d 条", purged)
        try:
            purged_projects = await run_sync_transaction(deleted_projects.purge_expired, layout)
        except Exception:
            logger.warning("清理过期的已删除项目失败，等下一轮", exc_info=True)
        else:
            if purged_projects:
                logger.info("清除过期的已删除项目 %d 个", len(purged_projects))
            for tombstone in purged_projects:
                await _release_deleted_project(tombstone)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


async def _release_deleted_project(tombstone: str) -> None:
    """彻底删除的已删除项目不再持有正式对象；失败时维护循环在保留期过后兜底撤下。"""
    try:
        await release_holder(tombstone)
    except Exception:
        logger.warning("撤下已删除项目的正式对象引用失败 deleted=%s", tombstone, exc_info=True)


def start_trash_expiry(layout: DataRootLayout) -> asyncio.Task[None]:
    """启动后台清理循环：立即清一次，之后每隔 :data:`SWEEP_INTERVAL_SECONDS` 一次。"""
    return asyncio.get_running_loop().create_task(_sweep_loop(layout), name="project-trash-expiry")


async def stop_trash_expiry(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
