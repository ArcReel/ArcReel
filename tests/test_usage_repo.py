"""Tests for UsageRepository."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.db.repositories.usage_repo import UsageRepository


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestUsageRepository:
    async def test_start_and_finish_call(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            prompt="test prompt",
            resolution="1K",
        )
        assert call_id > 0

        await repo.finish_call(
            call_id,
            status="success",
            output_path="storyboards/test.png",
            retry_count=0,
        )

        calls = await repo.get_calls(project_name="demo")
        assert calls["total"] == 1
        assert calls["items"][0]["status"] == "success"

    async def test_get_stats(self, db_session):
        repo = UsageRepository(db_session)
        call1 = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="test-model",
        )
        await repo.finish_call(call1, status="success")

        call2 = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="test-model",
            duration_seconds=8,
        )
        await repo.finish_call(call2, status="failed", error_message="timeout")

        stats = await repo.get_stats(project_name="demo")
        assert stats["image_count"] == 1
        assert stats["video_count"] == 1
        assert stats["failed_count"] == 1
        assert stats["total_count"] == 2

    async def test_get_projects_list(self, db_session):
        repo = UsageRepository(db_session)
        await repo.start_call(project_name="project_a", call_type="image", model="m")
        await repo.start_call(project_name="project_b", call_type="video", model="m")

        projects = await repo.get_projects_list()
        assert set(projects) == {"project_a", "project_b"}

    async def test_pagination(self, db_session):
        repo = UsageRepository(db_session)
        for i in range(5):
            await repo.start_call(project_name="demo", call_type="image", model="m")

        page1 = await repo.get_calls(page=1, page_size=2)
        assert len(page1["items"]) == 2
        assert page1["total"] == 5

        page2 = await repo.get_calls(page=2, page_size=2)
        assert len(page2["items"]) == 2
