"""TextGenerator — 文本生成 + 记账包装层。

类似 MediaGenerator，组合 TextBackend + Ledger，
调用方无需关心记账细节。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from lib.backends.providers import CallPurpose, require_provider_pair
from lib.backends.text_backends.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    TextGenerationRequest,
    TextGenerationResult,
    TextOutputTruncatedError,
    TextTaskType,
)
from lib.backends.text_backends.factory import create_text_backend_for_task, text_model_output_limit
from lib.billing.ledger import Ledger
from lib.db.base import DEFAULT_USER_ID

if TYPE_CHECKING:
    from lib.backends.text_backends.base import TextBackend

logger = logging.getLogger(__name__)


def effective_max_output_tokens(registered: int | None) -> int:
    """文本请求实际生效的输出上限：取 min(模型登记的最大输出长度, 64000)，未登记按 64000。"""
    if registered is None or registered <= 0:
        return DEFAULT_MAX_OUTPUT_TOKENS
    return min(registered, DEFAULT_MAX_OUTPUT_TOKENS)


class TextGenerator:
    """组合 TextBackend + Ledger，统一封装文本生成 + 记账。

    每次请求的输出上限按 :func:`effective_max_output_tokens` 收口：调用方传入的值只能更低，
    不能越过模型登记的最大输出长度与 64000 的共同上限。
    """

    def __init__(
        self,
        backend: TextBackend,
        ledger: Ledger,
        provider_id: str,
        *,
        purpose: CallPurpose,
        user_id: str = DEFAULT_USER_ID,
        registered_max_output_tokens: int | None = None,
        custom_model: bool = False,
    ):
        require_provider_pair("text", backend, provider_id)
        self.backend = backend
        self.ledger = ledger
        self._provider_id = provider_id
        self._purpose = purpose
        self._user_id = user_id
        self._max_output_tokens = effective_max_output_tokens(registered_max_output_tokens)
        self._custom_model = custom_model

    @property
    def model(self) -> str:
        """当前 backend 的模型名称。"""
        return self.backend.model

    @property
    def max_output_tokens(self) -> int:
        """本生成器每次请求实际生效的输出上限（单位 token）。"""
        return self._max_output_tokens

    @classmethod
    async def create(
        cls,
        task_type: TextTaskType,
        project_name: str | None = None,
        *,
        purpose: CallPurpose,
        user_id: str = DEFAULT_USER_ID,
    ) -> TextGenerator:
        """工厂方法：根据任务类型创建对应的 backend + ledger。

        ``purpose`` 是必填的：文本调用没有任务可回指，来源只能由调用点声明，
        同一个 ``task_type`` 会服务多种来源（剧本生成与分集规划都走 SCRIPT）。
        """
        backend, provider_id = await create_text_backend_for_task(task_type, project_name)
        limit = await text_model_output_limit(provider_id, backend.model)
        return cls(
            backend,
            Ledger(),
            provider_id,
            purpose=purpose,
            user_id=user_id,
            registered_max_output_tokens=limit.registered_max_output_tokens,
            custom_model=limit.custom_model,
        )

    async def generate(
        self,
        request: TextGenerationRequest,
        project_name: str | None = None,
        *,
        require_complete: bool = False,
    ) -> TextGenerationResult:
        """生成文本并自动记录用量。

        结构化输出被截断时重新抛出的 :class:`TextOutputTruncatedError` 带上解析层 provider_id、
        模型 ID 与是否为自定义供应商的模型。``require_complete`` 供需要完整回复的自由文本请求使用
        （如回复须整段解析为 JSON）：截断时同样抛出该异常，不返回残缺的结果。
        """
        ceiling = self._max_output_tokens
        request = replace(
            request,
            max_output_tokens=min(request.max_output_tokens, ceiling) if request.max_output_tokens else ceiling,
        )
        async with self.ledger.record(
            project_name=project_name or "",
            call_type="text",
            model=self.backend.model,
            prompt=request.prompt,
            provider=self._provider_id,
            user_id=self._user_id,
            purpose=self._purpose,
        ) as call:
            try:
                result = await self.backend.generate(request)
            except TextOutputTruncatedError as exc:
                raise self._truncation(exc.provider, exc.output_tokens) from exc
            if result.truncated and require_complete:
                raise self._truncation(result.provider, result.output_tokens)
            call.success(result)
            return result

    def _truncation(self, provider: str, output_tokens: int | None) -> TextOutputTruncatedError:
        return TextOutputTruncatedError(
            provider=provider,
            model=self.backend.model,
            output_tokens=output_tokens,
            provider_id=self._provider_id,
            custom_model=self._custom_model,
        )
