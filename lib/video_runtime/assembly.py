"""只消费视频装配规格，构造该次解析选定的通道。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import VideoBackend
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.capabilities.kernel import CapabilitySnapshot
from lib.custom_provider.backends import CustomVideoBackend
from lib.custom_provider.endpoints import BackendBuilder
from lib.custom_provider.path_overrides import PathOverrides
from lib.video_runtime.builtin import build_builtin_video


@dataclass(frozen=True)
class _BuiltinVideoSpec:
    snapshot: CapabilitySnapshot
    endpoint: str
    registry_backend: str
    config: LoadedConfig = field(repr=False)
    definition: Mapping[str, Any] | None
    entry: CatalogEntry | None


@dataclass(frozen=True)
class _Connection:
    id: int
    provider_id: str
    base_url: str
    api_key: str = field(repr=False)


@dataclass(frozen=True)
class _CustomVideoSpec:
    snapshot: CapabilitySnapshot
    endpoint: str
    definition: Mapping[str, Any] | None
    build_backend: BackendBuilder = field(compare=False, repr=False)
    connection: _Connection
    paths: PathOverrides
    entry: CatalogEntry | None


type VideoAssemblySpec = _BuiltinVideoSpec | _CustomVideoSpec


def _build_video(spec: VideoAssemblySpec) -> VideoBackend:
    model = spec.snapshot.model_id
    if isinstance(spec, _CustomVideoSpec):
        backend = spec.build_backend(spec.connection, model, spec.paths)
        if not isinstance(backend, CustomVideoBackend):
            raise ValueError(f"expected video endpoint: {spec.endpoint}")
        return (
            backend.with_endpoint(spec.endpoint, path_overrides=spec.paths)
            .with_snapshot(spec.snapshot)
            .with_catalog_entry(spec.entry)
        )
    backend = build_builtin_video(spec.config, model, spec.snapshot.provider_id, spec.registry_backend, spec.definition)
    if isinstance(backend, EntryBoundCapabilities):
        return backend.with_catalog_entry(spec.entry)
    return backend


async def assemble_video(spec: VideoAssemblySpec) -> VideoBackend:
    return await asyncio.to_thread(_build_video, spec)
