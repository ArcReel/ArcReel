"""ledger 直驱缝测试：直接驱动记账括号 / resume / backfill / union 分发。

特征化矩阵（tests/test_accounting_characterization.py）从生成器公开方法端到端锁落库行为；
本文件补齐矩阵覆盖不到的 CM 契约语义：块内 call_id 可用、漏调成功声明抛 RuntimeError、
Exception 翻 failed 后重抛、记账失败不吞原异常、CancelledError 穿透留 pending。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.api_call import ApiCall
from lib.ledger import Ledger, _settlement_from_result


@pytest.fixture
async def factory() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _only_row(factory: async_sessionmaker) -> ApiCall:
    async with factory() as session:
        rows = (await session.execute(select(ApiCall))).scalars().all()
    assert len(rows) == 1, f"期望恰好 1 行，实际 {len(rows)}"
    return rows[0]


# ---------------------------------------------------------------------------
# union 分发：两处语义转写的唯一落点
# ---------------------------------------------------------------------------


@dataclass
class _ImgResult:
    usage_tokens: int | None = 8
    quality: str | None = "high"
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None


@dataclass
class _AudioResult:
    characters: int = 25_000


@dataclass
class _VideoResult:
    usage_tokens: int | None = 0
    generate_audio: bool | None = False
    duration_seconds: int = 15


@dataclass
class _TextResult:
    input_tokens: int | None = 100
    output_tokens: int | None = 50


class TestSettlementDispatch:
    def test_image_extracts_token_and_quality_fields(self) -> None:
        s = _settlement_from_result("image", _ImgResult(usage_tokens=8, quality="high"))
        assert s.usage_tokens == 8
        assert s.quality == "high"

    def test_audio_transcribes_characters_to_usage_tokens(self) -> None:
        # 语义转写①：合成字符数 → 计费 token
        s = _settlement_from_result("audio", _AudioResult(characters=25_000))
        assert s.usage_tokens == 25_000

    def test_video_transcribes_duration_to_billed_and_carries_service_tier(self) -> None:
        # 语义转写②：backend 实际计费时长 → billed_duration_seconds（覆盖请求时长）
        s = _settlement_from_result(
            "video", _VideoResult(duration_seconds=15, generate_audio=False), service_tier="pro"
        )
        assert s.billed_duration_seconds == 15
        assert s.generate_audio is False
        assert s.service_tier == "pro"

    def test_text_extracts_input_output_tokens(self) -> None:
        s = _settlement_from_result("text", _TextResult(input_tokens=100, output_tokens=50))
        assert s.input_tokens == 100
        assert s.output_tokens == 50

    def test_unknown_channel_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown ledger channel"):
            _settlement_from_result("bogus", object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 记账括号 CM 契约
# ---------------------------------------------------------------------------


class TestRecordBracket:
    async def test_call_id_available_in_block_and_success_flips_row(self, factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=factory)
        seen_call_id: int | None = None
        async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
            seen_call_id = call.call_id
            call.success(_TextResult(input_tokens=100, output_tokens=50))

        assert seen_call_id is not None and seen_call_id > 0
        row = await _only_row(factory)
        assert row.status == "success"
        assert row.input_tokens == 100
        assert row.output_tokens == 50

    async def test_missing_success_declaration_raises_runtime_error(self, factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=factory)
        with pytest.raises(RuntimeError, match="未调用 call.success"):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                pass  # 正常退出但未声明成功

        # 未声明成功 → 未 finish，行停在 pending（不翻 success/failed）
        row = await _only_row(factory)
        assert row.status == "pending"

    async def test_exception_flips_failed_and_reraises(self, factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=factory)
        with pytest.raises(ValueError, match="boom"):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise ValueError("boom" * 200)

        row = await _only_row(factory)
        assert row.status == "failed"
        assert row.error_message is not None
        assert len(row.error_message) == 500  # 错误信息截断由仓储承担

    async def test_cancellation_passes_through_leaving_pending(self, factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=factory)
        caught = False
        try:
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise asyncio.CancelledError()
        except asyncio.CancelledError:
            caught = True

        assert caught, "expected CancelledError to propagate"

        # 穿透不记账：行停在 pending，不翻 failed
        row = await _only_row(factory)
        assert row.status == "pending"

    async def test_accounting_failure_does_not_swallow_original_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """失败分支记账写入自身抛异常时，必须让原异常照常冒泡（不被记账错误遮蔽）。"""

        class _BoomOnFailRepo:
            def __init__(self, _session: Any) -> None:
                pass

            async def start_call(self, **_kwargs: Any) -> int:
                return 1

            async def finish_call(self, _call_id: int, *, status: str, **_kwargs: Any) -> None:
                if status == "failed":
                    raise RuntimeError("ledger write down")

        monkeypatch.setattr("lib.ledger.UsageRepository", _BoomOnFailRepo)

        @asynccontextmanager
        async def _dummy_session() -> AsyncIterator[None]:
            yield None

        ledger = Ledger(session_factory=lambda: _dummy_session())

        # 原异常是 ValueError；记账失败分支的 RuntimeError 被吞（仅记日志），ValueError 冒泡
        with pytest.raises(ValueError, match="original"):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise ValueError("original")

    async def test_success_write_failure_flips_failed_and_reraises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """call.success() 已声明，但成功结算写入本身抛异常时，须尝试翻 failed 而非永久留 pending。"""

        statuses: list[str] = []

        class _BoomOnSuccessRepo:
            def __init__(self, _session: Any) -> None:
                pass

            async def start_call(self, **_kwargs: Any) -> int:
                return 1

            async def finish_call(self, _call_id: int, *, status: str, **_kwargs: Any) -> None:
                statuses.append(status)
                if status == "success":
                    raise RuntimeError("settlement write down")

        monkeypatch.setattr("lib.ledger.UsageRepository", _BoomOnSuccessRepo)

        @asynccontextmanager
        async def _dummy_session() -> AsyncIterator[None]:
            yield None

        ledger = Ledger(session_factory=lambda: _dummy_session())

        with pytest.raises(RuntimeError, match="settlement write down"):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
                call.success(_TextResult(input_tokens=1, output_tokens=1))

        assert statuses == ["success", "failed"]


# ---------------------------------------------------------------------------
# resume 补账 / 事后补录：语义齐备的直驱确认（端到端计费口径见特征化矩阵）
# ---------------------------------------------------------------------------


class TestResumeAndBackfill:
    async def _seed_pending_video(self, factory: async_sessionmaker) -> int:
        from lib.db.repositories.usage_repo import UsageRepository

        async with factory() as session:
            return await UsageRepository(session).start_call(
                project_name="demo",
                call_type="video",
                model="veo-3.1-generate-preview",
                duration_seconds=8,
                provider="gemini",
            )

    async def test_resume_success_flips_pending_by_call_id(self, factory: async_sessionmaker) -> None:
        call_id = await self._seed_pending_video(factory)
        ledger = Ledger(session_factory=factory)

        affected = await ledger.resume_success(
            call_id=call_id, result=_VideoResult(duration_seconds=6, generate_audio=False)
        )
        assert affected == 1

        row = await _only_row(factory)
        assert row.status == "success"
        assert row.duration_seconds == 6  # backend 实际计费时长覆盖请求 8s

    async def test_resume_failed_flips_pending_zero_cost(self, factory: async_sessionmaker) -> None:
        call_id = await self._seed_pending_video(factory)
        ledger = Ledger(session_factory=factory)

        affected = await ledger.resume_failed(call_id=call_id)
        assert affected == 1

        row = await _only_row(factory)
        assert row.status == "failed"
        assert row.cost_amount == 0.0

    async def test_resume_success_idempotent_on_terminal_row(self, factory: async_sessionmaker) -> None:
        call_id = await self._seed_pending_video(factory)
        ledger = Ledger(session_factory=factory)
        await ledger.resume_success(call_id=call_id, result=_VideoResult())

        # 二次 finalize 命中 0 行（WHERE status='pending' 幂等守卫），不抛异常
        affected = await ledger.resume_success(call_id=call_id, result=_VideoResult())
        assert affected == 0

    async def test_backfill_writes_single_terminal_row(self, factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=factory)
        await ledger.backfill(
            project_name="demo",
            call_type="text",
            model="claude-sonnet-4",
            provider="anthropic",
            prompt="u",
            user_id="default",
            status="success",
            input_tokens=1_000_000,
            output_tokens=200_000,
            usage_tokens=1_200_000,
            cost_amount=0.123,
            currency="USD",
        )

        row = await _only_row(factory)
        assert row.status == "success"
        assert row.cost_amount == pytest.approx(0.123)  # SDK 直报费用优先
        assert row.usage_tokens == 1_200_000
