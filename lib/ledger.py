"""记账账本（ledger）：三通道封装 API 调用记账落库。

对外三个通道对应三种真实记账形态：

1. **记账括号**（``record`` async context manager）—— image / audio / video / text 四条生成
   路径用。进入即落 pending 行并在块内暴露 ``call_id``（任务调用以 ``api_calls.task_id``
   关联）；成功以 ``call.success(result)`` 显式递交 backend 结果对象；``Exception`` 分支自动
   翻 failed（原文截断落 ``error_message``，可识别的失败类别另落 ``error_code`` + ``error_params``，
   见 :mod:`lib.call_failure`）后原样重抛，且记账失败不吞原异常；``CancelledError`` 先结算为
   cancelled（零费用）再原样重抛；正常退出未声明成功抛 ``RuntimeError``。
2. **resume 补账**（``resume_success`` / ``resume_failed`` / ``resume_cancelled``）—— 按 ``call_id`` 精准翻 pending，
   幂等守卫（``WHERE status='pending'``）由仓储承担；finalize 自身异常不吞、直接冒泡（交
   worker finally 兜底）。
3. **事后补录**（``backfill``）—— agent 会话用量一次调用写入终态行（含 SDK 直报费用），对调用
   方省掉需要自行管理的 pending 中间态。

成功通道的 backend 结果对象 union 分发集中在 ``_settlement_from_result``：audio 合成字符数、
video 实际计费时长两处语义转写在此单点完成，调用点成功分支不再有逐字段提取胶水。

结算落库后发一条 ``usage_record / recorded`` 项目变更（走 ``project_change_hints`` 的 batch 总线，
与任务终态事件同一条 SSE），界面据此刷新用量而不必轮询；无项目名的调用（端点试跑）不发，pending
阶段不发。发布口经 ``publish_change`` 注入，带生产默认值。``settle_interrupted_calls`` 是启动收口
入口：把崩溃留下的、没有存活任务的 pending 行翻成终态。

``session_factory`` 注入口保留：``Ledger(session_factory=...)`` 覆盖生产三处接线
（MediaGenerator / TextGenerator / SessionManager）与测试内存库替换需求。写侧直连
``UsageRepository``，不经用量透传层。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, assert_never

from lib.call_failure import CallFailure, classify_call_failure
from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.project_change_hints import emit_project_change_batch
from lib.providers import PROVIDER_GEMINI, CallPurpose, CallStatus, CallType

logger = logging.getLogger(__name__)

#: 记账结算的项目变更条目：entity_type 与项目实体、任务终态都区分开。
USAGE_RECORD_ENTITY_TYPE = "usage_record"
USAGE_RECORD_ACTION = "recorded"

#: 结算事件的发布口。生产默认是项目变更批次总线（与任务终态事件同一条 SSE），测试注入替身。
ProjectChangePublisher = Callable[[str, Sequence[dict[str, Any]]], None]


def build_usage_record_change(call_id: int, status: CallStatus) -> dict[str, Any]:
    """把一次结算转成项目变更 dict。

    与任务终态事件同构，是**刷新信号**而非实体变更：``important=False`` + ``focus=None``，
    消费方据此只重拉用量数据，不弹通知、不触发聚焦跳转。``status`` 让消费方不必回查即可
    判断这次结算是成功还是失败。
    """
    return {
        "entity_type": USAGE_RECORD_ENTITY_TYPE,
        "action": USAGE_RECORD_ACTION,
        "entity_id": str(call_id),
        # label 不进通知文案（important=False），仅作日志/调试可读标识。
        "label": str(call_id),
        "focus": None,
        "important": False,
        "status": str(status),
    }


def _settlement_from_result(call_type: CallType, result: Any, *, service_tier: str = "default") -> SettlementInput:
    """成功通道 union 分发：按 call_type 从 backend 结果对象提取计费维度。

    两处语义转写的唯一落点：
    - audio 的 ``characters``（合成字符数）→ ``usage_tokens``（驱动 per-character 计费）；
    - video 的 ``duration_seconds``（backend 回报的实际计费/生成时长）→
      ``billed_duration_seconds``（覆盖 start_call 时的请求时长）。

    四种 backend 结果对象结构独立、无共同基类，按调用点已知的 ``call_type`` 显式分发。
    """
    if call_type == "image":
        return SettlementInput(
            usage_tokens=result.usage_tokens,
            quality=result.quality,
            image_input_tokens=result.image_input_tokens,
            image_output_tokens=result.image_output_tokens,
            text_input_tokens=result.text_input_tokens,
            text_output_tokens=result.text_output_tokens,
        )
    if call_type == "audio":
        return SettlementInput(usage_tokens=result.characters)
    if call_type == "video":
        return SettlementInput(
            usage_tokens=result.usage_tokens,
            generate_audio=result.generate_audio,
            billed_duration_seconds=result.duration_seconds,
            service_tier=service_tier,
        )
    if call_type == "text":
        return SettlementInput(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
    assert_never(call_type)


class LedgerCall:
    """记账括号句柄：块内暴露 ``call_id``，``success(result)`` 递交 backend 结果对象。"""

    def __init__(self, call_id: int, *, call_type: CallType, service_tier: str) -> None:
        self.call_id = call_id
        self._call_type: CallType = call_type
        self._service_tier = service_tier
        self._settlement: SettlementInput | None = None

    def success(self, result: Any) -> None:
        """声明成功并递交 backend 结果对象；union 分发在此完成，结算在括号退出时执行。"""
        self._settlement = _settlement_from_result(self._call_type, result, service_tier=self._service_tier)


class Ledger:
    def __init__(self, *, session_factory=None, publish_change: ProjectChangePublisher = emit_project_change_batch):
        self._session_factory = session_factory or safe_session_factory
        self._publish_change = publish_change

    def _publish(self, project_name: str, changes: Sequence[dict[str, Any]]) -> None:
        """发一批结算事件。项目名为空（端点试跑）不发；发布失败不向上抛——账已落库，
        事件只是实时性优化，消费方有轮询兜底。"""
        if not project_name or not changes:
            return
        try:
            self._publish_change(project_name, changes)
        except Exception:
            logger.exception("发送记账结算项目事件失败 project=%s count=%d", project_name, len(changes))

    def _emit_recorded(self, project_name: str, call_id: int, status: CallStatus) -> None:
        """结算落库后推一条 ``usage_record / recorded``；pending 阶段不发。"""
        self._publish(project_name, [build_usage_record_change(call_id, status)])

    @asynccontextmanager
    async def record(
        self,
        *,
        project_name: str,
        call_type: CallType,
        model: str,
        provider: str = PROVIDER_GEMINI,
        prompt: str | None = None,
        resolution: str | None = None,
        duration_seconds: int | None = None,
        aspect_ratio: str | None = None,
        generate_audio: bool = True,
        user_id: str = DEFAULT_USER_ID,
        segment_id: str | None = None,
        service_tier: str = "default",
        output_path: str | None = None,
        task_id: str | None = None,
        purpose: CallPurpose | None = None,
        session_id: str | None = None,
        inputs: object | None = None,
    ) -> AsyncGenerator[LedgerCall]:
        """记账括号：进入落 pending，块内 ``call.success(result)`` 声明成功。

        退出语义：``CancelledError`` 结算为 cancelled（零费用）后原样重抛；``Exception`` 翻
        failed 后原样重抛；正常退出但未声明成功抛 ``RuntimeError``；声明成功则以 backend 结果
        对象结算翻 success。
        """
        call_id = await self._start_call(
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
        call = LedgerCall(call_id, call_type=call_type, service_tier=service_tier)
        try:
            yield call
        except asyncio.CancelledError:
            # 用户取消：零费用结算为 cancelled 后原样重抛，不留 pending 也不计入失败率。
            # （CancelledError 在 3.11+ 继承 BaseException 天然不进 except Exception，此处显式
            #  声明为契约，而非依赖隐式继承副作用。）
            if await self._finish_cancelled(call_id):
                self._emit_recorded(project_name, call_id, CallStatus.CANCELLED)
            raise
        except Exception as exc:
            # 自动翻 failed（错误信息截断由仓储承担）后原样重抛。
            if await self._finish_failed(call_id, exc):
                self._emit_recorded(project_name, call_id, CallStatus.FAILED)
            raise
        else:
            if call._settlement is None:
                raise RuntimeError(f"ledger.record(call_type={call_type!r}) 正常退出但未调用 call.success(result)")
            try:
                await self._finish_success(call_id, call._settlement, output_path=output_path)
            except asyncio.CancelledError:
                # 取消打在成功结算的写入上：结果已经拿到、费用已经产生，写入在护盾内落地后
                # 事件照发，取消再继续传播——这条调用是真实花费，不能按零费用取消结算。
                self._emit_recorded(project_name, call_id, CallStatus.SUCCESS)
                raise
            except Exception as exc:
                # 成功结算写入本身失败：不留永久 pending，尝试翻 failed 后原样重抛。
                if await self._finish_failed(call_id, exc):
                    self._emit_recorded(project_name, call_id, CallStatus.FAILED)
                raise
            self._emit_recorded(project_name, call_id, CallStatus.SUCCESS)

    async def pending_call_id_for_task(self, task_id: str) -> int | None:
        """按任务反查它那条待结算的调用行 id，供续跑与派发终态补账定位。"""
        async with self._session_factory() as session:
            return await UsageRepository(session).find_pending_call_id_by_task_id(task_id)

    async def resume_success(self, *, call_id: int, result: Any, service_tier: str = "default") -> int:
        """resume 成功补账：按 call_id 精准翻 pending → success，返回受影响行数（幂等 0/1）。

        finalize 自身异常不吞、直接冒泡（交 worker finally 兜底），避免 ApiCall 永久卡 pending。
        """
        settlement = _settlement_from_result("video", result, service_tier=service_tier)
        return await self._finalize(call_id=call_id, status=CallStatus.SUCCESS, settlement=settlement)

    async def resume_failed(self, *, call_id: int) -> int:
        """resume 过期/失败补账：翻 pending → failed，零费用不重扣（幂等 0/1）。"""
        return await self._finalize(
            call_id=call_id, status=CallStatus.FAILED, settlement=SettlementInput(cost_amount=0.0)
        )

    async def resume_cancelled(self, *, call_id: int) -> int:
        """resume 取消补账：翻 pending → cancelled，零费用不重扣（幂等 0/1）。"""
        return await self._finalize(
            call_id=call_id, status=CallStatus.CANCELLED, settlement=SettlementInput(cost_amount=0.0)
        )

    async def record_provider_response(self, *, call_id: int, body: object) -> None:
        """覆盖保存该供应商调用最后一次收到的响应体。"""
        async with self._session_factory() as session:
            await UsageRepository(session).update_last_provider_response(call_id, body)

    async def backfill(
        self,
        *,
        project_name: str,
        call_type: CallType,
        model: str,
        provider: str,
        prompt: str | None,
        user_id: str,
        status: CallStatus,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        usage_tokens: int | None = None,
        cost_amount: float | None = None,
        currency: str | None = None,
        task_id: str | None = None,
        purpose: CallPurpose | None = None,
        session_id: str | None = None,
        inputs: object | None = None,
    ) -> None:
        """事后补录：一次调用写入终态行（agent 会话用量含 SDK 直报费用）。

        无 backend 结果对象 union —— 用量与 SDK 直报费用由调用方显式给出。内部经 start_call +
        finish_call 复用结算口径（含 SQLite 跨 session 的 duration_ms 兜底语义），调用方不需自行
        管理 pending 中间态。
        """
        if status is CallStatus.PENDING:
            raise ValueError("backfill 只写终态行：status 不能是 pending")
        settlement = SettlementInput(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_tokens=usage_tokens,
            cost_amount=cost_amount,
            currency=currency,
        )
        call_id = await self._start_call(
            project_name=project_name,
            call_type=call_type,
            model=model,
            prompt=prompt,
            provider=provider,
            user_id=user_id,
            task_id=task_id,
            purpose=purpose,
            session_id=session_id,
            inputs=inputs,
        )
        async with self._session_factory() as session:
            await UsageRepository(session).finish_call(call_id, status=status, settlement=settlement)
        self._emit_recorded(project_name, call_id, status)

    async def settle_interrupted_calls(self, *, taskless_started_before: datetime | None) -> int:
        """服务启动收口：把没有存活任务的 pending 调用行翻成终态，返回翻掉的行数。

        进程崩溃会把「已落 pending、未结算」的调用行永远留在 pending。分流规则、零费用口径与
        ``taskless_started_before`` 的含义由仓储承担（见 ``UsageRepository.settle_interrupted_pending_calls``），
        这里只按项目分组把结算事件发出去，让正开着的界面立刻看到这些行不再悬着。
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

    async def _settle[T](self, write: Coroutine[Any, Any, T]) -> T:
        """结算写入不被取消打断：取消到达时先等写入落地，再把取消继续传播。

        括号内的三种终态写入都经此处。写入跑在独立 task 里，外层被取消只中断等待，
        不中断写入；等到写入完成才重抛，行不会因取消时机停在 pending。
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
        """翻 failed；返回是否真的写进去了（写失败时不发事件）。

        记账失败不吞原异常：写入失败仅记日志，原异常继续冒泡。分类也在 try 内：它读的是异常
        自身的属性，真抛出来也只该跟写入失败同样处理，不该顶替原异常冒出去。
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
        """翻 cancelled；返回是否真的写进去了（写失败时不发事件）。

        取消分支同样不吞 CancelledError：写入失败仅记日志，取消照常重抛
        （行留 pending，交由取消方的兜底结算或启动收口）。
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

    async def _finalize(self, *, call_id: int, status: CallStatus, settlement: SettlementInput) -> int:
        async with self._session_factory() as session:
            repo = UsageRepository(session)
            affected = await repo.finalize_pending_by_call_id(call_id=call_id, status=status, settlement=settlement)
            # 事件要带项目名，而 resume 链路只有 call_id；幂等命中 0 行时不查也不发。
            project_name = await repo.get_call_project_name(call_id) if affected else ""
        self._emit_recorded(project_name, call_id, status)
        return affected
