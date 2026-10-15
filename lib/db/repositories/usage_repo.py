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
from lib.backends.providers import CALL_TYPE_TEXT, PROVIDER_GEMINI, CallPurpose, CallStatus, CallType
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

# 计费时长合理上限（24 小时），语义单点定义：repo 写入层是全部 backend 落账的最后防线，
# 超出上限的计费时长视同未提供、回落请求时长，防超大数值写入 DB Integer 列溢出；
# 解析侧（grok / dashscope extractor）的 clamp 引用同一常量，保持口径一致。
MAX_BILLED_DURATION_SECONDS = 86400
MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024


def _persisted_size(value: object) -> int:
    """该值落进 JSON 列时占的字节数。

    量的必须是 SQLAlchemy 真正写出去的那份文本，因此这里逐项对齐它的缺省：引擎没有配
    ``json_serializer``，用的就是裸 ``json.dumps``——``ensure_ascii=True`` 把非 ASCII escape 成
    ``\\uXXXX``，分隔符带空白。任一项按更紧凑的口径去量，上限都会被真实写入体量突破。
    """
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return len(str(value).encode("utf-8"))


def bound_provider_response(body: object) -> object:
    """把最后一次供应商响应限制在 64 KiB；超限保留可诊断前缀与截断标记。"""
    if _persisted_size(body) <= MAX_PROVIDER_RESPONSE_BYTES:
        return body
    try:
        text = json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError, RecursionError):
        text = str(body)
    # 逐次减半到包装后的写出体量真正落进上限内：escape 的膨胀率随内容而变（CJK 2 倍、
    # 表情符号 3 倍），按固定比例预留兜不住最坏情形。
    prefix = text[: MAX_PROVIDER_RESPONSE_BYTES // 2]
    while prefix and _persisted_size({"truncated": True, "body": prefix}) > MAX_PROVIDER_RESPONSE_BYTES:
        prefix = prefix[: len(prefix) // 2]
    return {"truncated": True, "body": prefix}


# segment_id 为 NULL（资产图、文本调用等非分镜维度）的记账在按 segment 汇总时归入的哨兵键。
# 消费方按此键把项目级支出与分镜级支出分开，故键名在生产/消费两侧共用同一常量。前导 NUL 保证
# 它撞不上任何真实 segment_id——后者取自 resource_id（分镜/单元 ID、资产名），不含控制字符。
# 撞键会让剧本里同名的那个单元的支出既算进集合计、又作为项目级支出再算一次。
PROJECT_LEVEL_SEGMENT_KEY = "\x00__project__"

# 产出项目媒体（资产图、分镜、视频、配音）的调用类型
_MEDIA_CALL_TYPES = ("image", "video", "audio")


@dataclass(frozen=True)
class SettlementInput:
    """仓储写侧的申报值对象：承载 caller 在快照时刻提交的原始计费维度。

    这些是"申报时刻"的原始值——``billed_duration_seconds`` 可能非法/超限、显式
    ``cost_amount`` 绕过自动计算——由 ``_settle`` 归一为生效值。

    ``usage`` 是按用量契约上报的计量项用量与选择维度，每次供应商请求一份（降级链上被丢弃的请求也已计费，各占
    一份）：内置与自定义供应商的这次调用按所对应条目的价目表逐次计价后相加；不给时计量项全部落空，记为未定价。
    供应商既不是内置也不是自定义时记为未定价。Agent 会话的轮次不经这里，见 :meth:`UsageRepository.record_agent_turn`。
    三项缓存 token 是各次请求之和，原样写入同名列。新增计费字段自此只扩本对象，不再穿仓储写侧散参。
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
    """Agent 会话一轮里一个模型的用量，写成一条供应商调用记录。

    ``requests`` 是这一轮各次请求的计量，计价时各自落上下文档；四个 token 字段是各次请求之和，``input_tokens``
    是总输入（含缓存读与两档缓存写）。
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
    """``_settle`` 输出：两条快照路径共用的生效结算值，各自 UPDATE 直接取用。"""

    duration_ms: int
    effective_duration_seconds: int | None
    effective_generate_audio: bool | None
    input_tokens: int | None
    output_tokens: int | None
    #: ``None`` 表示未定价，原因见 ``unpriced_reason``。
    cost_amount: float | None
    currency: str
    unpriced_reason: str | None = None


def _classify_asset_output_path(output_path: str | None) -> str:
    """从 api_call.output_path 推断资产类型（characters/scenes/props/products/other）。

    v0→v1 迁移前的历史任务会写入 ``clues/...`` 路径，这里归并到 props，
    与迁移默认的 clue→prop 映射一致，避免旧账单被静默归入 other 而丢失。
    """
    if not output_path:
        return "other"
    # 兼容绝对路径与相对路径
    normalized = output_path.replace("\\", "/").lower()
    for asset_type in ("characters", "scenes", "props", "products"):
        if f"/{asset_type}/" in normalized or normalized.startswith(f"{asset_type}/"):
            return asset_type
    if "/clues/" in normalized or normalized.startswith("clues/"):
        return "props"
    return "other"


@dataclass(frozen=True, slots=True)
class InterruptedCallSettlement:
    """启动收口翻掉的一行：调用 id、所属项目与翻成的终态，供上层发项目事件。"""

    call_id: int
    project_name: str
    status: CallStatus


def _interrupted_target(task_id: str | None, task_statuses: dict[str, str]) -> tuple[CallStatus, str | None] | None:
    """判定一行 pending 调用该翻成什么终态；任务仍存活时返回 ``None``（不动这一行）。

    - 无 ``task_id``：这次调用不归属任何任务（端点试跑、文本调用等），进程重启后没有任何
      在跑的东西能结算它 —— 翻 failed 并写 ``interrupted``，读侧据此渲染「被中断」。
    - ``task_id`` 查不到任务行：任务已被清理，调用同样无人接续，与上一条同处理。
    - 任务已终态：按任务的结局翻（cancelled → cancelled，succeeded / failed → failed）。
      成功任务也翻 failed —— 调用没走完结算就是没记成账，把它记成 success 会凭空补一笔费用。
    - 任务未终态（queued / running）：还活着，它自己的结算路径会收尾。
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
        provider: str = PROVIDER_GEMINI,
        user_id: str = DEFAULT_USER_ID,
        segment_id: str | None = None,
        task_id: str | None = None,
        purpose: CallPurpose | None = None,
        session_id: str | None = None,
        inputs: object | None = None,
    ) -> int:
        """落一条 pending 行并返回它的 id。

        ``prompt`` 全文入库（详情要显示完整输入）；``task_id`` 空表示这次调用不服务任何生成
        任务，此时来源由 ``purpose`` 说明。
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
        """两条快照路径共享的结算函数：输入 ApiCall 行与申报值，输出生效计费时长、
        生效有声标志、duration_ms、费用与币种（含 OpenAI 图片 token 聚合列归一）。

        - duration_ms 按 ``finished_at - started_at`` 回写；SQLite 跨 session 回读的
          ``started_at`` 为 naive datetime，相减前按 UTC 补齐 tzinfo 与 aware 的
          ``finished_at`` 对齐口径；计算异常仍兜底为 0（最终防线）。
        - 计费时长/有声标志覆盖：backend 回报值覆盖 start_call 时的请求值，非正或超出
          ``MAX_BILLED_DURATION_SECONDS`` 的计费时长视同未提供、回落请求时长。
        - 费用：显式 ``cost_amount`` 视作 provider 直报计费数据、优先于自动计算；否则在
          ``auto_calc`` 为真时计算并冻结：内置与自定义供应商的调用按模型目录的价目表与两级倍率计价，查不到单价时
          费用为 ``None`` 并带未定价原因；其余供应商记为未定价（``no_entry``）。
        - ``base_currency`` 由 caller 传入承载各自的币种兜底口径（finish_call 兜底
          ``row.currency``，resume 兜底 ``settlement.currency``），保持行为不分叉。

        防重复计费的 pending 守卫由各 public 方法的 UPDATE WHERE 子句显式承担，不在此。
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

        # OpenAI 图片调用：input_tokens/output_tokens 列的"总和"语义
        # = image_*_tokens + text_*_tokens（用于跨 call_type 聚合查询保持兼容）。
        # resume 路径无图片 token，has_image_tokens 恒 False → 保持申报值原样。
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
            # 没有上报用量的调用同样走价目表：计量项全部落空，记为未定价，不按秒数或默认模型猜。
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
            # 自定义供应商按所对应条目的价目表与两级倍率计价，对应不上条目时记为未定价（docs/adr/0106）。没有上报
            # 用量时计量项全部落空，同样记为未定价，不按媒体类型硬算、不猜秒数。
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
            # 供应商既不在注册表、也不是自定义供应商（如已下线供应商的存量行）：对应不上条目，记为未定价。
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
        """按任务反查它那条尚未结算的调用行 id；没有则 None。

        任务与调用的关联只有 ``api_calls.task_id`` 一个真相源。一个任务至多一条 pending 调用
        （重试下载与 resume 都原地复用同一行），并发提交时按 id 取最新的那条。
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
        """Resume 路径专用：按 call_id 精准翻 pending → success/failed。

        Repo WHERE 子句包含 ``status='pending'`` —— 已 success 行不 touch
        （防止 generate 已 finish_call 后崩、resume 反向把 success 行覆写）；provider 端
        已扣费的事实由此守卫，绝不触发再次扣费。结算逻辑（计费时长/有声覆盖、自动 cost、
        duration_ms 回写）与 finish_call 共享 ``_settle``，唯币种兜底口径按 resume 语义
        取 ``settlement.currency``。返回受影响行数（0=幂等无操作；1=正常翻一行）。

        失败三元组与 ``finish_call`` 同口径（原文截断 500 字），只在调用方给了失败信息时才写：
        success / cancelled 出口不带它，那三列保持原样。
        """
        finished_at = utc_now()

        # 无条件 fetch row：既用于 auto-calc cost 路径，也用于 duration_ms 回写
        # （即便 caller 显式传 cost_amount，duration_ms 计算仍需要 started_at）。
        # row.status='pending' 守卫继续由下面的 UPDATE WHERE 子句保证幂等性。
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
        """取该调用所属项目名；行不存在时返回空串（调用方据此不发事件）。"""
        result = await self.session.execute(select(ApiCall.project_name).where(ApiCall.id == call_id))
        return result.scalar_one_or_none() or ""

    async def settle_interrupted_pending_calls(
        self, *, taskless_started_before: datetime | None
    ) -> list[InterruptedCallSettlement]:
        """服务启动收口：把没有存活任务的 pending 调用行翻成终态（零费用），返回翻掉的行。

        进程崩溃/重启会让「已落 pending、还没结算」的调用行永远停在 pending —— 用量报表里
        它既不是成功也不是失败，只是一直悬着。启动时一次性收口：分流规则见 ``_interrupted_target``，
        有存活任务的行一律不动（它们的结算路径还在）。

        无任务身份的调用（文本调用、端点试跑）由发起它的进程自己结算，只有「本进程启动之前
        发起的」才能断定没人接续：``taskless_started_before`` 给这个时刻，只收口在它之前发起的
        无任务行；进程启动后、收口跑起来之前发起的调用仍在跑，不碰。传 ``None`` 则完全不碰
        无任务行（进程存活期间的重扫）。

        零费用是这里的口径：调用没走完结算，没有任何可信的计费维度可用，按 0 记账不给用户凭空
        补账；``duration_ms`` 同理写 NULL（墙钟时长不是调用耗时，长期停摆的行还会超出整数宽度）。
        每行的 UPDATE 带 ``status='pending'`` 守卫，与并发的正常结算竞态时只会有一方生效。
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
            task_statuses = {task_id: status for task_id, status in rows.all()}  # noqa: C416 -- Row 元组不是二元序列，dict() 无法直接消费

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
                # 中断调用没走完结算，「发起到本次重启」的墙钟时长不是调用耗时，与零费用同口径留空。
                "duration_ms": None,
                "cost_amount": effective.cost_amount,
                "currency": effective.currency,
            }
            if error_code is not None:
                # 已分类必有参数对象（读侧不变量：有码 ⇒ params 是对象），中断没有参数可带。
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
        """Agent 会话的一轮：每个模型写一条终态行，返回各行 id（docs/adr/0102）。

        同一轮的几行共用 ``session_id``、prompt 与写入时间。费用是该模型本轮各请求之和，按 ``pricing``（会话连接时
        注入的 Agent 凭证及其当时的倍率与条目绑定）计价，不随 ``status`` 归零：中断与失败的轮次里已完成的
        请求同样产生了花费。对应不到条目时照常写入 token，费用记为未定价。
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
        """按 segment_id + call_type + currency 汇总实际费用。

        Returns:
            {segment_id: {call_type: {currency: total_amount}}}
            segment_id 为 None 的记录归入 ``PROJECT_LEVEL_SEGMENT_KEY`` 键。
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
        """成功但未定价（``cost_amount`` 为 NULL）的媒体调用，按 (call_type, provider, model) 计数。

        0 是定价为零，不在其中；历史上记为 0 的调用不会被追认为未定价。
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
        """本机是否有这个项目成功的媒体调用记录。

        文本调用与失败、进行中的调用都对应不到已有产物的费用，不计入。
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
        """project-level（segment_id is null）的 image 成本按 output_path 前缀分拆。

        Returns:
            {asset_type: {currency: total_amount}}，asset_type ∈ {characters, scenes, props, products, other}。
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
    # 使用记录读接口
    # 筛选维度、投影与游标编码定义在本文件末尾的模块级「使用记录读侧」一节。
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
        """按 ``started_at DESC, id DESC`` 取一页使用记录。

        返回 ``{"items", "next_cursor", "total"}``：``total`` 是筛选后的总数、不随游标变化，
        ``next_cursor`` 为 ``None`` 表示已到末页。``task_type`` 经 ``task_id`` 外连 ``tasks``
        得到，任务行已被清理时留空。
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
        # 多取一行只为判断还有没有下一页，省掉第二次 COUNT；它不进 items。
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
        """单条使用记录的详情；不存在或不在 scope 内返回 ``None``。

        不套列表那条「有任务的 pending 不出现」——那条只为避免进行中区与记录表重复展示，
        按 id 直达（``record=<id>`` 深链）的一行仍应可读。
        """
        stmt = (
            select(ApiCall, Task.task_type)
            .outerjoin(Task, Task.task_id == ApiCall.task_id)
            .where(ApiCall.id == record_id)
        )
        row = (await self.session.execute(self._scope_query(stmt, ApiCall))).first()
        return _record_detail_to_dict(row[0], row[1]) if row is not None else None

    # --- 汇总读接口（GET /usage/summary）------------------------------------------------
    # 只取行，不在 SQL 里聚合：切天与桶填充交给 lib/billing/usage_summary.py，回避 SQLite 与
    # PostgreSQL 的日期函数差异，也让时区与异常判定的边界能脱离数据库单独驱动。

    async def provider_display_names(self, provider_ids: set[str], *, locale: str = DEFAULT_LOCALE) -> dict[str, str]:
        """供应商 id → 目录里的显示名；目录查不到的回退 id 本身，查不到的 Agent 供应商给泛称。"""
        return await provider_display_names(self.session, provider_ids, locale=locale)

    async def fetch_summary_rows(self, *, filters: UsageFilters | None = None) -> list[UsageSummaryRow]:
        """期间内可聚合的调用行（轻投影）；pending 行不参与任何汇总口径。"""
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
        """筛选候选值：期间不限的 distinct，不受本次筛选影响，好让下拉项在筛选后不消失。

        ``agent`` 选定视图：``False`` 只列生成调用的项目、供应商与模型，``True`` 只列 Agent 会话的，
        ``None`` 不分视图。
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

    # --- Agent 会话视图 ------------------------------------------------------------------
    # 记账粒度不变：每轮每个模型一条供应商调用记录。会话视图只是把 Agent 记录按 session_id 聚合展示。

    async def list_agent_sessions(
        self,
        *,
        filters: UsageFilters | None = None,
        limit: int = 20,
        cursor: UsageSessionCursor | None = None,
    ) -> dict[str, Any]:
        """按最近一轮的时间倒序取一页 Agent 会话；每个会话一行。

        ``filters`` 与记录列表同口径，先筛记录再按会话聚合（会话只含落在筛选内的记录）。``session_id``
        为空的记录不成会话，不出现在这里。返回 ``{"items", "next_cursor", "total"}``。
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
        # 多取一行只为判断还有没有下一页。
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

        # 费用按币种分别合计：不同模型的价目表币种可以不同；未定价（NULL）的记录不计入。
        costs_stmt = (
            select(ApiCall.session_id, ApiCall.currency, func.sum(ApiCall.cost_amount))
            .where(*in_page, ApiCall.cost_amount.is_not(None))
            .group_by(ApiCall.session_id, ApiCall.currency)
        )
        costs: dict[str, dict[str, float]] = {}
        for session_id, currency, amount in (await self.session.execute(self._scope_query(costs_stmt, ApiCall))).all():
            costs.setdefault(session_id, {})[currency or "USD"] = float(amount or 0.0)

        # 模型按在会话里首次出现的先后列出。
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
                    # 会话元数据已删除时标题为 None，由界面显示「已删除的会话」。
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
        """``api_calls.session_id``（SDK 会话 id）→ 会话标题；会话元数据已删除的不在结果里。"""
        stmt = select(AgentSession.sdk_session_id, AgentSession.title).where(
            AgentSession.sdk_session_id.in_(session_ids)
        )
        rows = (await self.session.execute(self._scope_query(stmt, AgentSession))).all()
        return {sdk_session_id: title or "" for sdk_session_id, title in rows}

    async def list_agent_session_records(self, session_id: str) -> list[dict[str, Any]]:
        """一个 Agent 会话的全部记录，按时间正序；同一轮的几条记录写入时间相同，自然排在一起。"""
        stmt = (
            select(ApiCall)
            .where(*usage_filter_clauses(UsageFilters(agent=True)), ApiCall.session_id == session_id)
            .order_by(ApiCall.started_at, ApiCall.id)
        )
        rows = (await self.session.execute(self._scope_query(stmt, ApiCall))).scalars().all()
        return [_session_record_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 使用记录读侧
#
# 这一段只读，与上面的记账写侧互不引用：写侧关心一次调用如何结算，读侧把 api_calls
# 投影成「使用记录」交给设置页与顶栏。筛选维度、UTC 归一与游标编码在此定义一次，
# ``/usage/records`` 与 ``/usage/summary`` 共用。
# ---------------------------------------------------------------------------


class UsageCursorError(ValueError):
    """游标串无法解码——不是合法 base64，或载荷不是 ``(started_at, id)``。"""


@dataclass(frozen=True)
class UsageFilters:
    """使用记录的筛选维度，``/usage/records`` 与 ``/usage/summary`` 同口径。

    ``providers`` / ``models`` / ``media_types`` 是多选，空元组表示该维度不筛。
    ``since`` / ``until`` 是作用于 ``started_at`` 的半开区间 ``[since, until)``。
    """

    project_name: str | None = None
    providers: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()
    since: datetime | None = None
    until: datetime | None = None
    # 视图：``False`` 只要生成调用，``True`` 只要 Agent 会话的记录（来源 assistant_session），
    # ``None`` 不分。汇总默认不分，计入合计的口径包含 Agent。
    agent: bool | None = None


def _view_clauses(agent: bool | None) -> list[ColumnElement[bool]]:
    """按视图筛记录。来源为空的记录不是 Agent 记录。"""
    if agent is None:
        return []
    if agent:
        return [ApiCall.purpose == CallPurpose.ASSISTANT_SESSION]
    return [or_(ApiCall.purpose.is_(None), ApiCall.purpose != CallPurpose.ASSISTANT_SESSION)]


def as_utc(value: datetime) -> datetime:
    """无时区的时刻按 UTC 理解，带时区的换算到 UTC。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def usage_filter_clauses(filters: UsageFilters) -> list[ColumnElement[bool]]:
    """把 ``UsageFilters`` 展开成 WHERE 子句。

    ``project_name`` 用 ``is not None`` 而非真值判断：端点试跑的记录以 ``""`` 落库，
    ``project_name=""`` 要能筛出它们，只有 ``None`` 表示不筛这一维。
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
    """记录列表的完整 WHERE：共享维度 + 状态/分镜多选 + 「有任务的 pending 不出现」。

    ``status=pending`` 且 ``task_id`` 非空的行由它服务的那个生成任务代表（进行中区读任务
    store），列表里再出现一次就是重复；无任务的 pending 调用没有别的代表，必须留下。
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
    """keyset 分页的位置：上一页最后一行的 ``(started_at, id)``。

    对客户端不透明——只允许原样回传，故编码成 base64。
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
            # OverflowError：时刻带极端偏移，换算到 UTC 越过 datetime 边界，与解析失败同等对待。
            raise UsageCursorError(f"无法解码的游标: {raw!r}") from exc


@dataclass(frozen=True)
class UsageSessionCursor:
    """会话列表 keyset 分页的位置：上一页最后一个会话的 ``(最近一轮时间, session_id)``。"""

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
    """记录里的时刻一律输出 UTC ISO 串。

    不能直接 ``isoformat``：SQLite 的 ``DateTime(timezone=True)`` 取回来是 naive 的，
    不带偏移量的串会被前端按浏览器本地时区解读，同一行在 SQLite 与 PostgreSQL 上还会
    得到不同含义。
    """
    return as_utc(value).isoformat() if value is not None else None


def _record_to_dict(row: ApiCall, task_type: str | None) -> dict[str, Any]:
    """一次调用的列表投影。``user_id`` 不出现在响应里。"""
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
    """详情比列表多三项重载荷：提示词全文、输入与最后一次供应商响应。"""
    return {
        **_record_to_dict(row, task_type),
        "prompt": row.prompt,
        "inputs": row.inputs,
        "last_provider_response": row.last_provider_response,
    }


# 会话明细里「消息」一栏的截断长度：同一轮的几条记录共用同一条提示词，全文放进每一行没有意义。
SESSION_PROMPT_PREVIEW_CHARS = 300


def _session_record_to_dict(row: ApiCall) -> dict[str, Any]:
    """会话明细里一条记录的投影：时间、这一轮的用户消息、模型、各项 token 与费用。"""
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
