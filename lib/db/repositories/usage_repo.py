"""Async repository for API call usage tracking."""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, case, func, or_, select, update

from lib.call_failure import CallErrorCode
from lib.cost_calculator import cost_calculator
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.db.repositories.base import BaseRepository, rowcount
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.pricing.strategies import PricingParams
from lib.providers import PROVIDER_GEMINI, CallPurpose, CallStatus, CallType
from lib.task_terminal_events import TERMINAL_TASK_STATUSES
from lib.usage_summary import UsageFilterOptions, UsageSummaryRow

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

# 存量裸 provider 值的报表显示兜底：身份反转前，文本 gemini 调用以 backend.name 落账为裸
# "gemini"（图像/视频侧已是 "gemini-aistudio"）。这些历史行不迁移，仅在分组报表按此表补一个
# 友好显示名；registry 只登记新格式 key（gemini-aistudio / gemini-vertex），故裸值查不到 meta。
_LEGACY_PROVIDER_DISPLAY_NAMES = {PROVIDER_GEMINI: "Gemini"}


@dataclass(frozen=True)
class SettlementInput:
    """仓储写侧的申报值对象：承载 caller 在快照时刻提交的原始计费维度。

    这些是"申报时刻"的原始值——``billed_duration_seconds`` 可能非法/超限、显式
    ``cost_amount`` 绕过自动计算——由 ``_settle`` 归一为生效定价（``PricingParams``）。
    与 ``PricingParams``（结算后的生效定价输入）刻意分成两个对象，避免原始值与生效值
    在同一结构里语义混淆。新增计费字段自此只扩本对象，不再穿仓储写侧散参。
    """

    cost_amount: float | None = None
    currency: str | None = None
    service_tier: str = "default"
    generate_audio: bool | None = None
    billed_duration_seconds: int | None = None
    usage_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    quality: str | None = None
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None


@dataclass(frozen=True)
class _SettledCall:
    """``_settle`` 输出：两条快照路径共用的生效结算值，各自 UPDATE 直接取用。"""

    duration_ms: int
    effective_duration_seconds: int | None
    effective_generate_audio: bool | None
    input_tokens: int | None
    output_tokens: int | None
    cost_amount: float
    currency: str


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
    - 任务未终态（queued / running / cancelling）：还活着，它自己的结算路径会收尾。
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


