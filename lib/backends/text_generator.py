"""TextGenerator: text generation and accounting wrapper.

Like MediaGenerator, compose TextBackend + Ledger so callers need not handle accounting details.
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
    """Effective text output limit: min(registered model maximum, 64000), defaulting to 64000 when unregistered."""
    if registered is None or registered <= 0:
        return DEFAULT_MAX_OUTPUT_TOKENS
    return min(registered, DEFAULT_MAX_OUTPUT_TOKENS)


class TextGenerator:
    """Compose TextBackend + Ledger, consistently wrapping text generation/accounting.

    Centralize per-request output limits through :func:`effective_max_output_tokens`: caller values
    can only lower them, never exceeding the joint model-registered and 64000 ceiling.
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
        """Current backend model name."""
        return self.backend.model

    @property
    def max_output_tokens(self) -> int:
        """Effective per-request output-token limit for this generator."""
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
        """Factory constructing backend + ledger by task type.

        ``purpose`` is required: text calls have no task to trace and need caller-declared origins.
        One ``task_type`` serves multiple purposes; script generation and episode planning both use SCRIPT.
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
        """Generate text and record usage automatically.

        Reraised structured-output :class:`TextOutputTruncatedError` carries resolved provider_id, model ID,
        and custom-provider status. ``require_complete`` serves free-text calls requiring complete replies,
        such as whole-reply JSON parsing; truncation raises the same exception instead of returning partial results.
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
