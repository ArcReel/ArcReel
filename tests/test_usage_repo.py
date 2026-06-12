"""Tests for UsageRepository."""

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.api_call import ApiCall
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


class TestClassifyAssetOutputPath:
    def test_products_bucketed_separately_from_props(self):
        from lib.db.repositories.usage_repo import _classify_asset_output_path

        assert _classify_asset_output_path("products/保温杯.png") == "products"
        assert _classify_asset_output_path("/abs/path/products/保温杯.png") == "products"
        assert _classify_asset_output_path("props/玉佩.png") == "props"
        assert _classify_asset_output_path("characters/Alice.png") == "characters"
        assert _classify_asset_output_path(None) == "other"


class TestFinalizePendingByCallId:
    """Resume 路径专用：按 call_id 精准翻 pending → success/failed。"""

    async def test_flips_pending_to_success(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")

        # 显式 cost_amount=0.0 维持单元测试的确定性，绕过 auto-calc 路径
        affected = await repo.finalize_pending_by_call_id(call_id=call_id, cost_amount=0.0)
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["status"] == "success"
        assert calls["items"][0]["cost_amount"] == 0.0

    async def test_auto_calculates_cost_when_amount_omitted(self, db_session):
        """cost_amount=None + status='success' → 按 ApiCall 行字段调 cost_calculator 算实际 cost。"""
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.0-fast-generate-001",
            duration_seconds=8,
            resolution="1080p",
            aspect_ratio="9:16",
            generate_audio=True,
            provider="gemini",
        )

        affected = await repo.finalize_pending_by_call_id(call_id=call_id)
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        # auto-calc 由 cost_calculator 按 model/duration/resolution/audio 算出，应为正数
        assert calls["items"][0]["status"] == "success"
        assert calls["items"][0]["cost_amount"] > 0.0, "auto-calc 应算出真实 cost，不应是 0"

    async def test_service_tier_passed_to_cost_calculator(self, db_session, monkeypatch):
        """service_tier 应从 caller 透传到 cost_calculator.calculate_cost，非 default 档位才算对。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, str] = {}

        def _spy_calculate_cost(**kwargs):
            captured["service_tier"] = kwargs.get("service_tier", "MISSING")
            return (1.5, "USD")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="sora-2",
            duration_seconds=8,
            provider="openai",
        )

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, service_tier="priority")
        assert affected == 1
        assert captured["service_tier"] == "priority", "service_tier 必须从 caller 透传到 cost_calculator"

    async def test_usage_tokens_passed_to_cost_calculator(self, db_session, monkeypatch):
        """Ark video 按 usage_tokens 计费，repo 必须把 caller 传入的 usage_tokens 透传到 cost_calculator，
        否则按 token 计费的视频走 usage_tokens or 0 路径 → cost 永远为 0 CNY。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, object] = {}

        def _spy_calculate_cost(**kwargs):
            captured["usage_tokens"] = kwargs.get("usage_tokens", "MISSING")
            return (3.2, "CNY")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="doubao-seedance-1-0-pro",
            duration_seconds=8,
            provider="ark",
        )

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, usage_tokens=12345)
        assert affected == 1
        assert captured["usage_tokens"] == 12345, "usage_tokens 必须从 caller 透传到 cost_calculator"

        # 同时必须写回 ApiCall.usage_tokens 列，否则用量明细/抽屉里这条记录的
        # tokens 字段永远为 null（resume 路径与正常 finish_call 路径行为不一致）。
        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["usage_tokens"] == 12345, "usage_tokens 必须 UPDATE 写回 ApiCall 行"

    async def test_billed_duration_passed_to_cost_calculator_and_ledger(self, db_session, monkeypatch):
        """provider 回报的实际计费时长必须透传到 cost_calculator 并回写 ApiCall.duration_seconds，
        与 finish_call 的 billed_duration_seconds 覆盖语义一致（resume 路径不分叉）。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, object] = {}

        def _spy_calculate_cost(**kwargs):
            captured["duration_seconds"] = kwargs.get("duration_seconds", "MISSING")
            return (6.0, "CNY")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="wan2.7-r2v",
            duration_seconds=6,
            provider="dashscope",
        )

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, billed_duration_seconds=15)
        assert affected == 1
        assert captured["duration_seconds"] == 15, "实际计费时长必须从 caller 透传到 cost_calculator"

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["duration_seconds"] == 15, "实际计费时长必须 UPDATE 写回 ApiCall 行"

    async def test_billed_duration_non_positive_falls_back_to_request_duration(self, db_session, monkeypatch):
        """非正的实际计费时长视同未提供：cost_calculator 入参与账本均回落 start_call 的请求时长。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, object] = {}

        def _spy_calculate_cost(**kwargs):
            captured["duration_seconds"] = kwargs.get("duration_seconds", "MISSING")
            return (2.4, "CNY")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="wan2.7-r2v",
            duration_seconds=6,
            provider="dashscope",
        )

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, billed_duration_seconds=0)
        assert affected == 1
        assert captured["duration_seconds"] == 6, "非正计费时长不得传给 cost_calculator，应回落请求时长"

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["duration_seconds"] == 6, "非正计费时长不得写回账本，应保留请求时长"

    async def test_billed_duration_over_limit_falls_back_to_request_duration(self, db_session, monkeypatch):
        """超出合理上限（24h）的计费时长视同未提供：repo 写入层是全部 backend 的最后防线，
        防超大数值写入 DB Integer 列溢出。"""
        from lib import cost_calculator as cc_module
        from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS

        captured: dict[str, object] = {}

        def _spy_calculate_cost(**kwargs):
            captured["duration_seconds"] = kwargs.get("duration_seconds", "MISSING")
            return (2.4, "CNY")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="wan2.7-r2v",
            duration_seconds=6,
            provider="dashscope",
        )

        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, billed_duration_seconds=MAX_BILLED_DURATION_SECONDS + 1
        )
        assert affected == 1
        assert captured["duration_seconds"] == 6, "超限计费时长不得传给 cost_calculator，应回落请求时长"

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["duration_seconds"] == 6, "超限计费时长不得写回账本，应保留请求时长"

    async def test_does_not_touch_other_pending_call(self, db_session):
        repo = UsageRepository(db_session)
        cid_a = await repo.start_call(project_name="demo", call_type="video", model="m", segment_id="E1S01")
        cid_b = await repo.start_call(project_name="demo", call_type="video", model="m", segment_id="E1S01")

        affected = await repo.finalize_pending_by_call_id(call_id=cid_a, cost_amount=0.0)
        assert affected == 1

        # 全量查询
        calls = await repo.get_calls(project_name="demo", page_size=100)
        by_id = {c["id"]: c for c in calls["items"]}
        assert by_id[cid_a]["status"] == "success"
        assert by_id[cid_b]["status"] == "pending", "另一条 pending 不应被 touch"

    async def test_idempotent_when_already_success(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")
        await repo.finish_call(call_id, status="success", cost_amount=5.0, currency="USD")

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, cost_amount=999.0)
        assert affected == 0, "已 success 行应保持不变"

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["cost_amount"] == 5.0, "cost 未被覆写"

    async def test_finalize_failed_status(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, status="failed")
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["status"] == "failed"
        assert calls["items"][0]["cost_amount"] == 0.0

    async def test_unknown_call_id_returns_zero(self, db_session):
        repo = UsageRepository(db_session)
        affected = await repo.finalize_pending_by_call_id(call_id=99999)
        assert affected == 0

    async def test_writes_duration_ms(self, db_session):
        """resume 完成的调用必须回写 duration_ms，否则 get_stats_grouped_by_provider 的
        provider 级时长统计会因 NULL 系统性压低。"""
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, cost_amount=0.0)
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        # started_at 与 finished_at 同瞬间内完成；duration_ms 必须是已写入的非 None 整数
        assert item["duration_ms"] is not None
        assert item["duration_ms"] >= 0

    async def test_generate_audio_override_passed_to_cost_calculator(self, db_session, monkeypatch):
        """provider 在 submit 后可能降级/关闭音频；finalize 接受 caller 透传的 generate_audio
        覆盖 ApiCall 行上 start_call 时的请求值（与 finish_call 同语义），cost_calculator 也应收到
        覆盖后的值，避免按请求值误计费。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, object] = {}

        def _spy_calculate_cost(**kwargs):
            captured["generate_audio"] = kwargs.get("generate_audio", "MISSING")
            return (1.5, "USD")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(db_session)
        # start_call 时请求 generate_audio=True
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.0-fast-generate-001",
            duration_seconds=8,
            generate_audio=True,
            provider="gemini",
        )

        # provider 实际降级到关闭音频
        affected = await repo.finalize_pending_by_call_id(call_id=call_id, generate_audio=False)
        assert affected == 1
        assert captured["generate_audio"] is False, "generate_audio 透传必须覆盖到 cost_calculator"

        # 并且 ApiCall.generate_audio 也回写为降级后的实际值
        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["generate_audio"] is False


class TestMultiProviderUsage:
    async def test_ark_call_records_provider_and_tokens(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="doubao-seedance-1-5-pro-251215",
            prompt="test",
            resolution="1080p",
            duration_seconds=5,
            generate_audio=True,
            provider="ark",
        )

        await repo.finish_call(
            call_id,
            status="success",
            usage_tokens=246840,
            service_tier="default",
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["provider"] == "ark"
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
            project_name="demo",
            call_type="video",
            model="veo-3.1-generate-001",
            duration_seconds=8,
            resolution="1080p",
            generate_audio=True,
        )
        await repo.finish_call(c1, status="success")

        # Ark call
        c2 = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="doubao-seedance-1-5-pro-251215",
            duration_seconds=5,
            resolution="1080p",
            generate_audio=True,
            provider="ark",
        )
        await repo.finish_call(c2, status="success", usage_tokens=246840, service_tier="default")

        stats = await repo.get_stats(project_name="demo")
        assert stats["total_count"] == 2
        assert "cost_by_currency" in stats
        assert stats["cost_by_currency"]["USD"] == pytest.approx(3.2)
        assert stats["cost_by_currency"]["CNY"] == pytest.approx(3.9494, rel=1e-3)
        assert stats["total_cost"] == pytest.approx(3.2)

    async def test_get_stats_cost_by_currency_excludes_failed_billed_calls(self, db_session):
        """金额维度与项目成本口径一致：只统计 success 且已扣费调用。"""
        repo = UsageRepository(db_session)

        ok = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="viduq2",
            resolution="1080p",
            provider="vidu",
        )
        await repo.finish_call(ok, status="success", usage_tokens=8)

        failed = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="claude-sonnet-4",
            provider="anthropic",
        )
        await repo.finish_call(failed, status="failed", error_message="boom")
        await db_session.execute(
            update(ApiCall)
            .where(ApiCall.id == failed)
            .values(cost_amount=0.0456, currency="USD", input_tokens=100, output_tokens=20)
        )
        await db_session.commit()

        failed_unbilled = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="viduq2",
            resolution="1080p",
            provider="vidu",
        )
        await repo.finish_call(failed_unbilled, status="failed", error_message="boom")

        zero_cost = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            provider="gemini",
        )
        await repo.finish_call(zero_cost, status="success", input_tokens=0, output_tokens=0)

        stats = await repo.get_stats(project_name="demo")
        # failed 即使有实付记录也不计入金额；零费用/未扣费记录也不计入金额。
        assert stats["total_count"] == 4
        assert stats["failed_count"] == 2
        assert stats["total_cost"] == pytest.approx(0)
        assert stats["cost_by_currency"] == {
            "CNY": pytest.approx(0.25),
        }

    async def test_get_stats_grouped_by_provider_includes_cost_by_currency(self, db_session):
        repo = UsageRepository(db_session)

        gemini_id = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            resolution="1K",
            provider="gemini",
        )
        await repo.finish_call(gemini_id, status="success")

        vidu_id = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="viduq2",
            resolution="1080p",
            provider="vidu",
        )
        await repo.finish_call(vidu_id, status="success", usage_tokens=8)

        failed_vidu_id = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="viduq2",
            resolution="1080p",
            provider="vidu",
        )
        await repo.finish_call(failed_vidu_id, status="failed", error_message="boom")

        failed_anthropic_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="claude-sonnet-4",
            provider="anthropic",
        )
        await repo.finish_call(failed_anthropic_id, status="failed", error_message="boom")
        await db_session.execute(
            update(ApiCall)
            .where(ApiCall.id == failed_anthropic_id)
            .values(cost_amount=0.0456, currency="USD", input_tokens=100, output_tokens=20)
        )
        await db_session.commit()

        stats = await repo.get_stats_grouped_by_provider(project_name="demo")
        by_group = {(item["provider"], item["call_type"]): item for item in stats["stats"]}

        assert set(by_group) == {
            ("anthropic", "text"),
            ("gemini", "image"),
            ("vidu", "image"),
        }

        assert by_group[("anthropic", "text")]["total_cost_usd"] == pytest.approx(0)
        assert by_group[("anthropic", "text")]["cost_by_currency"] == {}
        assert by_group[("anthropic", "text")]["total_calls"] == 1
        assert by_group[("anthropic", "text")]["success_calls"] == 0
        assert by_group[("gemini", "image")]["total_cost_usd"] == pytest.approx(0.067)
        assert by_group[("gemini", "image")]["cost_by_currency"] == {"USD": pytest.approx(0.067)}
        assert by_group[("vidu", "image")]["total_cost_usd"] == 0
        assert by_group[("vidu", "image")]["cost_by_currency"] == {"CNY": pytest.approx(0.25)}
        assert by_group[("vidu", "image")]["total_calls"] == 2
        assert by_group[("vidu", "image")]["success_calls"] == 1

    async def test_text_call_gemini_cost(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            prompt="分析小说内容",
            provider="gemini",
        )

        await repo.finish_call(
            call_id,
            status="success",
            input_tokens=1000,
            output_tokens=500,
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["call_type"] == "text"
        assert item["input_tokens"] == 1000
        assert item["output_tokens"] == 500
        assert item["currency"] == "USD"
        # cost = (1000 * 0.50 + 500 * 3.00) / 1_000_000 = 0.002
        assert item["cost_amount"] == pytest.approx((1000 * 0.50 + 500 * 3.00) / 1_000_000)

    async def test_text_call_ark_cost(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="doubao-seed-2-0-lite-260215",
            prompt="分析小说内容",
            provider="ark",
        )

        await repo.finish_call(
            call_id,
            status="success",
            input_tokens=2000,
            output_tokens=1000,
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["currency"] == "CNY"
        # cost = (2000 * 0.60 + 1000 * 3.60) / 1_000_000 = 0.0048
        assert item["cost_amount"] == pytest.approx((2000 * 0.60 + 1000 * 3.60) / 1_000_000)

    async def test_text_call_failed_zero_cost(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            provider="gemini",
        )

        await repo.finish_call(
            call_id,
            status="failed",
            error_message="API error",
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["cost_amount"] == 0.0

    async def test_get_stats_includes_text_count(self, db_session):
        repo = UsageRepository(db_session)
        c1 = await repo.start_call(project_name="demo", call_type="image", model="m")
        await repo.finish_call(c1, status="success")

        c2 = await repo.start_call(project_name="demo", call_type="video", model="m", duration_seconds=8)
        await repo.finish_call(c2, status="failed", error_message="timeout")

        c3 = await repo.start_call(project_name="demo", call_type="text", model="m", provider="gemini")
        await repo.finish_call(c3, status="success", input_tokens=100, output_tokens=50)

        stats = await repo.get_stats(project_name="demo")
        assert stats["image_count"] == 1
        assert stats["video_count"] == 1
        assert stats["text_count"] == 1
        assert stats["failed_count"] == 1
        assert stats["total_count"] == 3
