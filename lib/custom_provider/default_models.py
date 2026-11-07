"""Custom model selection: direct requested-model usability and bucketed defaults, sharing filters between fallback/capability gates.

Defaults have task-bucket slots: save-time uniqueness (``server/routers/custom_providers.py``
``_check_unique_defaults``) uses image-capability disjointness, permitting separate t2i/i2i defaults. Provider + media
therefore identifies candidates only; choosing rows requires buckets through
``lib.backends.generation_type_buckets.custom_model_buckets``, never duplicated capability interpretation.

Unknown buckets (unbucketed video, text/audio) do not filter: saves already guarantee at most one default per media,
so candidates never exceed one.

Lifecycle shares built-in read models (``lib.catalog.lifecycle``, docs/adr/0103): models bind/name-match entries
(``lib.custom_provider.model_facts.model_entry``). Discontinued entries cannot default or become candidates; explicit references
error without default substitution (:func:`requested_model`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.backends.generation_type_buckets import (
    BUCKETS_BY_MEDIA_TYPE,
    GenerationTypeBucket,
    custom_model_buckets,
)
from lib.catalog import query as catalog
from lib.catalog.lifecycle import Lifecycle, entry_lifecycle
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.model_facts import model_entry
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from lib.db.models.custom_provider import CustomProviderModel


def model_lifecycle(model: CustomProviderModel, media_type: str) -> Lifecycle:
    """Matching-entry lifecycle today; ``media_type`` is endpoint media. Unmatched entries are available."""
    return entry_lifecycle(model_entry(model.model_id, media_type, model.entry_binding))


def callable_models(models: list[CustomProviderModel], media_type: str) -> list[CustomProviderModel]:
    """Filter discontinued-entry models, preserving order; defaults/candidates use remaining rows only."""
    return [model for model in models if not model_lifecycle(model, media_type).discontinued]


async def requested_model(
    session: AsyncSession,
    *,
    provider_id: str,
    db_id: int,
    model_id: str,
    media_type: str,
    submitted_job: bool = False,
) -> CustomProviderModel | None:
    """Return directly usable requested rows for ``media_type``; ``None`` for missing, unresolved/wrong-media endpoints, or disabled models,
    letting callers fall back to media/bucket defaults.

    Discontinued entries raise ``ModelDiscontinuedError`` with dates/successors, even disabled: fallback would substitute user models,
    so users reselect. True ``submitted_job`` means resuming submitted tasks, polling only,
    bypassing discontinuation checks while retaining other decisions.

    Raises:
        ModelDiscontinuedError: Matching entries discontinued today when ``submitted_job`` is false.
    """
    model = await CustomProviderRepository(session).get_model_by_ids(db_id, model_id)
    if model is None:
        return None
    try:
        endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        return None
    if endpoint_spec.media_type != media_type:
        return None
    if not submitted_job:
        catalog.reject_discontinued_entry(
            provider_id, model.model_id, media_type, model_entry(model.model_id, media_type, model.entry_binding)
        )
    return model if model.is_enabled else None


async def model_buckets(session: AsyncSession, model: CustomProviderModel) -> frozenset[GenerationTypeBucket]:
    """Model task buckets; empty for retired/unresolved endpoints, preferring missing candidates over invalid ones."""
    try:
        endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        endpoint_spec = None
    return custom_model_buckets(
        endpoint=model.endpoint,
        model_id=model.model_id,
        capability_overrides=model.capability_overrides,
        endpoint_spec=endpoint_spec,
        entry_binding=model.entry_binding,
    )


#: Bucket → media, inverted from ``BUCKETS_BY_MEDIA_TYPE``, never independently listed.
_MEDIA_TYPE_BY_BUCKET: dict[str, str] = {
    bucket: media_type for media_type, buckets in BUCKETS_BY_MEDIA_TYPE.items() for bucket in buckets
}


async def lacks_bucket(session: AsyncSession, model: CustomProviderModel, generation_type: str) -> bool:
    """Whether rows generate the requested media but lack buckets, for resolution capability gates.

    Unresolved/wrong-media endpoints return False: invalid references belong to fallback, not capability failures.
    """
    buckets = await model_buckets(session, model)
    same_media = BUCKETS_BY_MEDIA_TYPE.get(_MEDIA_TYPE_BY_BUCKET.get(generation_type, ""), ())
    return bool(buckets & frozenset(same_media)) and generation_type not in buckets


async def filter_by_bucket(
    session: AsyncSession,
    models: list[CustomProviderModel],
    generation_type: str | None,
) -> list[CustomProviderModel]:
    """Keep bucket-capable models in input order; ``generation_type`` None returns unchanged."""
    if generation_type is None:
        return models
    return [model for model in models if generation_type in await model_buckets(session, model)]


async def resolve_default_model(
    session: AsyncSession,
    *,
    provider_id: str,
    db_id: int,
    media_type: str,
    generation_type: str | None = None,
) -> CustomProviderModel:
    """Unique enabled media×bucket defaults, skipping discontinued entries as unspecified defaults.

    Zero/multiple rows fail loudly: silently selecting would substitute models. Multiple defaults arise only when
    save-time prevention failed to reject two defaults in one bucket.

    Raises:
        ValueError: Missing or multiple bucket defaults.
    """
    candidates = await CustomProviderRepository(session).list_default_models(db_id, media_type)
    candidates = await filter_by_bucket(session, callable_models(candidates, media_type), generation_type)
    label = f"{generation_type} {media_type}" if generation_type is not None else media_type
    if not candidates:
        raise ValueError(f"自定义供应商 {provider_id} 没有默认 {label} 模型")
    if len(candidates) > 1:
        conflict = ", ".join(model.model_id for model in candidates)
        raise ValueError(f"自定义供应商 {provider_id} 有多个默认 {label} 模型：{conflict}")
    return candidates[0]
