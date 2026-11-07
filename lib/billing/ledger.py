"""Accounting ledger: three channels encapsulating API-call persistence.

Three public channels match real accounting forms:

1. **Accounting scope** (``record`` async context manager) for image/audio/video/text generation.
   Enter persists pending rows and exposes ``call_id`` (tasks associate via ``api_calls.task_id``).
   Explicit ``call.success(result)`` supplies backend results. ``Exception`` automatically marks failed,
   storing truncated ``error_message`` plus recognized ``error_code`` / ``error_params``
   (:mod:`lib.billing.call_failure`), then reraises unchanged; accounting failures never swallow originals. ``CancelledError`` settles
   cancelled at zero cost, then reraises unchanged; normal exits without declared success raise ``RuntimeError``.
2. **Resume accounting** (``resume_success`` / ``resume_failed`` / ``resume_cancelled``) precisely finalizes pending by ``call_id``.
   Repository ``WHERE status='pending'`` guards idempotency; finalize exceptions propagate to
   worker finally. ``resume_failed`` accepts exceptions/raw failure text, sharing
   :mod:`lib.billing.call_failure` encoding into persisted triples.
3. **Agent turns** (``record_agent_turn``) write terminal rows per model/turn; repositories price through connection-time
   Agent credentials/catalogs without caller-visible pending intermediates.

Successful backend-result union dispatch centralizes in ``_settlement_from_result``: text/audio usage converts here into
contract measurements for catalog settlement, alongside video's actual billing-duration semantics.
Success call sites never extract individual fields. Video metering depends on sent requests, obtained by callers from channels and submitted with results.

After persistence, publish ``usage_record / recorded`` project changes through ``project_change_hints`` batch buses,
sharing terminal-task SSE, refreshing UI usage without polling. Project-less endpoint trials/pending phases do not publish.
Inject ``publish_change`` with production defaults. Startup ``settle_interrupted_calls``
finalizes crash-left pending rows without live tasks.

Retain ``session_factory`` injection: ``Ledger(session_factory=...)`` covers three production integrations
(MediaGenerator / TextGenerator / SessionManager) and in-memory test replacements. Writes directly use
``UsageRepository``, without usage pass-through layers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, assert_never

from lib.agent.credential_tiers import AgentPricing
from lib.backends.providers import CallPurpose, CallStatus, CallType
from lib.billing.call_failure import CallFailure, classify_call_failure
from lib.billing.metering import Usage, token_usage
from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.usage_repo import AgentModelCall, SettlementInput, UsageRepository
from lib.project.project_change_hints import emit_project_change_batch

logger = logging.getLogger(__name__)

#: Accounting settlement project-change entities, distinct from project entities/task terminals.
USAGE_RECORD_ENTITY_TYPE = "usage_record"
USAGE_RECORD_ACTION = "recorded"

#: Settlement publisher, default project-change batch buses sharing terminal-task SSE; tests inject substitutes.
ProjectChangePublisher = Callable[[str, Sequence[dict[str, Any]]], None]


def build_usage_record_change(call_id: int, status: CallStatus) -> dict[str, Any]:
    """Convert settlements into project-change dicts.

    Like terminal-task events, **refresh signals**, not entity changes: ``important=False`` + ``focus=None``;
    consumers reload usage only, without notifications/focus navigation. ``status`` identifies
    success/failure without requerying.
    """
    return {
        "entity_type": USAGE_RECORD_ENTITY_TYPE,
        "action": USAGE_RECORD_ACTION,
        "entity_id": str(call_id),
        # label is diagnostic only, excluded from notifications by important=False.
        "label": str(call_id),
        "focus": None,
        "important": False,
        "status": str(status),
    }


def _settlement_from_result(call_type: CallType, result: Any, *, usage: Usage | None = None) -> SettlementInput:
    """Success union dispatch: extract billing dimensions by call_type from backend results.

    Sole semantic conversion point:
    - images report contract measurements/dimensions already, passed unchanged;
    - text total input/three caches → nonoverlapping token measurements (uncached = total − reads − two writes),
      separate usage for each fallback request, individually context-tiered for pricing;
    - audio raw text → metering ``text`` (calculator counts per price tables), also record characters in ``usage_tokens``;
      token-priced TTS also includes input/output-audio tokens;
    - video ``duration_seconds`` (actual billed/generated backend durations) →
      ``billed_duration_seconds``, replacing start_call request durations; ``usage`` is channel-reported
      contract metering for this request.

    Four result types are structurally independent without common bases; explicitly dispatch by caller-known ``call_type``.
    """
    if call_type == "image":
        return SettlementInput(
            usage_tokens=result.usage_tokens,
            image_input_tokens=result.image_input_tokens,
            image_output_tokens=result.image_output_tokens,
            text_input_tokens=result.text_input_tokens,
            text_output_tokens=result.text_output_tokens,
            # Channels report contract usage; custom wrappers supply generic metering if wrapped channels omit it.
            usage=(result.usage,) if result.usage is not None else (),
        )
    if call_type == "audio":
        return SettlementInput(
            usage_tokens=len(result.text),
            input_tokens=result.input_tokens,
            output_tokens=result.audio_output_tokens,
            usage=(
                Usage(
                    quantities=token_usage(
                        input_tokens=result.input_tokens,
                        output_tokens=result.audio_output_tokens,
                        output_meter="audio_output_tokens",
                    ),
                    text=result.text,
                ),
            ),
        )
    if call_type == "video":
        return SettlementInput(
            usage_tokens=result.usage_tokens,
            generate_audio=result.generate_audio,
            billed_duration_seconds=result.duration_seconds,
            usage=(usage,) if usage is not None else (),
        )
    if call_type == "text":
        return SettlementInput(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_5m_tokens=result.cache_write_5m_tokens,
            cache_write_1h_tokens=result.cache_write_1h_tokens,
            # Entirely untracked measurements still price one zero-usage request.
            usage=tuple(
                Usage(
                    quantities=token_usage(
                        input_tokens=request.input_tokens,
                        output_tokens=request.output_tokens,
                        cache_read_tokens=request.cache_read_tokens,
                        cache_write_5m_tokens=request.cache_write_5m_tokens,
                        cache_write_1h_tokens=request.cache_write_1h_tokens,
                    )
                )
                for request in result.request_tokens()
            )
            or (Usage(),),
        )
    assert_never(call_type)


class LedgerCall:
    """Accounting-scope handle: expose ``call_id`` and accept backend results through ``success(result)``."""

    def __init__(self, call_id: int, *, call_type: CallType) -> None:
        self.call_id = call_id
        self._call_type: CallType = call_type
        self._settlement: SettlementInput | None = None

    def success(self, result: Any, *, usage: Usage | None = None) -> None:
        """Declare success and submit backend results, dispatching unions here and settling on scope exit.

        ``usage`` is video-only, channel-reported request measurements under usage contracts.
        """
        self._settlement = _settlement_from_result(self._call_type, result, usage=usage)


class Ledger:
    def __init__(self, *, session_factory=None, publish_change: ProjectChangePublisher = emit_project_change_batch):
        self._session_factory = session_factory or safe_session_factory
        self._publish_change = publish_change

    def _publish(self, project_name: str, changes: Sequence[dict[str, Any]]) -> None:
        """Publish settlement batches; project-less endpoint trials do not publish. Publication failures never propagate: accounting persisted,
        events optimize realtime only, with consumer polling fallback."""
        if not project_name or not changes:
            return
        try:
            self._publish_change(project_name, changes)
        except Exception:
            logger.exception("发送记账结算项目事件失败 project=%s count=%d", project_name, len(changes))

    def _emit_recorded(self, project_name: str, call_id: int, status: CallStatus) -> None:
        """Publish ``usage_record / recorded`` after settlement persistence, never pending phases."""
        self._publish(project_name, [build_usage_record_change(call_id, status)])

    @asynccontextmanager
    async def record(
        self,
        *,
        project_name: str,
        call_type: CallType,
        model: str,
        provider: str,
        prompt: str | None = None,
        resolution: str | None = None,
        duration_seconds: int | None = None,
        aspect_ratio: str | None = None,
        generate_audio: bool | None = None,
        user_id: str = DEFAULT_USER_ID,
        segment_id: str | None = None,
        output_path: str | None = None,
        task_id: str | None = None,
        purpose: CallPurpose | None = None,
        session_id: str | None = None,
        inputs: object | None = None,
    ) -> AsyncGenerator[LedgerCall]:
        """Accounting scopes persist pending on entry; ``call.success(result)`` declares success.

        Exit: ``CancelledError`` settles zero-cost cancelled then reraises unchanged; ``Exception`` marks
        failed then reraises unchanged; normal exits without success raise ``RuntimeError``. Declared success settles backend
        results as success. Cancellation during opening writes also waits for pending persistence, settles cancelled, then reraises.
        """
        call_id = await self._open_call(
            project_name=project_name,
            call_type=call_type,
            model=model,
            prompt=prompt,
            resolution=resolution,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            provider=provider,
            user_id=user_id,
            segment_id=segment_id,
            task_id=task_id,
            purpose=purpose,
            session_id=session_id,
            inputs=inputs,
        )
        call = LedgerCall(call_id, call_type=call_type)
        try:
            yield call
        except asyncio.CancelledError:
            # User cancellations settle cancelled/zero then reraise, without pending or failure-rate contribution.
            # CancelledError inherits BaseException in 3.11+, outside except Exception; explicitly
            # state this contract rather than rely on inheritance side effects.
            if await self._finish_cancelled(call_id):
                self._emit_recorded(project_name, call_id, CallStatus.CANCELLED)
            raise
        except Exception as exc:
            # Automatically mark failed then reraise; repositories truncate errors.
            if await self._finish_failed(call_id, exc):
                self._emit_recorded(project_name, call_id, CallStatus.FAILED)
            raise
        else:
            if call._settlement is None:
                raise RuntimeError(f"ledger.record(call_type={call_type!r}) 正常退出但未调用 call.success(result)")
            try:
                await self._finish_success(call_id, call._settlement, output_path=output_path)
            except asyncio.CancelledError:
                # Cancellation during success settlement: results/costs already exist; shield persistence,
                # publish normally, then propagate cancellation. Real expenses cannot become zero-cost cancellations.
                self._emit_recorded(project_name, call_id, CallStatus.SUCCESS)
                raise
            except Exception as exc:
                # Success-write failures attempt failed finalization before reraising, avoiding permanent pending.
                if await self._finish_failed(call_id, exc):
                    self._emit_recorded(project_name, call_id, CallStatus.FAILED)
                raise
            self._emit_recorded(project_name, call_id, CallStatus.SUCCESS)

    async def pending_call_id_for_task(self, task_id: str) -> int | None:
        """Find task pending-call ids for resume/dispatch-terminal accounting."""
        async with self._session_factory() as session:
            return await UsageRepository(session).find_pending_call_id_by_task_id(task_id)

    async def resume_success(self, *, call_id: int, result: Any, usage: Usage | None = None) -> int:
        """Finalize resume pending → success precisely by call_id; return idempotent affected counts 0/1.

        ``usage`` is channel-reported resume metering. Finalize exceptions propagate to worker finally,
        avoiding permanently pending ApiCall rows.
        """
        settlement = _settlement_from_result("video", result, usage=usage)
        return await self._finalize(call_id=call_id, status=CallStatus.SUCCESS, settlement=settlement)

    async def resume_failed(self, *, call_id: int, failure: BaseException | str | None = None) -> int:
        """Finalize expired/failed resume pending → failed, zero cost without rebilling, idempotent 0/1.

        ``failure`` accepts caller exceptions/raw text, persisted through ``classify_call_failure``:
        ``error_message`` retains originals, recognized types add ``error_code`` / ``error_params``. Without
        failure information, all three columns remain empty, leaving failure status without phrases/raw text.
        """
        return await self._finalize(
            call_id=call_id,
            status=CallStatus.FAILED,
            settlement=SettlementInput(cost_amount=0.0),
            failure=None if failure is None else classify_call_failure(failure),
        )

    async def resume_cancelled(self, *, call_id: int) -> int:
        """Finalize resume pending → cancelled, zero cost without rebilling, idempotent 0/1."""
        return await self._finalize(
            call_id=call_id, status=CallStatus.CANCELLED, settlement=SettlementInput(cost_amount=0.0)
        )

    async def record_provider_response(self, *, call_id: int, body: object) -> None:
        """Overwrite persisted latest received response bodies for provider calls."""
        async with self._session_factory() as session:
            await UsageRepository(session).update_last_provider_response(call_id, body)

    async def record_agent_turn(
        self,
        *,
        project_name: str,
        session_id: str,
        prompt: str | None,
        user_id: str,
        provider: str,
        pricing: AgentPricing,
        status: CallStatus,
        calls: Sequence[AgentModelCall],
    ) -> None:
        """Agent turns: terminal rows per model, priced through connection-time credential multipliers/entry bindings (docs/adr/0102).

        Skip turns without usage. Entire writes resist cancellation: wait for rows/events before propagating cancellation.
        """
        if not calls:
            return
        await self._settle(
            self._write_agent_turn(
                project_name=project_name,
                session_id=session_id,
                prompt=prompt,
                user_id=user_id,
                provider=provider,
                pricing=pricing,
                status=status,
                calls=list(calls),
            )
        )

    async def _write_agent_turn(self, *, project_name: str, status: CallStatus, **kwargs: Any) -> None:
        async with self._session_factory() as session:
            call_ids = await UsageRepository(session).record_agent_turn(
                project_name=project_name, status=status, **kwargs
            )
        self._publish(project_name, [build_usage_record_change(call_id, status) for call_id in call_ids])

    async def settle_interrupted_calls(self, *, taskless_started_before: datetime | None) -> int:
        """Startup finalization of pending calls without live tasks; return affected counts.

        Crashes otherwise leave opened/unsettled calls permanently pending. Repositories own routing/zero-cost rules and
        ``taskless_started_before`` semantics (``UsageRepository.settle_interrupted_pending_calls``).
        Here group/publish project settlements so open UIs immediately see finalized rows.
        """
        async with self._session_factory() as session:
            settled = await UsageRepository(session).settle_interrupted_pending_calls(
                taskless_started_before=taskless_started_before
            )

        by_project: dict[str, list[dict[str, Any]]] = {}
        for item in settled:
            if not item.project_name:
                continue
            by_project.setdefault(item.project_name, []).append(build_usage_record_change(item.call_id, item.status))
        for project_name, changes in by_project.items():
            self._publish(project_name, changes)
        return len(settled)

    async def _start_call(self, **kwargs: Any) -> int:
        async with self._session_factory() as session:
            return await UsageRepository(session).start_call(**kwargs)

    async def _open_call(self, *, project_name: str, **kwargs: Any) -> int:
        """Opening accounting writes resist cancellation: first wait for pending persistence/ids, settle cancelled,
        then propagate cancellation.

        Repositories commit before reading ids; cancellation between them leaves persisted rows before scopes enter ``try``,
        without caller ids, requiring startup recovery. Shielding matches ``_settle``.
        """
        opening = asyncio.ensure_future(self._start_call(project_name=project_name, **kwargs))
        try:
            return await asyncio.shield(opening)
        except asyncio.CancelledError:
            call_id = await asyncio.shield(opening)
            if await self._finish_cancelled(call_id):
                self._emit_recorded(project_name, call_id, CallStatus.CANCELLED)
            raise

    async def _settle[T](self, write: Coroutine[Any, Any, T]) -> T:
        """Settlement writes resist cancellation: finish persistence before propagating cancellation.

        All three scope terminal writes pass here. Independent tasks perform writes; outer cancellation interrupts waiting,
        not writing. Reraise only after completion so cancellation timing cannot strand pending rows.
        """
        pending = asyncio.ensure_future(write)
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            if not pending.done():
                await asyncio.shield(pending)
            raise

    async def _finish_success(self, call_id: int, settlement: SettlementInput, *, output_path: str | None) -> None:
        await self._settle(self._write_success(call_id, settlement, output_path=output_path))

    async def _write_success(self, call_id: int, settlement: SettlementInput, *, output_path: str | None) -> None:
        async with self._session_factory() as session:
            await UsageRepository(session).finish_call(
                call_id, status=CallStatus.SUCCESS, settlement=settlement, output_path=output_path
            )

    async def _finish_failed(self, call_id: int, exc: BaseException) -> bool:
        """Mark failed; return whether persistence succeeded, suppressing events on failed writes.

        Accounting errors never swallow originals: only log write failures and propagate original exceptions. Classification belongs inside try too:
        reading exception attributes might raise, which should behave like persistence failure, never replace originals.
        """
        try:
            failure = classify_call_failure(exc)
            await self._settle(self._write_failed(call_id, failure))
        except Exception:
            logger.exception("ledger 失败分支记账写入自身失败 call_id=%s（原异常照常重抛）", call_id)
            return False
        return True

    async def _write_failed(self, call_id: int, failure: CallFailure) -> None:
        async with self._session_factory() as session:
            await UsageRepository(session).finish_call(
                call_id,
                status=CallStatus.FAILED,
                settlement=SettlementInput(),
                error_message=failure.error_message,
                error_code=failure.error_code,
                error_params=failure.error_params,
            )

    async def _finish_cancelled(self, call_id: int) -> bool:
        """Mark cancelled; return whether persistence succeeded, suppressing events on failed writes.

        Never swallow CancelledError: log write failures and reraise cancellation unchanged,
        leaving pending for caller fallback/startup finalization.
        """
        try:
            await self._settle(self._write_cancelled(call_id))
        except Exception:
            logger.exception("ledger 取消分支记账写入自身失败 call_id=%s（取消照常重抛）", call_id)
            return False
        return True

    async def _write_cancelled(self, call_id: int) -> None:
        async with self._session_factory() as session:
            await UsageRepository(session).finish_call(
                call_id, status=CallStatus.CANCELLED, settlement=SettlementInput(cost_amount=0.0)
            )

    async def _finalize(
        self, *, call_id: int, status: CallStatus, settlement: SettlementInput, failure: CallFailure | None = None
    ) -> int:
        async with self._session_factory() as session:
            repo = UsageRepository(session)
            affected = await repo.finalize_pending_by_call_id(
                call_id=call_id,
                status=status,
                settlement=settlement,
                error_message=None if failure is None else failure.error_message,
                error_code=None if failure is None else failure.error_code,
                error_params=None if failure is None else failure.error_params,
            )
            # Events need projects while resume has call_id only; idempotent zero-row results neither query nor publish.
            project_name = await repo.get_call_project_name(call_id) if affected else ""
        self._emit_recorded(project_name, call_id, status)
        return affected
