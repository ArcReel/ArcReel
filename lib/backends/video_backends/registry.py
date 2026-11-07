"""Video backend registration and factories."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from arcreel_market_core.video_backend_contract import (
    VideoBackend,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.video_backends.name_family import catalog_entry_of
from lib.billing.metering import Usage

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry

_BACKEND_FACTORIES: dict[str, Callable[..., VideoBackend]] = {}


def register_backend(name: str, factory: Callable[..., VideoBackend]) -> None:
    """Register a video backend factory."""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> VideoBackend:
    """Construct video backends by name."""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown video backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """Return all registered backend names."""
    return list(_BACKEND_FACTORIES.keys())


def video_capabilities_for_model(name: str, model_id: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
    """Read backend model-declared capabilities through pure lookup, without instances/api_key.

    ``name`` is registry identity, built-in ``ProviderSpec.registry_backend``, not necessarily provider_id:
    gemini-aistudio / gemini-vertex both map to ``gemini``. Presentation resolves built-in booleans here,
    keeping backends authoritative instead of duplicating declarations. ``entry`` identifies the model;
    backends recognize channels through its name. Without it, look up ``model_id`` live (docs/adr/0105).
    """
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown video backend: {name}")
    caps_fn = getattr(factory, "video_capabilities_for_model", None)
    if caps_fn is None:
        raise ValueError(f"video backend {name!r} does not declare video_capabilities_for_model")
    caps = caps_fn(model_id, entry)
    if not isinstance(caps, VideoCapabilities):
        raise ValueError(f"video backend {name!r} video_capabilities_for_model returned {type(caps).__name__}")
    return caps


def effective_generate_audio_for_model(name: str, model_id: str, entry: CatalogEntry | None = None) -> bool:
    """Read effective ``generate_audio`` pricing parameters for backend default execution tiers.

    Backends without special rules retain request values. Model/default-tier narrowing uses same-name
    static queries rather than duplicating execution logic in estimates. ``entry`` identifies the same
    model entry as capability declarations.
    """
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown video backend: {name}")
    effective_fn = getattr(factory, "effective_generate_audio_for_model", None)
    if effective_fn is None:
        return True
    result = effective_fn(model_id, entry)
    if not isinstance(result, bool):
        raise ValueError(f"video backend {name!r} effective_generate_audio_for_model returned {type(result).__name__}")
    return result


def backend_metered_usage(
    backend: VideoBackend, request: VideoGenerationRequest, result: VideoGenerationResult | None = None
) -> Usage | None:
    """Per-request instance usage under metering contracts, or ``None`` when channels omit ``metered_usage``.

    Built-ins all declare it (:mod:`lib.backends.video_backends.metering`); custom wrappers delegate or
    report generic usage (:mod:`lib.custom_provider.metering`). Identify models through assembly entries
    (:func:`~lib.backends.video_backends.name_family.catalog_entry_of`), sharing capability/request sources.
    """
    metered_usage = getattr(backend, "metered_usage", None)
    if metered_usage is None:
        return None
    usage = metered_usage(backend.model, request, result, entry=catalog_entry_of(backend))
    if not isinstance(usage, Usage):
        raise ValueError(f"video backend {backend.name!r} metered_usage returned {type(usage).__name__}")
    return usage


def metered_usage_for_model(
    name: str,
    model_id: str,
    request: VideoGenerationRequest,
    result: VideoGenerationResult | None = None,
    *,
    entry: CatalogEntry | None = None,
) -> Usage:
    """Read per-request backend metering by registry name through pure calculation, without instances; ``result`` ``None`` means presubmission estimates.

    ``entry`` identifies models for channels (docs/adr/0105), matching execution instance assembly entries.
    """
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown video backend: {name}")
    metered_usage = getattr(factory, "metered_usage", None)
    if metered_usage is None:
        raise ValueError(f"video backend {name!r} does not declare metered_usage")
    usage = metered_usage(model_id, request, result, entry=entry)
    if not isinstance(usage, Usage):
        raise ValueError(f"video backend {name!r} metered_usage returned {type(usage).__name__}")
    return usage
