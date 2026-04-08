"""Text backend registry and factory."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lib.text_backends.base import TextBackend

_BACKEND_FACTORIES: dict[str, Callable[..., TextBackend]] = {}


def register_backend(name: str, factory: Callable[..., TextBackend]) -> None:
    """Register a text backend factory function."""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> TextBackend:
    """Create a text backend instance by name."""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown text backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """Return all registered backend names."""
    return list(_BACKEND_FACTORIES.keys())
