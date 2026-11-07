"""Direct ledger seam tests: accounting contexts / resume / Agent turns / union dispatch.

The characterization matrix (tests/test_accounting_characterization.py) locks generator-to-database
behavior end to end. These tests add CM contracts it misses: block-local call_id availability;
RuntimeError for missing success declaration; Exception settles failed (recognized errors also
record error_code + error_params), then rethrows; accounting failure preserves the original exception;
CancelledError settles cancelled, then rethrows.
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

from arcreel_market_core.video_backend_contract import ResumeExpiredError
from lib.agent.credential_tiers import AgentPricing
from lib.backends.http_status_errors import ArtifactDownloadError
from lib.backends.providers import CallPurpose, CallStatus
from lib.backends.text_backends.base import TextGenerationResult, merge_billed_usage
from lib.billing.ledger import Ledger, _settlement_from_result
from lib.billing.metering import Usage, token_usage
from lib.db.base import utc_now
from lib.db.models.api_call import ApiCall
from lib.db.repositories.usage_repo import AgentModelCall, UsageRepository

_HTTP_REQUEST = httpx.Request("POST", "https://provider.example/v1/videos")


def _download_error_from_status(status: int) -> ArtifactDownloadError:
    """Artifact-download failure status belongs to the wrapped download exception, classified through ``__cause__``."""
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
# Union dispatch: the only location for two semantic translations.
# ---------------------------------------------------------------------------


@dataclass
class _ImgResult:
    usage_tokens: int | None = 8
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None
    usage: Usage | None = None


@dataclass
class _AudioResult:
    text: str = "念" * 25_000
    input_tokens: int | None = None
    audio_output_tokens: int | None = None


@dataclass
class _VideoResult:
    usage_tokens: int | None = 0
    generate_audio: bool | None = False
    duration_seconds: int = 15


def _text_result(
    *, input_tokens: int | None = 100, output_tokens: int | None = 50, **cache_tokens: int
) -> TextGenerationResult:
    return TextGenerationResult(
        text="ok", provider="p", model="m", input_tokens=input_tokens, output_tokens=output_tokens, **cache_tokens
    )


class TestSettlementDispatch:
    def test_image_extracts_token_fields(self) -> None:
        s = _settlement_from_result("image", _ImgResult(usage_tokens=8))
        assert s.usage_tokens == 8

    def test_audio_reports_raw_text_and_records_character_count(self) -> None:
        # Translation 1: calculators count raw-text characters; usage_tokens records source-character count.
        s = _settlement_from_result("audio", _AudioResult(text="念" * 25_000))
        assert s.usage_tokens == 25_000
        assert [usage.text for usage in s.usage] == ["念" * 25_000]

    def test_video_transcribes_duration_to_billed(self) -> None:
        # Translation 2: actual backend billed duration → billed_duration_seconds, overriding request duration.
        s = _settlement_from_result("video", _VideoResult(duration_seconds=15, generate_audio=False))
        assert s.billed_duration_seconds == 15
        assert s.generate_audio is False

    def test_text_splits_total_input_into_non_overlapping_meters(self) -> None:
        s = _settlement_from_result(
            "text", _text_result(input_tokens=100, output_tokens=50, cache_read_tokens=30, cache_write_1h_tokens=20)
        )
        assert (s.input_tokens, s.output_tokens, s.cache_read_tokens, s.cache_write_1h_tokens) == (100, 50, 30, 20)
        assert [usage.quantities for usage in s.usage] == [
            {"uncached_input_tokens": 50, "cache_read_tokens": 30, "cache_write_1h_tokens": 20, "output_tokens": 50}
        ]

    def test_text_fallback_reports_one_usage_per_billed_request(self) -> None:
        # Discarded fallback requests are already billed; record usage/context tiers separately for every request.
        kept = _text_result(input_tokens=100, output_tokens=50)
        merge_billed_usage(kept, _text_result(input_tokens=70, output_tokens=10, cache_read_tokens=30))

        s = _settlement_from_result("text", kept)

        assert (s.input_tokens, s.output_tokens, s.cache_read_tokens) == (170, 60, 30)
        assert [usage.quantities for usage in s.usage] == [
            {"uncached_input_tokens": 100, "output_tokens": 50},
            {"cache_read_tokens": 30, "uncached_input_tokens": 40, "output_tokens": 10},
        ]

    def test_unknown_channel_raises(self) -> None:
        with pytest.raises(AssertionError, match="Expected code to be unreachable"):
            _settlement_from_result("bogus", object())


# ---------------------------------------------------------------------------
# Accounting context-manager contract.
# ---------------------------------------------------------------------------


class TestRecordBracket:
    async def test_call_id_available_in_block_and_success_flips_row(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        seen_call_id: int | None = None
        async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
            seen_call_id = call.call_id
            call.success(_text_result(input_tokens=100, output_tokens=50))

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
                pass  # Normal exit without a success declaration.

        # No success declaration → no finish; row stays pending, without success/failed transition.
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
        assert len(row.error_message) == 500  # Repository handles error-text truncation.

    async def test_cancellation_settles_cancelled_zero_cost_and_reraises(self, db_factory: async_sessionmaker) -> None:
        ledger = Ledger(session_factory=db_factory)
        caught = False
        try:
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise asyncio.CancelledError()
        except asyncio.CancelledError:
            caught = True

        assert caught, "expected CancelledError to propagate"

        # Cancellation settles zero-cost cancelled, without pending rows or failure counting.
        row = await _only_row(db_factory)
        assert row.status == "cancelled"
        assert row.cost_amount == 0.0
        assert row.finished_at is not None
        assert row.error_message is None

    async def test_cancel_during_success_settlement_lands_the_write_then_reraises(
        self, db_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancellation during successful settlement waits for real-cost persistence, then propagates; no pending row remains."""
        ledger = Ledger(session_factory=db_factory)
        outer = asyncio.current_task()
        assert outer is not None
        real_finish_call = UsageRepository.finish_call
        write_done = False

        async def _cancel_then_finish(repo: UsageRepository, *args: Any, **kwargs: Any) -> None:
            nonlocal write_done
            if kwargs.get("status") is CallStatus.SUCCESS:
                outer.cancel()
                # Deliver cancellation to the outer scope first, delay writing; premature rethrow would let assertions precede persistence.
                await asyncio.sleep(0.05)
            await real_finish_call(repo, *args, **kwargs)
            write_done = True

        monkeypatch.setattr(UsageRepository, "finish_call", _cancel_then_finish)

        with pytest.raises(asyncio.CancelledError):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
                call.success(_text_result(input_tokens=100, output_tokens=50))

        assert write_done, "取消重抛之前写入已经落地"
        row = await _only_row(db_factory)
        assert row.status == "success"
        assert row.input_tokens == 100
        assert row.output_tokens == 50
        assert row.finished_at is not None

    async def test_cancel_during_open_write_settles_cancelled_then_reraises(
        self, db_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancellation during opening (pending committed, ID not returned) waits for persistence, settles cancelled, then propagates."""
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)
        real_start_call = UsageRepository.start_call
        committed = asyncio.Event()
        release = asyncio.Event()

        async def _commit_then_hold(repo: UsageRepository, **kwargs: Any) -> int:
            call_id = await real_start_call(repo, **kwargs)
            committed.set()
            await release.wait()
            return call_id

        monkeypatch.setattr(UsageRepository, "start_call", _commit_then_hold)
        entered = False

        async def _run() -> None:
            nonlocal entered
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                entered = True

        task = asyncio.create_task(_run())
        await committed.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not entered, "取消发生在开账期间，括号块不应执行"
        row = await _only_row(db_factory)
        assert row.status == "cancelled"
        assert row.cost_amount == 0.0
        assert row.finished_at is not None
        assert [(project, [c["status"] for c in changes]) for project, changes in publisher.batches] == [
            ("demo", ["cancelled"])
        ]

    async def test_cancellation_settlement_failure_still_reraises_cancellation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Accounting-write failure in cancellation handling preserves cancellation propagation; pending rows await fallback cleanup."""

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

        monkeypatch.setattr("lib.billing.ledger.UsageRepository", _BoomOnCancelRepo)
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
        assert row.prompt == "p" * 900  # Persist full prompts without 500-character truncation.

    async def test_generate_audio_stays_null_unless_the_caller_declares_it(
        self, db_factory: async_sessionmaker
    ) -> None:
        """``generate_audio`` is video-only: undeclared stays unset, without defaulting non-video rows."""
        ledger = Ledger(session_factory=db_factory)
        async with ledger.record(project_name="demo", call_type="image", model="m", provider="gemini-aistudio") as call:
            call.success(_ImgResult())
        async with ledger.record(
            project_name="demo", call_type="video", model="m", provider="gemini-aistudio", generate_audio=False
        ) as call:
            call.success(_VideoResult(duration_seconds=6, generate_audio=False))

        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall).order_by(ApiCall.id))).scalars().all()
        assert [row.call_type for row in rows] == ["image", "video"]
        assert rows[0].generate_audio is None
        assert rows[1].generate_audio is False

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
        assert row.error_message == str(exc)  # Preserve raw text alongside machine codes.

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
        """Accounting-write failure during exception handling must not obscure the original exception."""

        class _BoomOnFailRepo:
            def __init__(self, _session: Any) -> None:
                pass

            async def start_call(self, **_kwargs: Any) -> int:
                return 1

            async def finish_call(self, _call_id: int, *, status: str, **_kwargs: Any) -> None:
                if status == "failed":
                    raise RuntimeError("ledger write down")

        monkeypatch.setattr("lib.billing.ledger.UsageRepository", _BoomOnFailRepo)

        @asynccontextmanager
        async def _dummy_session() -> AsyncGenerator[None]:
            yield None

        ledger = Ledger(session_factory=lambda: _dummy_session())

        # Original ValueError propagates; settlement RuntimeError is swallowed after logging.
        with pytest.raises(ValueError, match="original"):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic"):
                raise ValueError("original")

    async def test_success_write_failure_flips_failed_and_reraises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After call.success(), failed success-settlement writes must attempt failed settlement rather than leave pending forever."""

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

        monkeypatch.setattr("lib.billing.ledger.UsageRepository", _BoomOnSuccessRepo)

        @asynccontextmanager
        async def _dummy_session() -> AsyncGenerator[None]:
            yield None

        ledger = Ledger(session_factory=lambda: _dummy_session())

        with pytest.raises(RuntimeError, match="settlement write down"):
            async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
                call.success(_text_result(input_tokens=1, output_tokens=1))

        assert statuses == ["success", "failed"]


# ---------------------------------------------------------------------------
# Resume accounting / Agent turns: direct complete-semantic checks; characterization matrix covers end-to-end billing.
# ---------------------------------------------------------------------------


class TestResumeAndAgentTurn:
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
        assert row.duration_seconds == 6  # Actual backend billed duration overrides requested 8s.

    async def test_resume_failed_flips_pending_zero_cost(self, db_factory: async_sessionmaker) -> None:
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)

        affected = await ledger.resume_failed(call_id=call_id)
        assert affected == 1

        row = await _only_row(db_factory)
        assert row.status == "failed"
        assert row.cost_amount == 0.0

    async def test_resume_failed_lands_the_raw_message_of_the_expired_exception(
        self, db_factory: async_sessionmaker
    ) -> None:
        """Only callers hold expired-resume failure text; omitting it leaves a bare failure label."""
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)
        expired = ResumeExpiredError(job_id="job-1", provider="ark")

        affected = await ledger.resume_failed(call_id=call_id, failure=expired)

        assert affected == 1
        row = await _only_row(db_factory)
        assert row.status == "failed"
        assert row.error_message == str(expired)
        # Expiration has no HTTP/upstream code, so classification misses; readers display raw text.
        assert (row.error_code, row.error_params) == (None, None)

    async def test_resume_failed_classifies_the_exception_it_is_given(self, db_factory: async_sessionmaker) -> None:
        """Recognizable resume errors record codes consistently with accounting contexts."""
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)
        exc = httpx.HTTPStatusError(
            "429 response",
            request=_HTTP_REQUEST,
            response=httpx.Response(429, headers={"Retry-After": "30"}, request=_HTTP_REQUEST),
        )

        await ledger.resume_failed(call_id=call_id, failure=exc)

        row = await _only_row(db_factory)
        assert row.error_code == "rate_limited"
        assert row.error_params == {"retry_after_seconds": 30}
        assert row.error_message == str(exc)

    async def test_resume_failed_takes_a_plain_failure_text(self, db_factory: async_sessionmaker) -> None:
        """Pre-dispatch failure exits have raw text only, without exceptions; preserve it without codes."""
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)

        await ledger.resume_failed(call_id=call_id, failure="provider has no video capacity")

        row = await _only_row(db_factory)
        assert row.error_message == "provider has no video capacity"
        assert (row.error_code, row.error_params) == (None, None)

    async def test_resume_failed_without_failure_leaves_the_three_columns_empty(
        self, db_factory: async_sessionmaker
    ) -> None:
        call_id = await self._seed_pending_video(db_factory)
        ledger = Ledger(session_factory=db_factory)

        await ledger.resume_failed(call_id=call_id)

        row = await _only_row(db_factory)
        assert (row.error_message, row.error_code, row.error_params) == (None, None, None)

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

        # Second finalize matches 0 rows (idempotent WHERE status='pending'), without raising.
        affected = await ledger.resume_success(call_id=call_id, result=_VideoResult())
        assert affected == 0

    async def test_cancel_during_agent_turn_write_still_lands_the_rows(
        self, db_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Agent-turn cancellation waits for row/event persistence before propagating, preserving incurred cost."""
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)
        real_record = UsageRepository.record_agent_turn
        committed = asyncio.Event()
        release = asyncio.Event()

        async def _commit_then_hold(repo: UsageRepository, **kwargs: Any) -> list[int]:
            call_ids = await real_record(repo, **kwargs)
            committed.set()
            await release.wait()
            return call_ids

        monkeypatch.setattr(UsageRepository, "record_agent_turn", _commit_then_hold)
        task = asyncio.create_task(
            ledger.record_agent_turn(
                project_name="demo",
                session_id="s1",
                prompt="u",
                user_id="default",
                provider="agent-1",
                pricing=AgentPricing(credential_id=1),
                status=CallStatus.CANCELLED,
                calls=[_agent_call("claude-sonnet-4-6"), _agent_call("claude-haiku-4-5")],
            )
        )
        await committed.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall).order_by(ApiCall.id))).scalars().all()
        assert [(row.model, row.status, row.cost_amount) for row in rows] == [
            ("claude-sonnet-4-6", "cancelled", pytest.approx(3.0)),
            ("claude-haiku-4-5", "cancelled", pytest.approx(1.0)),
        ]
        assert [(project, [c["status"] for c in changes]) for project, changes in publisher.batches] == [
            ("demo", ["cancelled", "cancelled"])
        ]


