"""视频后端注册与工厂。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lib.video_backends.base import VideoBackend, VideoCapabilities

_BACKEND_FACTORIES: dict[str, Callable[..., VideoBackend]] = {}


def register_backend(name: str, factory: Callable[..., VideoBackend]) -> None:
    """注册一个视频后端工厂函数。"""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> VideoBackend:
    """根据名称创建视频后端实例。"""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown video backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """返回所有已注册的后端名称。"""
    return list(_BACKEND_FACTORIES.keys())


def video_capabilities_for_model(name: str, model_id: str) -> VideoCapabilities:
    """读某后端对某 model 声明的视频能力 —— 纯查表，不构造实例（无需 api_key）。

    ``name`` 是 registry 名（内置侧由 ``ProviderSpec.registry_backend`` 给出，与 provider_id
    不必相同：gemini-aistudio / gemini-vertex 同映射到 ``gemini``）。展示层解析内置模型的
    布尔能力位走这里，使 backend 保持唯一真相源，不在注册表复制第二份能力声明。
    """
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown video backend: {name}")
    caps_fn = getattr(factory, "video_capabilities_for_model", None)
    if caps_fn is None:
        raise ValueError(f"video backend {name!r} does not declare video_capabilities_for_model")
    caps = caps_fn(model_id)
    if not isinstance(caps, VideoCapabilities):
        raise ValueError(f"video backend {name!r} video_capabilities_for_model returned {type(caps).__name__}")
    return caps


def effective_generate_audio_for_model(name: str, model_id: str) -> bool:
    """读 backend 对默认执行档声明的有效 ``generate_audio`` 计价参数。

    未声明专属规则的 backend 沿用请求值；需要按 model / 默认档收窄的 backend 通过同名
    静态方法提供纯查询，避免估算侧复制执行实现。
    """
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown video backend: {name}")
    effective_fn = getattr(factory, "effective_generate_audio_for_model", None)
    if effective_fn is None:
        return True
    result = effective_fn(model_id)
    if not isinstance(result, bool):
        raise ValueError(f"video backend {name!r} effective_generate_audio_for_model returned {type(result).__name__}")
    return result