def _row_to_dict(row: ApiCall) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_name": row.project_name,
        "call_type": row.call_type,
        "model": row.model,
        "prompt": row.prompt,
        "resolution": row.resolution,
        "duration_seconds": row.duration_seconds,
        "aspect_ratio": row.aspect_ratio,
        "generate_audio": row.generate_audio,
        "status": row.status,
        "error_message": row.error_message,
        "output_path": row.output_path,
        "segment_id": row.segment_id,
        "started_at": dt_to_iso(row.started_at),
        "finished_at": dt_to_iso(row.finished_at),
        "duration_ms": row.duration_ms,
        "retry_count": row.retry_count,
        "cost_amount": row.cost_amount,
        "currency": row.currency,
        "provider": row.provider,
        "usage_tokens": row.usage_tokens,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "image_input_tokens": row.image_input_tokens,
        "image_output_tokens": row.image_output_tokens,
        "text_input_tokens": row.text_input_tokens,
        "text_output_tokens": row.text_output_tokens,
        "last_provider_response": row.last_provider_response,
        "created_at": dt_to_iso(row.created_at),
    }


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
        generate_audio: bool = True,
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
          ``auto_calc`` 为真时按行字段 + 申报值调 ``cost_calculator``（自定义供应商价格预查
          避免 CostCalculator 内 sync-over-async）。
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

        cost_amount = 0.0
        currency = base_currency
        if settlement.cost_amount is not None:
            cost_amount = settlement.cost_amount
            currency = settlement.currency or base_currency
        elif auto_calc:
            effective_provider = row.provider or PROVIDER_GEMINI

            # 自定义供应商价格预查（避免 CostCalculator 内 sync-over-async）；与费用预估链路共用
            # CustomProviderRepository.resolve_price，非自定义 / 畸形 id / 查询异常 / 缺价统一降级为无价。
            custom_price = await CustomProviderRepository(self.session).resolve_price(
                effective_provider, row.model or ""
            )

            params = PricingParams(
                call_type=row.call_type,
                model=row.model,
                resolution=row.resolution,
                aspect_ratio=row.aspect_ratio,
                duration_seconds=effective_duration_seconds,
                generate_audio=bool(effective_generate_audio),
                usage_tokens=settlement.usage_tokens,
                service_tier=settlement.service_tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                quality=settlement.quality,
                image_input_tokens=settlement.image_input_tokens,
                image_output_tokens=settlement.image_output_tokens,
                text_input_tokens=settlement.text_input_tokens,
                text_output_tokens=settlement.text_output_tokens,
            )
            cost_amount, currency = cost_calculator.calculate_cost(
                effective_provider,
                params,
                custom_price_input=custom_price.price_input,
                custom_price_output=custom_price.price_output,
                custom_currency=custom_price.currency,
            )

        return _SettledCall(
            duration_ms=duration_ms,
            effective_duration_seconds=effective_duration_seconds,
            effective_generate_audio=effective_generate_audio,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_amount=cost_amount,
            currency=currency,
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
    ) -> int:
        """Resume 路径专用：按 call_id 精准翻 pending → success/failed。

        Repo WHERE 子句包含 ``status='pending'`` —— 已 success 行不 touch
        （防止 generate 已 finish_call 后崩、resume 反向把 success 行覆写）；provider 端
        已扣费的事实由此守卫，绝不触发再次扣费。结算逻辑（计费时长/有声覆盖、自动 cost、
        duration_ms 回写）与 finish_call 共享 ``_settle``，唯币种兜底口径按 resume 语义
        取 ``settlement.currency``。返回受影响行数（0=幂等无操作；1=正常翻一行）。
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

        result = await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id, ApiCall.status == CallStatus.PENDING)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=settled.duration_ms,
                duration_seconds=settled.effective_duration_seconds,
                cost_amount=settled.cost_amount,
                currency=settled.currency,
                usage_tokens=settlement.usage_tokens,
                generate_audio=settled.effective_generate_audio,
            )
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
        补账。每行的 UPDATE 带 ``status='pending'`` 守卫，与并发的正常结算竞态时只会有一方生效。
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
                "duration_ms": effective.duration_ms,
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
                usage_tokens=settlement.usage_tokens,
                input_tokens=settled.input_tokens,
                output_tokens=settled.output_tokens,
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

    @staticmethod
    def _build_filters(
        *,
        call_id: int | None = None,
        project_name: str | None = None,
        provider: str | None = None,
        call_type: CallType | None = None,
        status: CallStatus | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list:
        filters: list = []
        if call_id is not None:
            filters.append(ApiCall.id == call_id)
        if project_name:
            filters.append(ApiCall.project_name == project_name)
        if provider:
            filters.append(ApiCall.provider == provider)
        if call_type:
            filters.append(ApiCall.call_type == call_type)
        if status:
            filters.append(ApiCall.status == status)
        if start_date:
            start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
            filters.append(ApiCall.started_at >= start)
        if end_date:
            end_exclusive = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(days=1)
            filters.append(ApiCall.started_at < end_exclusive)
        return filters

    async def get_stats(
        self,
        *,
        project_name: str | None = None,
        provider: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        filters = self._build_filters(
            project_name=project_name,
            provider=provider,
            start_date=start_date,
            end_date=end_date,
        )
        # Main aggregation query
        main_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (ApiCall.status == CallStatus.SUCCESS)
                                & (ApiCall.currency == "USD")
                                & (ApiCall.cost_amount > 0),
                                ApiCall.cost_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("total_cost_usd"),
                func.count(case((ApiCall.call_type == "image", 1))).label("image_count"),
                func.count(case((ApiCall.call_type == "video", 1))).label("video_count"),
                func.count(case((ApiCall.call_type == "text", 1))).label("text_count"),
                func.count(case((ApiCall.call_type == "audio", 1))).label("audio_count"),
                func.count(case((ApiCall.status == CallStatus.FAILED, 1))).label("failed_count"),
                func.count().label("total_count"),
            )
            .select_from(ApiCall)
            .where(*filters)
        )
        main_stmt = self._scope_query(main_stmt, ApiCall)
        row = (await self.session.execute(main_stmt)).one()

        # Cost by currency mirrors project cost estimates: only successful billed calls count.
        currency_stmt = (
            select(
                ApiCall.currency,
                func.coalesce(func.sum(ApiCall.cost_amount), 0).label("total"),
            )
            .select_from(ApiCall)
            .where(
                *filters,
                ApiCall.status == CallStatus.SUCCESS,
                ApiCall.cost_amount > 0,
                ApiCall.currency.isnot(None),
            )
            .group_by(ApiCall.currency)
        )
        currency_stmt = self._scope_query(currency_stmt, ApiCall)
        currency_rows = (await self.session.execute(currency_stmt)).all()

        cost_by_currency = {r.currency: round(r.total, 4) for r in currency_rows}

        return {
            "total_cost": round(row.total_cost_usd, 4),
            "cost_by_currency": cost_by_currency,
            "image_count": row.image_count,
            "video_count": row.video_count,
            "text_count": row.text_count,
            "audio_count": row.audio_count,
            "failed_count": row.failed_count,
            "total_count": row.total_count,
        }

    async def get_stats_grouped_by_provider(
        self,
        *,
        project_name: str | None = None,
        provider: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        filters = self._build_filters(
            project_name=project_name,
            provider=provider,
            start_date=start_date,
            end_date=end_date,
        )

        stmt = (
            select(
                ApiCall.provider,
                ApiCall.call_type,
                func.count().label("total_calls"),
                func.count(case((ApiCall.status == CallStatus.SUCCESS, 1))).label("success_calls"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (ApiCall.status == CallStatus.SUCCESS)
                                & (ApiCall.currency == "USD")
                                & (ApiCall.cost_amount > 0),
                                ApiCall.cost_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("total_cost_usd"),
                func.coalesce(func.sum(ApiCall.duration_ms), 0).label("total_duration_ms"),
            )
            .select_from(ApiCall)
            .where(*filters)
            .group_by(ApiCall.provider, ApiCall.call_type)
            .order_by(ApiCall.provider, ApiCall.call_type)
        )
        stmt = self._scope_query(stmt, ApiCall)
        rows = (await self.session.execute(stmt)).all()

        currency_stmt = (
            select(
                ApiCall.provider,
                ApiCall.call_type,
                ApiCall.currency,
                func.coalesce(func.sum(ApiCall.cost_amount), 0).label("total"),
            )
            .select_from(ApiCall)
            .where(
                *filters,
                ApiCall.status == CallStatus.SUCCESS,
                ApiCall.cost_amount > 0,
                ApiCall.currency.isnot(None),
            )
            .group_by(ApiCall.provider, ApiCall.call_type, ApiCall.currency)
        )
        currency_stmt = self._scope_query(currency_stmt, ApiCall)
        currency_rows = (await self.session.execute(currency_stmt)).all()
        cost_by_group: dict[tuple[str | None, str | None], dict[str, float]] = {}
        for provider_value, call_type_value, currency, total in currency_rows:
            cost_by_group.setdefault((provider_value, call_type_value), {})[currency] = round(total, 4)

        stats = [
            {
                "provider": row.provider,
                "call_type": row.call_type,
                "total_calls": row.total_calls,
                "success_calls": row.success_calls,
                "total_cost_usd": round(row.total_cost_usd, 4),
                "cost_by_currency": cost_by_group.get((row.provider, row.call_type), {}),
                "total_duration_seconds": round(row.total_duration_ms / 1000, 1) if row.total_duration_ms else 0,
            }
            for row in rows
        ]

        # Enrich each stat entry with display_name (batch query for custom providers)
        from lib.config.registry import PROVIDER_REGISTRY
        from lib.db.models.custom_provider import CustomProvider

        custom_ids = set()
        for stat in stats:
            p = stat["provider"]
            if p and is_custom_provider(p):
                # 防御畸形 provider 字符串（如 "custom-abc"）
                with contextlib.suppress(ValueError):
                    custom_ids.add(parse_provider_id(p))

        custom_names: dict[int, str] = {}
        if custom_ids:
            cp_stmt = select(CustomProvider).where(CustomProvider.id.in_(custom_ids))
            cp_rows = (await self.session.execute(cp_stmt)).scalars()
            custom_names = {cp.id: cp.display_name for cp in cp_rows}

        for stat in stats:
            provider_str = stat["provider"]
            if provider_str and is_custom_provider(provider_str):
                try:
                    db_id = parse_provider_id(provider_str)
                    stat["display_name"] = custom_names.get(db_id, provider_str)
                except ValueError:
                    stat["display_name"] = provider_str
            else:
                meta = PROVIDER_REGISTRY.get(provider_str or "")
                if meta:
                    stat["display_name"] = meta.display_name
                else:
                    stat["display_name"] = _LEGACY_PROVIDER_DISPLAY_NAMES.get(provider_str or "", provider_str)

        period_start: str | None = None
        period_end: str | None = None
        if start_date:
            period_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC).isoformat()
        if end_date:
            period_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC).isoformat()

        return {
            "stats": stats,
            "period": {"start": period_start, "end": period_end},
        }

    async def get_calls(
        self,
        *,
        call_id: int | None = None,
        project_name: str | None = None,
        call_type: CallType | None = None,
        status: CallStatus | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        filters = self._build_filters(
            call_id=call_id,
            project_name=project_name,
            call_type=call_type,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        # Total count
        count_stmt = select(func.count()).select_from(ApiCall).where(*filters)
        count_stmt = self._scope_query(count_stmt, ApiCall)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # Paginated items
        offset = (page - 1) * page_size
        items_stmt = select(ApiCall).where(*filters).order_by(ApiCall.started_at.desc()).limit(page_size).offset(offset)
        items_stmt = self._scope_query(items_stmt, ApiCall)
        result = await self.session.execute(items_stmt)
        items = [_row_to_dict(row) for row in result.scalars().all()]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

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

    async def get_projects_list(self) -> list[str]:
        stmt = select(ApiCall.project_name).distinct().order_by(ApiCall.project_name)
        stmt = self._scope_query(stmt, ApiCall)
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

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
    # 只取行，不在 SQL 里聚合：切天与桶填充交给 lib/usage_summary.py，回避 SQLite 与
    # PostgreSQL 的日期函数差异，也让时区与异常判定的边界能脱离数据库单独驱动。

    async def _provider_display_names(self, provider_ids: set[str]) -> dict[str, str]:
        """供应商 id → 目录里的显示名；目录查不到的回退 id 本身。"""
        from lib.config.registry import PROVIDER_REGISTRY
        from lib.db.models.custom_provider import CustomProvider

        custom_db_ids: set[int] = set()
        for provider_id in provider_ids:
            if is_custom_provider(provider_id):
                # 防御畸形 provider 字符串（如 "custom-abc"）
                with contextlib.suppress(ValueError):
                    custom_db_ids.add(parse_provider_id(provider_id))

        custom_names: dict[int, str] = {}
        if custom_db_ids:
            cp_stmt = select(CustomProvider).where(CustomProvider.id.in_(custom_db_ids))
            custom_names = {cp.id: cp.display_name for cp in (await self.session.execute(cp_stmt)).scalars()}

        names: dict[str, str] = {}
        for provider_id in provider_ids:
            if is_custom_provider(provider_id):
                try:
                    names[provider_id] = custom_names.get(parse_provider_id(provider_id), provider_id)
                except ValueError:
                    names[provider_id] = provider_id
                continue
            meta = PROVIDER_REGISTRY.get(provider_id)
            names[provider_id] = (
                meta.display_name if meta else _LEGACY_PROVIDER_DISPLAY_NAMES.get(provider_id, provider_id)
            )
        return names

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
                cost_amount=row.cost_amount or 0.0,
                currency=row.currency or "USD",
                segment_id=row.segment_id,
                error_code=row.error_code,
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def fetch_usage_filter_options(self) -> UsageFilterOptions:
        """筛选候选值：全表 distinct，不受本次筛选影响，好让下拉项在筛选后不消失。"""
        projects_stmt = self._scope_query(select(ApiCall.project_name).distinct(), ApiCall)
        models_stmt = self._scope_query(select(ApiCall.provider, ApiCall.model).distinct(), ApiCall)

        projects = sorted({row[0] for row in (await self.session.execute(projects_stmt)).all()})
        models = sorted({(row.provider, row.model) for row in (await self.session.execute(models_stmt)).all()})
        provider_ids = {provider for provider, _ in models}
        labels = await self._provider_display_names(provider_ids)

        return UsageFilterOptions(
            projects=projects,
            providers=[(provider, labels[provider]) for provider in sorted(provider_ids)],
            models=models,
        )


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
        "currency": row.currency,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
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