def _agent_call(model: str) -> AgentModelCall:
    """One request per model per turn, one million uncached input tokens."""
    return AgentModelCall(
        model=model,
        requests=(Usage(quantities=token_usage(input_tokens=1_000_000, output_tokens=0)),),
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
    )


class _RecordingPublisher:
    """Capture (project_name, changes) published on the project-change bus."""

    def __init__(self) -> None:
        self.batches: list[tuple[str, list[dict[str, Any]]]] = []

    def __call__(self, project_name: str, changes: Any) -> None:
        self.batches.append((project_name, [dict(change) for change in changes]))

    @property
    def changes(self) -> list[dict[str, Any]]:
        return [change for _, changes in self.batches for change in changes]


class TestSettlementEvents:
    """Settlement emits usage_record / recorded for three terminal states, not pending or missing project names."""

    async def test_success_emits_recorded_change_with_project(self, db_factory: async_sessionmaker) -> None:
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
            assert publisher.batches == [], "pending 阶段不发事件"
            call.success(_text_result())

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
        """Endpoint trials have no project ownership and nowhere to send project events."""
        publisher = _RecordingPublisher()
        ledger = Ledger(session_factory=db_factory, publish_change=publisher)

        async with ledger.record(
            project_name="", call_type="text", model="m", provider="anthropic", purpose=CallPurpose.ENDPOINT_TRIAL
        ) as call:
            call.success(_text_result())

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
                project_name="demo",
                call_type="video",
                model="veo-3.1-generate-preview",
                provider="gemini",
                duration_seconds=8,
            )

        assert await ledger.resume_success(call_id=call_id, result=_VideoResult()) == 1
        assert await ledger.resume_success(call_id=call_id, result=_VideoResult()) == 0

        assert [(project, [c["status"] for c in changes]) for project, changes in publisher.batches] == [
            ("demo", ["success"])
        ]

    async def test_publisher_failure_does_not_break_settlement(self, db_factory: async_sessionmaker) -> None:
        """Events only optimize freshness: publication failure preserves accounting without surfacing errors to callers."""

        def _boom(_project_name: str, _changes: Any) -> None:
            raise RuntimeError("bus down")

        ledger = Ledger(session_factory=db_factory, publish_change=_boom)
        async with ledger.record(project_name="demo", call_type="text", model="m", provider="anthropic") as call:
            call.success(_text_result())

        assert (await _only_row(db_factory)).status == "success"


class TestSettleInterruptedCalls:
    """Startup closure settles pending rows without live tasks and emits settlement events grouped by project."""

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
