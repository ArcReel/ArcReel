"""Custom provider repository."""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import String, cast, delete, literal, or_, select

from lib.custom_provider import CUSTOM_ENDPOINT_KEY_PREFIX, is_custom_provider, make_provider_id, parse_provider_id
from lib.db.models.config import ProviderConfig
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


def _media_type_endpoint_filter(media_type: str):
    """Filter endpoints by media type: look up built-in keys; read a custom endpoint's own mirrored column.

    Custom endpoint keys are system-assigned and absent from the built-in registry. Their media type is
    stored in the ``custom_endpoint.media_type`` mirror (``docs/adr/0067``). The definition determines
    whether a workflow produces images or video; its key cannot tell us. Match ``ce-<id>`` against
    that mirrored column instead of treating all keys with the prefix as video.
    """
    from lib.custom_provider.endpoints import ENDPOINT_KEYS_BY_MEDIA_TYPE

    builtin = CustomProviderModel.endpoint.in_(ENDPOINT_KEYS_BY_MEDIA_TYPE.get(media_type, ()))
    custom = select(literal(CUSTOM_ENDPOINT_KEY_PREFIX).concat(cast(CustomEndpoint.id, String))).where(
        CustomEndpoint.media_type == media_type
    )
    return or_(builtin, CustomProviderModel.endpoint.in_(custom))


class CustomModelPricing(NamedTuple):
    """Pricing basis for a custom-provider model: model multiplier, bound entry and endpoint (docs/adr/0100, 0105).

    Prices are not stored on model rows: a matched entry supplies its price list; otherwise the model is unpriced.
    If no model row is found (deleted model, malformed provider ID or query failure), use defaults:
    multiplier 1, no binding and no endpoint, while still matching an entry by model name.
    """

    multiplier: float = 1.0
    entry_binding: str | None = None
    endpoint: str | None = None
    # Raw model capability override: pre-submit estimates use it to add the capabilities injected at execution, metering expanded dimensions from the actual request.
    capability_overrides: object | None = None


_DEFAULT_PRICING = CustomModelPricing()


