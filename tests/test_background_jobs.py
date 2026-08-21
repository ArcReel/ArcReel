from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.background_job_worker import CHARACTER_CATALOG_SYNC_JOB, BackgroundJobWorker
from lib.db.base import Base
from lib.db.repositories.background_job_repo import BackgroundJobRepository
from tests.conftest import make_translator


@pytest.mark.unit
async def test_background_job_enqueue_dedupes_active_job(db_factory) -> None:
    async with db_factory() as session:
        first, first_deduped = await BackgroundJobRepository(session).enqueue(CHARACTER_CATALOG_SYNC_JOB)
    async with db_factory() as session:
        second, second_deduped = await BackgroundJobRepository(session).enqueue(CHARACTER_CATALOG_SYNC_JOB)

    assert first_deduped is False
    assert second_deduped is True
    assert second["job_id"] == first["job_id"]


@pytest.mark.unit
async def test_background_worker_persists_progress_and_result(tmp_path, monkeypatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    observed_progress: list[tuple[int, int]] = []

    async def fake_sync(_session, *, progress_callback):
        for current in (0, 1, 2):
            await progress_callback(current, 2)
            observed_progress.append((current, 2))
        return {
            "publishVersion": {"id": "p1", "name": "Published", "activatedAt": "2026-08-21T00:00:00Z"},
            "remoteCharacters": 2,
            "added": 2,
            "updated": 0,
            "unchanged": 0,
            "assetsDownloaded": 4,
        }

    monkeypatch.setattr("lib.background_job_worker.sync_character_catalog", fake_sync)
    async with factory() as session:
        queued, _ = await BackgroundJobRepository(session).enqueue(CHARACTER_CATALOG_SYNC_JOB)

    worker = BackgroundJobWorker(session_factory=factory, poll_interval=0.01)
    await worker.start()
    try:
        for _ in range(100):
            async with factory() as session:
                latest = await BackgroundJobRepository(session).get_latest(CHARACTER_CATALOG_SYNC_JOB)
            if latest and latest["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("background job did not finish")
    finally:
        await worker.stop()
        await engine.dispose()

    assert latest is not None
    assert latest["job_id"] == queued["job_id"]
    assert latest["progress_current"] == 2
    assert latest["progress_total"] == 2
    assert latest["result"]["added"] == 2
    assert observed_progress == [(0, 2), (1, 2), (2, 2)]


@pytest.mark.unit
async def test_recover_interrupted_job_returns_it_to_queue(db_factory) -> None:
    async with db_factory() as session:
        queued, _ = await BackgroundJobRepository(session).enqueue(CHARACTER_CATALOG_SYNC_JOB)
        claimed = await BackgroundJobRepository(session).claim_next()
        assert claimed and claimed["status"] == "running"

    async with db_factory() as session:
        recovered = await BackgroundJobRepository(session).recover_interrupted()
        latest = await BackgroundJobRepository(session).get_latest(CHARACTER_CATALOG_SYNC_JOB)

    assert recovered == 1
    assert latest is not None
    assert latest["job_id"] == queued["job_id"]
    assert latest["status"] == "queued"
    assert latest["phase"] == "queued"


@pytest.mark.unit
async def test_character_catalog_route_enqueues_and_reports_status(db_factory, monkeypatch) -> None:
    from server.routers import character_catalog

    monkeypatch.setattr(character_catalog, "async_session_factory", db_factory)
    queued = await character_catalog.sync_catalog(make_translator())
    status = await character_catalog.sync_catalog_status(make_translator())

    assert queued["deduped"] is False
    assert queued["job"]["status"] == "queued"
    assert status["job"]["job_id"] == queued["job"]["job_id"]
