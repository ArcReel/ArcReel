"""文本目标的配置读取、纯能力内核快照与通道装配（ADR 0049、0104）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from lib.backends.backend_assembly.assembler import load_builtin_config
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.backends.text_backends.base import TEXT_TASK_TIERS, VISION_REQUIRED_TASKS, TextBackend, TextTaskType
from lib.capabilities.kernel import CapabilitySnapshot
from lib.catalog import query as catalog
from lib.catalog.errors import ModelDiscontinuedError
from lib.config.resolver import ConfigResolver, NoAvailableProviderError, ProviderModel, VisionCapabilityError
from lib.custom_provider import is_custom_provider, make_provider_id, parse_provider_id
from lib.custom_provider.backends import CustomTextBackend
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.endpoints import BackendBuilder, ProviderConnectionSnapshot
from lib.custom_provider.model_facts import builtin_text_snapshot_from_view, model_entry, text_capability_snapshot
from lib.custom_provider.path_overrides import PathOverrides, effective_path_overrides
from lib.db.models.custom_provider import CustomProviderModel
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.project.project_manager import get_project_manager
from lib.video_runtime.builtin import build_builtin_text


@dataclass(frozen=True)
class BuiltinTextSpec:
    snapshot: CapabilitySnapshot
    endpoint: str
    registry_backend: str
    config: LoadedConfig = field(repr=False)


@dataclass(frozen=True)
class CustomTextSpec:
    snapshot: CapabilitySnapshot
    endpoint: str
    build_backend: BackendBuilder = field(compare=False, repr=False)
    connection: ProviderConnectionSnapshot
    paths: PathOverrides


type TextAssemblySpec = BuiltinTextSpec | CustomTextSpec


@dataclass(frozen=True)
class TextTarget:
    snapshot: CapabilitySnapshot
    spec: TextAssemblySpec = field(repr=False)

    @property
    def provider_id(self) -> str:
        return self.snapshot.provider_id

    @property
    def model_id(self) -> str:
        return self.snapshot.model_id

    @property
    def max_output_tokens(self) -> int | None:
        value = self.snapshot.effective.get("max_output_tokens")
        return value if isinstance(value, int) else None

    @property
    def is_custom(self) -> bool:
        return isinstance(self.spec, CustomTextSpec)


class TextRuntime:
    def __init__(self, resolver: ConfigResolver):
        self.resolver = resolver

    async def for_task(self, task_type: TextTaskType, project_name: str | None = None) -> TextTarget:
        project = (
            await asyncio.to_thread(lambda: get_project_manager().load_project(project_name)) if project_name else None
        )
        tier = TEXT_TASK_TIERS[task_type].value
        async with self.resolver.session() as resolver:
            selected = await resolver.configured_text_identity(tier, project)
            if selected is None:
                async with resolver._open_session() as (session, _):
                    spec: TextAssemblySpec = await _automatic_text_spec(session)
            elif is_custom_provider(selected.provider_id):
                async with resolver._open_session() as (session, _):
                    spec = await _custom_text_spec(session, selected)
            else:
                spec = await _builtin_text_spec(resolver, selected, task_type=task_type)
        if task_type in VISION_REQUIRED_TASKS and spec.snapshot.effective.get("image_input") is False:
            raise VisionCapabilityError(
                task_type=task_type,
                provider_id=spec.snapshot.provider_id,
                model_id=spec.snapshot.model_id,
            )
        return TextTarget(spec.snapshot, spec)


async def _builtin_text_spec(
    resolver: ConfigResolver, selected: ProviderModel, *, task_type: TextTaskType
) -> BuiltinTextSpec:
    provider, model = selected.provider_id, selected.model_id
    view, endpoint = catalog.callable_model_view(provider, model, "text", submitted=False)
    snapshot = builtin_text_snapshot_from_view(provider, model, view)
    if task_type in VISION_REQUIRED_TASKS and snapshot.effective.get("image_input") is False:
        raise VisionCapabilityError(task_type=task_type, provider_id=provider, model_id=model)
    builtin = get_provider_spec(provider, "text", endpoint)
    config = await load_builtin_config(resolver, provider, None)
    return BuiltinTextSpec(snapshot, builtin.endpoint, builtin.registry_backend, config)


async def _row_text_spec(session: AsyncSession, provider_id: str, row: CustomProviderModel) -> CustomTextSpec | None:
    try:
        endpoint = await resolve_endpoint_spec(row.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        return None
    if endpoint.media_type != "text":
        return None
    entry = model_entry(row.model_id, "text", row.entry_binding)
    if entry is not None:
        catalog.reject_discontinued_entry(provider_id, row.model_id, "text", entry)
    if not row.is_enabled:
        return None
    provider = await CustomProviderRepository(session).get_provider(row.provider_id)
    if provider is None:
        return None
    snapshot = text_capability_snapshot(
        row.model_id,
        row.capability_overrides,
        entry=entry,
        provider_id=provider_id,
    )
    connection = ProviderConnectionSnapshot(provider.id, provider_id, provider.base_url, provider.api_key)
    return CustomTextSpec(
        snapshot,
        endpoint.key,
        endpoint.build_backend,
        connection,
        effective_path_overrides(row.path_overrides, endpoint.requests),
    )


async def _custom_text_spec(session: AsyncSession, selected: ProviderModel) -> CustomTextSpec:
    provider_id = selected.provider_id
    db_id = parse_provider_id(provider_id)
    repo = CustomProviderRepository(session)
    row = await repo.get_model_by_ids(db_id, selected.model_id)
    spec = await _row_text_spec(session, provider_id, row) if row is not None else None
    if spec is not None:
        return spec
    candidates: list[CustomTextSpec] = []
    for default_row in await repo.list_default_models(db_id, "text"):
        try:
            candidate = await _row_text_spec(session, provider_id, default_row)
        except ModelDiscontinuedError:
            continue
        if candidate is not None:
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(f"custom model not found: {provider_id}/{selected.model_id}")


async def _automatic_text_spec(session: AsyncSession) -> CustomTextSpec:
    for row in await CustomProviderRepository(session).list_enabled_models_by_media_type("text"):
        if not row.is_default:
            continue
        try:
            spec = await _row_text_spec(session, make_provider_id(row.provider_id), row)
        except ModelDiscontinuedError:
            continue
        if spec is not None:
            return spec
    raise NoAvailableProviderError(
        "没有设置 text 默认模型。请在「全局设置 → 默认模型」中选择，或先在「全局设置 → 供应商」配置供应商。",
        media_type="text",
    )


def _build_text(spec: TextAssemblySpec) -> TextBackend:
    model = spec.snapshot.model_id
    if isinstance(spec, CustomTextSpec):
        backend = spec.build_backend(spec.connection, model, spec.paths)
        if not isinstance(backend, CustomTextBackend):
            raise ValueError(f"expected text endpoint: {spec.endpoint}")
        return backend
    return build_builtin_text(spec.config, model, spec.snapshot.provider_id, spec.registry_backend)


async def assemble_text(spec: TextAssemblySpec) -> TextBackend:
    return await asyncio.to_thread(_build_text, spec)


async def resolve_and_assemble_text(
    task_type: TextTaskType, project_name: str | None = None
) -> tuple[TextBackend, TextTarget]:
    from lib.db import async_session_factory

    target = await TextRuntime(ConfigResolver(async_session_factory)).for_task(task_type, project_name)
    backend = await assemble_text(target.spec)
    return backend, target
