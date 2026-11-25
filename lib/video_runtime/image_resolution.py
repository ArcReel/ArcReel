"""图片目标的配置读取、能力快照与通道装配（ADR 0049、0104）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import CatalogEntry
from lib.backends.backend_assembly.assembler import load_builtin_config
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.backends.builtin_endpoints import get_provider_endpoints
from lib.backends.image_capabilities import builtin_image_snapshot_from_view
from lib.capabilities.kernel import CapabilitySnapshot, bucket_fit
from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver, ImageBucketCapabilityError, ProviderModel
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.custom_provider.capabilities import custom_image_snapshot_from_entry
from lib.custom_provider.default_models import resolve_default_model
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.endpoints import EndpointSpec, ProviderConnectionSnapshot
from lib.custom_provider.model_facts import model_entry
from lib.custom_provider.path_overrides import effective_path_overrides
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.video_runtime.image_assembly import BuiltinImageSpec, CustomImageSpec, ImageAssemblySpec

ImageGenerationType = Literal["t2i", "i2i"]


@dataclass(frozen=True)
class ImageTarget:
    generation: int
    spec: ImageAssemblySpec = field(repr=False)

    @property
    def snapshot(self) -> CapabilitySnapshot:
        return self.spec.snapshot


class ImageRuntime:
    def __init__(
        self, resolver: ConfigResolver, *, capture_generation: Callable[[], int] = lambda: 0, rate_limiter: Any = None
    ):
        self.resolver = resolver
        self.capture_generation = capture_generation
        self.rate_limiter = rate_limiter

    async def resolve(self, project: dict | None, payload: dict | None, *, bucket: ImageGenerationType) -> ImageTarget:
        generation = self.capture_generation()
        async with self.resolver.session() as resolver:
            selected = await resolver.configured_image_identity(project, payload, bucket)
            if is_custom_provider(selected.provider_id):
                async with resolver._open_session() as (session, _):
                    spec: ImageAssemblySpec = await _custom_image_spec(session, selected, bucket=bucket)
            else:
                spec = await builtin_image_spec(resolver, selected, rate_limiter=self.rate_limiter)
        return ImageTarget(generation, spec)


async def builtin_image_spec(
    resolver: ConfigResolver,
    selected: ProviderModel,
    *,
    rate_limiter: Any = None,
) -> BuiltinImageSpec:
    provider, model = selected.provider_id, selected.model_id
    get_provider_endpoints(provider, "image")
    view, endpoint = catalog.callable_model_view(provider, model, "image", submitted=False)
    builtin = get_provider_spec(provider, "image", endpoint)
    snapshot = builtin_image_snapshot_from_view(provider, model, view, endpoint=builtin.endpoint)
    config = await load_builtin_config(resolver, provider, rate_limiter)
    return BuiltinImageSpec(
        snapshot,
        builtin.endpoint,
        builtin.registry_backend,
        config,
        api_model_name=view.model.api_model_name,
    )


def custom_image_spec(
    provider: CustomProvider, row: CustomProviderModel, endpoint: EndpointSpec, entry: CatalogEntry | None
) -> CustomImageSpec:
    provider_id = provider.provider_id
    if endpoint.kind == "comfyui":
        entry = None
    snapshot = custom_image_snapshot_from_entry(
        endpoint,
        row.model_id,
        entry,
        row.capability_overrides,
        provider_id=provider_id,
        default_resolution=row.resolution or None,
    )
    connection = ProviderConnectionSnapshot(provider.id, provider_id, provider.base_url, provider.api_key)
    return CustomImageSpec(
        snapshot,
        endpoint.key,
        endpoint.build_backend,
        connection,
        effective_path_overrides(row.path_overrides, endpoint.requests),
    )


async def _custom_image_spec(
    session: AsyncSession,
    selected: ProviderModel,
    *,
    bucket: ImageGenerationType,
) -> CustomImageSpec:
    provider_id = selected.provider_id
    db_id = parse_provider_id(provider_id)
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(db_id)
    if provider is None:
        raise ValueError(f"自定义供应商 {provider_id} 不存在")
    endpoint_repo = CustomEndpointRepository(session)
    if selected.model_id:
        row = await repo.get_model_by_ids(db_id, selected.model_id)
        if row is not None:
            try:
                endpoint = await resolve_endpoint_spec(row.endpoint, endpoint_repo.get)
            except ValueError:
                endpoint = None
            if endpoint is not None and endpoint.media_type == "image":
                entry = None if endpoint.kind == "comfyui" else model_entry(row.model_id, "image", row.entry_binding)
                if entry is not None:
                    catalog.reject_discontinued_entry(provider_id, row.model_id, "image", entry)
                if row.is_enabled:
                    spec = custom_image_spec(provider, row, endpoint, entry)
                    if bucket in bucket_fit("image", spec.snapshot):
                        raise ImageBucketCapabilityError(
                            generation_type=bucket,
                            provider_id=provider_id,
                            model_id=selected.model_id,
                        )
                    return spec
    default_row = await resolve_default_model(
        session,
        provider_id=provider_id,
        db_id=db_id,
        media_type="image",
        generation_type=bucket,
    )
    default_endpoint = await resolve_endpoint_spec(default_row.endpoint, endpoint_repo.get)
    default_entry = (
        None
        if default_endpoint.kind == "comfyui"
        else model_entry(default_row.model_id, "image", default_row.entry_binding)
    )
    return custom_image_spec(provider, default_row, default_endpoint, default_entry)
