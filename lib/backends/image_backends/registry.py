"""Image backend registration and factories."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from lib.backends.image_backends.base import ImageBackend, ImageCapability, ImageUsageEstimator

_BACKEND_FACTORIES: dict[str, Callable[..., ImageBackend]] = {}
_UNDECLARED = object()


def register_backend(name: str, factory: Callable[..., ImageBackend]) -> None:
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> ImageBackend:
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown image backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    return list(_BACKEND_FACTORIES.keys())


def channel_capabilities(name: str) -> frozenset[ImageCapability]:
    """Read built-in channel text/image-to-image declarations (class ``CHANNEL_CAPABILITIES``) without constructing instances."""
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    declared = getattr(factory, "CHANNEL_CAPABILITIES", None)
    if not isinstance(declared, frozenset):
        raise ValueError(f"image backend {name!r} does not declare CHANNEL_CAPABILITIES")
    return declared


def channel_reference_limit(name: str) -> int | None:
    """Read static built-in channel reference limits (class ``CHANNEL_REFERENCE_LIMIT``) without instances.

    ``None`` means channel-unbounded counts, constrained by entries. Runtime model-querying channels
    such as OpenRouter expose static endpoint limits here, narrowing by models at runtime.
    """
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    return reference_limit_of(factory)


def reference_limit_of(backend_class: object) -> int | None:
    """Channel-class ``CHANNEL_REFERENCE_LIMIT``; raise ``ValueError`` when undeclared."""
    declared = getattr(backend_class, "CHANNEL_REFERENCE_LIMIT", _UNDECLARED)
    if declared is _UNDECLARED:
        raise ValueError(f"image backend {backend_class!r} does not declare CHANNEL_REFERENCE_LIMIT")
    if declared is not None and (not isinstance(declared, int) or isinstance(declared, bool) or declared <= 0):
        raise ValueError(f"image backend {backend_class!r} declares invalid CHANNEL_REFERENCE_LIMIT {declared!r}")
    return declared


def channel_usage_estimator(name: str) -> ImageUsageEstimator:
    """Read built-in channel estimators (class ``estimate_usage``) without instances."""
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    estimator = getattr(factory, "estimate_usage", None)
    if not callable(estimator):
        raise ValueError(f"image backend {name!r} does not provide estimate_usage")
    return cast(ImageUsageEstimator, estimator)
