"""Built-in endpoint list: validates catalog endpoint declarations and derives minimum app versions.

Endpoints are code facts released with ArcReel; this copy is read by the catalog data repository without depending on the app. ArcReel
repository tests ensure it matches the app endpoint registry and built-in provider declarations. Editing and initial-version rules are in
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
    """One built-in endpoint. ``since`` is the first app version containing it; baseline endpoints have no initial version."""

    media_type: MediaType
    since: str | None = None


@dataclass(frozen=True)
class ProviderEndpointOptions:
    """Eligible built-in endpoints and default endpoint for one built-in provider and media type."""

    endpoints: tuple[str, ...]
    default: str


def _baseline(media_type: MediaType, *names: str) -> dict[str, BuiltinEndpoint]:
    return dict.fromkeys(names, BuiltinEndpoint(media_type))


def _single(endpoint: str) -> ProviderEndpointOptions:
    return ProviderEndpointOptions((endpoint,), endpoint)


#: Built-in endpoints eligible for built-in providers: endpoint name → media type and initial version. Endpoints predating the first list release are baseline endpoints.
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
        "kling-image-single": BuiltinEndpoint("image", since="0.35.1"),
        "kling-video-model-path": BuiltinEndpoint("video", since="0.35.1"),
        "dashscope-speech-synthesizer": BuiltinEndpoint("audio", since="0.35.1"),
        "gemini-interactions-video": BuiltinEndpoint("video", since="0.35.1"),
        "gemini-vertex-interactions-video": BuiltinEndpoint("video", since="0.35.1"),
        "vidu-video-q4": BuiltinEndpoint("video", since="0.35.1"),
    }
)

_SEEDREAM = ProviderEndpointOptions(("ark-seedream-3", "ark-seedream-4"), "ark-seedream-4")
_SEEDANCE = ProviderEndpointOptions(("ark-seedance-1", "ark-seedance-2", "ark-seedance-2-5"), "ark-seedance-2")

#: Built-in provider → media type (one of ``MEDIA_TYPES``) → eligible and default endpoints. Officially listed models may attach only eligible endpoints for their provider and media type.
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
            "audio": ProviderEndpointOptions(("dashscope-tts", "dashscope-speech-synthesizer"), "dashscope-tts"),
        },
        "gemini-aistudio": {
            "text": _single("gemini-generate"),
            "image": _single("gemini-image"),
            "video": ProviderEndpointOptions(("gemini-video", "gemini-interactions-video"), "gemini-video"),
            "audio": _single("gemini-tts"),
        },
        "gemini-vertex": {
            "text": _single("gemini-generate"),
            "image": _single("gemini-image"),
            "video": ProviderEndpointOptions(
                ("gemini-vertex-video", "gemini-vertex-interactions-video"), "gemini-vertex-video"
            ),
            "audio": _single("gemini-tts"),
        },
        "grok": {
            "text": _single("grok-chat"),
            "image": _single("grok-image"),
            "video": ProviderEndpointOptions(("grok-video-classic", "grok-video-mixed"), "grok-video-classic"),
        },
        "kling": {
            "image": ProviderEndpointOptions(("kling-image", "kling-image-single"), "kling-image"),
            "video": ProviderEndpointOptions(
                (
                    "kling-video-pro-silent",
                    "kling-video-pro-audio",
                    "kling-video-audio",
                    "kling-video-silent",
                    "kling-video-model-path",
                ),
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
                    "vidu-video-q4",
                ),
                "vidu-video-q3-turbo",
            ),
        },
    }
)

#: App version at which instances began reading endpoint declarations. Earlier instances select endpoints by API model name.
ENDPOINT_DECLARATIONS_SINCE: str | None = "0.35.1"

_APP_VERSION = re.compile(r"\d+(\.\d+){0,2}")


def is_app_version(value: str) -> bool:
    """Whether ``value`` is an app version such as ``0.34.0``."""
    return _APP_VERSION.fullmatch(value) is not None


def app_version_key(value: str) -> tuple[int, int, int]:
    """Numeric comparison key for app versions; omitted minor and patch numbers count as 0, and invalid format raises ``ValueError``."""
    if not is_app_version(value):
        raise ValueError(f"invalid application version: {value!r}")
    major, minor, patch = (*map(int, value.split(".")), 0, 0)[:3]
    return major, minor, patch


def provider_endpoint_options(provider_id: str, media_type: str) -> ProviderEndpointOptions | None:
    """Eligible and default endpoints for a built-in provider and media type; None when the provider or media type is undeclared."""
    return BUILTIN_PROVIDER_ENDPOINTS.get(provider_id, {}).get(media_type)


def endpoint_version_floor(
    provider_id: str, media_type: str, endpoint: str, *, include_declaration_version: bool = True
) -> str | None:
    """Endpoint-derived minimum app version for an officially listed provider model using this endpoint; None when no lower bound exists.

    Take the higher of the endpoint's initial version and, when this provider has multiple eligible endpoints for the media type, the version that began reading endpoint declarations.
    The latter applies regardless of whether the model uses the default endpoint: earlier instances select by name and may route new models elsewhere. Manually specified minimum app versions
    are handled by catalog builds. Individually verified launch models use ``include_declaration_version=False`` to exclude the declaration-reading version,
    while still including the endpoint's initial version.
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
