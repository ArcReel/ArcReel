"""Audio backend registration and factories."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lib.backends.audio_backends.base import AudioBackend

_BACKEND_FACTORIES: dict[str, Callable[..., AudioBackend]] = {}


def register_backend(name: str, factory: Callable[..., AudioBackend]) -> None:
    """Register an audio backend factory."""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> AudioBackend:
    """Construct audio backends by name."""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown audio backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """Return all registered backend names."""
    return list(_BACKEND_FACTORIES.keys())
