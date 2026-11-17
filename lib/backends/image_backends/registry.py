"""图片后端注册与工厂。"""

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
    """读某个内置调用通道声明实现了的文生图 / 图生图（类属性 ``CHANNEL_CAPABILITIES``），不构造实例。"""
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    declared = getattr(factory, "CHANNEL_CAPABILITIES", None)
    if not isinstance(declared, frozenset):
        raise ValueError(f"image backend {name!r} does not declare CHANNEL_CAPABILITIES")
    return declared


def channel_reference_limit(name: str) -> int | None:
    """读某个内置调用通道静态声明的参考图上限（类属性 ``CHANNEL_REFERENCE_LIMIT``），不构造实例。

    ``None`` 表示通道本身不限张数，上限由条目给出。按模型运行时查询的通道（OpenRouter）在这里给出端点的静态上限，
    运行时再按模型收窄。
    """
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    return reference_limit_of(factory)


def reference_limit_of(backend_class: object) -> int | None:
    """调用通道类上的 ``CHANNEL_REFERENCE_LIMIT``；没有声明时抛 ``ValueError``。"""
    declared = getattr(backend_class, "CHANNEL_REFERENCE_LIMIT", _UNDECLARED)
    if declared is _UNDECLARED:
        raise ValueError(f"image backend {backend_class!r} does not declare CHANNEL_REFERENCE_LIMIT")
    if declared is not None and (not isinstance(declared, int) or isinstance(declared, bool) or declared <= 0):
        raise ValueError(f"image backend {backend_class!r} declares invalid CHANNEL_REFERENCE_LIMIT {declared!r}")
    return declared


def channel_accepts_explicit_quality(name: str) -> bool:
    """某个内置调用通道是否接受显式画质（类属性 ``CHANNEL_ACCEPTS_QUALITY``），不构造实例；没有声明时为不接受。"""
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    return accepts_explicit_quality_of(factory)


def accepts_explicit_quality_of(backend_class: object) -> bool:
    """调用通道类是否接受显式画质，即用户选择的画质取值，区别于由分辨率档位推出的那一个（docs/adr/0104）。

    读类属性 ``CHANNEL_ACCEPTS_QUALITY``，缺省为不接受：不读请求 ``quality`` 字段的通道不用声明。
    """
    return getattr(backend_class, "CHANNEL_ACCEPTS_QUALITY", False) is True


def channel_usage_estimator(name: str) -> ImageUsageEstimator:
    """读某个内置调用通道的预估函数（类上的 ``estimate_usage``），不构造实例。"""
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown image backend: {name}")
    estimator = getattr(factory, "estimate_usage", None)
    if not callable(estimator):
        raise ValueError(f"image backend {name!r} does not provide estimate_usage")
    return cast(ImageUsageEstimator, estimator)
