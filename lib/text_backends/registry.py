"""文本后端注册与工厂。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lib.text_backends.base import TextBackend

_BACKEND_FACTORIES: dict[str, Callable[..., TextBackend]] = {}


def register_backend(name: str, factory: Callable[..., TextBackend]) -> None:
    """注册一个文本后端工厂函数。"""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> TextBackend:
    """根据名称创建文本后端实例。"""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown text backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """返回所有已注册的后端名称。"""
    return list(_BACKEND_FACTORIES.keys())
