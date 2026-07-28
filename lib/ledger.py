"""记账账本（ledger）：三通道封装 API 调用记账落库。

对外三个通道对应三种真实记账形态：

1. **记账括号**（``record`` async context manager）—— image / audio / video / text 四条生成
   路径用。进入即落 pending 行并在块内暴露 ``call_id``（视频路径先持久化 call_id 再调
   backend）；成功以 ``call.success(result)`` 显式递交 backend 结果对象；``Exception`` 分支自动
   翻 failed（错误信息截断）后原样重抛，且记账失败不吞原异常；``CancelledError`` 穿透不记账
   （留 pending 供 resume 补账）；正常退出未声明成功抛 ``RuntimeError``。
2. **resume 补账**（``resume_success`` / ``resume_failed``）—— 按 ``call_id`` 精准翻 pending，
   幂等守卫（``WHERE status='pending'``）由仓储承担；finalize 自身异常不吞、直接冒泡（交
   worker finally 兜底）。
3. **事后补录**（``backfill``）—— agent 会话用量一次调用写入终态行（含 SDK 直报费用），对调用
   方省掉需要自行管理的 pending 中间态。

成功通道的 backend 结果对象 union 分发集中在 ``_settlement_from_result``：audio 合成字符数、
video 实际计费时长两处语义转写在此单点完成，调用点成功分支不再有逐字段提取胶水。

``session_factory`` 注入口保留：``Ledger(session_factory=...)`` 覆盖生产三处接线
（MediaGenerator / TextGenerator / SessionManager）与测试内存库替换需求。写侧直连
``UsageRepository``，不经用量透传层。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.providers import PROVIDER_GEMINI, CallType

logger = logging.getLogger(__name__)


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
    raise ValueError(f"unknown ledger channel: {call_type!r}")


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
    def __init__(self, *, session_factory=None):
        self._session_factory = session_factory or safe_session_factory

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
    ) -> AsyncIterator[LedgerCall]:
        """记账括号：进入落 pending，块内 ``call.success(result)`` 声明成功。

        退出语义：``CancelledError`` 穿透留 pending；``Exception`` 翻 failed 后原样重抛；正常
        退出但未声明成功抛 ``RuntimeError``；声明成功则以 backend 结果对象结算翻 success。
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
        )
        call = LedgerCall(call_id, call_type=call_type, service_tier=service_tier)
        try:
            yield call
        except asyncio.CancelledError:
            # 取消穿透：显式立约不记账，pending 行留待 resume 补账。
            # （CancelledError 在 3.11+ 继承 BaseException 天然不进 except Exception，此处显式
            #  声明为契约，而非依赖隐式继承副作用。）
            raise
        except Exception as exc:
            # 自动翻 failed（错误信息截断由仓储承担）后原样重抛。
            await self._finish_failed(call_id, exc)
            raise
        else:
            if call._settlement is None:
                raise RuntimeError(f"ledger.record(call_type={call_type!r}) 正常退出但未调用 call.success(result)")
            try:
                await self._finish_success(call_id, call._settlement, output_path=output_path)
            except Exception as exc:
                # 成功结算写入本身失败：不留永久 pending，尝试翻 failed 后原样重抛。
                await self._finish_failed(call_id, exc)
                raise

    async def resume_success(self, *, call_id: int, result: Any, service_tier: str = "default") -> int:
        """resume 成功补账：按 call_id 精准翻 pending → success，返回受影响行数（幂等 0/1）。

        finalize 自身异常不吞、直接冒泡（交 worker finally 兜底），避免 ApiCall 永久卡 pending。
        """
        settlement = _settlement_from_result("video", result, service_tier=service_tier)
        return await self._finalize(call_id=call_id, status="success", settlement=settlement)

    async def resume_failed(self, *, call_id: int) -> int:
        """resume 过期/失败补账：翻 pending → failed，零费用不重扣（幂等 0/1）。"""
        return await self._finalize(call_id=call_id, status="failed", settlement=SettlementInput(cost_amount=0.0))

    async def backfill(
        self,
        *,
        project_name: str,
        call_type: CallType,
        model: str,
        provider: str,
        prompt: str | None,
        user_id: str,
        status: Literal["success", "failed"],
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        usage_tokens: int | None = None,
        cost_amount: float | None = None,
        currency: str | None = None,
    ) -> None:
        """事后补录：一次调用写入终态行（agent 会话用量含 SDK 直报费用）。

        无 backend 结果对象 union —— 用量与 SDK 直报费用由调用方显式给出。内部经 start_call +
        finish_call 复用结算口径（含 SQLite 跨 session 的 duration_ms 兜底语义），调用方不需自行
        管理 pending 中间态。
        """
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
        )
        async with self._session_factory() as session:
            await UsageRepository(session).finish_call(call_id, status=status, settlement=settlement)

    async def _start_call(self, **kwargs: Any) -> int:
        async with self._session_factory() as session:
            return await UsageRepository(session).start_call(**kwargs)

    async def _finish_success(self, call_id: int, settlement: SettlementInput, *, output_path: str | None) -> None:
        async with self._session_factory() as session:
            await UsageRepository(session).finish_call(
                call_id, status="success", settlement=settlement, output_path=output_path
            )

    async def _finish_failed(self, call_id: int, exc: BaseException) -> None:
        # 记账失败不吞原异常：写入失败仅记日志，原异常继续冒泡。
        try:
            async with self._session_factory() as session:
                await UsageRepository(session).finish_call(
                    call_id, status="failed", settlement=SettlementInput(), error_message=str(exc)
                )
        except Exception:
            logger.exception("ledger 失败分支记账写入自身失败 call_id=%s（原异常照常重抛）", call_id)

    async def _finalize(self, *, call_id: int, status: str, settlement: SettlementInput) -> int:
        async with self._session_factory() as session:
            return await UsageRepository(session).finalize_pending_by_call_id(
                call_id=call_id, status=status, settlement=settlement
            )
