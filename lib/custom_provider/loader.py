"""Custom-provider backend DB loading.

Find providers, validate requested models (existence/enabled/endpoint media), falling back when invalid to unique
media_type × task-bucket enabled defaults, then delegate existing create_custom_backend
without changing ENDPOINT_REGISTRY. Discontinued-entry models error without fallback; defaults skip discontinued models too
(``lib.custom_provider.default_models``).
Library loading shares custom resolution between media/text factories without reverse dependencies on server.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lib.custom_provider import parse_provider_id
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.default_models import requested_model, resolve_default_model
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.factory import create_custom_backend
from lib.custom_provider.model_facts import text_model_facts
from lib.db.repositories.custom_provider_repo import CustomProviderRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

logger = logging.getLogger(__name__)


async def custom_model_max_output_tokens(*, session: AsyncSession, provider_id: str, model_id: str) -> int | None:
    """Custom maximum output lengths: overrides > matching entries, otherwise None."""
    row = await CustomProviderRepository(session).get_model_by_ids(parse_provider_id(provider_id), model_id)
    if row is None:
        return None
    return text_model_facts(model_id, row.capability_overrides, entry_binding=row.entry_binding).max_output_tokens


async def select_custom_model(
    *,
    session: AsyncSession,
    provider_id: str,
    model_id: str | None,
    media_type: str,
    generation_type: str | None = None,
    submitted_job: bool = False,
) -> tuple[CustomProvider, CustomProviderModel]:
    """Resolve actual custom-provider/model rows without backend construction.

    Missing/disabled/wrong-media requested models fall back to enabled media defaults;
    discontinued matching entries error without fallback, even disabled. Check lifecycle by current dates; backend reuse callers
    also pass here before caches (``lib.backends.backend_assembly.callable_model_key``).

    ``generation_type`` buckets affect fallback only: separate defaults per bucket (image t2i
    and i2i), required to uniquely select rows; ``lib.custom_provider.default_models``.
    Callers without bucket promises pass None, without filtering.

    True ``submitted_job`` resumes already-submitted vendor tasks (polling, never new generation), permitting discontinued requested models
    (``lib.custom_provider.default_models.requested_model``).

    Raises:
        ModelDiscontinuedError: Discontinued requested entries when ``submitted_job`` is false.
        ValueError: Missing providers or nonunique nondiscontinued enabled media×bucket defaults, zero or multiple.
    """
    db_id = parse_provider_id(provider_id)
    provider = await CustomProviderRepository(session).get_provider(db_id)
    if provider is None:
        raise ValueError(f"自定义供应商 {provider_id} 不存在")

    if model_id:
        model = await requested_model(
            session,
            provider_id=provider_id,
            db_id=db_id,
            model_id=model_id,
            media_type=media_type,
            submitted_job=submitted_job,
        )
        if model is not None:
            return provider, model
        logger.warning(
            "自定义模型 %s/%s 已不存在 / 已禁用 / 媒体类型不符（期望 %s），回退到默认模型",
            provider_id,
            model_id,
            media_type,
        )
    default_model = await resolve_default_model(
        session,
        provider_id=provider_id,
        db_id=db_id,
        media_type=media_type,
        generation_type=generation_type,
    )
    return provider, default_model


async def load_custom_backend(
    *,
    session: AsyncSession,
    provider_id: str,
    model_id: str | None,
    media_type: str,
    generation_type: str | None = None,
    submitted_job: bool = False,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend:
    """Load/construct custom backends: resolve provider/models through :func:`select_custom_model`, then delegate factories.

    media_type validates requested endpoint media and groups fallback defaults; actual dispatch uses model.endpoint.
    ``submitted_job`` follows :func:`select_custom_model`.

    Raises:
        ModelDiscontinuedError: Discontinued requested entries when ``submitted_job`` is false.
        ValueError: Missing providers or nonunique nondiscontinued enabled media×bucket defaults, zero or multiple.
    """
    from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

    provider, model = await select_custom_model(
        session=session,
        provider_id=provider_id,
        model_id=model_id,
        media_type=media_type,
        generation_type=generation_type,
        submitted_job=submitted_job,
    )
    spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    return create_custom_backend(
        provider=provider,
        model_id=model.model_id,
        endpoint=model.endpoint,
        capability_overrides=model.capability_overrides,
        path_overrides=model.path_overrides,
        endpoint_spec=spec,
        entry_binding=model.entry_binding,
    )
