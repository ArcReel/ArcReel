"""图片后端注册与工厂。"""
from __future__ import annotations
from typing import Any, Callable
from lib.image_backends.base import ImageBackend

_BACKEND_FACTORIES: dict[str, Callable[..., ImageBackend]] = {}

def register_backend(name: str, factory: Callable[..., ImageBackend]) -> None:
    _BACKEND_FACTORIES[name] = factory

def create_backend(name: str, **kwargs: Any) -> ImageBackend:
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown image backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)

def get_registered_backends() -> list[str]:
    return list(_BACKEND_FACTORIES.keys())
