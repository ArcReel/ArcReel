"""图片后端注册与工厂。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from lib.backends.image_backends.base import ImageBackend, ImageCapability, ImageUsageEstimator

_BACKEND_FACTORIES: dict[str, Callable[..., ImageBackend]] = {}


def register_backend(name: str, factory: Callable[..., ImageBackend]) -> None:
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> ImageBackend:
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown image backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    return list(_BACKEND_FACTORIES.keys())


def channel_capabilities(name: str) -> frozenset[ImageCapability]:
    """读某个内置调用通道声明实现了的文生图 / 图生图（类属性 ``CHANNEL_CAPABILITIES``），不构造实例。"""
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    declared = getattr(factory, "CHANNEL_CAPABILITIES", None)
    if not isinstance(declared, frozenset):
        raise ValueError(f"image backend {name!r} does not declare CHANNEL_CAPABILITIES")
    return declared


def channel_usage_estimator(name: str) -> ImageUsageEstimator:
    """读某个内置调用通道的预估函数（类上的 ``estimate_usage``），不构造实例。"""
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    estimator = getattr(factory, "estimate_usage", None)
    if not callable(estimator):
        raise ValueError(f"image backend {name!r} does not provide estimate_usage")
    return cast(ImageUsageEstimator, estimator)
