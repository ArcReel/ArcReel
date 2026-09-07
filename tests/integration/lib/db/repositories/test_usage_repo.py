"""Tests for UsageRepository."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from lib.db.base import DEFAULT_USER_ID, utc_now
from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.db.repositories.usage_repo import (
    MAX_PROVIDER_RESPONSE_BYTES,
    SettlementInput,
    UsageCursor,
    UsageCursorError,
    UsageFilters,
    UsageRepository,
)
from lib.providers import CallStatus


class TestUsageRepository:
    async def test_start_and_finish_call(self, async_session):
        repo = UsageRepository(async_session)
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
            settlement=SettlementInput(),
            output_path="storyboards/test.png",
        )

        calls = await repo.get_calls(project_name="demo")
        assert calls["total"] == 1
        assert calls["items"][0]["status"] == "success"

    async def test_get_stats(self, async_session):
        repo = UsageRepository(async_session)
        call1 = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="test-model",
        )
        await repo.finish_call(call1, status="success", settlement=SettlementInput())

        call2 = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="test-model",
            duration_seconds=8,
        )
        await repo.finish_call(call2, status="failed", settlement=SettlementInput(), error_message="timeout")

        stats = await repo.get_stats(project_name="demo")
        assert stats["image_count"] == 1
        assert stats["video_count"] == 1
        assert stats["failed_count"] == 1
        assert stats["total_count"] == 2

    async def test_get_projects_list(self, async_session):
        repo = UsageRepository(async_session)
        await repo.start_call(project_name="project_a", call_type="image", model="m")
        await repo.start_call(project_name="project_b", call_type="video", model="m")

        projects = await repo.get_projects_list()
        assert set(projects) == {"project_a", "project_b"}

    async def test_pagination(self, async_session):
        repo = UsageRepository(async_session)
        for _ in range(5):
            await repo.start_call(project_name="demo", call_type="image", model="m")

        page1 = await repo.get_calls(page=1, page_size=2)
        assert len(page1["items"]) == 2
        assert page1["total"] == 5

        page2 = await repo.get_calls(page=2, page_size=2)
        assert len(page2["items"]) == 2

    async def test_filters_calls_by_id(self, async_session):
        repo = UsageRepository(async_session)
        wanted = await repo.start_call(project_name="demo", call_type="video", model="wanted")
        await repo.start_call(project_name="demo", call_type="video", model="other")

        result = await repo.get_calls(call_id=wanted)

        assert result["total"] == 1
        assert result["items"][0]["id"] == wanted

    async def test_last_provider_response_is_bounded(self, async_session):
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")

        await repo.update_last_provider_response(call_id, {"payload": "x" * (MAX_PROVIDER_RESPONSE_BYTES + 1)})

        stored = (await repo.get_calls(project_name="demo"))["items"][0]["last_provider_response"]
        assert stored["truncated"] is True
        assert len(json.dumps(stored, ensure_ascii=False).encode()) < MAX_PROVIDER_RESPONSE_BYTES

    async def test_non_ascii_response_is_bounded_by_what_gets_persisted(self, async_session):
        """量的必须是 JSON 列真正写出去的那份文本。

        JSON 列按 ensure_ascii=True 序列化，非 ASCII escape 成 \\uXXXX；按 ensure_ascii=False
        量体积，一段表情符号能量出 60 KiB 却写出 180 KiB，上限就形同虚设。
        """
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")

        # 每个表情符号 4 字节 UTF-8，escape 成两个 \uXXXX 转义序列后是 12 字节。
        await repo.update_last_provider_response(call_id, {"payload": "🎬" * 15000})

        stored = (await repo.get_calls(project_name="demo"))["items"][0]["last_provider_response"]
        assert stored["truncated"] is True
        assert len(json.dumps(stored).encode()) <= MAX_PROVIDER_RESPONSE_BYTES

    async def test_response_just_under_the_cap_is_stored_verbatim(self, async_session):
        """判据按 JSON 列的缺省序列化算——它带空白分隔符，比紧凑写法更占地方。"""
        from lib.db.repositories.usage_repo import bound_provider_response

        body = {"payload": "x" * (MAX_PROVIDER_RESPONSE_BYTES - 1024)}
        assert len(json.dumps(body).encode()) <= MAX_PROVIDER_RESPONSE_BYTES

        repo = UsageRepository(async_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")
        await repo.update_last_provider_response(call_id, body)

        assert bound_provider_response(body) == body
        stored = (await repo.get_calls(project_name="demo"))["items"][0]["last_provider_response"]
        assert stored == body


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

    async def test_flips_pending_to_success(self, async_session):
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")

        # 显式 cost_amount=0.0 维持单元测试的确定性，绕过 auto-calc 路径
        affected = await repo.finalize_pending_by_call_id(call_id=call_id, settlement=SettlementInput(cost_amount=0.0))
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["status"] == "success"
        assert calls["items"][0]["cost_amount"] == 0.0

    async def test_auto_calculates_cost_when_amount_omitted(self, async_session):
        """cost_amount=None + status='success' → 按 ApiCall 行字段调 cost_calculator 算实际 cost。"""
        repo = UsageRepository(async_session)
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

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, settlement=SettlementInput())
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        # auto-calc 由 cost_calculator 按 model/duration/resolution/audio 算出，应为正数
        assert calls["items"][0]["status"] == "success"
        assert calls["items"][0]["cost_amount"] > 0.0, "auto-calc 应算出真实 cost，不应是 0"

    async def test_service_tier_passed_to_cost_calculator(self, async_session, monkeypatch):
        """service_tier 应从 caller 透传到 cost_calculator.calculate_cost，非 default 档位才算对。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, str] = {}

        def _spy_calculate_cost(provider, params, **kwargs):
            captured["service_tier"] = params.service_tier
            return (1.5, "USD")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(async_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="sora-2",
            duration_seconds=8,
            provider="openai",
        )

        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, settlement=SettlementInput(service_tier="priority")
        )
        assert affected == 1
        assert captured["service_tier"] == "priority", "service_tier 必须从 caller 透传到 cost_calculator"

    async def test_usage_tokens_passed_to_cost_calculator(self, async_session, monkeypatch):
        """Ark video 按 usage_tokens 计费，repo 必须把 caller 传入的 usage_tokens 透传到 cost_calculator，
        否则按 token 计费的视频走 usage_tokens or 0 路径 → cost 永远为 0 CNY。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, object] = {}

        def _spy_calculate_cost(provider, params, **kwargs):
            captured["usage_tokens"] = params.usage_tokens
            return (3.2, "CNY")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(async_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="doubao-seedance-1-0-pro",
            duration_seconds=8,
            provider="ark",
        )

        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, settlement=SettlementInput(usage_tokens=12345)
        )
        assert affected == 1
        assert captured["usage_tokens"] == 12345, "usage_tokens 必须从 caller 透传到 cost_calculator"

        # 同时必须写回 ApiCall.usage_tokens 列，否则用量明细/抽屉里这条记录的
        # tokens 字段永远为 null（resume 路径与正常 finish_call 路径行为不一致）。
        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["usage_tokens"] == 12345, "usage_tokens 必须 UPDATE 写回 ApiCall 行"

    async def test_settlement_does_not_approximate_missing_usage_tokens(self, async_session):
        """provider 成功响应但漏报 usage（``usage_tokens`` 为 None）时，实付结算必须如实按
        0 计费，不能借费用预估侧的 token 近似换算兜底把估算近似值当成真实支出记录——
        该兜底只应在 ``CostCalculator.calculate_cost(estimate_only=True)`` 时生效
        （见 ``server/services/cost_estimation.py`` 的视频预估分支），
        ``UsageRepository._settle`` 走真实结算，不传 ``estimate_only``。"""
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="doubao-seedance-1-0-pro",
            duration_seconds=8,
            provider="ark",
        )

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, settlement=SettlementInput())
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["cost_amount"] == 0.0, "usage_tokens 缺失时实付结算不得伪造近似金额"

    async def test_billed_duration_passed_to_cost_calculator_and_ledger(self, async_session, monkeypatch):
        """provider 回报的实际计费时长必须透传到 cost_calculator 并回写 ApiCall.duration_seconds，
        与 finish_call 的 billed_duration_seconds 覆盖语义一致（resume 路径不分叉）。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, object] = {}

        def _spy_calculate_cost(provider, params, **kwargs):
            captured["duration_seconds"] = params.duration_seconds
            return (6.0, "CNY")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(async_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="wan2.7-r2v",
            duration_seconds=6,
            provider="dashscope",
        )

        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, settlement=SettlementInput(billed_duration_seconds=15)
        )
        assert affected == 1
        assert captured["duration_seconds"] == 15, "实际计费时长必须从 caller 透传到 cost_calculator"

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["duration_seconds"] == 15, "实际计费时长必须 UPDATE 写回 ApiCall 行"

    async def test_billed_duration_non_positive_falls_back_to_request_duration(self, async_session, monkeypatch):
        """非正的实际计费时长视同未提供：cost_calculator 入参与账本均回落 start_call 的请求时长。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, object] = {}

        def _spy_calculate_cost(provider, params, **kwargs):
            captured["duration_seconds"] = params.duration_seconds
            return (2.4, "CNY")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(async_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="wan2.7-r2v",
            duration_seconds=6,
            provider="dashscope",
        )

        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, settlement=SettlementInput(billed_duration_seconds=0)
        )
        assert affected == 1
        assert captured["duration_seconds"] == 6, "非正计费时长不得传给 cost_calculator，应回落请求时长"

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["duration_seconds"] == 6, "非正计费时长不得写回账本，应保留请求时长"

    async def test_billed_duration_over_limit_falls_back_to_request_duration(self, async_session, monkeypatch):
        """超出合理上限（24h）的计费时长视同未提供：repo 写入层是全部 backend 的最后防线，
        防超大数值写入 DB Integer 列溢出。"""
        from lib import cost_calculator as cc_module
        from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS

        captured: dict[str, object] = {}

        def _spy_calculate_cost(provider, params, **kwargs):
            captured["duration_seconds"] = params.duration_seconds
            return (2.4, "CNY")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(async_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="wan2.7-r2v",
            duration_seconds=6,
            provider="dashscope",
        )

        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, settlement=SettlementInput(billed_duration_seconds=MAX_BILLED_DURATION_SECONDS + 1)
        )
        assert affected == 1
        assert captured["duration_seconds"] == 6, "超限计费时长不得传给 cost_calculator，应回落请求时长"

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["duration_seconds"] == 6, "超限计费时长不得写回账本，应保留请求时长"

    async def test_does_not_touch_other_pending_call(self, async_session):
        repo = UsageRepository(async_session)
        cid_a = await repo.start_call(project_name="demo", call_type="video", model="m", segment_id="E1S01")
        cid_b = await repo.start_call(project_name="demo", call_type="video", model="m", segment_id="E1S01")

        affected = await repo.finalize_pending_by_call_id(call_id=cid_a, settlement=SettlementInput(cost_amount=0.0))
        assert affected == 1

        # 全量查询
        calls = await repo.get_calls(project_name="demo", page_size=100)
        by_id = {c["id"]: c for c in calls["items"]}
        assert by_id[cid_a]["status"] == "success"
        assert by_id[cid_b]["status"] == "pending", "另一条 pending 不应被 touch"

    async def test_idempotent_when_already_success(self, async_session):
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")
        await repo.finish_call(call_id, status="success", settlement=SettlementInput(cost_amount=5.0, currency="USD"))

        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, settlement=SettlementInput(cost_amount=999.0)
        )
        assert affected == 0, "已 success 行应保持不变"

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["cost_amount"] == 5.0, "cost 未被覆写"

    async def test_finalize_failed_status(self, async_session):
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")

        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, settlement=SettlementInput(), status="failed"
        )
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["status"] == "failed"
        assert calls["items"][0]["cost_amount"] == 0.0

    async def test_unknown_call_id_returns_zero(self, async_session):
        repo = UsageRepository(async_session)
        affected = await repo.finalize_pending_by_call_id(call_id=99999, settlement=SettlementInput())
        assert affected == 0

    async def test_writes_duration_ms(self, async_session):
        """resume 完成的调用必须回写 duration_ms，否则 get_stats_grouped_by_provider 的
        provider 级时长统计会因 NULL 系统性压低。"""
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(project_name="demo", call_type="video", model="m")

        affected = await repo.finalize_pending_by_call_id(call_id=call_id, settlement=SettlementInput(cost_amount=0.0))
        assert affected == 1

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        # started_at 与 finished_at 同瞬间内完成；duration_ms 必须是已写入的非 None 整数
        assert item["duration_ms"] is not None
        assert item["duration_ms"] >= 0

    async def test_generate_audio_override_passed_to_cost_calculator(self, async_session, monkeypatch):
        """provider 在 submit 后可能降级/关闭音频；finalize 接受 caller 透传的 generate_audio
        覆盖 ApiCall 行上 start_call 时的请求值（与 finish_call 同语义），cost_calculator 也应收到
        覆盖后的值，避免按请求值误计费。"""
        from lib import cost_calculator as cc_module

        captured: dict[str, object] = {}

        def _spy_calculate_cost(provider, params, **kwargs):
            captured["generate_audio"] = params.generate_audio
            return (1.5, "USD")

        monkeypatch.setattr(cc_module.cost_calculator, "calculate_cost", _spy_calculate_cost)

        repo = UsageRepository(async_session)
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
        affected = await repo.finalize_pending_by_call_id(
            call_id=call_id, settlement=SettlementInput(generate_audio=False)
        )
        assert affected == 1
        assert captured["generate_audio"] is False, "generate_audio 透传必须覆盖到 cost_calculator"

        # 并且 ApiCall.generate_audio 也回写为降级后的实际值
        calls = await repo.get_calls(project_name="demo")
        assert calls["items"][0]["generate_audio"] is False


class TestMultiProviderUsage:
    async def test_ark_call_records_provider_and_tokens(self, async_session):
        repo = UsageRepository(async_session)
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
            settlement=SettlementInput(usage_tokens=246840, service_tier="default"),
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["provider"] == "ark"
        assert item["currency"] == "CNY"
        assert item["usage_tokens"] == 246840
        assert item["cost_amount"] == pytest.approx(3.9494, rel=1e-3)

    async def test_gemini_call_defaults_to_usd(self, async_session):
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.1-generate-001",
            resolution="1080p",
            duration_seconds=8,
            generate_audio=True,
        )
        await repo.finish_call(call_id, status="success", settlement=SettlementInput())

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["provider"] == "gemini"
        assert item["currency"] == "USD"
        assert item["cost_amount"] == pytest.approx(3.2)

    async def test_get_stats_groups_by_currency(self, async_session):
        repo = UsageRepository(async_session)

        # Gemini call
        c1 = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.1-generate-001",
            duration_seconds=8,
            resolution="1080p",
            generate_audio=True,
        )
        await repo.finish_call(c1, status="success", settlement=SettlementInput())

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
        await repo.finish_call(
            c2, status="success", settlement=SettlementInput(usage_tokens=246840, service_tier="default")
        )

        stats = await repo.get_stats(project_name="demo")
        assert stats["total_count"] == 2
        assert "cost_by_currency" in stats
        assert stats["cost_by_currency"]["USD"] == pytest.approx(3.2)
        assert stats["cost_by_currency"]["CNY"] == pytest.approx(3.9494, rel=1e-3)
        assert stats["total_cost"] == pytest.approx(3.2)

    async def test_get_stats_cost_by_currency_excludes_failed_billed_calls(self, async_session):
        """金额维度与项目成本口径一致：只统计 success 且已扣费调用。"""
        repo = UsageRepository(async_session)

        ok = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="viduq2",
            resolution="1080p",
            provider="vidu",
        )
        await repo.finish_call(ok, status="success", settlement=SettlementInput(usage_tokens=8))

        failed = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="claude-sonnet-4",
            provider="anthropic",
        )
        await repo.finish_call(failed, status="failed", settlement=SettlementInput(), error_message="boom")
        await async_session.execute(
            update(ApiCall)
            .where(ApiCall.id == failed)
            .values(cost_amount=0.0456, currency="USD", input_tokens=100, output_tokens=20)
        )
        await async_session.commit()

        failed_unbilled = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="viduq2",
            resolution="1080p",
            provider="vidu",
        )
        await repo.finish_call(failed_unbilled, status="failed", settlement=SettlementInput(), error_message="boom")

        zero_cost = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            provider="gemini",
        )
        await repo.finish_call(zero_cost, status="success", settlement=SettlementInput(input_tokens=0, output_tokens=0))

        stats = await repo.get_stats(project_name="demo")
        # failed 即使有实付记录也不计入金额；零费用/未扣费记录也不计入金额。
        assert stats["total_count"] == 4
        assert stats["failed_count"] == 2
        assert stats["total_cost"] == pytest.approx(0)
        assert stats["cost_by_currency"] == {
            "CNY": pytest.approx(0.25),
        }

    async def test_get_stats_grouped_by_provider_includes_cost_by_currency(self, async_session):
        repo = UsageRepository(async_session)

        gemini_id = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            resolution="1K",
            provider="gemini",
        )
        await repo.finish_call(gemini_id, status="success", settlement=SettlementInput())

        vidu_id = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="viduq2",
            resolution="1080p",
            provider="vidu",
        )
        await repo.finish_call(vidu_id, status="success", settlement=SettlementInput(usage_tokens=8))

        failed_vidu_id = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="viduq2",
            resolution="1080p",
            provider="vidu",
        )
        await repo.finish_call(failed_vidu_id, status="failed", settlement=SettlementInput(), error_message="boom")

        failed_anthropic_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="claude-sonnet-4",
            provider="anthropic",
        )
        await repo.finish_call(failed_anthropic_id, status="failed", settlement=SettlementInput(), error_message="boom")
        await async_session.execute(
            update(ApiCall)
            .where(ApiCall.id == failed_anthropic_id)
            .values(cost_amount=0.0456, currency="USD", input_tokens=100, output_tokens=20)
        )
        await async_session.commit()

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

    async def test_text_call_gemini_cost(self, async_session):
        repo = UsageRepository(async_session)
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
            settlement=SettlementInput(input_tokens=1000, output_tokens=500),
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["call_type"] == "text"
        assert item["input_tokens"] == 1000
        assert item["output_tokens"] == 500
        assert item["currency"] == "USD"
        # cost = (1000 * 0.50 + 500 * 3.00) / 1_000_000 = 0.002
        assert item["cost_amount"] == pytest.approx((1000 * 0.50 + 500 * 3.00) / 1_000_000)

    async def test_text_call_ark_cost(self, async_session):
        repo = UsageRepository(async_session)
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
            settlement=SettlementInput(input_tokens=2000, output_tokens=1000),
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["currency"] == "CNY"
        # cost = (2000 * 0.60 + 1000 * 3.60) / 1_000_000 = 0.0048
        assert item["cost_amount"] == pytest.approx((2000 * 0.60 + 1000 * 3.60) / 1_000_000)

    async def test_text_call_failed_zero_cost(self, async_session):
        repo = UsageRepository(async_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            provider="gemini",
        )

        await repo.finish_call(
            call_id,
            status="failed",
            settlement=SettlementInput(),
            error_message="API error",
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["cost_amount"] == 0.0

    async def test_get_stats_includes_text_count(self, async_session):
        repo = UsageRepository(async_session)
        c1 = await repo.start_call(project_name="demo", call_type="image", model="m")
        await repo.finish_call(c1, status="success", settlement=SettlementInput())

        c2 = await repo.start_call(project_name="demo", call_type="video", model="m", duration_seconds=8)
        await repo.finish_call(c2, status="failed", settlement=SettlementInput(), error_message="timeout")

        c3 = await repo.start_call(project_name="demo", call_type="text", model="m", provider="gemini")
        await repo.finish_call(c3, status="success", settlement=SettlementInput(input_tokens=100, output_tokens=50))

        stats = await repo.get_stats(project_name="demo")
        assert stats["image_count"] == 1
        assert stats["video_count"] == 1
        assert stats["text_count"] == 1
        assert stats["failed_count"] == 1
        assert stats["total_count"] == 3


BASE_TIME = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def make_call(**overrides) -> ApiCall:
    """一行 api_calls；未覆写的列取一组不影响筛选的中性值。"""
    fields: dict = {
        "project_name": "demo",
        "call_type": "image",
        "model": "gemini-3.1-flash-image-preview",
        "provider": "gemini",
        "status": CallStatus.SUCCESS,
        "started_at": BASE_TIME,
        "cost_amount": 0.0,
        "currency": "USD",
        "user_id": DEFAULT_USER_ID,
    }
    fields.update(overrides)
    return ApiCall(**fields)


def make_task(task_id: str, *, task_type: str = "image_generation") -> Task:
    return Task(
        task_id=task_id,
        project_name="demo",
        task_type=task_type,
        media_type="image",
        resource_id="S1",
        status="succeeded",
        queued_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


class TestUsageCursor:
    def test_round_trips_started_at_and_id(self):
        cursor = UsageCursor(started_at=BASE_TIME, id=42)
        decoded = UsageCursor.decode(cursor.encode())
        assert decoded == cursor

    def test_encoded_form_is_opaque_base64(self):
        encoded = UsageCursor(started_at=BASE_TIME, id=42).encode()
        assert "2026" not in encoded
        assert base64.urlsafe_b64decode(encoded.encode("ascii"))

    def test_naive_started_at_encodes_as_utc(self):
        naive = UsageCursor(started_at=BASE_TIME.replace(tzinfo=None), id=7)
        assert UsageCursor.decode(naive.encode()).started_at == BASE_TIME

    @pytest.mark.parametrize("raw", ["not-base64!!", "", base64.urlsafe_b64encode(b"{}").decode()])
    def test_undecodable_cursor_raises(self, raw):
        with pytest.raises(UsageCursorError):
            UsageCursor.decode(raw)


class TestListRecords:
    async def test_orders_by_started_at_then_id_desc(self, async_session):
        async_session.add_all(
            [
                make_call(started_at=BASE_TIME, segment_id="older"),
                make_call(started_at=BASE_TIME + timedelta(minutes=1), segment_id="tie-a"),
                make_call(started_at=BASE_TIME + timedelta(minutes=1), segment_id="tie-b"),
            ]
        )
        await async_session.commit()

        page = await UsageRepository(async_session).list_records()

        # 同一 started_at 上按 id 倒序，后写入的 tie-b 排在 tie-a 之前。
        assert [item["segment_id"] for item in page["items"]] == ["tie-b", "tie-a", "older"]
        assert page["total"] == 3
        assert page["next_cursor"] is None

    async def test_cursor_walks_every_row_once(self, async_session):
        async_session.add_all([make_call(started_at=BASE_TIME + timedelta(minutes=i)) for i in range(7)])
        await async_session.commit()
        repo = UsageRepository(async_session)

        seen: list[int] = []
        cursor = None
        for _ in range(4):
            page = await repo.list_records(limit=3, cursor=cursor)
            seen.extend(item["id"] for item in page["items"])
            assert page["total"] == 7
            if page["next_cursor"] is None:
                break
            cursor = UsageCursor.decode(page["next_cursor"])

        assert len(seen) == len(set(seen)) == 7

    async def test_cursor_steps_over_rows_sharing_started_at(self, async_session):
        async_session.add_all([make_call(started_at=BASE_TIME) for _ in range(4)])
        await async_session.commit()
        repo = UsageRepository(async_session)

        first = await repo.list_records(limit=2)
        second = await repo.list_records(limit=2, cursor=UsageCursor.decode(first["next_cursor"]))

        ids = [item["id"] for item in first["items"] + second["items"]]
        assert len(set(ids)) == 4
        assert ids == sorted(ids, reverse=True)

    async def test_next_cursor_is_none_on_last_page(self, async_session):
        async_session.add_all([make_call(started_at=BASE_TIME + timedelta(minutes=i)) for i in range(4)])
        await async_session.commit()

        page = await UsageRepository(async_session).list_records(limit=4)

        assert len(page["items"]) == 4
        assert page["next_cursor"] is None

    async def test_pending_with_task_hidden_and_taskless_pending_kept(self, async_session):
        async_session.add(make_task("t-1"))
        async_session.add_all(
            [
                make_call(status=CallStatus.PENDING, task_id="t-1", segment_id="represented-by-task"),
                make_call(status=CallStatus.PENDING, task_id=None, segment_id="taskless"),
                make_call(status=CallStatus.SUCCESS, task_id="t-1", segment_id="settled"),
            ]
        )
        await async_session.commit()

        page = await UsageRepository(async_session).list_records()

        assert {item["segment_id"] for item in page["items"]} == {"taskless", "settled"}
        assert page["total"] == 2

    async def test_task_type_comes_from_tasks_and_is_none_without_task(self, async_session):
        async_session.add(make_task("t-9", task_type="video_generation"))
        async_session.add_all(
            [
                make_call(task_id="t-9", segment_id="joined"),
                make_call(task_id="t-gone", segment_id="dangling"),
                make_call(task_id=None, segment_id="taskless"),
            ]
        )
        await async_session.commit()

        page = await UsageRepository(async_session).list_records()
        by_segment = {item["segment_id"]: item["task_type"] for item in page["items"]}

        assert by_segment == {"joined": "video_generation", "dangling": None, "taskless": None}

    async def test_user_id_not_projected(self, async_session):
        async_session.add(make_call())
        await async_session.commit()

        page = await UsageRepository(async_session).list_records()

        assert "user_id" not in page["items"][0]

    async def test_detail_only_fields_absent_from_list(self, async_session):
        async_session.add(make_call(prompt="full prompt", inputs={"voice": "v1"}, last_provider_response={"raw": 1}))
        await async_session.commit()

        item = (await UsageRepository(async_session).list_records())["items"][0]

        assert not {"prompt", "inputs", "last_provider_response"} & set(item)


class TestListRecordsFilters:
    async def test_multi_select_dimensions(self, async_session):
        async_session.add_all(
            [
                make_call(provider="ark", model="doubao", call_type="text", segment_id="S1"),
                make_call(provider="grok", model="grok-image", call_type="image", segment_id="S2"),
                make_call(provider="vidu", model="vidu-q1", call_type="video", segment_id="S3"),
            ]
        )
        await async_session.commit()
        repo = UsageRepository(async_session)

        by_provider = await repo.list_records(filters=UsageFilters(providers=("ark", "vidu")))
        by_model = await repo.list_records(filters=UsageFilters(models=("doubao", "grok-image")))
        by_media = await repo.list_records(filters=UsageFilters(media_types=("text", "image")))
        by_segment = await repo.list_records(segment_ids=("S2", "S3"))

        assert {item["provider"] for item in by_provider["items"]} == {"ark", "vidu"}
        assert {item["model"] for item in by_model["items"]} == {"doubao", "grok-image"}
        assert {item["media_type"] for item in by_media["items"]} == {"text", "image"}
        assert {item["segment_id"] for item in by_segment["items"]} == {"S2", "S3"}

    async def test_status_multi_select(self, async_session):
        async_session.add_all(
            [
                make_call(status=CallStatus.SUCCESS),
                make_call(status=CallStatus.FAILED),
                make_call(status=CallStatus.CANCELLED),
            ]
        )
        await async_session.commit()

        page = await UsageRepository(async_session).list_records(statuses=("failed", "cancelled"))

        assert {item["status"] for item in page["items"]} == {"failed", "cancelled"}
        assert page["total"] == 2

    async def test_empty_project_name_selects_trial_runs(self, async_session):
        async_session.add_all([make_call(project_name=""), make_call(project_name="demo")])
        await async_session.commit()
        repo = UsageRepository(async_session)

        trials = await repo.list_records(filters=UsageFilters(project_name=""))
        unfiltered = await repo.list_records()

        assert [item["project_name"] for item in trials["items"]] == [""]
        assert unfiltered["total"] == 2

    async def test_since_until_is_half_open_on_started_at(self, async_session):
        async_session.add_all(
            [
                make_call(started_at=BASE_TIME - timedelta(seconds=1), segment_id="before"),
                make_call(started_at=BASE_TIME, segment_id="at-since"),
                make_call(started_at=BASE_TIME + timedelta(hours=1), segment_id="at-until"),
            ]
        )
        await async_session.commit()

        page = await UsageRepository(async_session).list_records(
            filters=UsageFilters(since=BASE_TIME, until=BASE_TIME + timedelta(hours=1))
        )

        assert [item["segment_id"] for item in page["items"]] == ["at-since"]

    async def test_naive_bounds_are_read_as_utc(self, async_session):
        async_session.add(make_call(started_at=BASE_TIME))
        await async_session.commit()

        page = await UsageRepository(async_session).list_records(
            filters=UsageFilters(since=BASE_TIME.replace(tzinfo=None), until=None)
        )

        assert page["total"] == 1

    async def test_total_counts_filtered_rows_not_page(self, async_session):
        async_session.add_all([make_call(provider="ark") for _ in range(5)])
        async_session.add_all([make_call(provider="grok") for _ in range(3)])
        await async_session.commit()

        page = await UsageRepository(async_session).list_records(filters=UsageFilters(providers=("ark",)), limit=2)

        assert len(page["items"]) == 2
        assert page["total"] == 5


class TestGetRecord:
    async def test_detail_carries_prompt_inputs_and_provider_response(self, async_session):
        async_session.add(
            make_call(
                prompt="a very long prompt",
                inputs={"reference_images": [{"path": "a.png", "label": "角色", "role": "reference"}]},
                last_provider_response={"raw": "body"},
                task_id="t-3",
            )
        )
        async_session.add(make_task("t-3"))
        await async_session.commit()
        repo = UsageRepository(async_session)
        record_id = (await repo.list_records())["items"][0]["id"]

        detail = await repo.get_record(record_id)

        assert detail is not None
        assert detail["prompt"] == "a very long prompt"
        assert detail["inputs"]["reference_images"][0]["path"] == "a.png"
        assert detail["last_provider_response"] == {"raw": "body"}
        assert detail["task_type"] == "image_generation"
        assert "user_id" not in detail

    async def test_unknown_id_returns_none(self, async_session):
        assert await UsageRepository(async_session).get_record(999) is None

    async def test_pending_row_hidden_from_list_is_still_readable_by_id(self, async_session):
        async_session.add(make_task("t-5"))
        async_session.add(make_call(status=CallStatus.PENDING, task_id="t-5"))
        await async_session.commit()
        repo = UsageRepository(async_session)
        row_id = (await async_session.execute(select(ApiCall.id))).scalar_one()

        assert (await repo.list_records())["total"] == 0
        assert (await repo.get_record(row_id)) is not None

    async def test_timestamps_are_utc_iso(self, async_session):
        async_session.add(make_call(started_at=BASE_TIME, finished_at=BASE_TIME + timedelta(seconds=3)))
        await async_session.commit()
        repo = UsageRepository(async_session)
        item = (await repo.list_records())["items"][0]

        assert item["started_at"] == "2026-03-01T12:00:00+00:00"
        assert item["finished_at"] == "2026-03-01T12:00:03+00:00"

    async def test_scope_applies_to_page_count_and_detail(self, async_session):
        class ProjectScopedUsageRepository(UsageRepository):
            def _scope_query(self, stmt, model):
                return stmt.where(model.project_name == "visible")

        async_session.add_all(
            [
                make_call(project_name="visible", segment_id="in-scope"),
                make_call(project_name="hidden", segment_id="out-of-scope"),
            ]
        )
        await async_session.commit()
        repo = ProjectScopedUsageRepository(async_session)
        ids = {row.segment_id: row.id for row in (await async_session.execute(select(ApiCall))).scalars()}

        page = await repo.list_records()

        assert page["total"] == 1
        assert [item["segment_id"] for item in page["items"]] == ["in-scope"]
        assert await repo.get_record(ids["out-of-scope"]) is None


class SummaryProjectScopedUsageRepository(UsageRepository):
    """只看得到一个项目的仓储，用来验证汇总读接口的查询确实经过 ``_scope_query``。"""

    def _scope_query(self, stmt, model):
        return stmt.where(ApiCall.project_name == "visible")


class TestSummaryQueriesRespectScope:
    async def test_summary_rows_and_filter_options_are_scoped(self, async_session):
        seeder = UsageRepository(async_session)
        for project_name in ("visible", "hidden"):
            call_id = await seeder.start_call(
                project_name=project_name, call_type="image", model=f"{project_name}-model", provider="gemini"
            )
            await seeder.finish_call(call_id, status="success", settlement=SettlementInput())

        scoped = SummaryProjectScopedUsageRepository(async_session)
        rows = await scoped.fetch_summary_rows()
        options = await scoped.fetch_usage_filter_options()

        assert [row.project_name for row in rows] == ["visible"]
        assert options.projects == ["visible"]
        assert options.models == [("gemini", "visible-model")]

    async def test_pending_rows_stay_out_of_the_projection(self, async_session):
        repo = UsageRepository(async_session)
        done = await repo.start_call(project_name="demo", call_type="image", model="m")
        await repo.finish_call(done, status="success", settlement=SettlementInput())
        await repo.start_call(project_name="demo", call_type="image", model="m")

        rows = await repo.fetch_summary_rows()
        assert [row.id for row in rows] == [done]


class TestSettleInterruptedPendingCalls:
    """启动收口：没有存活任务的 pending 调用行按任务结局翻终态。"""

    async def _seed_task(self, async_session, task_id: str, status: str) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        async_session.add(
            Task(
                task_id=task_id,
                project_name="demo",
                task_type="video",
                media_type="video",
                resource_id="E1S01",
                status=status,
                queued_at=now,
                updated_at=now,
            )
        )
        await async_session.commit()

    async def _seed_pending_call(self, async_session, *, task_id: str | None = None) -> int:
        return await UsageRepository(async_session).start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.1-generate-preview",
            duration_seconds=8,
            task_id=task_id,
        )

    async def _row(self, async_session, call_id: int) -> ApiCall:
        return (await async_session.execute(select(ApiCall).where(ApiCall.id == call_id))).scalar_one()

    async def test_call_without_task_flips_failed_with_interrupted_code(self, async_session):
        call_id = await self._seed_pending_call(async_session)

        settled = await UsageRepository(async_session).settle_interrupted_pending_calls(
            taskless_started_before=utc_now()
        )

        assert [(s.call_id, s.project_name, s.status) for s in settled] == [(call_id, "demo", CallStatus.FAILED)]
        row = await self._row(async_session, call_id)
        assert row.status == "failed"
        assert row.error_code == "interrupted"
        assert row.error_params == {}
        assert row.cost_amount == 0.0
        assert row.finished_at is not None

    async def test_taskless_call_is_kept_when_excluded_but_task_bound_still_settles(self, async_session):
        """进程存活期间的重扫只收口绑定任务的行：无任务身份的调用可能正在跑，原样留 pending。"""
        taskless_id = await self._seed_pending_call(async_session)
        await self._seed_task(async_session, "t-done", "failed")
        bound_id = await self._seed_pending_call(async_session, task_id="t-done")

        settled = await UsageRepository(async_session).settle_interrupted_pending_calls(taskless_started_before=None)

        assert [s.call_id for s in settled] == [bound_id]
        assert (await self._row(async_session, taskless_id)).status == "pending"
        assert (await self._row(async_session, bound_id)).status == "failed"

    async def test_taskless_call_started_after_the_cutoff_is_left_pending(self, async_session):
        """进程启动后才发起的无任务调用还在本进程里跑：启动收口只收在启动时刻之前发起的。"""
        old_id = await self._seed_pending_call(async_session)
        cutoff = utc_now()
        await asyncio.sleep(0.001)
        new_id = await self._seed_pending_call(async_session)

        settled = await UsageRepository(async_session).settle_interrupted_pending_calls(taskless_started_before=cutoff)

        assert [s.call_id for s in settled] == [old_id]
        assert (await self._row(async_session, new_id)).status == "pending"

    async def test_call_of_cancelled_task_flips_cancelled_without_code(self, async_session):
        await self._seed_task(async_session, "t-cancelled", "cancelled")
        call_id = await self._seed_pending_call(async_session, task_id="t-cancelled")

        settled = await UsageRepository(async_session).settle_interrupted_pending_calls(
            taskless_started_before=utc_now()
        )

        assert [s.status for s in settled] == [CallStatus.CANCELLED]
        row = await self._row(async_session, call_id)
        assert row.status == "cancelled"
        assert row.error_code is None
        assert row.cost_amount == 0.0

    @pytest.mark.parametrize("task_status", ["failed", "succeeded"])
    async def test_call_of_other_terminal_task_flips_failed_without_code(self, async_session, task_status):
        await self._seed_task(async_session, "t-term", task_status)
        call_id = await self._seed_pending_call(async_session, task_id="t-term")

        await UsageRepository(async_session).settle_interrupted_pending_calls(taskless_started_before=utc_now())

        row = await self._row(async_session, call_id)
        assert row.status == "failed"
        assert row.error_code is None

    @pytest.mark.parametrize("task_status", ["queued", "running", "cancelling"])
    async def test_call_of_live_task_is_left_pending(self, async_session, task_status):
        await self._seed_task(async_session, "t-live", task_status)
        call_id = await self._seed_pending_call(async_session, task_id="t-live")

        settled = await UsageRepository(async_session).settle_interrupted_pending_calls(
            taskless_started_before=utc_now()
        )

        assert settled == []
        row = await self._row(async_session, call_id)
        assert row.status == "pending"
        assert row.finished_at is None

    async def test_call_of_missing_task_flips_failed_with_interrupted_code(self, async_session):
        call_id = await self._seed_pending_call(async_session, task_id="t-purged")

        await UsageRepository(async_session).settle_interrupted_pending_calls(taskless_started_before=utc_now())

        row = await self._row(async_session, call_id)
        assert row.status == "failed"
        assert row.error_code == "interrupted"

    async def test_terminal_rows_are_not_touched(self, async_session):
        repo = UsageRepository(async_session)
        call_id = await self._seed_pending_call(async_session)
        await repo.finish_call(call_id, status=CallStatus.SUCCESS, settlement=SettlementInput(cost_amount=1.5))

        settled = await repo.settle_interrupted_pending_calls(taskless_started_before=utc_now())

        assert settled == []
        row = await self._row(async_session, call_id)
        assert row.status == "success"
        assert row.cost_amount == pytest.approx(1.5)
