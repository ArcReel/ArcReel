"""Text backend factory.

Provider/model resolution remains here (resolver.text_backend_for_task), while backend construction converges on
assemble_backend (media_type=text): all built-in text providers use ProviderSpec tables,
custom providers use load_custom_backend moved into lib, eliminating duplicated imperative construction/custom resolution in text code.
"""

from __future__ import annotations

from lib.backends.backend_assembly import assemble_backend
from lib.backends.backend_assembly.assembler import OutputLimitFacts, output_limit_facts
from lib.backends.text_backends.base import TextBackend, TextTaskType
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory


async def create_text_backend_for_task(
    task_type: TextTaskType,
    project_name: str | None = None,
) -> tuple[TextBackend, str]:
    """Create text backends from DB configuration, returning resolution-layer registry provider_id alongside each backend.

    provider_id is the sole billing-provider authority, delivered with the backend; callers (TextGenerator)
    account using it, never backend.name.
    """
    resolver = ConfigResolver(async_session_factory)

    async with resolver.session() as r:
        provider_id, model_id = await r.text_backend_for_task(task_type, project_name)
        backend = await assemble_backend(
            provider_id=provider_id,
            media_type="text",
            model_id=model_id,
            resolver=r,
        )
    return backend, provider_id


async def text_model_output_limit(provider_id: str, model_id: str) -> OutputLimitFacts:
    """Read a text model's registered maximum output length and whether it comes from a custom provider."""
    return await output_limit_facts(
        provider_id=provider_id, model_id=model_id, resolver=ConfigResolver(async_session_factory)
    )
