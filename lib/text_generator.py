"""TextGenerator — text generation + usage tracking wrapper layer.

Similar to MediaGenerator, combines TextBackend + UsageTracker so that
callers do not need to worry about usage tracking details.
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
    """Combines TextBackend + UsageTracker to uniformly encapsulate text generation + usage tracking."""

    def __init__(self, backend: TextBackend, usage_tracker: UsageTracker):
        self.backend = backend
        self.usage_tracker = usage_tracker

    @property
    def model(self) -> str:
        """Model name of the current backend."""
        return self.backend.model

    @classmethod
    async def create(
        cls,
        task_type: TextTaskType,
        project_name: str | None = None,
    ) -> TextGenerator:
        """Factory method: create the appropriate backend + usage_tracker for the given task type."""
        backend = await create_text_backend_for_task(task_type, project_name)
        usage_tracker = UsageTracker()
        return cls(backend, usage_tracker)

    async def generate(
        self,
        request: TextGenerationRequest,
        project_name: str | None = None,
    ) -> TextGenerationResult:
        """Generate text and automatically record usage."""
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
