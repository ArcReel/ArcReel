"""ledger 直驱缝测试：直接驱动记账括号 / resume / backfill / union 分发。

特征化矩阵（tests/test_accounting_characterization.py）从生成器公开方法端到端锁落库行为；
本文件补齐矩阵覆盖不到的 CM 契约语义：块内 call_id 可用、漏调成功声明抛 RuntimeError、
Exception 翻 failed（可识别的失败类别另落 error_code + error_params）后重抛、记账失败不吞原异常、
CancelledError 结算 cancelled 后重抛。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from openai import BadRequestError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.db.base import utc_now
from lib.db.models.api_call import ApiCall
from lib.db.repositories.usage_repo import UsageRepository
from lib.http_status_errors import ArtifactDownloadError
from lib.ledger import Ledger, _settlement_from_result
from lib.providers import CallPurpose, CallStatus

_HTTP_REQUEST = httpx.Request("POST", "https://provider.example/v1/videos")


def _download_error_from_status(status: int) -> ArtifactDownloadError:
    """产物下载失败：状态码在它包裹的下载异常上，分类沿 ``__cause__`` 取。"""
    exc = ArtifactDownloadError(detail=f"{status} response")
    exc.__cause__ = httpx.HTTPStatusError(
        f"{status} response",
        request=_HTTP_REQUEST,
        response=httpx.Response(status, request=_HTTP_REQUEST),
    )
    return exc


async def _only_row(db_factory: async_sessionmaker) -> ApiCall:
    async with db_factory() as session:
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
        with pytest.raises(AssertionError, match="Expected code to be unreachable"):
            _settlement_from_result("bogus", object())


# ---------------------------------------------------------------------------
# 记账括号 CM 契约
# ---------------------------------------------------------------------------


class TestRecordBracket:
    async def test_call_id_available_in_block_and_success_flips_row(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        seen_call_id: int | None = None
        async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
            seen_call_id = call.call_id
            call.success(_TextResult(input_tokens=100, output_tokens=50))

        assert seen_call_id is not None
        assert seen_call_id > 0
        row = await _only_row(db_factory)
        assert row.status == "success"
        assert row.input_tokens == 100
        assert row.output_tokens == 50

    async def test_missing_success_declaration_raises_runtime_error(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        with pytest.raises(RuntimeError, match=re.escape("未调用 call.success")):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                pass  # 正常退出但未声明成功

        # 未声明成功 → 未 finish，行停在 pending（不翻 success/failed）
        row = await _only_row(db_factory)
        assert row.status == "pending"

    async def test_exception_flips_failed_and_reraises(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        with pytest.raises(ValueError, match="boom"):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise ValueError("boom" * 200)

        row = await _only_row(db_factory)
        assert row.status == "failed"
        assert row.error_message is not None
        assert len(row.error_message) == 500  # 错误信息截断由仓储承担

    async def test_cancellation_settles_cancelled_zero_cost_and_reraises(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        caught = False
        try:
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise asyncio.CancelledError()
        except asyncio.CancelledError:
            caught = True

        assert caught, "expected CancelledError to propagate"

        # 取消结算：零费用翻 cancelled，不留 pending 也不计入失败
        row = await _only_row(db_factory)
        assert row.status == "cancelled"
        assert row.cost_amount == 0.0
        assert row.finished_at is not None
        assert row.error_message is None

    async def test_cancel_during_success_settlement_lands_the_write_then_reraises(
        self, db_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """取消打在成功结算的写入上：写入照常落地（真实费用），取消随后传播，行不留 pending。"""
        ledger = Ledger(session_factory=db_factory)
        outer = asyncio.current_task()
        assert outer is not None
        real_finish_call = UsageRepository.finish_call
        write_done = False

        async def _cancel_then_finish(repo: UsageRepository, *args: Any, **kwargs: Any) -> None:
            nonlocal write_done
            if kwargs.get("status") is CallStatus.SUCCESS:
                outer.cancel()
                # 让取消先送达外层，再让写入拖一拍：外层若不等写入就重抛，下面的断言会先于写入完成。
                await asyncio.sleep(0.05)
            await real_finish_call(repo, *args, **kwargs)
            write_done = True

        monkeypatch.setattr(UsageRepository, "finish_call", _cancel_then_finish)

        with pytest.raises(asyncio.CancelledError):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
                call.success(_TextResult(input_tokens=100, output_tokens=50))

        assert write_done, "取消重抛之前写入已经落地"
        row = await _only_row(db_factory)
        assert row.status == "success"
        assert row.input_tokens == 100
        assert row.output_tokens == 50
        assert row.finished_at is not None

    async def test_cancellation_settlement_failure_still_reraises_cancellation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """取消分支记账写入自身抛异常时，取消必须照常冒泡（行留 pending 交兜底处理）。"""

        class _BoomOnCancelRepo:
            def __init__(self, _session: Any) -> None:
                pass

            async def start_call(self, **_kwargs: Any) -> int:
                return 1

            async def finish_call(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("db down")

        @asynccontextmanager
        async def _factory() -> AsyncGenerator[Any]:
            yield object()

        monkeypatch.setattr("lib.ledger.UsageRepository", _BoomOnCancelRepo)
        ledger = Ledger(session_factory=_factory)

        with pytest.raises(asyncio.CancelledError):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise asyncio.CancelledError()

    async def test_record_persists_task_purpose_session_and_inputs(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        inputs = {"reference_images": [{"path": "characters/婉儿.png", "label": "角色", "role": "array"}]}
        async with ledger.record(
            project_name="demo",
            call_type="image",
            model="m",
            provider="gemini-aistudio",
            prompt="p" * 900,
            task_id="T-1",
            purpose=CallPurpose.GENERATION_TASK,
            session_id="s1",
            inputs=inputs,
        ) as call:
            call.success(_ImgResult())

        row = await _only_row(db_factory)
        assert row.task_id == "T-1"
        assert row.purpose == "generation_task"
        assert row.session_id == "s1"
        assert row.inputs == inputs
        assert row.prompt == "p" * 900  # 提示词全文入库，不截 500 字

    @pytest.mark.parametrize(
        ("exc", "expected_code", "expected_params"),
        [
            pytest.param(
                httpx.HTTPStatusError(
                    "429 response",
                    request=_HTTP_REQUEST,
                    response=httpx.Response(429, headers={"Retry-After": "30"}, request=_HTTP_REQUEST),
                ),
                "rate_limited",
                {"retry_after_seconds": 30},
                id="rate_limited",
            ),
            pytest.param(
                BadRequestError(
                    "rejected",
                    response=httpx.Response(400, request=_HTTP_REQUEST),
                    body={"code": "content_policy_violation"},
                ),
                "content_policy",
                {},
                id="content_policy",
            ),
            pytest.param(TimeoutError("任务超时（600秒）"), "timeout", {}, id="timeout"),
            pytest.param(_download_error_from_status(404), "download_failed", {"status": 404}, id="download_failed"),
        ],
    )
    async def test_failure_lands_error_code_and_params_beside_the_raw_message(
        self,
        db_factory: async_sessionmaker,
        exc: Exception,
        expected_code: str,
        expected_params: dict[str, object],
    ) -> None:
        ledger = Ledger(session_factory=db_factory)
        with pytest.raises(type(exc)):
            async with ledger.record(project_name="demo", call_type="video", model="m", provider="kling"):
                raise exc

        row = await _only_row(db_factory)
        assert row.status == "failed"
        assert row.error_code == expected_code
        assert row.error_params == expected_params
        assert row.error_message == str(exc)  # 原文照落，与机器码并存

    async def test_unrecognised_failure_leaves_code_and_params_empty(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        with pytest.raises(ValueError, match="API 未返回图片"):
            async with ledger.record(project_name="demo", call_type="image", model="m", provider="gemini-aistudio"):
                raise ValueError("API 未返回图片")

        row = await _only_row(db_factory)
        assert row.status == "failed"
        assert row.error_code is None
        assert row.error_params is None
        assert row.error_message == "API 未返回图片"

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
        async def _dummy_session() -> AsyncGenerator[None]:
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
        async def _dummy_session() -> AsyncGenerator[None]:
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
    async def _seed_pending_video(self, db_factory: async_sessionmaker) -> int:
        from lib.db.repositories.usage_repo import UsageRepository

        async with db_factory() as session:
            return await UsageRepository(session).start_call(
                project_name="demo",
                call_type="video",
                model="veo-3.1-generate-preview",
                duration_seconds=8,
                provider="gemini",
            )

    async def test_resume_success_flips_pending_by_call_id(self, db_factory: async_sessionmaker) -> None:
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)

        affected = await ledger.resume_success(
            call_id=call_id, result=_VideoResult(duration_seconds=6, generate_audio=False)
        )
        assert affected == 1

        row = await _only_row(db_factory)
        assert row.status == "success"
        assert row.duration_seconds == 6  # backend 实际计费时长覆盖请求 8s

    async def test_resume_failed_flips_pending_zero_cost(self, db_factory: async_sessionmaker) -> None:
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)

        affected = await ledger.resume_failed(call_id=call_id)
        assert affected == 1

        row = await _only_row(db_factory)
        assert row.status == "failed"
        assert row.cost_amount == 0.0

    async def test_resume_cancelled_flips_pending_zero_cost(self, db_factory: async_sessionmaker) -> None:
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)

        affected = await ledger.resume_cancelled(call_id=call_id)

        assert affected == 1
        row = await _only_row(db_factory)
        assert row.status == "cancelled"
        assert row.cost_amount == 0.0

    async def test_resume_success_idempotent_on_terminal_row(self, db_factory: async_sessionmaker) -> None:
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)
        await ledger.resume_success(call_id=call_id, result=_VideoResult())

        # 二次 finalize 命中 0 行（WHERE status='pending' 幂等守卫），不抛异常
        affected = await ledger.resume_success(call_id=call_id, result=_VideoResult())
        assert affected == 0

    async def test_backfill_rejects_pending_status(self, db_factory: async_sessionmaker) -> None:
        """补录只写终态行：pending 会留下一条无人结算的行，进门就拒。"""
        ledger = Ledger(session_factory=db_factory)
        with pytest.raises(ValueError, match="pending"):
            await ledger.backfill(
                project_name="demo",
                call_type="text",
                model="m",
                provider="anthropic",
                prompt=None,
                user_id="default",
                status=CallStatus.PENDING,
                purpose=CallPurpose.ASSISTANT_SESSION,
            )

    async def test_backfill_writes_single_terminal_row(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        await ledger.backfill(
            project_name="demo",
            call_type="text",
            model="claude-sonnet-4",
            provider="anthropic",
            prompt="u",
            user_id="default",
            status=CallStatus.SUCCESS,
            input_tokens=1_000_000,
            output_tokens=200_000,
            usage_tokens=1_200_000,
            cost_amount=0.123,
            currency="USD",
            purpose=CallPurpose.ASSISTANT_SESSION,
            session_id="s1",
        )

        row = await _only_row(db_factory)
        assert row.status == "success"
        assert row.cost_amount == pytest.approx(0.123)  # SDK 直报费用优先
        assert row.usage_tokens == 1_200_000
        assert row.purpose == "assistant_session"
        assert row.session_id == "s1"


class _RecordingPublisher:
    """记下发上项目变更总线的 (project_name, changes)。"""

    def __init__(self) -> None:
        self.batches: list[tuple[str, list[dict[str, Any]]]] = []

    def __call__(self, project_name: str, changes: Any) -> None:
        self.batches.append((project_name, [dict(change) for change in changes]))

    @property
    def changes(self) -> list[dict[str, Any]]:
        return [change for _, changes in self.batches for change in changes]


class TestSettlementEvents:
    """结算即项目事件：usage_record / recorded 随三种终态发出，pending 与无项目名不发。"""

    async def test_success_emits_recorded_change_with_project(self, db_factory: async_sessionmaker) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
            assert publisher.batches == [], "pending 阶段不发事件"
            call.success(_TextResult())

        row = await _only_row(db_factory)
        assert publisher.batches == [
            (
                "demo",
                [
                    {
                        "entity_type": "usage_record",
                        "action": "recorded",
                        "entity_id": str(row.id),
                        "label": str(row.id),
                        "focus": None,
                        "important": False,
                        "status": "success",
                    }
                ],
            )
        ]

    async def test_failure_emits_recorded_change(self, db_factory: async_sessionmaker) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        with pytest.raises(ValueError, match="boom"):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise ValueError("boom")

        assert [(project, [c["status"] for c in changes]) for project, changes in publisher.batches] == [
            ("demo", ["failed"])
        ]

    async def test_cancellation_emits_recorded_change(self, db_factory: async_sessionmaker) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        with pytest.raises(asyncio.CancelledError):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise asyncio.CancelledError()

        assert [(project, [c["status"] for c in changes]) for project, changes in publisher.batches] == [
            ("demo", ["cancelled"])
        ]

    async def test_row_left_pending_emits_nothing(self, db_factory: async_sessionmaker) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        with pytest.raises(RuntimeError, match=re.escape("未调用 call.success")):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                pass

        assert (await _only_row(db_factory)).status == "pending"
        assert publisher.batches == []

    async def test_call_without_project_name_emits_nothing(self, db_factory: async_sessionmaker) -> None:
        """端点试跑没有项目归属，事件无处可送。"""
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        async with ledger.record(
            project_name="", call_type="text", model="m", provider="anthropic", purpose=CallPurpose.ENDPOINT_TRIAL
        ) as call:
            call.success(_TextResult())

        assert (await _only_row(db_factory)).status == "success"
        assert publisher.batches == []

    async def test_resume_emits_once_and_stays_silent_on_idempotent_replay(
        self, db_factory: async_sessionmaker
    ) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)
        async with db_factory() as session:
            from lib.db.repositories.usage_repo import UsageRepository

            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="veo-3.1-generate-preview", duration_seconds=8
            )

        assert await ledger.resume_success(call_id=call_id, result=_VideoResult()) == 1
        assert await ledger.resume_success(call_id=call_id, result=_VideoResult()) == 0

        assert [(project, [c["status"] for c in changes]) for project, changes in publisher.batches] == [
            ("demo", ["success"])
        ]

    async def test_backfill_emits_recorded_change(self, db_factory: async_sessionmaker) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        await ledger.backfill(
            project_name="demo",
            call_type="text",
            model="claude-sonnet-4",
            provider="anthropic",
            prompt="u",
            user_id="default",
            status=CallStatus.SUCCESS,
            cost_amount=0.1,
        )

        assert [(project, [c["status"] for c in changes]) for project, changes in publisher.batches] == [
            ("demo", ["success"])
        ]

    async def test_publisher_failure_does_not_break_settlement(self, db_factory: async_sessionmaker) -> None:
        """事件只是实时性优化：发布炸了，账照样落，调用方看不见异常。"""

        def _boom(_project_name: str, _changes: Any) -> None:
            raise RuntimeError("bus down")

        ledger = Ledger(session_factory=db_factory, publish_change=_boom)
        async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
            call.success(_TextResult())

        assert (await _only_row(db_factory)).status == "success"


class TestSettleInterruptedCalls:
    """启动收口：翻掉无存活任务的 pending 行，并按项目分组发结算事件。"""

    async def _seed_pending(self, db_factory: async_sessionmaker, *, project_name: str) -> int:
        from lib.db.repositories.usage_repo import UsageRepository

        async with db_factory() as session:
            return await UsageRepository(session).start_call(
                project_name=project_name, call_type="text", model="m", provider="anthropic"
            )

    async def test_settles_orphan_rows_and_emits_one_batch_per_project(self, db_factory: async_sessionmaker) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)
        first = await self._seed_pending(db_factory, project_name="demo")
        second = await self._seed_pending(db_factory, project_name="demo")
        other = await self._seed_pending(db_factory, project_name="other")

        assert await ledger.settle_interrupted_calls(taskless_started_before=utc_now()) == 3

        async with db_factory() as session:
            rows = {row.id: row for row in (await session.execute(select(ApiCall))).scalars().all()}
        assert [rows[call_id].status for call_id in (first, second, other)] == ["failed"] * 3
        assert rows[first].error_code == "interrupted"

        assert sorted((project, len(changes)) for project, changes in publisher.batches) == [
            ("demo", 2),
            ("other", 1),
        ]

    async def test_no_orphan_rows_emits_nothing(self, db_factory: async_sessionmaker) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        assert await ledger.settle_interrupted_calls(taskless_started_before=utc_now()) == 0
        assert publisher.batches == []
