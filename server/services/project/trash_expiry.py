"""Expired project-trash and deleted-project cleanup: sweep once at application startup, then periodically."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import DataRootLayout, list_project_dirs
from lib.material.formal_objects import reconcile_and_collect, release_holder
from lib.project import deleted_projects, project_trash

logger = logging.getLogger(__name__)

#: Retention is measured in days; a sweep every 6 hours is sufficiently timely.
SWEEP_INTERVAL_SECONDS = 6 * 3600


async def _sweep_loop(layout: DataRootLayout) -> None:
    while True:
        purged = 0
        try:
            project_dirs = await asyncio.to_thread(list_project_dirs, layout.projects_dir)
        except Exception:
            logger.warning("项目回收站过期清理列项目失败，等下一轮", exc_info=True)
            project_dirs = []
        # One transaction per project: shutdown waits only for the project being cleaned; one failure does not affect others.
        for project_dir in project_dirs:
            try:
                count = await run_sync_transaction(project_trash.purge_expired, project_dir)
                purged += count
                if count:
                    # Removed records no longer retain formal objects; on failure, the maintenance loop recalculates them.
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
    """Permanently deleted projects no longer retain formal objects; on failure, the maintenance loop removes them after the retention period as a fallback."""
    try:
        await release_holder(tombstone)
    except Exception:
        logger.warning("撤下已删除项目的正式对象引用失败 deleted=%s", tombstone, exc_info=True)


def start_trash_expiry(layout: DataRootLayout) -> asyncio.Task[None]:
    """Start the background cleanup loop: sweep immediately, then every :data:`SWEEP_INTERVAL_SECONDS`."""
    return asyncio.get_running_loop().create_task(_sweep_loop(layout), name="project-trash-expiry")


async def stop_trash_expiry(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
