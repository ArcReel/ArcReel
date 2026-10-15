"""视频后端注册与工厂。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from arcreel_market_core.video_backend_contract import (
    VideoBackend,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.billing.metering import Usage

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


def backend_metered_usage(
    backend: VideoBackend, request: VideoGenerationRequest, result: VideoGenerationResult | None = None
) -> Usage | None:
    """调用通道实例按用量契约上报的这次请求的计量；通道没有声明 ``metered_usage`` 时为 ``None``。

    内置调用通道都声明了它（见 :mod:`lib.backends.video_backends.metering`），自定义供应商的包装层转交被包装
    的通道或按通用口径上报（见 :mod:`lib.custom_provider.metering`）。
    """
    metered_usage = getattr(backend, "metered_usage", None)
    if metered_usage is None:
        return None
    usage = metered_usage(backend.model, request, result)
    if not isinstance(usage, Usage):
        raise ValueError(f"video backend {backend.name!r} metered_usage returned {type(usage).__name__}")
    return usage


def metered_usage_for_model(
    name: str, model_id: str, request: VideoGenerationRequest, result: VideoGenerationResult | None = None
) -> Usage:
    """按 registry 名读某后端对一个请求上报的计量 —— 纯计算，不构造实例；``result`` 为 ``None`` 时是提交前预估。"""
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown video backend: {name}")
    metered_usage = getattr(factory, "metered_usage", None)
    if metered_usage is None:
        raise ValueError(f"video backend {name!r} does not declare metered_usage")
    usage = metered_usage(model_id, request, result)
    if not isinstance(usage, Usage):
        raise ValueError(f"video backend {name!r} metered_usage returned {type(usage).__name__}")
    return usage
