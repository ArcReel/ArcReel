"""内置调用端点清单：目录数据里的端点声明按它校验，最低应用版本按它推导。

调用端点是代码事实，随 ArcReel 发版；这里是它的一份副本，供不依赖 ArcReel 应用的目录数据仓库读取。ArcReel
仓库用测试保证它与应用代码里的端点注册表和内置供应商端点声明一致。修改方式与起始版本的填写规则见
``docs/adr/0103``。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from .vocabulary import MediaType


@dataclass(frozen=True)
class BuiltinEndpoint:
    """一个内置调用端点。``since`` 是起始版本，即第一个带这个端点的应用版本；基线端点不带起始版本。"""

    media_type: MediaType
    since: str | None = None


@dataclass(frozen=True)
class ProviderEndpointOptions:
    """一个内置供应商在一个媒体类型下可挂接的内置调用端点与默认端点。"""

    endpoints: tuple[str, ...]
    default: str


def _baseline(media_type: MediaType, *names: str) -> dict[str, BuiltinEndpoint]:
    return dict.fromkeys(names, BuiltinEndpoint(media_type))


def _single(endpoint: str) -> ProviderEndpointOptions:
    return ProviderEndpointOptions((endpoint,), endpoint)


#: 内置供应商可挂接的内置调用端点：端点名 → 媒体类型与起始版本。清单首次发布时已有的端点都是基线端点。
BUILTIN_ENDPOINTS: Final[Mapping[str, BuiltinEndpoint]] = MappingProxyType(
    {
        **_baseline(
            "text", "agnes-chat", "ark-chat", "gemini-generate", "grok-chat", "openai-chat", "openai-responses"
        ),
        **_baseline(
            "image",
            "agnes-image-pixels",
            "agnes-image-tier",
            "ark-seedream-3",
            "ark-seedream-4",
            "dashscope-qwen-image",
            "dashscope-qwen-image-edit",
            "dashscope-wan-image",
            "gemini-image",
            "grok-image",
            "kling-image",
            "minimax-image",
            "openai-images",
            "vidu-image",
        ),
        **_baseline(
            "video",
            "agnes-video-2",
            "agnes-video-2-5",
            "ark-seedance-1",
            "ark-seedance-2",
            "ark-seedance-2-5",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-video",
            "dashscope-wan3-video",
            "gemini-vertex-video",
            "gemini-video",
            "grok-video-classic",
            "grok-video-mixed",
            "kling-video-audio",
            "kling-video-pro-audio",
            "kling-video-pro-silent",
            "kling-video-silent",
            "minimax-h3",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "openai-video",
            "vidu-video-2",
            "vidu-video-generic",
            "vidu-video-q1",
            "vidu-video-q1-classic",
            "vidu-video-q2",
            "vidu-video-q2-fast",
            "vidu-video-q2-pro",
            "vidu-video-q3-fast",
            "vidu-video-q3-pro",
            "vidu-video-q3-reference",
            "vidu-video-q3-turbo",
        ),
        **_baseline("audio", "dashscope-tts", "gemini-tts", "minimax-tts"),
    }
)

_SEEDREAM = ProviderEndpointOptions(("ark-seedream-3", "ark-seedream-4"), "ark-seedream-4")
_SEEDANCE = ProviderEndpointOptions(("ark-seedance-1", "ark-seedance-2", "ark-seedance-2-5"), "ark-seedance-2")

#: 内置供应商 → 媒体类型（``MEDIA_TYPES`` 之一）→ 可选端点与默认端点。官方收录的供应商模型只能挂接所在供应商这一媒体类型的可选端点。
BUILTIN_PROVIDER_ENDPOINTS: Final[Mapping[str, Mapping[str, ProviderEndpointOptions]]] = MappingProxyType(
    {
        "agnes": {
            "text": _single("agnes-chat"),
            "image": ProviderEndpointOptions(("agnes-image-pixels", "agnes-image-tier"), "agnes-image-tier"),
            "video": ProviderEndpointOptions(("agnes-video-2", "agnes-video-2-5"), "agnes-video-2-5"),
        },
        "ark": {"text": _single("ark-chat"), "image": _SEEDREAM, "video": _SEEDANCE},
        "ark-agent-plan": {"text": _single("ark-chat"), "image": _SEEDREAM, "video": _SEEDANCE},
        "dashscope": {
            "text": _single("openai-chat"),
            "image": ProviderEndpointOptions(
                ("dashscope-qwen-image", "dashscope-wan-image", "dashscope-qwen-image-edit"), "dashscope-qwen-image"
            ),
            "video": ProviderEndpointOptions(
                (
                    "dashscope-video",
                    "dashscope-always-on-video",
                    "dashscope-reference-video",
                    "dashscope-reference-voice-video",
                    "dashscope-wan3-video",
                ),
                "dashscope-always-on-video",
            ),
            "audio": _single("dashscope-tts"),
        },
        "gemini-aistudio": {
            "text": _single("gemini-generate"),
            "image": _single("gemini-image"),
            "video": _single("gemini-video"),
            "audio": _single("gemini-tts"),
        },
        "gemini-vertex": {
            "text": _single("gemini-generate"),
            "image": _single("gemini-image"),
            "video": _single("gemini-vertex-video"),
            "audio": _single("gemini-tts"),
        },
        "grok": {
            "text": _single("grok-chat"),
            "image": _single("grok-image"),
            "video": ProviderEndpointOptions(("grok-video-classic", "grok-video-mixed"), "grok-video-classic"),
        },
        "kling": {
            "image": _single("kling-image"),
            "video": ProviderEndpointOptions(
                ("kling-video-pro-silent", "kling-video-pro-audio", "kling-video-audio", "kling-video-silent"),
                "kling-video-pro-silent",
            ),
        },
        "minimax": {
            "text": _single("openai-chat"),
            "image": _single("minimax-image"),
            "video": ProviderEndpointOptions(
                ("minimax-hailuo-v1", "minimax-hailuo-v1-fast", "minimax-s2v-01", "minimax-h3"), "minimax-hailuo-v1"
            ),
            "audio": _single("minimax-tts"),
        },
        "openai": {
            "text": _single("openai-responses"),
            "image": _single("openai-images"),
            "video": _single("openai-video"),
        },
        "vidu": {
            "image": _single("vidu-image"),
            "video": ProviderEndpointOptions(
                (
                    "vidu-video-generic",
                    "vidu-video-q3-pro",
                    "vidu-video-q3-turbo",
                    "vidu-video-q3-fast",
                    "vidu-video-q3-reference",
                    "vidu-video-q2",
                    "vidu-video-q2-pro",
                    "vidu-video-q2-fast",
                    "vidu-video-q1",
                    "vidu-video-q1-classic",
                    "vidu-video-2",
                ),
                "vidu-video-q3-turbo",
            ),
        },
    }
)

#: 实例开始读取端点声明的应用版本。在此之前的实例按 API 模型名选端点。
ENDPOINT_DECLARATIONS_SINCE: str | None = "0.35.1"

_APP_VERSION = re.compile(r"\d+(\.\d+){0,2}")


def is_app_version(value: str) -> bool:
    """``value`` 是否是形如 ``0.34.0`` 的应用版本。"""
    return _APP_VERSION.fullmatch(value) is not None


def app_version_key(value: str) -> tuple[int, int, int]:
    """应用版本的数字比较键；省略的次版本、补丁号按 0 计，格式不合法时抛 ``ValueError``。"""
    if not is_app_version(value):
        raise ValueError(f"invalid application version: {value!r}")
    major, minor, patch = (*map(int, value.split(".")), 0, 0)[:3]
    return major, minor, patch


def provider_endpoint_options(provider_id: str, media_type: str) -> ProviderEndpointOptions | None:
    """内置供应商在这个媒体类型下的可选端点与默认端点；供应商或媒体类型没有声明时为 None。"""
    return BUILTIN_PROVIDER_ENDPOINTS.get(provider_id, {}).get(media_type)


def endpoint_version_floor(
    provider_id: str, media_type: str, endpoint: str, *, include_declaration_version: bool = True
) -> str | None:
    """挂接这个端点的官方收录供应商模型由端点决定的最低应用版本；没有下限时为 None。

    取两项中较高的那个：端点的起始版本；该供应商在这个媒体类型下有多个可选端点时，实例开始读取端点声明的版本。
    后者与模型是否挂默认端点无关：读取端点声明之前的实例按名字选端点，新模型可能被路由到别的端点。手写的最低应用版本
    由目录构建处理。已逐条核验的首发模型传 ``include_declaration_version=False`` 豁免声明读取版本，
    仍计入端点起始版本。
    """
    declared = BUILTIN_ENDPOINTS.get(endpoint)
    options = provider_endpoint_options(provider_id, media_type)
    floors = [declared.since] if declared is not None and declared.since is not None else []
    if (
        include_declaration_version
        and options is not None
        and len(options.endpoints) > 1
        and ENDPOINT_DECLARATIONS_SINCE is not None
    ):
        floors.append(ENDPOINT_DECLARATIONS_SINCE)
    return max(floors, key=app_version_key, default=None)
