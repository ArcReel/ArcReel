"""Async repository for API call usage tracking."""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, case, func, null, or_, select, update

from lib.agent.credential_tiers import AgentPricing
from lib.backends.providers import CALL_TYPE_TEXT, CallPurpose, CallStatus, CallType
from lib.billing.call_failure import CallErrorCode
from lib.billing.catalog_pricing import (
    is_builtin_provider,
    price_agent_call,
    price_builtin_call,
    price_custom_call,
)
from lib.billing.metering import Priced, UnpricedReason, Usage
from lib.billing.multipliers import get_provider_multiplier
from lib.billing.usage_summary import UsageFilterOptions, UsageSummaryRow
from lib.custom_provider import is_custom_provider
from lib.db.base import DEFAULT_USER_ID, utc_now
from lib.db.models.api_call import ApiCall
from lib.db.models.session import AgentSession
from lib.db.models.task import Task
from lib.db.repositories.base import BaseRepository, rowcount
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.db.repositories.display_names import provider_display_names
from lib.generation.task_terminal_events import TERMINAL_TASK_STATUSES
from lib.i18n import DEFAULT_LOCALE

logger = logging.getLogger(__name__)

# Reasonable billed-duration limit (24 hours), defined once: repository writes are the final accounting defense for all backends.
# Treat duration exceeding the limit as absent and fall back to requested duration, preventing oversized DB Integer writes;
# parser clamps (grok / dashscope extractors) share this constant for consistent treatment.
MAX_BILLED_DURATION_SECONDS = 86400
MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024


def _persisted_size(value: object) -> int:
    """Byte size of this value when stored in a JSON column.

    Measure the text SQLAlchemy actually writes, matching each default: the engine has no
    ``json_serializer``, so it uses plain ``json.dumps``. ``ensure_ascii=True`` escapes non-ASCII
    as ``\\uXXXX`` and separators include whitespace. Measuring any part more compactly would
    let actual persisted size exceed the limit.
    """
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return len(str(value).encode("utf-8"))


