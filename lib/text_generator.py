"""TextGenerator — 文本生成 + 记账包装层。

类似 MediaGenerator，组合 TextBackend + Ledger，
调用方无需关心记账细节。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lib.ledger import Ledger
from lib.text_backends.base import (
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskType,
)
from lib.text_backends.factory import create_text_backend_for_task

if TYPE_CHECKING:
    from lib.text_backends.base import TextBackend

logger = logging.getLogger(__name__)


class TextGenerator:
    """组合 TextBackend + Ledger，统一封装文本生成 + 记账。"""

    def __init__(self, backend: TextBackend, ledger: Ledger):
        self.backend = backend
        self.ledger = ledger

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
        """工厂方法：根据任务类型创建对应的 backend + ledger。"""
        backend = await create_text_backend_for_task(task_type, project_name)
        return cls(backend, Ledger())

    async def generate(
        self,
        request: TextGenerationRequest,
        project_name: str | None = None,
    ) -> TextGenerationResult:
        """生成文本并自动记录用量。"""
        async with self.ledger.record(
            project_name=project_name or "",
            call_type="text",
            model=self.backend.model,
            prompt=request.prompt[:500],
            provider=self.backend.name,
        ) as call:
            result = await self.backend.generate(request)
            call.success(result)
            return result
