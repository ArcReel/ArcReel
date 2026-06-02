"""音频后端注册与工厂。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lib.audio_backends.base import AudioBackend

_BACKEND_FACTORIES: dict[str, Callable[..., AudioBackend]] = {}


def register_backend(name: str, factory: Callable[..., AudioBackend]) -> None:
    """注册一个音频后端工厂函数。"""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> AudioBackend:
    """根据名称创建音频后端实例。"""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown audio backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """返回所有已注册的后端名称。"""
    return list(_BACKEND_FACTORIES.keys())
