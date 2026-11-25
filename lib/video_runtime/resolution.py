"""视频目标的配置读取、能力快照与通道装配（ADR 0049）。"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
from lib.backends.backend_assembly.assembler import load_builtin_config
from lib.backends.backend_assembly.specs import (
    builtin_video_snapshot_from_view,
    get_provider_spec,
)
from lib.backends.providers import video_audio_always_billed
from lib.backends.resume_guard import builtin_endpoint_guard, resume_guard_accepts
from lib.capabilities.kernel import CapabilitySnapshot, bucket_fit
from lib.catalog import query as catalog
from lib.catalog.errors import ModelDiscontinuedError, ModelNotInCatalogError
from lib.config.resolver import (
    ConfigResolver,
    NoAvailableProviderError,
    ProviderModel,
    VideoBucketCapabilityError,
    VideoGenerationType,
    payload_video_pinned_pair,
    reference_limits_from_config,
    video_bucket_capability_missing,
    video_bucket_reference_unavailable,
)
from lib.custom_provider import is_custom_provider, make_provider_id, parse_provider_id
from lib.custom_provider.capabilities import custom_video_snapshot_from_entry
from lib.custom_provider.definition_capabilities import decode_definition
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.endpoints import EndpointSpec, ProviderConnectionSnapshot
from lib.custom_provider.model_facts import model_entry
from lib.custom_provider.path_overrides import effective_path_overrides, endpoint_guard
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.video_request_facts import (
    ResolutionOverride,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    VideoRoute,
    route_failure_code,
    snapshot_video_request_facts,
)
from lib.video_runtime.assembly import BuiltinVideoSpec, CustomVideoSpec, VideoAssemblySpec
from lib.video_runtime.builtin import declarative_definition


@dataclass(frozen=True)
class VideoReadSnapshot:
    snapshot: CapabilitySnapshot
    requested_audio: bool


@dataclass(frozen=True)
class VideoTarget:
    generation: int
    endpoint_guard: str
    spec: VideoAssemblySpec = field(repr=False)

    @property
    def snapshot(self) -> CapabilitySnapshot:
        return self.spec.snapshot

    @property
    def reference_payload_limits(self) -> tuple[int, int] | None:
        if isinstance(self.spec, BuiltinVideoSpec):
            return reference_limits_from_config(self.spec.config.credentials)
        return None


class VideoRuntime:
    def __init__(
        self, resolver: ConfigResolver, *, capture_generation: Callable[[], int] = lambda: 0, rate_limiter: Any = None
    ):
        self.resolver = resolver
        self.capture_generation = capture_generation
        self.rate_limiter = rate_limiter

    async def bucket(self, project: dict | None, bucket: VideoGenerationType) -> VideoTarget:
        return await self._read(project=project, bucket=bucket)

    async def read_bucket(
        self, project: dict | None, bucket: VideoGenerationType | None, *, payload: dict | None = None
    ) -> VideoReadSnapshot:
        async with self.resolver.session(snapshot_settings=True) as resolver:
            snapshot = await self._read_snapshot(resolver, project=project, bucket=bucket, payload=payload)
            requested_audio = await resolver.video_generate_audio_for_project(project)
        return VideoReadSnapshot(snapshot, requested_audio)

    async def default(self, project: dict | None) -> VideoTarget:
        return await self._read(project=project)

    async def materialized(self, identity: ProviderModel) -> VideoTarget:
        return await self._read(identity=self._locked(identity))

    async def submitted(self, identity: ProviderModel, endpoint_guard: str | None, *, job_id: str) -> VideoTarget:
        target = await self._read(identity=self._locked(identity), submitted=True)
        if not resume_guard_accepts(endpoint_guard, target.endpoint_guard):
            raise ResumeEndpointChangedError(
                job_id=job_id,
                provider=identity.provider_id,
                submitted_endpoint=endpoint_guard or "<builtin>",
                current_endpoint=target.endpoint_guard,
            )
        return target

    @staticmethod
    def _locked(identity: ProviderModel) -> ProviderModel:
        if not identity or not identity.provider_id or not identity.model_id:
            raise ValueError("expected a locked video provider and model")
        return identity

    async def _read(
        self,
        *,
        project: dict | None = None,
        bucket: VideoGenerationType | None = None,
        identity: ProviderModel | None = None,
        submitted: bool = False,
    ) -> VideoTarget:
        generation = self.capture_generation()
        async with self.resolver.session() as resolver:
            selected = identity or await resolver.configured_video_identity(project, bucket)
            if selected is None:
                async with resolver._open_session() as (session, _):
                    spec = await _automatic_spec(session, bucket)
                return VideoTarget(generation, endpoint_guard(spec.endpoint, spec.paths), spec)
            provider, model = selected.provider_id, selected.model_id
            if is_custom_provider(provider):
                async with resolver._open_session() as (session, _):
                    spec = await _custom_spec(
                        session,
                        selected,
                        fallback=identity is None and bucket is None,
                        bucket=bucket,
                        submitted=submitted,
                    )
                guard = endpoint_guard(spec.endpoint, spec.paths)
            else:
                spec = await builtin_video_spec(
                    resolver, selected, submitted=submitted, bucket=bucket, rate_limiter=self.rate_limiter
                )
                guard = builtin_endpoint_guard(spec.endpoint)
        if bucket and bucket in bucket_fit("video", spec.snapshot):
            raise video_bucket_capability_missing(bucket, provider, model)
        return VideoTarget(generation, guard, spec)

    async def _read_snapshot(
        self,
        resolver: ConfigResolver,
        *,
        project: dict | None,
        bucket: VideoGenerationType | None,
        payload: dict | None = None,
    ) -> CapabilitySnapshot:
        pinned = payload_video_pinned_pair(payload, bucket) if payload else None
        if pinned is not None:
            pinned_bucket, pair = pinned
            selected = ProviderModel(*pair)
            provider = selected.provider_id
            if is_custom_provider(provider):
                async with resolver._open_session() as (session, _):
                    return (
                        await _custom_spec(session, selected, fallback=False, bucket=pinned_bucket, submitted=False)
                    ).snapshot
            return _builtin_video_snapshot(selected, submitted=False, bucket=pinned_bucket)[0]
        selected = await resolver.configured_video_identity(project, bucket)
        if selected is None:
            async with resolver._open_session() as (session, _):
                return (await _automatic_spec(session, bucket)).snapshot
        provider, model = selected.provider_id, selected.model_id
        if is_custom_provider(provider):
            async with resolver._open_session() as (session, _):
                spec = await _custom_spec(session, selected, fallback=bucket is None, bucket=bucket, submitted=False)
            snapshot = spec.snapshot
        else:
            snapshot = _builtin_video_snapshot(selected, submitted=False, bucket=bucket)[0]
        if bucket is not None and bucket in bucket_fit("video", snapshot):
            raise video_bucket_capability_missing(bucket, provider, model)
        return snapshot


def _builtin_video_snapshot(
    selected: ProviderModel, *, submitted: bool, bucket: VideoGenerationType | None = None
) -> tuple[CapabilitySnapshot, Any, Any, CatalogEntry | None]:
    provider, model = selected.provider_id, selected.model_id
    view, endpoint = catalog.callable_model_view(provider, model, "video", submitted=submitted)
    try:
        builtin = get_provider_spec(provider, "video", endpoint)
    except ValueError as exc:
        if bucket is not None:
            raise video_bucket_reference_unavailable(bucket, provider, model) from exc
        raise
    definition = deepcopy(declarative_definition(builtin))
    snapshot = builtin_video_snapshot_from_view(
        provider,
        model,
        view,
        endpoint=builtin.endpoint,
        always_bill_audio=video_audio_always_billed(provider),
        description=decode_definition(definition) if definition is not None else None,
    )
    return snapshot, builtin, definition, view.entry


async def builtin_video_spec(
    resolver: ConfigResolver,
    selected: ProviderModel,
    *,
    submitted: bool,
    bucket: VideoGenerationType | None = None,
    rate_limiter: Any = None,
) -> BuiltinVideoSpec:
    snapshot, builtin, definition, entry = _builtin_video_snapshot(selected, submitted=submitted, bucket=bucket)
    config = await load_builtin_config(resolver, selected.provider_id, rate_limiter)
    return BuiltinVideoSpec(snapshot, builtin.endpoint, builtin.registry_backend, config, definition, entry)


async def _read_bucket_or_fail(
    project: dict | None,
    *,
    route: VideoRoute,
    generation_type: VideoGenerationType,
    resolver: ConfigResolver,
) -> VideoReadSnapshot | VideoRequestFactsFailure:
    try:
        return await VideoRuntime(resolver).read_bucket(project, generation_type)
    except (VideoBucketCapabilityError, ModelNotInCatalogError) as exc:
        return VideoRequestFactsFailure(exc.code, tuple(exc.params.items()))
    except (ValueError, SQLAlchemyError):
        return VideoRequestFactsFailure(route_failure_code(route, "unavailable"), (("capability", generation_type),))


async def read_video_request_facts(
    project: dict | None,
    *,
    route: VideoRoute,
    generation_type: VideoGenerationType,
    resolver: ConfigResolver,
    resolution_override: ResolutionOverride | None = None,
    uses_end_frame: bool = False,
    service_tier: str = "default",
) -> VideoRequestFacts | VideoRequestFactsFailure:
    read = await _read_bucket_or_fail(project, route=route, generation_type=generation_type, resolver=resolver)
    if isinstance(read, VideoRequestFactsFailure):
        return read
    return snapshot_video_request_facts(
        read.snapshot,
        project,
        route=route,
        generation_type=generation_type,
        requested_audio=read.requested_audio,
        uses_end_frame=uses_end_frame,
        service_tier=service_tier,
        resolution_override=resolution_override,
    )


async def read_video_facts_pair(
    project: dict | None,
    *,
    route: VideoRoute,
    generation_type: VideoGenerationType,
    resolver: ConfigResolver,
    include_end_frame: bool,
) -> tuple[VideoRequestFacts | VideoRequestFactsFailure, VideoRequestFacts | VideoRequestFactsFailure | None]:
    """单次解析快照后同步求值无尾帧事实，以及按需同源求值带尾帧事实。"""
    read = await _read_bucket_or_fail(project, route=route, generation_type=generation_type, resolver=resolver)
    if isinstance(read, VideoRequestFactsFailure):
        return read, read if include_end_frame else None
    base = snapshot_video_request_facts(
        read.snapshot,
        project,
        route=route,
        generation_type=generation_type,
        requested_audio=read.requested_audio,
    )
    end_frame = (
        snapshot_video_request_facts(
            read.snapshot,
            project,
            route=route,
            generation_type=generation_type,
            requested_audio=read.requested_audio,
            uses_end_frame=True,
        )
        if include_end_frame
        else None
    )
    return base, end_frame


async def _custom_spec(
    session: AsyncSession,
    selected: ProviderModel,
    *,
    fallback: bool,
    bucket: VideoGenerationType | None,
    submitted: bool,
) -> CustomVideoSpec:
    provider_id = selected.provider_id
    try:
        db_id = parse_provider_id(provider_id)
    except ValueError as exc:
        if bucket is not None:
            raise video_bucket_reference_unavailable(bucket, provider_id, selected.model_id) from exc
        raise
    repo = CustomProviderRepository(session)
    row = await repo.get_model_by_ids(db_id, selected.model_id)
    spec = await _row_spec(session, provider_id, row, submitted=submitted) if row else None
    if spec:
        return spec
    if fallback:
        candidates = []
        for row in await repo.list_default_models(db_id, "video"):
            try:
                candidate = await _row_spec(session, provider_id, row)
            except ModelDiscontinuedError:
                continue
            if candidate:
                candidates.append(candidate)
        if len(candidates) == 1:
            return candidates[0]
    if bucket:
        raise video_bucket_reference_unavailable(bucket, provider_id, selected.model_id)
    raise ValueError(f"custom model not found: {provider_id}/{selected.model_id}")


async def _row_spec(
    session: AsyncSession, provider_id: str, row: CustomProviderModel, *, submitted: bool = False
) -> CustomVideoSpec | None:
    try:
        endpoint = await resolve_endpoint_spec(row.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        return None
    if endpoint.media_type != "video":
        return None
    entry = None if endpoint.kind == "comfyui" else model_entry(row.model_id, "video", row.entry_binding)
    if not submitted and entry is not None:
        catalog.reject_discontinued_entry(provider_id, row.model_id, "video", entry)
    if not submitted and not row.is_enabled:
        return None
    provider = await CustomProviderRepository(session).get_provider(row.provider_id)
    if provider is None:
        return None
    return custom_video_spec(provider, row, endpoint, entry)


def custom_video_spec(
    provider: CustomProvider | ProviderConnectionSnapshot,
    row: CustomProviderModel,
    endpoint: EndpointSpec,
    entry: CatalogEntry | None,
) -> CustomVideoSpec:
    provider_id = provider.provider_id
    if endpoint.kind == "comfyui":
        entry = None
    snapshot = custom_video_snapshot_from_entry(
        endpoint,
        row.model_id,
        entry,
        row.capability_overrides,
        provider_id=provider_id,
        default_resolution=row.resolution or None,
    )
    connection = ProviderConnectionSnapshot(provider.id, provider_id, provider.base_url, provider.api_key)
    return CustomVideoSpec(
        snapshot,
        endpoint.key,
        endpoint.definition,
        endpoint.build_backend,
        connection,
        effective_path_overrides(row.path_overrides, endpoint.requests),
        entry,
    )


async def _automatic_spec(session: AsyncSession, bucket: VideoGenerationType | None) -> CustomVideoSpec:
    for row in await CustomProviderRepository(session).list_enabled_models_by_media_type("video"):
        if not row.is_default:
            continue
        try:
            spec = await _row_spec(session, make_provider_id(row.provider_id), row)
        except ModelDiscontinuedError:
            continue
        if spec and (bucket is None or bucket not in bucket_fit("video", spec.snapshot)):
            return spec
    raise NoAvailableProviderError(
        "没有设置 video 默认模型。请在「全局设置 → 默认模型」中选择，或先在「全局设置 → 供应商」配置供应商。",
        media_type="video",
    )
