"""Tests for UsageTracker (async wrapper over UsageRepository)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
        assert item["cost_amount"] == 0.067
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
        assert failed["cost_amount"] == 0

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

    async def test_text_call_with_token_tracking(self, tracker):
        call_id = await tracker.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            prompt="测试 prompt",
            provider="gemini",
        )
        await tracker.finish_call(
            call_id,
            status="success",
            input_tokens=1000,
            output_tokens=500,
        )

        result = await tracker.get_calls(project_name="demo")
        item = result["items"][0]
        assert item["call_type"] == "text"
        assert item["input_tokens"] == 1000
        assert item["output_tokens"] == 500
        assert item["cost_amount"] == pytest.approx((1000 * 0.50 + 500 * 3.00) / 1_000_000)

        stats = await tracker.get_stats(project_name="demo")
        assert stats["text_count"] == 1
        assert stats["total_count"] == 1

    async def test_start_call_with_segment_id(self, tracker):
        call_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            resolution="1K",
            segment_id="E1S001",
        )
        await tracker.finish_call(call_id, status="success", output_path="a.png")

        result = await tracker.get_calls(project_name="demo")
        item = result["items"][0]
        assert item["segment_id"] == "E1S001"

    async def test_start_call_without_segment_id(self, tracker):
        call_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            resolution="1K",
        )
        await tracker.finish_call(call_id, status="success", output_path="a.png")

        result = await tracker.get_calls(project_name="demo")
        item = result["items"][0]
        assert item["segment_id"] is None

    async def test_openai_image_token_based_billing(self, tracker):
        """OpenAI 图片 token-based：4 个 token 字段写入 DB，cost_amount 按费率计算。"""
        call_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gpt-image-2",
            resolution="1K",
            aspect_ratio="9:16",
            provider="openai",
        )
        await tracker.finish_call(
            call_id,
            status="success",
            output_path="a.png",
            quality="medium",
            image_input_tokens=0,
            image_output_tokens=2200,
            text_input_tokens=350,
            text_output_tokens=0,
        )

        item = (await tracker.get_calls(project_name="demo"))["items"][0]
        assert item["image_input_tokens"] == 0
        assert item["image_output_tokens"] == 2200
        assert item["text_input_tokens"] == 350
        assert item["text_output_tokens"] == 0
        # input/output_tokens 总和列：image_in + text_in / image_out + text_out
        assert item["input_tokens"] == 350
        assert item["output_tokens"] == 2200
        # cost = (2200 × 30 + 350 × 5) / 1e6
        assert item["cost_amount"] == pytest.approx((2200 * 30 + 350 * 5) / 1_000_000)
        assert item["currency"] == "USD"

    async def test_openai_image_fallback_resolves_aspect_ratio(self, tracker):
        """SDK 不返回 usage 时，cost 应按 (resolution, aspect_ratio) 反查正确 size — #401 端到端回归。"""
        # 9:16 should yield $0.317 (high quality 1024x1792)
        portrait_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gpt-image-2",
            resolution="1K",
            aspect_ratio="9:16",
            provider="openai",
        )
        await tracker.finish_call(portrait_id, status="success", output_path="p.png", quality="high")

        # 1:1 should yield $0.211 (high quality 1024x1024)
        square_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gpt-image-2",
            resolution="1K",
            aspect_ratio="1:1",
            provider="openai",
        )
        await tracker.finish_call(square_id, status="success", output_path="s.png", quality="high")

        items = (await tracker.get_calls(project_name="demo"))["items"]
        portrait = next(i for i in items if i["id"] == portrait_id)
        square = next(i for i in items if i["id"] == square_id)
        assert portrait["cost_amount"] == pytest.approx(0.317)
        assert square["cost_amount"] == pytest.approx(0.211)
        # token 拆分列在 fallback 路径下应为 None
        assert portrait["image_input_tokens"] is None
        assert square["image_output_tokens"] is None


class TestActualCostsBySegment:
    async def test_aggregates_costs_by_segment_and_type(self, tracker):
        # E1S001: image 两次成功（累计）
        c1 = await tracker.start_call(
            "proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S001"
        )
        await tracker.finish_call(c1, status="success", output_path="a.png")
        c2 = await tracker.start_call(
            "proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S001"
        )
        await tracker.finish_call(c2, status="success", output_path="b.png")

        # E1S001: video 一次成功
        c3 = await tracker.start_call(
            "proj", "video", "veo-3.1-generate-001", resolution="1080p", duration_seconds=6, segment_id="E1S001"
        )
        await tracker.finish_call(c3, status="success", output_path="v.mp4")

        # E1S002: image 一次成功
        c4 = await tracker.start_call(
            "proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S002"
        )
        await tracker.finish_call(c4, status="success", output_path="c.png")

        # 失败的不计入
        c5 = await tracker.start_call(
            "proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S001"
        )
        await tracker.finish_call(c5, status="failed", error_message="err")

        result = await tracker.get_actual_costs_by_segment("proj")

        assert "E1S001" in result
        assert result["E1S001"]["image"]["USD"] == pytest.approx(0.067 * 2)
        assert result["E1S001"]["video"]["USD"] == pytest.approx(2.4)
        assert "E1S002" in result
        assert result["E1S002"]["image"]["USD"] == pytest.approx(0.067)

    async def test_project_level_costs(self, tracker):
        # 角色生成（无 segment_id）
        c1 = await tracker.start_call("proj", "image", "gemini-3.1-flash-image-preview", resolution="1K")
        await tracker.finish_call(c1, status="success", output_path="char.png")

        result = await tracker.get_actual_costs_by_segment("proj")
        assert result.get("__project__", {}).get("image", {}).get("USD") == pytest.approx(0.067)
