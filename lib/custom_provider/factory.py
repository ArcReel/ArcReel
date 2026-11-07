"""Custom-provider backend factory dispatching by endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from lib.backends.image_capabilities import (
    custom_image_model_capabilities,
    entry_reference_limit,
    image_reference_override,
)
from lib.backends.video_capabilities import entry_video_facts
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.capabilities import synthesize_video_capabilities_with_overrides
from lib.custom_provider.endpoints import EndpointSpec, get_endpoint_spec
from lib.custom_provider.model_facts import model_entry
from lib.custom_provider.path_overrides import effective_path_overrides

if TYPE_CHECKING:
    from lib.db.models.custom_provider import CustomProvider


def create_custom_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    endpoint: str,
    capability_overrides: object | None = None,
    path_overrides: object | None = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend:
    """Look up ENDPOINT_REGISTRY by endpoint and construct backends.

    Videos inject effective capabilities (joint endpoint declarations/model entries ⊕ model user overrides), used by execution gates,
    never directly wrapped-backend declarations. Images inject model-fact text/image-to-image flags (entries ⊕ overrides,
    unconstrained for ComfyUI) and entry reference maxima; execution jointly evaluates them with channel declarations.

    Args:
        provider: Custom-provider ORM objects with base_url / api_key / provider_id.
        model_id: Specific model ids used for calls.
        endpoint: ENDPOINT_REGISTRY keys.
        capability_overrides: Raw model `capability_overrides`; None follows all system declarations.
        path_overrides: Raw model `path_overrides`; ignore request names no longer declared by endpoints (docs/adr/0101).
        entry_binding: Vendor/official-name bindings; None matches by name.

    Raises:
        ValueError: Endpoints outside ENDPOINT_REGISTRY.
    """
    spec = endpoint_spec or get_endpoint_spec(endpoint)
    paths = effective_path_overrides(path_overrides, spec.requests)
    backend = spec.build_backend(provider, model_id, paths)
    if isinstance(backend, CustomVideoBackend):
        merged, applied = synthesize_video_capabilities_with_overrides(
            endpoint=endpoint,
            model_id=model_id,
            overrides=capability_overrides,
            endpoint_spec=spec,
            entry_binding=entry_binding,
        )
        # Retain endpoints/path overrides, the actual backend-construction granularity; resumes compare them
        # for changed protocols (docs/adr/0054, 0101). Channel capabilities/reference-asset slots use matching entries,
        # bindings first, shared with read-side composition/built-ins; requests still send model_id.
        entry = model_entry(model_id, spec.media_type, entry_binding)
        # Tier queries jointly evaluate protocol backend tier-aware declarations and the same entries; ComfyUI ignores entries.
        entry_facts = None if spec.kind == COMFYUI_KIND else entry_video_facts(entry)
        return (
            backend.with_endpoint(endpoint, path_overrides=paths)
            .with_video_capabilities(merged, overrides=applied, entry_facts=entry_facts)
            .with_catalog_entry(entry)
        )
    if isinstance(backend, CustomImageBackend):
        return backend.with_model_facts(
            custom_image_model_capabilities(
                model_id, overrides=capability_overrides, endpoint_kind=spec.kind, entry_binding=entry_binding
            ),
            reference_limit_override=image_reference_override(capability_overrides, endpoint_kind=spec.kind),
            entry_reference_limit=entry_reference_limit(
                model_id, builtin_provider=None, endpoint_kind=spec.kind, entry_binding=entry_binding
            ),
        )
    return backend
