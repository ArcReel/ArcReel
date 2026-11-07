"""Channel for project migrations to read task and invocation records.

Migration chains are synchronous worker-thread code; database sessions belong to the event loop. Post project-name queries back to
the event loop to provide synchronous query functions to migrators. Callers must use them in worker threads;
waiting synchronously on the event-loop thread deadlocks.
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
