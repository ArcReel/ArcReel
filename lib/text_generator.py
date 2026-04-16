"""TextGenerator — 文本生成 + 用量追踪包装层。

类似 MediaGenerator，组合 TextBackend + UsageTracker，
调用方无需关心 usage tracking 细节。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lib.text_backends.base import (
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskType,
)
from lib.text_backends.factory import create_text_backend_for_task
from lib.usage_tracker import UsageTracker

if TYPE_CHECKING:
    from lib.text_backends.base import TextBackend

logger = logging.getLogger(__name__)


class TextGenerator:
    """组合 TextBackend + UsageTracker，统一封装文本生成 + 用量追踪。"""

    def __init__(self, backend: TextBackend, usage_tracker: UsageTracker):
        self.backend = backend
        self.usage_tracker = usage_tracker

    @property
    def model(self) -> str:
        """当前 backend 的模型名称。"""
        return self.backend.model

    @classmethod
    async def create(
        cls,
        task_type: TextTaskType,
        project_name: str | None = None,
    ) -> TextGenerator:
        """工厂方法：根据任务类型创建对应的 backend + usage_tracker。"""
        backend = await create_text_backend_for_task(task_type, project_name)
        usage_tracker = UsageTracker()
        return cls(backend, usage_tracker)

    async def generate(
        self,
        request: TextGenerationRequest,
        project_name: str | None = None,
    ) -> TextGenerationResult:
        """生成文本并自动记录用量。

        若 request.max_output_tokens 超过 registry 中该模型的硬上限，
        自动 clamp 并 warning，避免输出被静默截断。
        """
        # --- clamp max_output_tokens ---
        if request.max_output_tokens is not None:
            from lib.config.registry import get_model_max_output_tokens

            model_limit = get_model_max_output_tokens(self.backend.name, self.backend.model)
            if model_limit is not None and request.max_output_tokens > model_limit:
                logger.warning(
                    "请求 max_output_tokens=%d 超过 %s/%s 上限 %d，已自动 clamp。"
                    "建议切换到更大输出上限的模型。",
                    request.max_output_tokens,
                    self.backend.name,
                    self.backend.model,
                    model_limit,
                )
                request = TextGenerationRequest(
                    prompt=request.prompt,
                    response_schema=request.response_schema,
                    images=request.images,
                    system_prompt=request.system_prompt,
                    max_output_tokens=model_limit,
                )

        call_id = await self.usage_tracker.start_call(
            project_name=project_name or "",
            call_type="text",
            model=self.backend.model,
            prompt=request.prompt[:500],
            provider=self.backend.name,
        )
        try:
            result = await self.backend.generate(request)
            await self.usage_tracker.finish_call(
                call_id,
                status="success",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            return result
        except Exception as e:
            await self.usage_tracker.finish_call(
                call_id,
                status="failed",
                error_message=str(e)[:500],
            )
            raise

