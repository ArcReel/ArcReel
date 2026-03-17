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


class TestMultiProviderUsage:
    async def test_seedance_call_records_provider_and_tokens(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="doubao-seedance-1-5-pro-251215",
            prompt="test",
            resolution="1080p",
            duration_seconds=5,
            generate_audio=True,
            provider="seedance",
        )

        await repo.finish_call(
            call_id,
            status="success",
            usage_tokens=246840,
            provider="seedance",
            service_tier="default",
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["provider"] == "seedance"
        assert item["currency"] == "CNY"
        assert item["usage_tokens"] == 246840
        assert item["cost_amount"] == pytest.approx(3.9494, rel=1e-3)

    async def test_gemini_call_defaults_to_usd(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.1-generate-001",
            resolution="1080p",
            duration_seconds=8,
            generate_audio=True,
        )
        await repo.finish_call(call_id, status="success")

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["provider"] == "gemini"
        assert item["currency"] == "USD"
        assert item["cost_amount"] == pytest.approx(3.2)

    async def test_get_stats_groups_by_currency(self, db_session):
        repo = UsageRepository(db_session)

        # Gemini call
        c1 = await repo.start_call(
            project_name="demo", call_type="video",
            model="veo-3.1-generate-001", duration_seconds=8,
            resolution="1080p", generate_audio=True,
        )
        await repo.finish_call(c1, status="success")

        # Seedance call
        c2 = await repo.start_call(
            project_name="demo", call_type="video",
            model="doubao-seedance-1-5-pro-251215", duration_seconds=5,
            resolution="1080p", generate_audio=True, provider="seedance",
        )
        await repo.finish_call(c2, status="success", usage_tokens=246840, provider="seedance", service_tier="default")

        stats = await repo.get_stats(project_name="demo")
        assert stats["total_count"] == 2
        assert "cost_by_currency" in stats
        assert stats["cost_by_currency"]["USD"] == pytest.approx(3.2)
        assert stats["cost_by_currency"]["CNY"] == pytest.approx(3.9494, rel=1e-3)
        assert stats["total_cost"] == pytest.approx(3.2)
