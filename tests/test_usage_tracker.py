"""Tests for UsageTracker (async wrapper over UsageRepository)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.usage_tracker import UsageTracker


@pytest.fixture
async def tracker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    t = UsageTracker(session_factory=factory)
    yield t
    await engine.dispose()


class TestUsageTracker:
    async def test_start_and_finish_image_call_success(self, tracker):
        call_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            prompt="x" * 700,
            resolution="1K",
        )
        await tracker.finish_call(call_id, status="success", output_path="a.png")

        result = await tracker.get_calls(project_name="demo")
        item = result["items"][0]
        assert item["id"] == call_id
        assert item["status"] == "success"
        assert item["cost_usd"] == 0.067
        assert len(item["prompt"]) == 500

    async def test_finish_video_and_failed_call(self, tracker):
        video_id = await tracker.start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.1-generate-001",
            resolution="4k",
            duration_seconds=6,
            generate_audio=False,
        )
        fail_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            resolution="1K",
        )

        await tracker.finish_call(video_id, status="success", output_path="v.mp4")
        await tracker.finish_call(fail_id, status="failed", error_message="e" * 700)

        stats = await tracker.get_stats(project_name="demo")
        assert stats["video_count"] == 1
        assert stats["failed_count"] == 1
        assert stats["total_count"] == 2
        assert stats["total_cost"] == 2.4

        failed = (await tracker.get_calls(status="failed"))["items"][0]
        assert len(failed["error_message"]) == 500
        assert failed["cost_usd"] == 0

    async def test_stats_with_date_range_and_project_filter(self, tracker):
        await tracker.finish_call(
            await tracker.start_call("p1", "image", "m", resolution="1K"),
            status="success",
        )
        await tracker.finish_call(
            await tracker.start_call("p2", "video", "m", resolution="1080p", duration_seconds=4),
            status="success",
        )

        today = datetime.now()
        stats_all = await tracker.get_stats(start_date=today - timedelta(days=1), end_date=today)
        stats_p1 = await tracker.get_stats(project_name="p1", start_date=today - timedelta(days=1), end_date=today)

        assert stats_all["total_count"] == 2
        assert stats_p1["total_count"] == 1
        assert stats_p1["image_count"] == 1

    async def test_get_calls_pagination_and_projects_list(self, tracker):
        for idx in range(5):
            call_id = await tracker.start_call(
                project_name="demo-a" if idx % 2 == 0 else "demo-b",
                call_type="image",
                model="m",
            )
            await tracker.finish_call(call_id, status="success")

        page1 = await tracker.get_calls(page=1, page_size=2)
        page2 = await tracker.get_calls(page=2, page_size=2)
        assert page1["total"] == 5
        assert len(page1["items"]) == 2
        assert len(page2["items"]) == 2

        projects = await tracker.get_projects_list()
        assert projects == ["demo-a", "demo-b"]
