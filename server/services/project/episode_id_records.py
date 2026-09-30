"""项目迁移读任务与调用记录的通道。

迁移链是同步代码、跑在工作线程里；数据库会话属于事件循环。这里把按项目名的查询投回
事件循环执行，得到迁移器需要的同步查询函数。调用方必须在工作线程里使用它，
在事件循环线程上同步等待会自锁。
"""

from __future__ import annotations

import asyncio

from lib.db import async_session_factory
from lib.db.repositories.episode_id_records import max_recorded_episode_id
from lib.project.project_migrations.v15_to_v16_edit_decisions import RecordedEpisodeIds


def recorded_episode_ids_on(loop: asyncio.AbstractEventLoop) -> RecordedEpisodeIds:
    async def _query(project_name: str) -> int:
        async with async_session_factory() as session:
            return await max_recorded_episode_id(session, project_name)

    def _lookup(project_name: str) -> int:
        return asyncio.run_coroutine_threadsafe(_query(project_name), loop).result()

    return _lookup


__all__ = ["recorded_episode_ids_on"]
