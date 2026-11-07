"""Effective image capabilities: intersection of model entries and call channels (docs/adr/0104).

Text/image-to-image are separate dimensions on both sides: entries state model acceptance; channels
state implemented paths (built-in ``CHANNEL_CAPABILITIES``, custom endpoint declarations). Bucket
candidates and execution validation both use these results.

Model facts use provider-model entries: catalog-referenced built-ins, live name lookup for custom providers
(docs/adr/0105). Unmatched entries impose no model constraints, leaving channel facts effective and
upstream rejecting model-limit violations. Custom capability overrides replace model-side text/image
whole dimensions (see :mod:`lib.custom_provider.model_facts`), never enabling unsupported channel paths;
write validation refuses those.

ComfyUI capabilities derive only from node bindings (docs/adr/0082): user-chosen model names do not
identify workflow capabilities. No entry lookup or overrides; endpoint declarations are effective.

Reference limits also evaluate both sides (:func:`image_reference_limit`), taking minimum entry/channel
limits. Without effective image-to-image, inputs are unsupported. Neither declared means unlisted:
no clipping, upstream rejects excess. Overrides replace the entire evaluated limit, zero refusing images,
while channels must still implement image-to-image. Candidates, validation, clipping, and settings share
this result. ComfyUI limits use only bound reference-slot counts, ignoring entries.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from lib.backends.backend_assembly.specs import builtin_model_spec, get_provider_spec
from lib.backends.image_backends.base import ImageCapability, ImageCapabilityError
from lib.backends.image_backends.registry import channel_capabilities, channel_reference_limit
from lib.backends.input_limits import InputLimit, effective_input_limit
from lib.catalog import query as catalog
from lib.custom_provider import is_custom_provider
from lib.custom_provider.model_facts import image_model_facts, model_fact_overrides

if TYPE_CHECKING:
    from lib.backends.backend_assembly.specs import ProviderSpec
    from lib.backends.image_backends.base import ImageBackend


def _builtin_image_spec(provider_id: str, model_id: str, endpoint: str | None) -> ProviderSpec:
    if endpoint is not None:
        return get_provider_spec(provider_id, "image", endpoint)
    return builtin_model_spec(provider_id, "image", model_id)


def builtin_channel_capabilities(
    provider_id: str, model_id: str, *, endpoint: str | None = None
) -> frozenset[ImageCapability]:
    """Text/image-to-image implemented by built-in model endpoints (channel facts), or ``ValueError`` without image channels.

    With ``endpoint``, evaluate it for pre-save checks of overrides after endpoint changes.
    """
    return channel_capabilities(_builtin_image_spec(provider_id, model_id, endpoint).registry_backend)


def builtin_channel_reference_limit(provider_id: str, model_id: str) -> int | None:
    """Static built-in model endpoint reference limit (channel facts), ``None`` for channel-unbounded counts.

    Raise ``ValueError`` without image channels.
    """
    return channel_reference_limit(builtin_model_spec(provider_id, "image", model_id).registry_backend)


def image_model_capabilities(
    model_id: str,
    *,
    builtin_provider: str | None,
    overrides: object | None = None,
    entry_binding: str | None = None,
) -> frozenset[ImageCapability] | None:
    """Model-side permitted text/image-to-image; ``None`` when neither dimension constrains.

    ``builtin_provider`` identifies built-ins for catalog entries; custom models pass ``None`` for live
    name lookup. ``overrides`` replaces whole entry dimensions; ``entry_binding`` binds entries.
    Unconstrained dimensions count as allowed.
    """
    if builtin_provider is not None:
        model = catalog.get_model(builtin_provider, model_id, media_type="image")
        if model is None:
            return None
        return frozenset(cap for cap in ImageCapability if cap.value in model.capabilities)
    facts = image_model_facts(model_id, overrides, entry_binding=entry_binding)
    declared = {
        ImageCapability.TEXT_TO_IMAGE: facts.text_to_image,
        ImageCapability.IMAGE_TO_IMAGE: facts.image_to_image,
    }
    if all(value is None for value in declared.values()):
        return None
    return frozenset(cap for cap, supported in declared.items() if supported is not False)


def custom_image_model_capabilities(
    model_id: str, *, overrides: object | None, endpoint_kind: str, entry_binding: str | None = None
) -> frozenset[ImageCapability] | None:
    """Custom image-model facts; ComfyUI uses node bindings only and always returns unconstrained ``None``."""
    if endpoint_kind == COMFYUI_KIND:
        return None
    return image_model_capabilities(model_id, builtin_provider=None, overrides=overrides, entry_binding=entry_binding)


def intersect_image_capabilities(
    channel: Iterable[ImageCapability], model: frozenset[ImageCapability] | None
) -> frozenset[ImageCapability]:
    """Channel-implemented and model-permitted capabilities; ``model`` ``None`` leaves channel facts unchanged."""
    implemented = frozenset(channel)
    return implemented if model is None else implemented & model


def effective_image_capabilities(
    channel: Iterable[ImageCapability], *, model_id: str, builtin_provider: str | None
) -> frozenset[ImageCapability]:
    """Channel-implemented text/image-to-image accepted by model entries; parameters match :func:`image_model_capabilities`."""
    return intersect_image_capabilities(channel, image_model_capabilities(model_id, builtin_provider=builtin_provider))


def entry_reference_limit(
    model_id: str, *, builtin_provider: str | None, endpoint_kind: str = "python", entry_binding: str | None = None
) -> int | None:
    """Model-side entry reference limit; ``None`` for unlisted/unmatched entries or ComfyUI endpoints.

    Parameters match :func:`image_model_capabilities`; ``endpoint_kind`` applies only to custom models.
    """
    if builtin_provider is not None:
        model = catalog.get_model(builtin_provider, model_id, media_type="image")
        return None if model is None else model.max_reference_images
    if endpoint_kind == COMFYUI_KIND:
        return None
    return image_model_facts(model_id, None, entry_binding=entry_binding).max_reference_images


def image_reference_override(overrides: object | None, *, endpoint_kind: str = "python") -> int | None:
    """Valid image-reference limit overrides; ComfyUI reads node bindings only, refusing overrides."""
    if endpoint_kind == COMFYUI_KIND:
        return None
    value = model_fact_overrides(overrides, "image").get("max_reference_images")
    return value if isinstance(value, int) else None


def image_reference_limit(
    *,
    capabilities: Iterable[ImageCapability],
    entry_limit: int | None,
    channel_limit: int | None,
    override: int | None = None,
) -> InputLimit[int]:
    """Effective reference limit. ``capabilities`` is effective text/image-to-image from :func:`intersect_image_capabilities`.

    Unsupported without image-to-image; otherwise minimum entry/channel value, then whole-dimension
    ``override`` replacement. Both sides ``None`` without overrides means unlisted.
    """
    return effective_input_limit(
        path_supported=ImageCapability.IMAGE_TO_IMAGE in frozenset(capabilities),
        declared=(entry_limit, channel_limit),
        override=override,
    )


async def backend_reference_image_limit(backend: ImageBackend, *, provider_id: str) -> InputLimit[int]:
    """Effective constructed-backend reference limit, shared by orchestration clipping and execution checks.

    Read channel ``reference_image_limit()``, which may query providers such as OpenRouter. Custom
    wrappers carry factory-injected effective capabilities and entry limits from model rows; other
    backends resolve entries through ``provider_id``.
    """
    from lib.custom_provider.backends import CustomImageBackend

    channel_limit = await backend.reference_image_limit()
    if isinstance(backend, CustomImageBackend):
        return image_reference_limit(
            capabilities=backend.effective_capabilities,
            entry_limit=backend.entry_reference_limit,
            override=backend.reference_limit_override,
            channel_limit=channel_limit,
        )
    builtin_provider = None if is_custom_provider(provider_id) else provider_id
    return image_reference_limit(
        capabilities=effective_image_capabilities(
            backend.capabilities, model_id=backend.model, builtin_provider=builtin_provider
        ),
        entry_limit=entry_reference_limit(backend.model, builtin_provider=builtin_provider),
        override=image_reference_override(catalog.user_added_capability_overrides(builtin_provider, backend.model))
        if builtin_provider is not None
        else None,
        channel_limit=channel_limit,
    )


async def gate_image_request(backend: ImageBackend, *, provider_id: str, reference_count: int) -> None:
    """Pre-submission capability checks shared by production/connection tests; raise :class:`ImageCapabilityError` on failure.

    ``reference_count`` is actual input count: zero requires text-to-image, otherwise image-to-image plus
    accepting :func:`backend_reference_image_limit` and counts within positive bounds. Overflow means
    callers failed to clip using orchestration's same effective value; reject before submitting,
    never truncate inside backends.
    """
    from lib.custom_provider.backends import CustomImageBackend

    if isinstance(backend, CustomImageBackend):
        # Custom factories already injected model-side facts: entries plus overrides, with unconstrained ComfyUI endpoints.
        available = backend.effective_capabilities
    else:
        available = effective_image_capabilities(
            backend.capabilities,
            model_id=backend.model,
            builtin_provider=None if is_custom_provider(provider_id) else provider_id,
        )
    needed = ImageCapability.IMAGE_TO_IMAGE if reference_count else ImageCapability.TEXT_TO_IMAGE
    reference_limit = await backend_reference_image_limit(backend, provider_id=provider_id) if reference_count else None
    if needed not in available or (reference_limit is not None and not reference_limit.accepts_input):
        raise ImageCapabilityError(
            "image_capability_missing_i2i" if reference_count else "image_capability_missing_t2i",
            provider=backend.name,
            model=backend.model,
        )
    bound = reference_limit.clamp_bound() if reference_limit is not None else None
    if bound is not None and reference_count > bound:
        raise ImageCapabilityError(
            "image_reference_images_exceeded", model=backend.model, limit=bound, count=reference_count
        )
