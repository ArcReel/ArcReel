"""只消费图片装配规格，构造该次解析选定的通道。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.image_backends.base import ImageBackend
from lib.capabilities.kernel import CapabilitySnapshot
from lib.custom_provider.backends import CustomImageBackend
from lib.custom_provider.endpoints import BackendBuilder, ProviderConnectionSnapshot
from lib.custom_provider.path_overrides import PathOverrides
from lib.video_runtime.builtin import build_builtin_image


@dataclass(frozen=True)
class BuiltinImageSpec:
    snapshot: CapabilitySnapshot
    endpoint: str
    registry_backend: str
    config: LoadedConfig = field(repr=False)
    api_model_name: str | None = None


@dataclass(frozen=True)
class CustomImageSpec:
    snapshot: CapabilitySnapshot
    endpoint: str
    build_backend: BackendBuilder = field(compare=False, repr=False)
    connection: ProviderConnectionSnapshot
    paths: PathOverrides


type ImageAssemblySpec = BuiltinImageSpec | CustomImageSpec


def _build_image(spec: ImageAssemblySpec) -> ImageBackend:
    model = spec.snapshot.model_id
    if isinstance(spec, CustomImageSpec):
        backend = spec.build_backend(spec.connection, model, spec.paths)
        if not isinstance(backend, CustomImageBackend):
            raise ValueError(f"expected image endpoint: {spec.endpoint}")
        return backend.with_snapshot(spec.snapshot)
    return build_builtin_image(
        spec.config,
        model,
        spec.snapshot.provider_id,
        spec.registry_backend,
        api_model_name=spec.api_model_name,
    )


async def assemble_image(spec: ImageAssemblySpec) -> ImageBackend:
    return await asyncio.to_thread(_build_image, spec)
