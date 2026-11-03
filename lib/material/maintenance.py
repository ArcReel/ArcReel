"""远端素材的后台维护：启动时结算中断的正式提交，之后定期重试删除待删除的正式对象，并按小时重算各项目的对象引用、
回收失去全部引用的对象、清理闲置的本地工作文件。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.material.formal_objects import recover_formal_commits, retain_formal_objects, sweep_formal_objects
from lib.material.work_files import DEFAULT_MIN_IDLE, cleanup_work_cache

logger = logging.getLogger(__name__)

#: 重试删除待删除对象的间隔。
SWEEP_INTERVAL_SECONDS = 300.0
#: 重算对象引用与清理闲置工作文件的间隔；闲置门槛见 :data:`lib.material.work_files.DEFAULT_MIN_IDLE`。
CACHE_CLEANUP_INTERVAL = timedelta(hours=1)


class MaterialMaintenance:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        projects_dir: Path,
        deleted_projects_dir: Path | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._projects_dir = projects_dir
        self._deleted_projects_dir = deleted_projects_dir
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        try:
            await recover_formal_commits(self._session_factory)
        except Exception:
            logger.warning("结算中断的正式提交失败", exc_info=True)
        last_cleanup = 0.0
        while True:
            try:
                await sweep_formal_objects(self._session_factory)
            except Exception:
                logger.warning("重试删除正式素材对象失败，等下一轮", exc_info=True)
            if time.monotonic() - last_cleanup >= CACHE_CLEANUP_INTERVAL.total_seconds():
                last_cleanup = time.monotonic()
                try:
                    await retain_formal_objects(
                        self._projects_dir, self._deleted_projects_dir, session_factory=self._session_factory
                    )
                except Exception:
                    logger.warning("重算正式对象引用失败，等下一轮", exc_info=True)
                try:
                    await cleanup_work_cache(
                        self._projects_dir, min_idle=DEFAULT_MIN_IDLE, session_factory=self._session_factory
                    )
                except Exception:
                    logger.warning("清理远端素材工作文件失败，等下一轮", exc_info=True)
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="material-maintenance")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


def start_material_maintenance(
    session_factory: async_sessionmaker[AsyncSession], projects_dir: Path, deleted_projects_dir: Path | None = None
) -> MaterialMaintenance:
    maintenance = MaterialMaintenance(session_factory, projects_dir, deleted_projects_dir)
    maintenance.start()
    return maintenance


__all__ = ["MaterialMaintenance", "start_material_maintenance"]
