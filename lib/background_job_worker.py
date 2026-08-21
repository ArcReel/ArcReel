"""Worker for durable, non-generation background jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from lib.character_catalog import CharacterCatalogSyncError, sync_character_catalog
from lib.db import safe_session_factory
from lib.db.repositories.background_job_repo import BackgroundJobRepository

logger = logging.getLogger(__name__)

CHARACTER_CATALOG_SYNC_JOB = "character_catalog_sync"


class BackgroundJobWorker:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = safe_session_factory,
        poll_interval: float = 0.5,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval = poll_interval
        self._stopping = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None

    async def start(self) -> None:
        async with self._session_factory() as session:
            recovered = await BackgroundJobRepository(session).recover_interrupted()
        if recovered:
            logger.info("Recovered %d interrupted background job(s)", recovered)
        self._stopping.clear()
        self._runner = asyncio.create_task(self._run(), name="background-job-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._runner is not None:
            await self._runner
            self._runner = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            async with self._session_factory() as session:
                job = await BackgroundJobRepository(session).claim_next()
            if job is None:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    pass
                continue
            await self._execute(job)

    async def _execute(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        if job["job_type"] != CHARACTER_CATALOG_SYNC_JOB:
            await self._fail(job_id, "background_job_unsupported")
            return

        async def report_progress(current: int, total: int) -> None:
            async with self._session_factory() as progress_session:
                await BackgroundJobRepository(progress_session).update_progress(
                    job_id,
                    phase="syncing_characters",
                    current=current,
                    total=total,
                )

        try:
            async with self._session_factory() as sync_session:
                result = await sync_character_catalog(sync_session, progress_callback=report_progress)
            async with self._session_factory() as complete_session:
                await BackgroundJobRepository(complete_session).mark_succeeded(job_id, result)
        except CharacterCatalogSyncError as exc:
            await self._fail(job_id, exc.code, str(exc.status) if exc.status is not None else None)
        except Exception:  # noqa: BLE001
            logger.exception("Background job failed job_id=%s type=%s", job_id, job["job_type"])
            await self._fail(job_id, "character_catalog_sync_failed")

    async def _fail(self, job_id: str, code: str, detail: str | None = None) -> None:
        async with self._session_factory() as session:
            await BackgroundJobRepository(session).mark_failed(job_id, error_code=code, error_detail=detail)