class CustomProviderRepository(BaseRepository):
    """Custom-provider and model CRUD."""

    # ── Provider CRUD ──────────────────────────────────────────────

    async def create_provider(
        self,
        display_name: str,
        discovery_format: str,
        base_url: str,
        api_key: str,
        models: list[dict] | None = None,
        *,
        image_max_workers: int | None = None,
        video_max_workers: int | None = None,
        audio_max_workers: int | None = None,
    ) -> CustomProvider:
        """Create a provider, optionally creating its model list at the same time."""
        provider = CustomProvider(
            display_name=display_name,
            discovery_format=discovery_format,
            base_url=base_url,
            api_key=api_key,
            image_max_workers=image_max_workers,
            video_max_workers=video_max_workers,
            audio_max_workers=audio_max_workers,
        )
        self.session.add(provider)
        await self.session.flush()  # Obtain provider.id

        if models:
            for m in models:
                model = CustomProviderModel(provider_id=provider.id, **m)
                self.session.add(model)
            await self.session.flush()

        return provider

    async def get_provider(self, provider_id: int) -> CustomProvider | None:
        stmt = select(CustomProvider).where(CustomProvider.id == provider_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_providers(self) -> list[CustomProvider]:
        stmt = select(CustomProvider).order_by(CustomProvider.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update_provider(self, provider_id: int, **kwargs) -> CustomProvider | None:
        """Update provider fields. Return the updated object, or None if it does not exist."""
        provider = await self.get_provider(provider_id)
        if provider is None:
            return None
        for key, value in kwargs.items():
            setattr(provider, key, value)
        return provider

    async def delete_provider(self, provider_id: int) -> None:
        """Delete a provider, all its models and settings recorded under it, such as the provider multiplier.

        Delete models explicitly instead of relying on FK CASCADE: cascades require foreign_keys pragma
        on the connection. The application engine enables it centrally (engine.py), but direct script or external
        tool connections may not; explicit deletion is independent of connection configuration.
        Settings are stored under ``custom-<id>`` in ``provider_config``. New providers may reuse IDs, so delete them too.
        """
        await self.session.execute(delete(CustomProviderModel).where(CustomProviderModel.provider_id == provider_id))
        await self.session.execute(
            delete(ProviderConfig).where(ProviderConfig.provider == make_provider_id(provider_id))
        )
        await self.session.execute(delete(CustomProvider).where(CustomProvider.id == provider_id))
        await self.session.flush()

    # ── Model management ──────────────────────────────────────────

    async def list_models(self, provider_id: int) -> list[CustomProviderModel]:
        stmt = (
            select(CustomProviderModel)
            .where(CustomProviderModel.provider_id == provider_id)
            .order_by(CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def replace_models(self, provider_id: int, models: list[dict]) -> list[CustomProviderModel]:
        """Delete old models and insert the new list. Return the newly created models."""
        await self.session.execute(delete(CustomProviderModel).where(CustomProviderModel.provider_id == provider_id))
        new_models = []
        for m in models:
            model = CustomProviderModel(provider_id=provider_id, **m)
            self.session.add(model)
            new_models.append(model)
        await self.session.flush()
        return new_models

    async def delete_model(self, model_id: int) -> None:
        """Delete one model."""
        await self.session.execute(delete(CustomProviderModel).where(CustomProviderModel.id == model_id))
        await self.session.flush()

    async def list_all_enabled_models(self) -> list[CustomProviderModel]:
        """Get all enabled models across providers."""
        stmt = (
            select(CustomProviderModel)
            .where(CustomProviderModel.is_enabled)
            .order_by(CustomProviderModel.provider_id, CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_providers_with_models(self) -> list[tuple[CustomProvider, list[CustomProviderModel]]]:
        """Get all providers and their models in only two queries."""
        providers = await self.list_providers()
        if not providers:
            return []
        provider_ids = [p.id for p in providers]
        stmt = (
            select(CustomProviderModel)
            .where(CustomProviderModel.provider_id.in_(provider_ids))
            .order_by(CustomProviderModel.provider_id, CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        all_models = list(result.scalars())

        models_by_provider: dict[int, list[CustomProviderModel]] = {p.id: [] for p in providers}
        for m in all_models:
            models_by_provider.setdefault(m.provider_id, []).append(m)
        return [(p, models_by_provider.get(p.id, [])) for p in providers]

    async def list_enabled_models_by_media_type(self, media_type: str) -> list[CustomProviderModel]:
        """Get enabled models of the specified media type across all providers.

        Determine media type with :func:`_media_type_endpoint_filter`: look up built-in keys; ``ce-`` rows
        read their own mirrored column. A ComfyUI workflow's key cannot tell us whether it produces images or video.
        """
        stmt = (
            select(CustomProviderModel)
            .where(
                _media_type_endpoint_filter(media_type),
                CustomProviderModel.is_enabled,
            )
            .order_by(CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_model_by_ids(self, provider_id: int, model_id: str) -> CustomProviderModel | None:
        """Get a model by provider ID and model ID."""
        stmt = select(CustomProviderModel).where(
            CustomProviderModel.provider_id == provider_id,
            CustomProviderModel.model_id == model_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def resolve_pricing(self, provider: str, model: str) -> CustomModelPricing:
        """Read the custom-provider model's pricing basis, shared by accounting and estimates to prevent drift from duplicated logic.

        Non-custom providers, malformed provider IDs, query failures and missing models all fall back to defaults; never raise.

        Deliberately do not validate enabled / media_type, unlike the model-resolution fallback in
        ``lib/custom_provider/loader.load_custom_backend``: accounting must price the model actually called,
        regardless of whether it is currently enabled. Enabled / media_type fallback belongs to model
        resolution (the loader), upstream of this data lookup.
        """
        if not is_custom_provider(provider):
            return _DEFAULT_PRICING
        try:
            # SAVEPOINT isolation: query failures roll back only this scope, preserving the caller's
            # subsequent settlement UPDATE/commit in the same session (for example usage_repo._settle).
            async with self.session.begin_nested():
                row = await self.get_model_by_ids(parse_provider_id(provider), model or "")
        except Exception:
            logger.debug("自定义供应商模型查询失败 provider=%s model=%s", provider, model, exc_info=True)
            return _DEFAULT_PRICING
        if row is None:
            return _DEFAULT_PRICING
        return CustomModelPricing(row.multiplier, row.entry_binding, row.endpoint, row.capability_overrides)

    async def list_default_models(self, provider_id: int, media_type: str) -> list[CustomProviderModel]:
        """All default enabled models for the specified provider and media type, in ascending ID order.

        Determine media type as in :meth:`list_enabled_models_by_media_type`.

        Return a list, not one row: image defaults occupy task-type buckets. Save-time uniqueness checks
        determine mutual exclusion from image capability sets, allowing separate defaults for t2i and i2i.
        Multiple rows of one media type may therefore be defaults simultaneously. Choosing the single row
        for a bucket belongs to ``lib.custom_provider.default_models``; the data layer does not choose.
        """
        stmt = (
            select(CustomProviderModel)
            .where(
                CustomProviderModel.provider_id == provider_id,
                _media_type_endpoint_filter(media_type),
                CustomProviderModel.is_default,
                CustomProviderModel.is_enabled,
            )
            .order_by(CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())