def bound_provider_response(body: object) -> object:
    """Limit the last provider response to 64 KiB, retaining a diagnostic prefix and truncation marker when oversized."""
    if _persisted_size(body) <= MAX_PROVIDER_RESPONSE_BYTES:
        return body
    try:
        text = json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError, RecursionError):
        text = str(body)
    # Halve repeatedly until the wrapped output actually fits: escaping expansion varies with content (CJK 2×,
    # emoji 3×), so fixed-ratio reservations cannot cover the worst case.
    prefix = text[: MAX_PROVIDER_RESPONSE_BYTES // 2]
    while prefix and _persisted_size({"truncated": True, "body": prefix}) > MAX_PROVIDER_RESPONSE_BYTES:
        prefix = prefix[: len(prefix) // 2]
    return {"truncated": True, "body": prefix}


# Sentinel for segment summaries of calls with NULL segment_id, such as asset images and text calls.
# Consumers use it to separate project-level from storyboard-level spend; producers and consumers share this constant. The leading NUL
# prevents collision with real segment_id values drawn from resource_id (storyboard/unit IDs or asset names), which contain no control characters.
# A collision would count the same-named script unit in both episode totals and project-level spend.
PROJECT_LEVEL_SEGMENT_KEY = "\x00__project__"

# Call types producing project media: asset images, storyboards, videos and voiceover
_MEDIA_CALL_TYPES = ("image", "video", "audio")


@dataclass(frozen=True)
class SettlementInput:
    """Declared write-side value object carrying the caller's raw billing dimensions at snapshot time.

    These are declaration-time raw values: ``billed_duration_seconds`` may be invalid or oversized,
    and explicit ``cost_amount`` bypasses automatic calculation. ``_settle`` normalizes effective values.

    ``usage`` reports metered-item quantities and selection dimensions under the usage contract, one per
    provider request, including discarded fallback-chain requests that were already billed. Price each
    built-in/custom-provider request against its matched entry's price list, then sum. If omitted, all
    metered items are absent and the call is unpriced. Other providers are also unpriced. Agent-session
    turns do not use this path; see :meth:`UsageRepository.record_agent_turn`. The three cache-token fields
    sum all requests and are written unchanged to their namesake columns. Add future billing fields only
    to this object instead of threading separate arguments through repository writes.
    """

    cost_amount: float | None = None
    currency: str | None = None
    generate_audio: bool | None = None
    billed_duration_seconds: int | None = None
    usage_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_5m_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    usage: tuple[Usage, ...] = ()


@dataclass(frozen=True)
class AgentModelCall:
    """One model's usage in an Agent-session turn, written as one provider-call record.

    ``requests`` contains measurements for each request in the turn, each priced in its own context tier.
    The four token fields sum requests; ``input_tokens`` is total input, including cache reads and both cache-write tiers.
    """

    model: str
    requests: tuple[Usage, ...]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int


@dataclass(frozen=True)
class _SettledCall:
    """``_settle`` output: effective settlement values shared by both snapshot paths for direct use in their UPDATEs."""

    duration_ms: int
    effective_duration_seconds: int | None
    effective_generate_audio: bool | None
    input_tokens: int | None
    output_tokens: int | None
    #: ``None`` means unpriced; see ``unpriced_reason``.
    cost_amount: float | None
    currency: str
    unpriced_reason: str | None = None


def _classify_asset_output_path(output_path: str | None) -> str:
    """Infer asset type from api_call.output_path (characters/scenes/props/products/other).

    Historical tasks before v0→v1 migration wrote ``clues/...`` paths. Group these under props,
    matching the migration's default clue→prop mapping so old bills do not silently disappear into other.
    """
    if not output_path:
        return "other"
    # Support both absolute and relative paths
    normalized = output_path.replace("\\", "/").lower()
    for asset_type in ("characters", "scenes", "props", "products"):
        if f"/{asset_type}/" in normalized or normalized.startswith(f"{asset_type}/"):
            return asset_type
    if "/clues/" in normalized or normalized.startswith("clues/"):
        return "props"
    return "other"


@dataclass(frozen=True, slots=True)
class InterruptedCallSettlement:
    """One row finalized at startup: call ID, owning project and resulting terminal status, for upper-layer project events."""

    call_id: int
    project_name: str
    status: CallStatus


def _interrupted_target(task_id: str | None, task_statuses: dict[str, str]) -> tuple[CallStatus, str | None] | None:
    """Determine a pending call's terminal status; return ``None`` while its task is alive, leaving the row untouched.

    - No ``task_id``: the call belongs to no task, for example endpoint trials or text calls. After process
      restart, nothing running can settle it. Mark failed with ``interrupted`` so readers display interruption.
    - No task row for ``task_id``: the task was cleaned up and nothing can resume the call; handle as above.
    - Terminal task: follow its outcome (cancelled → cancelled; succeeded / failed → failed).
      Even successful tasks produce failed calls here: incomplete settlement has not recorded a bill;
      marking success would invent a charge.
    - Nonterminal task (queued / running): it remains alive and its own settlement path will finish the call.
    """
    if not task_id:
        return CallStatus.FAILED, CallErrorCode.INTERRUPTED
    status = task_statuses.get(task_id)
    if status is None:
        return CallStatus.FAILED, CallErrorCode.INTERRUPTED
    if status not in TERMINAL_TASK_STATUSES:
        return None
    if status == "cancelled":
        return CallStatus.CANCELLED, None
    return CallStatus.FAILED, None


class UsageRepository(BaseRepository):
    async def update_last_provider_response(self, call_id: int, body: object) -> None:
        await self.session.execute(
            update(ApiCall).where(ApiCall.id == call_id).values(last_provider_response=bound_provider_response(body))
        )
        await self.session.commit()

    async def start_call(
        self,
        *,
        project_name: str,
        call_type: CallType,
        model: str,
        prompt: str | None = None,
        resolution: str | None = None,
        duration_seconds: int | None = None,
        aspect_ratio: str | None = None,
        generate_audio: bool | None = None,
        provider: str,
        user_id: str = DEFAULT_USER_ID,
        segment_id: str | None = None,
        task_id: str | None = None,
        purpose: CallPurpose | None = None,
        session_id: str | None = None,
        inputs: object | None = None,
    ) -> int:
        """Insert a pending row and return its ID.

        Persist the entire ``prompt`` so details can display full input. Empty ``task_id`` means this
        call serves no generation task; ``purpose`` then identifies its source.
        """
        now = utc_now()

        row = ApiCall(
            project_name=project_name,
            call_type=call_type,
            model=model,
            prompt=prompt,
            resolution=resolution,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            status=CallStatus.PENDING,
            started_at=now,
            provider=provider,
            user_id=user_id,
            segment_id=segment_id,
            task_id=task_id,
            purpose=purpose,
            session_id=session_id,
            inputs=inputs,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row.id

    async def _settle(
        self,
        *,
        row: ApiCall,
        finished_at: datetime,
        settlement: SettlementInput,
        auto_calc: bool,
        base_currency: str,
    ) -> _SettledCall:
        """Shared settlement for both snapshot paths: take an ApiCall row and declared values, returning effective
        billed duration, audio flag, duration_ms, cost and currency, including OpenAI image-token aggregate normalization.

        - Write duration_ms from ``finished_at - started_at``. SQLite ``started_at`` read across sessions
          is naive; attach UTC tzinfo before subtracting to match aware ``finished_at``. Calculation
          exceptions still fall back to 0 as the final defense.
        - Billed duration/audio overrides: backend-reported values override request values from start_call.
          Nonpositive duration or duration exceeding ``MAX_BILLED_DURATION_SECONDS`` counts as absent
          and falls back to requested duration.
        - Cost: explicit ``cost_amount`` is provider-reported billing data and takes precedence over
          automatic calculation. Otherwise, when ``auto_calc`` is true, calculate and freeze built-in/custom
          calls using catalog price lists and both multiplier levels. Missing unit prices yield ``None``
          with an unpriced reason; other providers are unpriced with ``no_entry``.
        - Caller-supplied ``base_currency`` carries each path's fallback: finish_call uses ``row.currency``
          and resume uses ``settlement.currency``, avoiding behavioral divergence.

        Public methods' UPDATE WHERE clauses explicitly enforce pending guards against double billing; this helper does not.
        """
        try:
            started_at = row.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            settled_finished_at = finished_at if finished_at.tzinfo is not None else finished_at.replace(tzinfo=UTC)
            duration_ms = int((settled_finished_at - started_at).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration_ms = 0

        effective_generate_audio = (
            settlement.generate_audio if settlement.generate_audio is not None else row.generate_audio
        )

        billed = settlement.billed_duration_seconds
        effective_duration_seconds = (
            billed if billed is not None and 0 < billed <= MAX_BILLED_DURATION_SECONDS else row.duration_seconds
        )

        # OpenAI image calls: input_tokens/output_tokens columns represent totals
        # = image_*_tokens + text_*_tokens, retaining compatibility with cross-call_type aggregates.
        # Resume has no image tokens: has_image_tokens stays False, preserving declared values.
        input_tokens = settlement.input_tokens
        output_tokens = settlement.output_tokens
        has_image_tokens = any(
            t is not None
            for t in (
                settlement.image_input_tokens,
                settlement.image_output_tokens,
                settlement.text_input_tokens,
                settlement.text_output_tokens,
            )
        )
        if has_image_tokens:
            input_tokens = (settlement.image_input_tokens or 0) + (settlement.text_input_tokens or 0)
            output_tokens = (settlement.image_output_tokens or 0) + (settlement.text_output_tokens or 0)

        cost_amount: float | None = 0.0
        currency = base_currency
        unpriced_reason: str | None = None
        provider = row.provider or ""
        if settlement.cost_amount is not None:
            cost_amount = settlement.cost_amount
            currency = settlement.currency or base_currency
        elif auto_calc and is_builtin_provider(provider):
            # Calls without reported usage still use price lists: all metered items are absent and the call is unpriced; do not guess seconds or default models.
            cost = price_builtin_call(
                provider=provider,
                model=row.model or "",
                media_type=row.call_type,
                requests=settlement.usage,
                provider_multiplier=await get_provider_multiplier(self.session, provider),
            )
            if isinstance(cost, Priced):
                cost_amount, currency = cost.amount, cost.currency
            else:
                cost_amount, unpriced_reason = None, str(cost.reason)
        elif auto_calc and is_custom_provider(provider):
            # Price custom providers from the matched entry price list and both multiplier levels; unmatched entries are unpriced (docs/adr/0106). Without
            # reported usage, all metered items are absent and unpriced too; do not force pricing by media type or guess seconds.
            pricing = await CustomProviderRepository(self.session).resolve_pricing(provider, row.model or "")
            cost = price_custom_call(
                model=row.model or "",
                media_type=row.call_type,
                entry_binding=pricing.entry_binding,
                requests=settlement.usage,
                provider_multiplier=await get_provider_multiplier(self.session, provider),
                model_multiplier=pricing.multiplier,
            )
            if isinstance(cost, Priced):
                cost_amount, currency = cost.amount, cost.currency
            else:
                cost_amount, unpriced_reason = None, str(cost.reason)
        elif auto_calc:
            # Providers neither registered nor custom, such as legacy rows for retired providers, match no entry and remain unpriced.
            cost_amount, unpriced_reason = None, str(UnpricedReason.NO_ENTRY)

        return _SettledCall(
            duration_ms=duration_ms,
            effective_duration_seconds=effective_duration_seconds,
            effective_generate_audio=effective_generate_audio,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_amount=cost_amount,
            currency=currency,
            unpriced_reason=unpriced_reason,
        )

    async def find_pending_call_id_by_task_id(self, task_id: str) -> int | None:
        """Find the unsettled call ID for a task, or None if absent.

        ``api_calls.task_id`` is the sole source of truth for task/call associations. Each task has at most
        one pending call, reused in place by download retries and resume; concurrent submissions select the latest ID.
        """
        result = await self.session.execute(
            select(ApiCall.id)
            .where(ApiCall.task_id == task_id, ApiCall.status == CallStatus.PENDING)
            .order_by(ApiCall.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def finalize_pending_by_call_id(
        self,
        *,
        call_id: int,
        settlement: SettlementInput,
        status: CallStatus = CallStatus.SUCCESS,
        error_message: str | None = None,
        error_code: str | None = None,
        error_params: dict[str, object] | None = None,
    ) -> int:
        """Resume-only path: transition exactly call_id from pending → success/failed.

        The repository WHERE includes ``status='pending'``, leaving successful rows untouched. If generate
        crashes after finish_call, resume cannot overwrite success or bill again: the guard preserves the
        fact that the provider already charged. Share ``_settle`` with finish_call for billed duration/audio
        overrides, automatic cost and duration_ms, but use ``settlement.currency`` as resume's currency fallback.
        Return affected row count (0 = idempotent no-op; 1 = one normal transition).

        The failure triple follows ``finish_call``, truncating raw text to 500 characters, and is written only
        when the caller supplies failure information. Success / cancelled exits omit it, leaving those three columns unchanged.
        """
        finished_at = utc_now()

        # Always fetch the row for both automatic cost calculation and duration_ms updates
        # (even explicit caller-supplied cost_amount still requires started_at for duration_ms).
        # The UPDATE WHERE below continues enforcing the row.status='pending' idempotency guard.
        select_result = await self.session.execute(select(ApiCall).where(ApiCall.id == call_id))
        row = select_result.scalar_one_or_none()
        if row is None:
            return 0

        settled = await self._settle(
            row=row,
            finished_at=finished_at,
            settlement=settlement,
            auto_calc=status == CallStatus.SUCCESS and row.status == CallStatus.PENDING,
            base_currency=settlement.currency or "USD",
        )

        values: dict[str, Any] = {
            "status": status,
            "finished_at": finished_at,
            "duration_ms": settled.duration_ms,
            "duration_seconds": settled.effective_duration_seconds,
            "cost_amount": settled.cost_amount,
            "currency": settled.currency,
            "unpriced_reason": settled.unpriced_reason,
            "usage_tokens": settlement.usage_tokens,
            "generate_audio": settled.effective_generate_audio,
        }
        if error_message is not None or error_code is not None:
            values["error_message"] = error_message[:500] if error_message else None
            values["error_code"] = error_code
            values["error_params"] = error_params

        result = await self.session.execute(
            update(ApiCall).where(ApiCall.id == call_id, ApiCall.status == CallStatus.PENDING).values(**values)
        )
        affected = rowcount(result)
        if affected > 0:
            await self.session.commit()
        return affected

    async def get_call_project_name(self, call_id: int) -> str:
        """Get the call's project name; return an empty string if absent, so callers emit no event."""
        result = await self.session.execute(select(ApiCall.project_name).where(ApiCall.id == call_id))
        return result.scalar_one_or_none() or ""

    async def settle_interrupted_pending_calls(
        self, *, taskless_started_before: datetime | None
    ) -> list[InterruptedCallSettlement]:
        """Finalize pending calls without live tasks at service startup with zero cost; return finalized rows.

        Crashes/restarts can strand persisted but unsettled calls in pending, neither success nor failure
        in usage reports. Finalize once at startup according to ``_interrupted_target``, leaving all calls
        with live tasks untouched because their settlement paths remain active.

        Taskless calls, such as text calls and endpoint trials, are settled by their initiating process.
        Only calls begun before this process started are certainly abandoned. ``taskless_started_before``
        supplies that cutoff: finalize only earlier taskless calls, leaving those started between process
        startup and this cleanup alone. ``None`` leaves all taskless calls untouched during live-process rescans.

        Zero cost is intentional: incomplete settlement has no trustworthy billing dimensions, so recording
        0 avoids inventing charges. Likewise write NULL for ``duration_ms``: wall-clock elapsed time is not
        call duration, and long-stranded rows may exceed integer width.
        Each UPDATE guards ``status='pending'`` so only cleanup or concurrent normal settlement can win.
        """
        finished_at = utc_now()
        pending_rows = (
            (await self.session.execute(select(ApiCall).where(ApiCall.status == CallStatus.PENDING))).scalars().all()
        )
        if not pending_rows:
            return []

        task_ids = {row.task_id for row in pending_rows if row.task_id}
        task_statuses: dict[str, str] = {}
        if task_ids:
            rows = await self.session.execute(select(Task.task_id, Task.status).where(Task.task_id.in_(task_ids)))
            task_statuses = {task_id: status for task_id, status in rows.all()}  # noqa: C416 -- Row tuples are not two-item sequences; dict() cannot consume them directly

        settled: list[InterruptedCallSettlement] = []
        for row in pending_rows:
            if not row.task_id and (
                taskless_started_before is None or as_utc(row.started_at) >= taskless_started_before
            ):
                continue
            target = _interrupted_target(row.task_id, task_statuses)
            if target is None:
                continue
            status, error_code = target
            settlement = SettlementInput(cost_amount=0.0)
            effective = await self._settle(
                row=row,
                finished_at=finished_at,
                settlement=settlement,
                auto_calc=False,
                base_currency=row.currency or "USD",
            )
            values: dict[str, Any] = {
                "status": status,
                "finished_at": finished_at,
                # Interrupted calls never completed settlement; wall time from initiation to restart is not call duration and stays empty, consistent with zero cost.
                "duration_ms": None,
                "cost_amount": effective.cost_amount,
                "currency": effective.currency,
            }
            if error_code is not None:
                # Classification guarantees a params object (read-side invariant: a code implies object params); interruption has no parameters to carry.
                values["error_code"] = error_code
                values["error_params"] = {}
            result = await self.session.execute(
                update(ApiCall).where(ApiCall.id == row.id, ApiCall.status == CallStatus.PENDING).values(**values)
            )
            if rowcount(result) > 0:
                settled.append(
                    InterruptedCallSettlement(call_id=row.id, project_name=row.project_name or "", status=status)
                )

        if settled:
            await self.session.commit()
        return settled

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
        calls: list[AgentModelCall],
    ) -> list[int]:
        """Write one terminal provider-call row per model for an Agent-session turn; return row IDs (docs/adr/0102).

        Rows from the same turn share ``session_id``, prompt and write time. Sum that model's request
        costs using ``pricing``, which captures the Agent credential, multiplier and entry binding injected
        at session connection. Do not zero cost by ``status``: completed requests in interrupted/failed turns
        still incurred charges. If no entry matches, persist tokens normally and mark cost unpriced.
        """
        now = utc_now()
        rows: list[ApiCall] = []
        for call in calls:
            cost = price_agent_call(
                model=call.model,
                entry_binding=pricing.entry_binding_for(call.model),
                requests=call.requests,
                multiplier=pricing.multiplier,
            )
            if isinstance(cost, Priced):
                cost_amount, currency, unpriced_reason = cost.amount, cost.currency, None
            else:
                cost_amount, currency, unpriced_reason = null(), "USD", str(cost.reason)
                logger.warning(
                    "Agent 模型对应不到可计价的条目，费用记为未定价 provider=%s model=%s reason=%s",
                    provider,
                    call.model,
                    cost.reason,
                )
            rows.append(
                ApiCall(
                    project_name=project_name,
                    call_type=CALL_TYPE_TEXT,
                    model=call.model,
                    prompt=prompt,
                    status=status,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    provider=provider,
                    user_id=user_id,
                    purpose=CallPurpose.ASSISTANT_SESSION,
                    session_id=session_id,
                    cost_amount=cost_amount,
                    currency=currency,
                    unpriced_reason=unpriced_reason,
                    usage_tokens=call.input_tokens + call.output_tokens,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    cache_read_tokens=call.cache_read_tokens,
                    cache_write_5m_tokens=call.cache_write_5m_tokens,
                    cache_write_1h_tokens=call.cache_write_1h_tokens,
                )
            )
        self.session.add_all(rows)
        await self.session.commit()
        return [row.id for row in rows]

    async def finish_call(
        self,
        call_id: int,
        *,
        status: CallStatus,
        settlement: SettlementInput,
        output_path: str | None = None,
        error_message: str | None = None,
        error_code: str | None = None,
        error_params: dict[str, object] | None = None,
    ) -> None:
        finished_at = utc_now()

        result = await self.session.execute(select(ApiCall).where(ApiCall.id == call_id))
        row = result.scalar_one_or_none()
        if not row:
            return

        settled = await self._settle(
            row=row,
            finished_at=finished_at,
            settlement=settlement,
            auto_calc=status == CallStatus.SUCCESS,
            base_currency=row.currency or "USD",
        )

        error_truncated = error_message[:500] if error_message else None

        await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=settled.duration_ms,
                duration_seconds=settled.effective_duration_seconds,
                generate_audio=settled.effective_generate_audio,
                cost_amount=settled.cost_amount,
                currency=settled.currency,
                unpriced_reason=settled.unpriced_reason,
                usage_tokens=settlement.usage_tokens,
                input_tokens=settled.input_tokens,
                output_tokens=settled.output_tokens,
                cache_read_tokens=settlement.cache_read_tokens,
                cache_write_5m_tokens=settlement.cache_write_5m_tokens,
                cache_write_1h_tokens=settlement.cache_write_1h_tokens,
                image_input_tokens=settlement.image_input_tokens,
                image_output_tokens=settlement.image_output_tokens,
                text_input_tokens=settlement.text_input_tokens,
                text_output_tokens=settlement.text_output_tokens,
                output_path=output_path,
                error_message=error_truncated,
                error_code=error_code,
                error_params=error_params,
            )
        )
        await self.session.commit()

    async def get_actual_costs_by_segment(
        self,
        project_name: str,
    ) -> dict[str, dict[str, dict[str, float]]]:
        """Sum actual cost by segment_id + call_type + currency.

        Returns:
            {segment_id: {call_type: {currency: total_amount}}}
            Records with None segment_id use ``PROJECT_LEVEL_SEGMENT_KEY``.
        """
        stmt = (
            select(
                ApiCall.segment_id,
                ApiCall.call_type,
                ApiCall.currency,
                func.sum(ApiCall.cost_amount).label("total"),
            )
            .where(
                ApiCall.project_name == project_name,
                ApiCall.status == CallStatus.SUCCESS,
                ApiCall.cost_amount > 0,
            )
            .group_by(ApiCall.segment_id, ApiCall.call_type, ApiCall.currency)
        )
        stmt = self._scope_query(stmt, ApiCall)
        rows = (await self.session.execute(stmt)).all()

        result: dict[str, dict[str, dict[str, float]]] = {}
        for seg_id, call_type, currency, total in rows:
            key = seg_id if seg_id is not None else PROJECT_LEVEL_SEGMENT_KEY
            result.setdefault(key, {}).setdefault(call_type, {})[currency] = round(total, 6)
        return result

    async def get_unpriced_calls_by_model(self, project_name: str) -> list[tuple[str, str, str, int]]:
        """Count successful but unpriced media calls (NULL ``cost_amount``) by (call_type, provider, model).

        Zero means priced at zero and is excluded; historical calls recorded as 0 are not retroactively classified as unpriced.
        """
        stmt = (
            select(ApiCall.call_type, ApiCall.provider, ApiCall.model, func.count().label("calls"))
            .where(
                ApiCall.project_name == project_name,
                ApiCall.status == CallStatus.SUCCESS,
                ApiCall.call_type.in_(_MEDIA_CALL_TYPES),
                ApiCall.cost_amount.is_(None),
            )
            .group_by(ApiCall.call_type, ApiCall.provider, ApiCall.model)
        )
        stmt = self._scope_query(stmt, ApiCall)
        rows = (await self.session.execute(stmt)).all()
        return [(call_type, provider, model, calls) for call_type, provider, model, calls in rows]

    async def has_media_calls(self, project_name: str) -> bool:
        """Whether this machine has successful media-call records for the project.

        Text, failed and in-progress calls cannot represent existing-artifact costs and are excluded.
        """
        stmt = self._scope_query(
            select(ApiCall.id)
            .where(
                ApiCall.project_name == project_name,
                ApiCall.status == CallStatus.SUCCESS,
                ApiCall.call_type.in_(_MEDIA_CALL_TYPES),
            )
            .limit(1),
            ApiCall,
        )
        return (await self.session.execute(stmt)).first() is not None

    async def get_project_image_costs_by_asset_type(
        self,
        project_name: str,
    ) -> dict[str, dict[str, float]]:
        """Split project-level image costs (segment_id is null) by output_path prefix.

        Returns:
            {asset_type: {currency: total_amount}}, asset_type ∈ {characters, scenes, props, products, other}.
        """
        stmt = (
            select(
                ApiCall.output_path,
                ApiCall.currency,
                func.sum(ApiCall.cost_amount).label("total"),
            )
            .where(
                ApiCall.project_name == project_name,
                ApiCall.status == CallStatus.SUCCESS,
                ApiCall.cost_amount > 0,
                ApiCall.call_type == "image",
                ApiCall.segment_id.is_(None),
            )
            .group_by(ApiCall.output_path, ApiCall.currency)
        )
        stmt = self._scope_query(stmt, ApiCall)
        rows = (await self.session.execute(stmt)).all()

        result: dict[str, dict[str, float]] = {}
        for output_path, currency, total in rows:
            asset_type = _classify_asset_output_path(output_path)
            bucket = result.setdefault(asset_type, {})
            bucket[currency] = round(bucket.get(currency, 0) + total, 6)
        return result

    # ------------------------------------------------------------------
    # Usage-record read API
    # Filter dimensions, projections and cursor encoding are defined in the module-level usage-record read-side section below.
    # ------------------------------------------------------------------

    async def list_records(
        self,
        *,
        filters: UsageFilters | None = None,
        statuses: tuple[str, ...] = (),
        segment_ids: tuple[str, ...] = (),
        limit: int = 20,
        cursor: UsageCursor | None = None,
    ) -> dict[str, Any]:
        """Get one usage-record page ordered by ``started_at DESC, id DESC``.

        Return ``{"items", "next_cursor", "total"}``: ``total`` is the filtered count independent of cursor;
        ``next_cursor`` ``None`` marks the last page. Outer-join ``tasks`` through ``task_id`` for ``task_type``,
        leaving it empty when the task row has been cleaned up.
        """
        clauses = _records_base_clauses(filters or UsageFilters(), statuses=statuses, segment_ids=segment_ids)

        count_stmt = self._scope_query(select(func.count()).select_from(ApiCall).where(*clauses), ApiCall)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        page_clauses = [*clauses]
        if cursor is not None:
            page_clauses.append(
                or_(
                    ApiCall.started_at < cursor.started_at,
                    and_(ApiCall.started_at == cursor.started_at, ApiCall.id < cursor.id),
                )
            )
        # Fetch one extra row only to detect another page, avoiding a second COUNT; exclude it from items.
        items_stmt = (
            select(ApiCall, Task.task_type)
            .outerjoin(Task, Task.task_id == ApiCall.task_id)
            .where(*page_clauses)
            .order_by(ApiCall.started_at.desc(), ApiCall.id.desc())
            .limit(limit + 1)
        )
        rows = (await self.session.execute(self._scope_query(items_stmt, ApiCall))).all()

        page = rows[:limit]
        next_cursor = UsageCursor(started_at=page[-1][0].started_at, id=page[-1][0].id) if len(rows) > limit else None
        return {
            "items": [_record_to_dict(row, task_type) for row, task_type in page],
            "next_cursor": next_cursor.encode() if next_cursor is not None else None,
            "total": total,
        }

    async def get_record(self, record_id: int) -> dict[str, Any] | None:
        """Get one usage record's details; return ``None`` if absent or outside scope.

        Do not apply the list's omission of pending calls with tasks, which only avoids duplicating the
        in-progress area and record table. Direct ID access via a ``record=<id>`` deep link must still work.
        """
        stmt = (
            select(ApiCall, Task.task_type)
            .outerjoin(Task, Task.task_id == ApiCall.task_id)
            .where(ApiCall.id == record_id)
        )
        row = (await self.session.execute(self._scope_query(stmt, ApiCall))).first()
        return _record_detail_to_dict(row[0], row[1]) if row is not None else None

    # --- Summary read API (GET /usage/summary) ------------------------------------------------
    # Read rows without SQL aggregation: lib/billing/usage_summary.py handles day boundaries and bucket filling, avoiding SQLite/PostgreSQL
    # date-function differences and allowing timezone/exception boundaries to be exercised independently of the database.

    async def provider_display_names(self, provider_ids: set[str], *, locale: str = DEFAULT_LOCALE) -> dict[str, str]:
        """Map provider IDs to catalog display names; unknown catalog providers fall back to their ID, unknown Agent providers to a generic label."""
        return await provider_display_names(self.session, provider_ids, locale=locale)

    async def fetch_summary_rows(self, *, filters: UsageFilters | None = None) -> list[UsageSummaryRow]:
        """Lightweight call rows eligible for period aggregates; pending rows never enter any aggregate."""
        stmt = select(
            ApiCall.id,
            ApiCall.project_name,
            ApiCall.call_type,
            ApiCall.provider,
            ApiCall.model,
            ApiCall.status,
            ApiCall.started_at,
            ApiCall.cost_amount,
            ApiCall.currency,
            ApiCall.segment_id,
            ApiCall.error_code,
            ApiCall.purpose,
        ).where(ApiCall.status != CallStatus.PENDING, *usage_filter_clauses(filters or UsageFilters()))
        stmt = self._scope_query(stmt, ApiCall)

        return [
            UsageSummaryRow(
                id=row.id,
                project_name=row.project_name,
                media_type=row.call_type,
                provider=row.provider,
                model=row.model,
                status=row.status,
                started_at=row.started_at,
                cost_amount=row.cost_amount,
                currency=row.currency or "USD",
                segment_id=row.segment_id,
                error_code=row.error_code,
                agent=row.purpose == CallPurpose.ASSISTANT_SESSION,
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def fetch_usage_filter_options(
        self, *, agent: bool | None = None, locale: str = DEFAULT_LOCALE
    ) -> UsageFilterOptions:
        """Filter choices: distinct values across all time, unaffected by current filters so dropdown choices do not disappear after filtering.

        ``agent`` selects the view: ``False`` lists only generation-call projects/providers/models;
        ``True`` lists Agent-session values only; ``None`` does not distinguish views.
        """
        view = _view_clauses(agent)
        projects_stmt = self._scope_query(select(ApiCall.project_name).where(*view).distinct(), ApiCall)
        models_stmt = self._scope_query(select(ApiCall.provider, ApiCall.model).where(*view).distinct(), ApiCall)

        projects = sorted({row[0] for row in (await self.session.execute(projects_stmt)).all()})
        models = sorted({(row.provider, row.model) for row in (await self.session.execute(models_stmt)).all()})
        provider_ids = {provider for provider, _ in models}
        labels = await self.provider_display_names(provider_ids, locale=locale)

        return UsageFilterOptions(
            projects=projects,
            providers=[(provider, labels[provider]) for provider in sorted(provider_ids)],
            models=models,
        )

    # --- Agent-session view ------------------------------------------------------------------
    # Accounting granularity remains one provider-call record per model per turn; the session view only aggregates Agent records by session_id.

    async def list_agent_sessions(
        self,
        *,
        filters: UsageFilters | None = None,
        limit: int = 20,
        cursor: UsageSessionCursor | None = None,
    ) -> dict[str, Any]:
        """Get an Agent-session page ordered by latest turn descending, one row per session.

        ``filters`` follow the record list: filter records before session aggregation, including only matching
        records in each session. Records with empty ``session_id`` form no session and are absent here.
        Return ``{"items", "next_cursor", "total"}``.
        """
        clauses = [
            *usage_filter_clauses(replace(filters or UsageFilters(), agent=True)),
            ApiCall.session_id.is_not(None),
            ApiCall.status != CallStatus.PENDING,
        ]
        last_started = func.max(ApiCall.started_at)
        grouped = select(ApiCall.session_id.label("session_id"), last_started.label("last_started_at")).where(*clauses)
        grouped = self._scope_query(grouped, ApiCall).group_by(ApiCall.session_id)

        count_stmt = select(func.count()).select_from(grouped.subquery())
        total = (await self.session.execute(count_stmt)).scalar() or 0

        page_stmt = grouped
        if cursor is not None:
            page_stmt = page_stmt.having(
                or_(
                    last_started < cursor.last_started_at,
                    and_(last_started == cursor.last_started_at, ApiCall.session_id < cursor.session_id),
                )
            )
        # Fetch one extra row only to detect another page.
        page_stmt = page_stmt.order_by(last_started.desc(), ApiCall.session_id.desc()).limit(limit + 1)
        heads = (await self.session.execute(page_stmt)).all()
        page = heads[:limit]
        if not page:
            return {"items": [], "next_cursor": None, "total": total}
        next_cursor = (
            UsageSessionCursor(last_started_at=page[-1].last_started_at, session_id=page[-1].session_id)
            if len(heads) > limit
            else None
        )

        session_ids = [row.session_id for row in page]
        in_page = [*clauses, ApiCall.session_id.in_(session_ids)]
        unpriced = ApiCall.cost_amount.is_(None)
        totals_stmt = (
            select(
                ApiCall.session_id,
                func.min(ApiCall.project_name).label("project_name"),
                func.count().label("calls"),
                func.coalesce(func.sum(ApiCall.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(ApiCall.output_tokens), 0).label("output_tokens"),
                func.min(ApiCall.started_at).label("first_started_at"),
                func.sum(case((unpriced, 1), else_=0)).label("unpriced_calls"),
            )
            .where(*in_page)
            .group_by(ApiCall.session_id)
        )
        totals = {row.session_id: row for row in (await self.session.execute(self._scope_query(totals_stmt, ApiCall)))}

        # Sum costs separately by currency: model price lists may differ in currency; exclude unpriced (NULL) records.
        costs_stmt = (
            select(ApiCall.session_id, ApiCall.currency, func.sum(ApiCall.cost_amount))
            .where(*in_page, ApiCall.cost_amount.is_not(None))
            .group_by(ApiCall.session_id, ApiCall.currency)
        )
        costs: dict[str, dict[str, float]] = {}
        for session_id, currency, amount in (await self.session.execute(self._scope_query(costs_stmt, ApiCall))).all():
            costs.setdefault(session_id, {})[currency or "USD"] = float(amount or 0.0)

        # Order models by first appearance within the session.
        models_stmt = (
            select(ApiCall.session_id, ApiCall.model, func.min(ApiCall.started_at).label("first_seen"))
            .where(*in_page)
            .group_by(ApiCall.session_id, ApiCall.model)
            .order_by("first_seen", ApiCall.model)
        )
        models: dict[str, list[str]] = {}
        for session_id, model, _first_seen in (
            await self.session.execute(self._scope_query(models_stmt, ApiCall))
        ).all():
            models.setdefault(session_id, []).append(model)

        titles = await self._session_titles(session_ids)
        items = []
        for head in page:
            row = totals[head.session_id]
            items.append(
                {
                    "session_id": head.session_id,
                    # Deleted session metadata gives title None; the UI displays a deleted-session label.
                    "title": titles.get(head.session_id),
                    "session_deleted": head.session_id not in titles,
                    "project_name": row.project_name,
                    "models": models.get(head.session_id, []),
                    "calls": row.calls,
                    "input_tokens": int(row.input_tokens),
                    "output_tokens": int(row.output_tokens),
                    "cost": costs.get(head.session_id, {}),
                    "unpriced_calls": int(row.unpriced_calls),
                    "first_started_at": _record_iso(row.first_started_at),
                    "last_started_at": _record_iso(head.last_started_at),
                }
            )
        return {
            "items": items,
            "next_cursor": next_cursor.encode() if next_cursor is not None else None,
            "total": total,
        }

    async def _session_titles(self, session_ids: list[str]) -> dict[str, str]:
        """Map ``api_calls.session_id`` (SDK session ID) to session title; omit entries whose session metadata was deleted."""
        stmt = select(AgentSession.sdk_session_id, AgentSession.title).where(
            AgentSession.sdk_session_id.in_(session_ids)
        )
        rows = (await self.session.execute(self._scope_query(stmt, AgentSession))).all()
        return {sdk_session_id: title or "" for sdk_session_id, title in rows}

    async def list_agent_session_records(self, session_id: str) -> list[dict[str, Any]]:
        """All records of an Agent session in chronological order; records from one turn share a write time and naturally group together."""
        stmt = (
            select(ApiCall)
            .where(*usage_filter_clauses(UsageFilters(agent=True)), ApiCall.session_id == session_id)
            .order_by(ApiCall.started_at, ApiCall.id)
        )
        rows = (await self.session.execute(self._scope_query(stmt, ApiCall))).scalars().all()
        return [_session_record_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Usage-record read side
#
# This read-only section and accounting writes above do not reference each other: writes settle calls; reads project api_calls
# into usage records for settings and the header. Define filter dimensions, UTC normalization and cursor encoding once here,
# shared by ``/usage/records`` and ``/usage/summary``.
# ---------------------------------------------------------------------------


class UsageCursorError(ValueError):
    """The cursor cannot be decoded: invalid base64 or a payload other than ``(started_at, id)``."""


@dataclass(frozen=True)
class UsageFilters:
    """Usage-record filter dimensions shared by ``/usage/records`` and ``/usage/summary``.

    ``providers`` / ``models`` / ``media_types`` accept multiple values; empty tuples leave that dimension unfiltered.
    ``since`` / ``until`` define the half-open ``[since, until)`` interval on ``started_at``.
    """

    project_name: str | None = None
    providers: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()
    since: datetime | None = None
    until: datetime | None = None
    # View: ``False`` includes generation calls only; ``True`` includes Agent-session records only (source assistant_session);
    # ``None`` combines both. Summaries default to combined views, including Agent usage in totals.
    agent: bool | None = None


def _view_clauses(agent: bool | None) -> list[ColumnElement[bool]]:
    """Filter records by view. Records without a source are not Agent records."""
    if agent is None:
        return []
    if agent:
        return [ApiCall.purpose == CallPurpose.ASSISTANT_SESSION]
    return [or_(ApiCall.purpose.is_(None), ApiCall.purpose != CallPurpose.ASSISTANT_SESSION)]


def as_utc(value: datetime) -> datetime:
    """Interpret naive datetimes as UTC and convert aware datetimes to UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def usage_filter_clauses(filters: UsageFilters) -> list[ColumnElement[bool]]:
    """Expand ``UsageFilters`` into WHERE clauses.

    Check ``project_name`` with ``is not None``, not truthiness: endpoint-trial records persist ``""``,
    and ``project_name=""`` must select them. Only ``None`` leaves this dimension unfiltered.
    """
    clauses: list[ColumnElement[bool]] = []
    if filters.project_name is not None:
        clauses.append(ApiCall.project_name == filters.project_name)
    if filters.providers:
        clauses.append(ApiCall.provider.in_(filters.providers))
    if filters.models:
        clauses.append(ApiCall.model.in_(filters.models))
    if filters.media_types:
        clauses.append(ApiCall.call_type.in_(filters.media_types))
    if filters.since is not None:
        clauses.append(ApiCall.started_at >= as_utc(filters.since))
    if filters.until is not None:
        clauses.append(ApiCall.started_at < as_utc(filters.until))
    clauses.extend(_view_clauses(filters.agent))
    return clauses


def _records_base_clauses(
    filters: UsageFilters,
    *,
    statuses: tuple[str, ...],
    segment_ids: tuple[str, ...],
) -> list[ColumnElement[bool]]:
    """Complete record-list WHERE: shared dimensions, multiselect status/storyboard, and omission of pending calls with tasks.

    Rows with ``status=pending`` and nonempty ``task_id`` are represented by their generation task in
    the in-progress area, which reads the task store. Listing them again duplicates them; taskless pending
    calls have no other representative and must remain.
    """
    clauses = usage_filter_clauses(filters)
    if statuses:
        clauses.append(ApiCall.status.in_(statuses))
    if segment_ids:
        clauses.append(ApiCall.segment_id.in_(segment_ids))
    clauses.append(or_(ApiCall.status != CallStatus.PENDING, ApiCall.task_id.is_(None)))
    return clauses


@dataclass(frozen=True)
class UsageCursor:
    """Keyset pagination position: the previous page's last ``(started_at, id)``.

    Opaque to clients, which may only return it unchanged; therefore encode it as base64.
    """

    started_at: datetime
    id: int

    def encode(self) -> str:
        payload = json.dumps({"started_at": as_utc(self.started_at).isoformat(), "id": self.id})
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")

    @classmethod
    def decode(cls, raw: str) -> UsageCursor:
        try:
            payload = json.loads(base64.b64decode(raw.encode("ascii"), altchars=b"-_", validate=True))
            started_at = payload["started_at"]
            cursor_id = payload["id"]
            if not isinstance(started_at, str) or type(cursor_id) is not int or not 1 <= cursor_id <= 2**31 - 1:
                raise ValueError("游标字段类型或范围无效")
            return cls(started_at=as_utc(datetime.fromisoformat(started_at)), id=cursor_id)
        except (binascii.Error, LookupError, OverflowError, TypeError, UnicodeError, ValueError) as exc:
            # OverflowError: extreme datetime offsets cross UTC datetime bounds; treat this the same as parsing failure.
            raise UsageCursorError(f"无法解码的游标: {raw!r}") from exc


@dataclass(frozen=True)
class UsageSessionCursor:
    """Session-list keyset position: the previous page's last session ``(最近一轮时间, session_id)`` (latest turn time and session ID)."""

    last_started_at: datetime
    session_id: str

    def encode(self) -> str:
        payload = json.dumps(
            {"last_started_at": as_utc(self.last_started_at).isoformat(), "session_id": self.session_id}
        )
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")

    @classmethod
    def decode(cls, raw: str) -> UsageSessionCursor:
        try:
            payload = json.loads(base64.b64decode(raw.encode("ascii"), altchars=b"-_", validate=True))
            last_started_at = payload["last_started_at"]
            session_id = payload["session_id"]
            if not isinstance(last_started_at, str) or not isinstance(session_id, str) or not session_id:
                raise ValueError("游标字段类型或范围无效")
            return cls(last_started_at=as_utc(datetime.fromisoformat(last_started_at)), session_id=session_id)
        except (binascii.Error, LookupError, OverflowError, TypeError, UnicodeError, ValueError) as exc:
            raise UsageCursorError(f"无法解码的游标: {raw!r}") from exc


def _record_iso(value: datetime | None) -> str | None:
    """Always output record timestamps as UTC ISO strings.

    Do not directly call ``isoformat``: SQLite returns naive values for ``DateTime(timezone=True)``.
    Frontends interpret offset-free strings in the browser's local timezone, and the same row would
    acquire different meanings on SQLite and PostgreSQL.
    """
    return as_utc(value).isoformat() if value is not None else None


def _record_to_dict(row: ApiCall, task_type: str | None) -> dict[str, Any]:
    """List projection of one call. ``user_id`` is absent from the response."""
    return {
        "id": row.id,
        "project_name": row.project_name,
        "purpose": row.purpose,
        "task_id": row.task_id,
        "task_type": task_type,
        "media_type": row.call_type,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "error_code": row.error_code,
        "error_params": row.error_params,
        "error_message": row.error_message,
        "segment_id": row.segment_id,
        "output_path": row.output_path,
        "started_at": _record_iso(row.started_at),
        "finished_at": _record_iso(row.finished_at),
        "duration_ms": row.duration_ms,
        "cost_amount": row.cost_amount,
        "unpriced_reason": row.unpriced_reason,
        "currency": row.currency,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cache_read_tokens": row.cache_read_tokens,
        "cache_write_5m_tokens": row.cache_write_5m_tokens,
        "cache_write_1h_tokens": row.cache_write_1h_tokens,
        "usage_tokens": row.usage_tokens,
        "image_input_tokens": row.image_input_tokens,
        "image_output_tokens": row.image_output_tokens,
        "text_input_tokens": row.text_input_tokens,
        "text_output_tokens": row.text_output_tokens,
        "resolution": row.resolution,
        "duration_seconds": row.duration_seconds,
        "aspect_ratio": row.aspect_ratio,
        "session_id": row.session_id,
    }


def _record_detail_to_dict(row: ApiCall, task_type: str | None) -> dict[str, Any]:
    """Details add three heavy payloads to the list projection: full prompt, inputs and last provider response."""
    return {
        **_record_to_dict(row, task_type),
        "prompt": row.prompt,
        "inputs": row.inputs,
        "last_provider_response": row.last_provider_response,
    }


# Truncation length for session-detail messages: records in one turn share a prompt, making full-text repetition in every row pointless.
SESSION_PROMPT_PREVIEW_CHARS = 300


def _session_record_to_dict(row: ApiCall) -> dict[str, Any]:
    """One session-detail record projection: time, user message for this turn, model, token fields and cost."""
    prompt = row.prompt
    if prompt is not None and len(prompt) > SESSION_PROMPT_PREVIEW_CHARS:
        prompt = prompt[:SESSION_PROMPT_PREVIEW_CHARS] + "…"
    return {
        "id": row.id,
        "started_at": _record_iso(row.started_at),
        "status": row.status,
        "prompt": prompt,
        "provider": row.provider,
        "model": row.model,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cache_read_tokens": row.cache_read_tokens,
        "cache_write_5m_tokens": row.cache_write_5m_tokens,
        "cache_write_1h_tokens": row.cache_write_1h_tokens,
        "cost_amount": row.cost_amount,
        "unpriced_reason": row.unpriced_reason,
        "currency": row.currency,
    }
