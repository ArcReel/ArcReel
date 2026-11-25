"""音频语音合成目标的身份解析与通道装配（ADR 0049）。

音频无能力覆盖维度，不构造空壳能力快照；完整保留既有音色枚举与 AudioBackend.capabilities 契约。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from lib.backends.audio_backends.base import AudioBackend
from lib.backends.backend_assembly.assembler import load_builtin_config
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.catalog import query as catalog
from lib.catalog.errors import ModelDiscontinuedError
from lib.config.resolver import ConfigResolver, NoAvailableProviderError, ProviderModel
from lib.custom_provider import is_custom_provider, make_provider_id, parse_provider_id
from lib.custom_provider.backends import CustomAudioBackend
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.endpoints import BackendBuilder, ProviderConnectionSnapshot
from lib.custom_provider.model_facts import model_entry
from lib.custom_provider.path_overrides import PathOverrides, effective_path_overrides
from lib.db.models.custom_provider import CustomProviderModel
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.video_runtime.builtin import build_builtin_audio


@dataclass(frozen=True)
class BuiltinAudioSpec:
    provider_id: str
    model_id: str
    endpoint: str
    registry_backend: str
    config: LoadedConfig = field(repr=False)


@dataclass(frozen=True)
class CustomAudioSpec:
    provider_id: str
    model_id: str
    endpoint: str
    definition: Mapping[str, Any] | None
    build_backend: BackendBuilder = field(compare=False, repr=False)
    connection: ProviderConnectionSnapshot
    paths: PathOverrides


type AudioAssemblySpec = BuiltinAudioSpec | CustomAudioSpec


@dataclass(frozen=True)
class AudioTarget:
    generation: int
    provider_id: str
    model_id: str
    spec: AudioAssemblySpec = field(repr=False)


class AudioRuntime:
    def __init__(self, resolver: ConfigResolver, *, capture_generation: Callable[[], int] = lambda: 0):
        self.resolver = resolver
        self.capture_generation = capture_generation

    async def resolve(self, project: dict | None, payload: dict | None = None) -> AudioTarget:
        return await self._read(project=project, payload=payload)

    async def read_identity(self, project: dict | None, payload: dict | None = None) -> ProviderModel:
        async with self.resolver.session() as resolver:
            selected = await resolver.configured_audio_identity(project, payload)
            if selected is None:
                async with resolver._open_session() as (session, _):
                    spec = await _automatic_audio_spec(session)
                return ProviderModel(spec.provider_id, spec.model_id)
            if not is_custom_provider(selected.provider_id):
                return selected
            try:
                async with resolver._open_session() as (session, _):
                    spec = await _custom_audio_spec(session, selected)
            except ModelDiscontinuedError:
                raise
            except ValueError:
                return selected
            return ProviderModel(spec.provider_id, spec.model_id)

    async def for_identity(self, identity: ProviderModel) -> AudioTarget:
        if not identity or not identity.provider_id or not identity.model_id:
            raise ValueError("expected an audio provider and model")
        return await self._read(identity=identity)

    async def _read(
        self,
        *,
        project: dict | None = None,
        payload: dict | None = None,
        identity: ProviderModel | None = None,
    ) -> AudioTarget:
        generation = self.capture_generation()
        async with self.resolver.session() as resolver:
            selected = identity or await resolver.configured_audio_identity(project, payload)
            if selected is None:
                async with resolver._open_session() as (session, _):
                    spec: AudioAssemblySpec = await _automatic_audio_spec(session)
            elif is_custom_provider(selected.provider_id):
                async with resolver._open_session() as (session, _):
                    spec = await _custom_audio_spec(session, selected)
            else:
                spec = await _builtin_audio_spec(resolver, selected)
        return AudioTarget(generation, spec.provider_id, spec.model_id, spec)


async def _builtin_audio_spec(resolver: ConfigResolver, selected: ProviderModel) -> BuiltinAudioSpec:
    provider, model = selected.provider_id, selected.model_id
    _, endpoint = catalog.callable_model_view(provider, model, "audio", submitted=False)
    builtin = get_provider_spec(provider, "audio", endpoint)
    config = await load_builtin_config(resolver, provider, None)
    return BuiltinAudioSpec(provider, model, builtin.endpoint, builtin.registry_backend, config)


async def _row_audio_spec(session: AsyncSession, provider_id: str, row: CustomProviderModel) -> CustomAudioSpec | None:
    try:
        endpoint = await resolve_endpoint_spec(row.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        return None
    if endpoint.media_type != "audio":
        return None
    entry = model_entry(row.model_id, "audio", row.entry_binding)
    if entry is not None:
        catalog.reject_discontinued_entry(provider_id, row.model_id, "audio", entry)
    if not row.is_enabled:
        return None
    provider = await CustomProviderRepository(session).get_provider(row.provider_id)
    if provider is None:
        return None
    connection = ProviderConnectionSnapshot(provider.id, provider_id, provider.base_url, provider.api_key)
    return CustomAudioSpec(
        provider_id,
        row.model_id,
        endpoint.key,
        endpoint.definition,
        endpoint.build_backend,
        connection,
        effective_path_overrides(row.path_overrides, endpoint.requests),
    )


async def _custom_audio_spec(session: AsyncSession, selected: ProviderModel) -> CustomAudioSpec:
    provider_id = selected.provider_id
    db_id = parse_provider_id(provider_id)
    repo = CustomProviderRepository(session)
    row = await repo.get_model_by_ids(db_id, selected.model_id)
    spec = await _row_audio_spec(session, provider_id, row) if row is not None else None
    if spec is not None:
        return spec
    candidates: list[CustomAudioSpec] = []
    for default_row in await repo.list_default_models(db_id, "audio"):
        try:
            candidate = await _row_audio_spec(session, provider_id, default_row)
        except ModelDiscontinuedError:
            continue
        if candidate is not None:
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(f"custom model not found: {provider_id}/{selected.model_id}")


async def _automatic_audio_spec(session: AsyncSession) -> CustomAudioSpec:
    for row in await CustomProviderRepository(session).list_enabled_models_by_media_type("audio"):
        if not row.is_default:
            continue
        try:
            spec = await _row_audio_spec(session, make_provider_id(row.provider_id), row)
        except ModelDiscontinuedError:
            continue
        if spec is not None:
            return spec
    raise NoAvailableProviderError(
        "没有设置 audio 默认模型。请在「全局设置 → 默认模型」中选择，或先在「全局设置 → 供应商」配置供应商。",
        media_type="audio",
    )


def _build_audio(spec: AudioAssemblySpec) -> AudioBackend:
    if isinstance(spec, CustomAudioSpec):
        backend = spec.build_backend(spec.connection, spec.model_id, spec.paths)
        if not isinstance(backend, CustomAudioBackend):
            raise ValueError(f"expected audio endpoint: {spec.endpoint}")
        return backend
    return build_builtin_audio(spec.config, spec.model_id, spec.provider_id, spec.registry_backend)


async def assemble_audio(spec: AudioAssemblySpec) -> AudioBackend:
    return await asyncio.to_thread(_build_audio, spec)


async def default_narration_tts(resolver: ConfigResolver) -> tuple[ProviderModel, str, float | None]:
    """新建 TTS 项目的预填值：全局默认音频 (provider, model)、旁白音色与配音语速。

    未配置任何音频供应商时抛 ValueError。项目创建后不再读取这些全局值（docs/adr/0089）。
    """
    async with resolver.session() as r:
        identity = await AudioRuntime(r).read_identity(None)
        voice, speed = await r.narration_defaults()
        return identity, voice, speed
