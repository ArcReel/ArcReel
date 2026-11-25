"""ENDPOINT_REGISTRY — 自定义供应商可用 endpoint 单一真相源。

每条 endpoint 是一个 EndpointSpec，绑定 media_type、family、HTTP 调用形态与 build_backend 闭包。
实现形态有两种：Python backend，或随版声明式定义（``builtin_endpoints/*.json``，import 期经
builtin_definitions 装入本注册表，用 EndpointSpec.definition 与前者区分）。两者共用同一键域。
运行时通过 endpoint 字符串查表派发；
server.routers.custom_providers 通过 GET /custom-providers/endpoints 把目录暴露给前端，
让前端的下拉选项、路径展示完全派生自此真相源。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlsplit

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, VIDEO_TIER_SHORT_EDGE, short_edge_to_resolution
from arcreel_market_core.endpoint_definition.kinds import COMFYUI_KIND
from arcreel_market_core.endpoint_definition.media_type import definition_media_type
from arcreel_market_core.video_backend_contract import VideoCapabilities
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.audio_backends.dashscope_speech_synthesizer import (
    SPEECH_SYNTHESIZER_PATH,
    DashScopeSpeechSynthesizerBackend,
)
from lib.backends.audio_backends.gemini import GeminiAudioBackend
from lib.backends.audio_backends.minimax import MiniMaxAudioBackend
from lib.backends.audio_backends.openai import OpenAIAudioBackend
from lib.backends.dashscope_shared import dashscope_native_base_url
from lib.backends.endpoint_prefill import infer_endpoint as infer_endpoint
from lib.backends.endpoint_prefill import infer_media_type as infer_media_type
from lib.backends.image_backends.ark import SEEDREAM_BACKENDS
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_backends.dashscope import DASHSCOPE_IMAGE_BACKENDS
from lib.backends.image_backends.gemini import GeminiImageBackend
from lib.backends.image_backends.kling import KLING_IMAGE_BACKENDS
from lib.backends.image_backends.minimax import H3_EDITS_PATH as MINIMAX_H3_EDITS_PATH
from lib.backends.image_backends.minimax import H3_GENERATIONS_PATH as MINIMAX_H3_GENERATIONS_PATH
from lib.backends.image_backends.minimax import MiniMaxH3ImageBackend, MiniMaxImageBackend
from lib.backends.image_backends.openai import OpenAIImageBackend
from lib.backends.image_backends.openai_chat import OpenAIChatImageBackend
from lib.backends.image_backends.openrouter import IMAGES_PATH as OPENROUTER_IMAGES_PATH
from lib.backends.image_backends.openrouter import MODELS_PATH as OPENROUTER_MODELS_PATH
from lib.backends.image_backends.openrouter import OpenRouterImageBackend
from lib.backends.image_backends.registry import (
    describe_image_backend_class,
)
from lib.backends.kling_shared import kling_model_path_base_url
from lib.backends.minimax_shared import minimax_text_base_url
from lib.backends.provider_assets import InferenceCredentialRef
from lib.backends.text_backends.anthropic import AnthropicTextBackend
from lib.backends.text_backends.gemini import GeminiTextBackend
from lib.backends.text_backends.openai import OpenAITextBackend
from lib.backends.text_backends.openai_responses import OpenAIResponsesTextBackend
from lib.backends.video_backends.ark import SEEDANCE_BACKENDS
from lib.backends.video_backends.dashscope import DASHSCOPE_VIDEO_BACKENDS
from lib.backends.video_backends.gemini import GeminiVideoBackend
from lib.backends.video_backends.gemini_interactions import GeminiInteractionsVideoBackend
from lib.backends.video_backends.kling import KLING_BACKENDS
from lib.backends.video_backends.kling_model_path import KlingModelPathVideoBackend
from lib.backends.video_backends.openai import OpenAIVideoBackend
from lib.backends.video_backends.vidu import VIDU_BACKENDS
from lib.backends.video_capabilities import channel_projection, video_endpoint
from lib.capabilities.kernel import Endpoint, EndpointDurations, FixedDuration
from lib.config.url_utils import (
    ANTHROPIC_MESSAGES_PATH,
    ensure_google_base_url,
    ensure_openai_base_url,
    normalize_anthropic_base_url,
)
from lib.custom_provider import is_custom_endpoint
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.builtin_definitions import (
    BuiltinDefinitionError,
    builtin_definitions,
    declarative_display_name,
    declarative_family,
    declarative_request_path,
    load_builtin_definitions,
)
from lib.custom_provider.comfyui.capabilities import (
    duration_is_fixed,
    native_short_edge,
    size_is_fixed,
)
from lib.custom_provider.comfyui.comfyui_backend import ComfyuiVideoBackend
from lib.custom_provider.comfyui.comfyui_image_backend import (
    ComfyuiImageBackend,
    binding_image_capabilities,
)
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.declarative_backend import (
    REQUEST_SECTIONS,
    DeclarativeVideoBackend,
    request_urls,
    strips_base_url_version,
)
from lib.custom_provider.declarative_image_backend import (
    DeclarativeImageBackend,
    image_capabilities_from_definition,
)
from lib.custom_provider.definition_capabilities import decode_definition
from lib.custom_provider.path_overrides import (
    NO_PATH_OVERRIDES,
    BaseUrlRule,
    EndpointRequest,
    PathOverrideLayer,
    PathOverrides,
    path_override_layer,
    placeholder_names,
)

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry


CUSTOM_ENDPOINT_FAMILY = "custom"


class ProviderConnection(Protocol):
    @property
    def provider_id(self) -> str: ...
    @property
    def base_url(self) -> str: ...
    @property
    def api_key(self) -> str: ...


@dataclass(frozen=True)
class ProviderConnectionSnapshot:
    id: int | None
    provider_id: str
    base_url: str
    api_key: str = field(repr=False)


class BackendBuilder(Protocol):
    """endpoint 的 backend 构造闭包。

    ``path_overrides`` 是该模型生效的路径覆盖（``docs/adr/0101``），键都在
    :attr:`EndpointSpec.requests` 里；没有声明请求的端点恒收到空映射。
    """

    def __call__(
        self, provider: ProviderConnection, model_id: str, path_overrides: PathOverrides = ..., /
    ) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend: ...


@dataclass(frozen=True)
class EndpointSpec:
    """单条 endpoint 的元数据 + backend 构造闭包。"""

    key: str
    media_type: str
    family: str
    display_name_key: str
    request_method: str
    request_path_template: str
    build_backend: BackendBuilder
    source: Literal["builtin", "custom"] = "builtin"
    image_capabilities: frozenset[ImageCapability] | None = None
    describe_image: Endpoint | None = None
    video_max_reference_images: int | None = None
    describe_video: Callable[[str, CatalogEntry | None], Endpoint] | None = None
    definition: Mapping[str, Any] | None = None
    requests: tuple[EndpointRequest, ...] = ()
    metering_backend: type | None = None

    def image_description(self) -> Endpoint:
        if self.media_type != "image":
            raise ValueError(f"endpoint {self.key!r} is {self.media_type}, not image")
        if self.describe_image is not None:
            return self.describe_image
        if self.definition is not None:
            return decode_definition(self.definition)
        if self.metering_backend is not None:
            return describe_image_backend_class(self.metering_backend, capabilities=self.image_capabilities)
        if self.image_capabilities is None:
            raise ValueError(f"invalid image capability declaration for {self.key!r}")
        return Endpoint("image", frozenset(cap.value for cap in self.image_capabilities))

    def video_description(self, model_id: str, entry: CatalogEntry | None = None) -> Endpoint:
        if self.media_type != "video":
            raise ValueError(f"endpoint {self.key!r} is {self.media_type}, not video")
        if self.describe_video is not None:
            return self.describe_video(model_id, entry)
        if self.video_max_reference_images is None or self.video_max_reference_images < 0:
            raise ValueError(f"invalid video capability declaration for {self.key!r}")
        return video_endpoint(VideoCapabilities(max_reference_images=self.video_max_reference_images))

    @property
    def video_caps_for_model(self) -> Callable[[str, CatalogEntry | None], VideoCapabilities] | None:
        if self.describe_video is None:
            return None
        return lambda model, entry=None: channel_projection(self.video_description(model, entry))

    @property
    def end_image_capable(self) -> bool:
        return self.media_type == "video" and "last_frame" in self.video_description("").slots

    @property
    def reference_audio_capable(self) -> bool:
        return self.media_type == "video" and "reference_audio" in self.video_description("").slots

    @property
    def kind(self) -> str:
        """实现形态：有定义时取定义的 ``kind``，Python backend 实现的端点为 ``python``。

        ``python`` 不是定义容器的 kind——它没有定义可读，是「没有定义」这件事本身的名字。
        """
        return "python" if self.definition is None else str(self.definition["kind"])

    @property
    def duration_tier_optional(self) -> bool:
        """该端点的 ``supported_durations`` 允不允许是空集。

        ComfyUI 端点的时长可以整维不由 ArcReel 驱动（``docs/adr/0082``）：``frames`` 未绑定或读不到
        帧率来源时，这份 workflow 出它自己那一档，档位空集是如实的声明而非缺失。其余端点照 ADR 0086
        办——空集即配置缺陷，解析期 fail loud 把用户引回配置页。

        判据挂在 spec 上而不是留给各消费方比 ``kind``：能力解析在 ``lib.config`` 层，那一层按分层
        契约够不到 ``kind`` 常量所在的模块，取一个已解析好的 spec 上的属性则无需 import。
        """
        return self.kind == COMFYUI_KIND

    @property
    def size_fixed(self) -> bool:
        """尺寸这一维是不是由端点固定、比例与分辨率选择对它无效。

        只有 ComfyUI 端点会为真：宽高两侧不都有节点绑定时 ArcReel 驱动不动尺寸，界面据此禁用
        分辨率选择器并明示，而不是照收用户的选择再在填值期丢掉。其余端点的尺寸一律由请求参数
        决定，没有「固定」这一形态。
        """
        return self.definition is not None and self.kind == COMFYUI_KIND and size_is_fixed(self.definition["bindings"])

    @property
    def duration_fixed(self) -> bool:
        """时长这一维是不是由端点固定：ComfyUI 端点上 ``frames`` 未绑定即为真。

        与 :attr:`duration_tier_optional` 是两件事：后者说的是「档位允不允许是空集」，本属性说的
        是「用户能不能编辑档位」。绑了 ``frames`` 却读不到帧率来源时档位同样是空集，但那是一份
        可修的定义（补一个 ``fps`` 绑定或手填帧率），不是这份 workflow 的时长天生固定。
        """
        if self.media_type == "video" and self.kind == COMFYUI_KIND:
            return self.video_description("").duration == FixedDuration("no_frames")
        return (
            self.definition is not None and self.kind == COMFYUI_KIND and duration_is_fixed(self.definition["bindings"])
        )

    @property
    def duration_frame_rate_missing(self) -> bool:
        """档位为空的成因是不是「读不到帧率来源」：``frames`` 绑了却既无 ``fps`` 绑定也没手填帧率。

        与 :attr:`duration_fixed` 一样只挑文案，不参与只读 / 禁用判据（那一律取
        :attr:`duration_tier_empty`）。两位都为假而档位仍为空，说的是第三支：帧数已绑定、帧率也
        读得到，只是换算不出一档能原样写回的整秒时长——那一支既不是「天生固定」也补不出帧率来。
        """
        return (
            self.kind == COMFYUI_KIND
            and self.media_type == "video"
            and self.video_description("").duration == FixedDuration("missing_fps")
        )

    @property
    def endpoint_durations(self) -> list[int] | None:
        """端点自己那一份时长档位；档位不由端点说了算时为 ``None``。

        只有 ComfyUI 视频端点给得出——它由绑定表推导，对每个挂在这个端点上的模型行都是同一份。
        其余端点的档位声明在模型行 / 注册表上，本属性不越俎代庖。

        空列表与 ``None`` 是两件事：空列表说「这个端点确实答了，答案是这一维它驱动不了」，
        ``None`` 说「这个问题不该问端点」。
        """
        if self.definition is None or self.kind != COMFYUI_KIND or self.media_type != "video":
            return None
        mode = self.video_description("").duration
        return list(mode.seconds) if isinstance(mode, EndpointDurations) else []

    @property
    def duration_tier_empty(self) -> bool:
        """时长这一维根本给不出档位：ComfyUI 端点上原生时长推不出来即为真。

        三支都落在这里——``frames`` 未绑定（读不到字面帧数）、绑了 ``frames`` 却没有帧率来源
        （既无 ``fps`` 只读绑定、也无条目手填 fps），以及帧率读得到但换算不出整秒档位。界面的
        只读与禁用判据是本属性而不是 :attr:`duration_fixed`：用户编不动的是「档位为空」这件事，
        说给用户听的是哪一句则由 :attr:`duration_fixed` 与 :attr:`duration_frame_rate_missing`
        挑——「时长天生固定」、「没提供帧率」，两位都为假时是「帧数与帧率都在、换算不出整秒档位」。
        """
        return self.endpoint_durations == []

    @property
    def native_resolution(self) -> str | None:
        """这份 workflow 不选分辨率档位时实际会出的那一档；非 ComfyUI 端点或读不出字面尺寸时 None。"""
        if self.definition is None or self.kind != COMFYUI_KIND:
            return None
        short_edge = native_short_edge(self.definition)
        if short_edge is None:
            return None
        tier_map = IMAGE_TIER_SHORT_EDGE if self.media_type == "image" else VIDEO_TIER_SHORT_EDGE
        return short_edge_to_resolution(short_edge, tier_map=tier_map)

    @property
    def display_name(self) -> str | None:
        """声明式端点的显示名（取 ``meta.name``）；Python 内置为 None，按 display_name_key 取文案。"""
        return None if self.definition is None else declarative_display_name(self.definition)


def _build_openai_chat(provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES) -> CustomTextBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAITextBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            _OPENAI_CHAT_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomTextBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_responses(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomTextBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIResponsesTextBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            _OPENAI_RESPONSES_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomTextBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_gemini_generate(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomTextBackend:
    base_url = ensure_google_base_url(provider.base_url) or None
    delegate = GeminiTextBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=_google_path_layer(_GEMINI_GENERATE_REQUESTS, provider.base_url, model_id, path_overrides),
    )
    return CustomTextBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_anthropic_messages(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomTextBackend:
    base_url = normalize_anthropic_base_url(provider.base_url or "") or None
    delegate = AnthropicTextBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            _ANTHROPIC_MESSAGES_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomTextBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_images(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            (_OPENAI_IMAGES_GENERATE, _OPENAI_IMAGES_EDIT), path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_images_generations(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        mode="generations_only",
        transport_layer=path_override_layer(
            (_OPENAI_IMAGES_GENERATE,), path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_images_edits(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        mode="edits_only",
        transport_layer=path_override_layer(
            (_OPENAI_IMAGES_EDIT,), path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_chat_image(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIChatImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        provider_name=provider.provider_id,
        transport_layer=path_override_layer(
            _OPENAI_CHAT_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openrouter_images(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomImageBackend:
    """OpenRouter 统一 /images 端点（T2I + I2I）。

    OpenRouter 不提供 /images/edits，I2I 与 T2I 同走一个 JSON 端点；base_url 已含 /api/v1，
    不做 ensure_openai_base_url 的 /v1 追加。
    """
    base_url = (provider.base_url or "").strip().rstrip("/") or None
    delegate = OpenRouterImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            _OPENROUTER_IMAGES_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_gemini_image(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomImageBackend:
    base_url = ensure_google_base_url(provider.base_url) or None
    delegate = GeminiImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        image_model=model_id,
        transport_layer=_google_path_layer(_GEMINI_GENERATE_REQUESTS, provider.base_url, model_id, path_overrides),
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_tts(provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES) -> CustomAudioBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIAudioBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        provider_name=provider.provider_id,
        transport_layer=path_override_layer(
            _OPENAI_TTS_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomAudioBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_dashscope_tts(
    provider,
    model_id: str,
    path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    *,
    backend_class: type[DashScopeAudioBackend] = DashScopeAudioBackend,
    requests: tuple[EndpointRequest, ...] | None = None,
) -> CustomAudioBackend:
    layer = path_override_layer(
        _DASHSCOPE_GENERATION_REQUESTS if requests is None else requests,
        path_overrides,
        provider.base_url,
        dashscope_native_base_url(provider.base_url),
        model_id,
    )
    delegate = backend_class(
        api_key=provider.api_key, base_url=provider.base_url, model=model_id, transport_layer=layer
    )
    return CustomAudioBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_minimax_tts(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomAudioBackend:
    delegate = MiniMaxAudioBackend(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=model_id,
        provider_name=provider.provider_id,
        transport_layer=path_override_layer(
            _MINIMAX_TTS_REQUESTS,
            path_overrides,
            provider.base_url,
            minimax_text_base_url(provider.base_url),
            model_id,
        ),
    )
    return CustomAudioBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_gemini_tts(provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES) -> CustomAudioBackend:
    base_url = ensure_google_base_url(provider.base_url) or None
    delegate = GeminiAudioBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        provider_name=provider.provider_id,
        transport_layer=_google_path_layer(_GEMINI_GENERATE_REQUESTS, provider.base_url, model_id, path_overrides),
    )
    return CustomAudioBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_video(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomVideoBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIVideoBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            _OPENAI_VIDEO_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_gemini_video(
    provider,
    model_id: str,
    path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    *,
    backend_class: type[GeminiVideoBackend] = GeminiVideoBackend,
    requests: tuple[EndpointRequest, ...] | None = None,
) -> CustomVideoBackend:
    base_url = ensure_google_base_url(provider.base_url) or None
    delegate = backend_class(
        backend_type="aistudio",
        api_key=provider.api_key,
        base_url=base_url,
        video_model=model_id,
        transport_layer=_google_path_layer(
            requests or _GEMINI_VIDEO_REQUESTS, provider.base_url, model_id, path_overrides
        ),
    )
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _python_requests(rule: BaseUrlRule, *requests: tuple[str, str, str]) -> tuple[EndpointRequest, ...]:
    """Python 端点的请求声明，每项为（请求名、显示名 key、默认路径）；占位符放行模型名与默认路径里已有的。"""
    return tuple(
        EndpointRequest(
            name=name,
            display_name_key=display_name_key,
            default_path=path,
            placeholders=tuple(sorted({"model", *placeholder_names(path)})),
            base_url_rule=rule,
        )
        for name, display_name_key, path in requests
    )


_ARK_SEEDANCE_REQUESTS = _python_requests(
    "ensure_api_v3",
    ("submit", "cp_request_submit", "/contents/generations/tasks"),
    ("poll", "cp_request_poll", "/contents/generations/tasks/{{ task_id }}"),
)
_ARK_SEEDREAM_REQUESTS = _python_requests("ensure_api_v3", ("generate", "cp_request_generate", "/images/generations"))
_VIDU_VIDEO_REQUESTS = _python_requests(
    "ensure_ent_v2",
    ("submit_text2video", "cp_request_submit_text2video", "/text2video"),
    ("submit_img2video", "cp_request_submit_img2video", "/img2video"),
    ("submit_start_end2video", "cp_request_submit_start_end2video", "/start-end2video"),
    ("submit_reference2video", "cp_request_submit_reference2video", "/reference2video"),
    ("poll", "cp_request_poll", "/tasks/{{ task_id }}/creations"),
)
_KLING_IMAGE_REQUESTS = _python_requests(
    "ensure_v1",
    ("submit", "cp_request_submit", "/images/generations"),
    ("poll", "cp_request_poll", "/images/generations/{{ task_id }}"),
)
_KLING_VIDEO_REQUESTS = _python_requests(
    "ensure_v1",
    ("submit_text2video", "cp_request_submit_text2video", "/videos/text2video"),
    ("poll_text2video", "cp_request_poll_text2video", "/videos/text2video/{{ task_id }}"),
    ("submit_image2video", "cp_request_submit_img2video", "/videos/image2video"),
    ("poll_image2video", "cp_request_poll_img2video", "/videos/image2video/{{ task_id }}"),
    ("submit_multi_image2video", "cp_request_submit_multi_image2video", "/videos/multi-image2video"),
    ("poll_multi_image2video", "cp_request_poll_multi_image2video", "/videos/multi-image2video/{{ task_id }}"),
)
_KLING_MODEL_PATH_VIDEO_REQUESTS = _python_requests(
    "strip_version",
    ("submit_text2video", "cp_request_submit_text2video", "/text-to-video/{{ model }}"),
    ("submit_image2video", "cp_request_submit_img2video", "/image-to-video/{{ model }}"),
    ("poll", "cp_request_poll", "/tasks"),
)
_DASHSCOPE_GENERATION_REQUESTS = _python_requests(
    "dashscope_api_v1", ("generate", "cp_request_generate", "/services/aigc/multimodal-generation/generation")
)
_DASHSCOPE_SPEECH_SYNTHESIZER_REQUESTS = _python_requests(
    "dashscope_api_v1", ("generate", "cp_request_generate", SPEECH_SYNTHESIZER_PATH)
)
_DASHSCOPE_ASYNC_VIDEO_REQUESTS = _python_requests(
    "dashscope_api_v1",
    ("submit", "cp_request_submit", "/services/aigc/video-generation/video-synthesis"),
    ("poll", "cp_request_poll", "/tasks/{{ task_id }}"),
)
_MINIMAX_IMAGE_REQUESTS = _python_requests("ensure_v1", ("generate", "cp_request_generate", "/image_generation"))
_MINIMAX_H3_IMAGE_REQUESTS = _python_requests(
    "ensure_v1",
    ("generate", "cp_request_generate", MINIMAX_H3_GENERATIONS_PATH),
    ("edit", "cp_request_edit", MINIMAX_H3_EDITS_PATH),
)
_OPENROUTER_IMAGES_REQUESTS = _python_requests(
    "as_is",
    ("generate", "cp_request_generate", OPENROUTER_IMAGES_PATH),
    ("model_catalog", "cp_request_model_catalog", OPENROUTER_MODELS_PATH),
)
_MINIMAX_TTS_REQUESTS = _python_requests("ensure_v1", ("generate", "cp_request_generate", "/t2a_v2"))
_GEMINI_GENERATE_REQUESTS = _python_requests(
    "strip_version", ("generate", "cp_request_generate", "/v1beta/models/{{ model }}:generateContent")
)
_GEMINI_VIDEO_REQUESTS = _python_requests(
    "strip_version",
    ("submit", "cp_request_submit", "/v1beta/models/{{ model }}:predictLongRunning"),
    # operation name 形如 models/<model>/operations/<id>，含 /，原样展开为多级路径
    ("poll", "cp_request_poll", "/v1beta/{{ task_id }}"),
    # SDK 从产物 uri 取出文件 id，再按 base_url 拼下载地址，所以下载也相对 base_url
    ("download", "cp_request_download", "/v1beta/files/{{ file_id }}:download"),
)


_GEMINI_INTERACTIONS_VIDEO_REQUESTS = _python_requests(
    "strip_version",
    ("submit", "cp_request_submit", "/v1beta/interactions"),
    ("poll", "cp_request_poll", "/v1beta/interactions/{{ task_id }}"),
)


def _google_path_layer(
    requests: tuple[EndpointRequest, ...], base_url: str | None, model_id: str, path_overrides: PathOverrides
) -> PathOverrideLayer | None:
    # 占位符不跨 ``:``：含 / 的 operation name 整段捕获，SDK 的方法后缀（:predictLongRunning、:download）仍区分得开
    return path_override_layer(
        requests, path_overrides, base_url, ensure_google_base_url(base_url), model_id, placeholder="[^:]+"
    )


_OPENAI_CHAT_REQUESTS = _python_requests("append_v1", ("generate", "cp_request_generate", "/chat/completions"))
_OPENAI_RESPONSES_REQUESTS = _python_requests("append_v1", ("generate", "cp_request_generate", "/responses"))
_OPENAI_IMAGES_GENERATE, _OPENAI_IMAGES_EDIT = _python_requests(
    "append_v1",
    ("generate", "cp_request_generate", "/images/generations"),
    ("edit", "cp_request_edit", "/images/edits"),
)
_OPENAI_TTS_REQUESTS = _python_requests("append_v1", ("generate", "cp_request_generate", "/audio/speech"))
_OPENAI_VIDEO_REQUESTS = _python_requests(
    "append_v1",
    ("submit", "cp_request_submit", "/videos"),
    ("poll", "cp_request_poll", "/videos/{{ task_id }}"),
    ("download", "cp_request_download", "/videos/{{ task_id }}/content"),
)
_ANTHROPIC_MESSAGES_REQUESTS = _python_requests(
    "strip_messages", ("generate", "cp_request_generate", ANTHROPIC_MESSAGES_PATH)
)


def _ensure_url_path_suffix(base_url: str | None, suffix: str) -> str | None:
    """补全协议已知挂载路径（ark /api/v3、vidu /ent/v2、kling /v1）；
    已带对应协议路径则原样信任，避免重复叠加。供 ark/vidu/kling 闭包复用。

    纯域名（无 scheme，如 ``relay.example.com``）会被 urlsplit 整体当作 path，
    先补 ``https://`` 再判定，否则 host-only 配置既补不上协议也挂不上路径。
    """
    s = (base_url or "").strip().rstrip("/")
    if not s:
        return None
    normalized = s if "://" in s else f"https://{s}"
    if urlsplit(normalized).path.rstrip("/").endswith(suffix):
        return normalized
    return normalized + suffix


def _custom_inference_credential(provider) -> InferenceCredentialRef | None:
    """自定义供应商作为推理凭证的引用；尚未入库（没有记录 ID）的供应商没有可绑定的记录。

    素材库绑定按自定义供应商记录与它配置里的原始 Base URL 确认（见 ``lib.asset_library.bindings``），
    不按补全 ``/api/v3`` 后的请求地址。
    """
    credential_id = getattr(provider, "id", None)
    if not isinstance(credential_id, int) or isinstance(credential_id, bool):
        return None
    return InferenceCredentialRef(
        credential_type="custom",
        credential_id=credential_id,
        api_key=provider.api_key or "",
        base_url=provider.base_url,
    )


def _build_ark_seedance(
    provider,
    model_id: str,
    path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    *,
    endpoint: str,
) -> CustomVideoBackend:
    """Seedance 各代端点共用的构造：``endpoint`` 选实现这一代请求结构与计量的调用通道。"""
    base_url = _ensure_url_path_suffix(provider.base_url, "/api/v3")
    layer = path_override_layer(_ARK_SEEDANCE_REQUESTS, path_overrides, provider.base_url, base_url, model_id)
    delegate = SEEDANCE_BACKENDS[endpoint](
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=layer,
        inference_credential=_custom_inference_credential(provider),
    )
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_ark_seedream(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES, *, endpoint: str
) -> CustomImageBackend:
    base_url = _ensure_url_path_suffix(provider.base_url, "/api/v3")
    layer = path_override_layer(_ARK_SEEDREAM_REQUESTS, path_overrides, provider.base_url, base_url, model_id)
    delegate = SEEDREAM_BACKENDS[endpoint](
        api_key=provider.api_key, base_url=base_url, model=model_id, transport_layer=layer
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_vidu_video(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES, *, endpoint: str
) -> CustomVideoBackend:
    base_url = _ensure_url_path_suffix(provider.base_url, "/ent/v2")
    layer = path_override_layer(_VIDU_VIDEO_REQUESTS, path_overrides, provider.base_url, base_url, model_id)
    delegate = VIDU_BACKENDS[endpoint](
        api_key=provider.api_key, base_url=base_url, model=model_id, transport_layer=layer
    )
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_dashscope_image(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES, *, endpoint: str
) -> CustomImageBackend:
    layer = path_override_layer(
        _DASHSCOPE_GENERATION_REQUESTS,
        path_overrides,
        provider.base_url,
        dashscope_native_base_url(provider.base_url),
        model_id,
    )
    delegate = DASHSCOPE_IMAGE_BACKENDS[endpoint](
        api_key=provider.api_key, base_url=provider.base_url, model=model_id, transport_layer=layer
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_dashscope_async_video(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES, *, endpoint: str
) -> CustomVideoBackend:
    layer = path_override_layer(
        _DASHSCOPE_ASYNC_VIDEO_REQUESTS,
        path_overrides,
        provider.base_url,
        dashscope_native_base_url(provider.base_url),
        model_id,
    )
    delegate = DASHSCOPE_VIDEO_BACKENDS[endpoint](
        api_key=provider.api_key, base_url=provider.base_url, model=model_id, transport_layer=layer
    )
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_minimax_image(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomImageBackend:
    layer = path_override_layer(
        _MINIMAX_IMAGE_REQUESTS,
        path_overrides,
        provider.base_url,
        minimax_text_base_url(provider.base_url),
        model_id,
    )
    delegate = MiniMaxImageBackend(
        api_key=provider.api_key, base_url=provider.base_url, model=model_id, transport_layer=layer
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_minimax_h3_image(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomImageBackend:
    layer = path_override_layer(
        _MINIMAX_H3_IMAGE_REQUESTS,
        path_overrides,
        provider.base_url,
        minimax_text_base_url(provider.base_url),
        model_id,
    )
    delegate = MiniMaxH3ImageBackend(
        api_key=provider.api_key, base_url=provider.base_url, model=model_id, transport_layer=layer
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_kling_image(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES, *, endpoint: str
) -> CustomImageBackend:
    base_url = _ensure_url_path_suffix(provider.base_url, "/v1")
    delegate = KLING_IMAGE_BACKENDS[endpoint](
        auth_mode="bearer",
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            _KLING_IMAGE_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_kling_video(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES, *, endpoint: str
) -> CustomVideoBackend:
    base_url = _ensure_url_path_suffix(provider.base_url, "/v1")
    delegate = KLING_BACKENDS[endpoint](
        auth_mode="bearer",
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            _KLING_VIDEO_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_kling_model_path_video(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
) -> CustomVideoBackend:
    base_url = kling_model_path_base_url(provider.base_url)
    delegate = KlingModelPathVideoBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        transport_layer=path_override_layer(
            _KLING_MODEL_PATH_VIDEO_REQUESTS, path_overrides, provider.base_url, base_url, model_id
        ),
    )
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


ENDPOINT_REGISTRY: dict[str, EndpointSpec] = {
    "openai-chat": EndpointSpec(
        key="openai-chat",
        media_type="text",
        family="openai",
        display_name_key="endpoint_openai_chat_display",
        request_method="POST",
        request_path_template="/v1/chat/completions",
        build_backend=_build_openai_chat,
        requests=_OPENAI_CHAT_REQUESTS,
    ),
    "openai-responses": EndpointSpec(
        key="openai-responses",
        media_type="text",
        family="openai",
        display_name_key="endpoint_openai_responses_display",
        request_method="POST",
        request_path_template="/v1/responses",
        build_backend=_build_openai_responses,
        requests=_OPENAI_RESPONSES_REQUESTS,
    ),
    "gemini-generate": EndpointSpec(
        key="gemini-generate",
        media_type="text",
        family="google",
        display_name_key="endpoint_gemini_generate_display",
        request_method="POST",
        request_path_template="/v1beta/models/{model}:generateContent",
        build_backend=_build_gemini_generate,
        requests=_GEMINI_GENERATE_REQUESTS,
    ),
    "anthropic-messages": EndpointSpec(
        key="anthropic-messages",
        media_type="text",
        family="anthropic",
        display_name_key="endpoint_anthropic_messages_display",
        request_method="POST",
        request_path_template=ANTHROPIC_MESSAGES_PATH,
        build_backend=_build_anthropic_messages,
        requests=_ANTHROPIC_MESSAGES_REQUESTS,
    ),
    "openai-images": EndpointSpec(
        key="openai-images",
        media_type="image",
        family="openai",
        display_name_key="endpoint_openai_images_display",
        request_method="POST",
        request_path_template="/v1/images/{generations,edits}",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_openai_images,
        requests=(_OPENAI_IMAGES_GENERATE, _OPENAI_IMAGES_EDIT),
        metering_backend=OpenAIImageBackend,
    ),
    "openai-images-generations": EndpointSpec(
        key="openai-images-generations",
        media_type="image",
        family="openai",
        display_name_key="endpoint_openai_images_generations_display",
        request_method="POST",
        request_path_template="/v1/images/generations",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE}),
        build_backend=_build_openai_images_generations,
        requests=(_OPENAI_IMAGES_GENERATE,),
        metering_backend=OpenAIImageBackend,
    ),
    "openai-images-edits": EndpointSpec(
        key="openai-images-edits",
        media_type="image",
        family="openai",
        display_name_key="endpoint_openai_images_edits_display",
        request_method="POST",
        request_path_template="/v1/images/edits",
        image_capabilities=frozenset({ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_openai_images_edits,
        requests=(_OPENAI_IMAGES_EDIT,),
        metering_backend=OpenAIImageBackend,
    ),
    "openai-chat-image": EndpointSpec(
        key="openai-chat-image",
        media_type="image",
        family="openai",
        display_name_key="endpoint_openai_chat_image_display",
        request_method="POST",
        request_path_template="/v1/chat/completions",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_openai_chat_image,
        requests=_OPENAI_CHAT_REQUESTS,
        metering_backend=OpenAIChatImageBackend,
    ),
    "openrouter-images": EndpointSpec(
        key="openrouter-images",
        media_type="image",
        family="openrouter",
        display_name_key="endpoint_openrouter_images_display",
        request_method="POST",
        request_path_template="/images",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_openrouter_images,
        requests=_OPENROUTER_IMAGES_REQUESTS,
        metering_backend=OpenRouterImageBackend,
    ),
    "gemini-image": EndpointSpec(
        key="gemini-image",
        media_type="image",
        family="google",
        display_name_key="endpoint_gemini_image_display",
        request_method="POST",
        request_path_template="/v1beta/models/{model}:generateContent",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_gemini_image,
        requests=_GEMINI_GENERATE_REQUESTS,
        metering_backend=GeminiImageBackend,
    ),
    "openai-video": EndpointSpec(
        key="openai-video",
        media_type="video",
        family="openai",
        display_name_key="endpoint_openai_video_display",
        request_method="POST",
        request_path_template="/v1/videos",
        build_backend=_build_openai_video,
        requests=_OPENAI_VIDEO_REQUESTS,
        metering_backend=OpenAIVideoBackend,
        describe_video=OpenAIVideoBackend.describe_video_endpoint,
    ),
    **{
        key: EndpointSpec(
            key=key,
            media_type="video",
            family="ark",
            display_name_key=f"endpoint_{key.replace('-', '_')}_display",
            request_method="POST",
            request_path_template="/api/v3/contents/generations/tasks",
            build_backend=partial(_build_ark_seedance, endpoint=key),
            requests=_ARK_SEEDANCE_REQUESTS,
            metering_backend=backend_class,
            describe_video=backend_class.describe_video_endpoint,
        )
        for key, backend_class in SEEDANCE_BACKENDS.items()
    },
    **{
        key: EndpointSpec(
            key=key,
            media_type="image",
            family="ark",
            display_name_key=f"endpoint_{key.replace('-', '_')}_display",
            request_method="POST",
            request_path_template="/api/v3/images/generations",
            image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
            build_backend=partial(_build_ark_seedream, endpoint=key),
            requests=_ARK_SEEDREAM_REQUESTS,
            metering_backend=backend_class,
        )
        for key, backend_class in SEEDREAM_BACKENDS.items()
    },
    **{
        key: EndpointSpec(
            key=key,
            media_type="video",
            family="vidu",
            display_name_key="endpoint_" + key.replace("-", "_") + "_display",
            request_method="POST",
            request_path_template="/ent/v2/img2video",
            build_backend=partial(_build_vidu_video, endpoint=key),
            requests=_VIDU_VIDEO_REQUESTS,
            metering_backend=backend,
            describe_video=backend.describe_video_endpoint,
        )
        for key, backend in VIDU_BACKENDS.items()
    },
    **{
        key: EndpointSpec(
            key=key,
            media_type="image",
            family="dashscope",
            display_name_key=f"endpoint_{key.replace('-', '_')}_display",
            request_method="POST",
            request_path_template="/api/v1/services/aigc/multimodal-generation/generation",
            image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
            build_backend=partial(_build_dashscope_image, endpoint=key),
            requests=_DASHSCOPE_GENERATION_REQUESTS,
            metering_backend=backend_class,
        )
        for key, backend_class in DASHSCOPE_IMAGE_BACKENDS.items()
    },
    "openai-tts": EndpointSpec(
        key="openai-tts",
        media_type="audio",
        family="openai",
        display_name_key="endpoint_openai_tts_display",
        request_method="POST",
        request_path_template="/v1/audio/speech",
        build_backend=_build_openai_tts,
        requests=_OPENAI_TTS_REQUESTS,
    ),
    "dashscope-tts": EndpointSpec(
        key="dashscope-tts",
        media_type="audio",
        family="dashscope",
        display_name_key="endpoint_dashscope_tts_display",
        request_method="POST",
        request_path_template="/api/v1/services/aigc/multimodal-generation/generation",
        build_backend=_build_dashscope_tts,
        requests=_DASHSCOPE_GENERATION_REQUESTS,
    ),
    "dashscope-speech-synthesizer": EndpointSpec(
        key="dashscope-speech-synthesizer",
        media_type="audio",
        family="dashscope",
        display_name_key="endpoint_dashscope_speech_synthesizer_display",
        request_method="POST",
        request_path_template=f"/api/v1{SPEECH_SYNTHESIZER_PATH}",
        build_backend=partial(
            _build_dashscope_tts,
            backend_class=DashScopeSpeechSynthesizerBackend,
            requests=_DASHSCOPE_SPEECH_SYNTHESIZER_REQUESTS,
        ),
        requests=_DASHSCOPE_SPEECH_SYNTHESIZER_REQUESTS,
    ),
    "gemini-video": EndpointSpec(
        key="gemini-video",
        media_type="video",
        family="google",
        display_name_key="endpoint_gemini_video_display",
        request_method="POST",
        request_path_template="/v1beta/models/{model}:predictLongRunning",
        build_backend=_build_gemini_video,
        requests=_GEMINI_VIDEO_REQUESTS,
        metering_backend=GeminiVideoBackend,
        describe_video=GeminiVideoBackend.describe_video_endpoint,
    ),
    "gemini-interactions-video": EndpointSpec(
        key="gemini-interactions-video",
        media_type="video",
        family="google",
        display_name_key="endpoint_gemini_interactions_video_display",
        request_method="POST",
        request_path_template="/v1beta/interactions",
        build_backend=partial(
            _build_gemini_video,
            backend_class=GeminiInteractionsVideoBackend,
            requests=_GEMINI_INTERACTIONS_VIDEO_REQUESTS,
        ),
        requests=_GEMINI_INTERACTIONS_VIDEO_REQUESTS,
        metering_backend=GeminiInteractionsVideoBackend,
        describe_video=GeminiInteractionsVideoBackend.describe_video_endpoint,
    ),
    "minimax-tts": EndpointSpec(
        key="minimax-tts",
        media_type="audio",
        family="minimax",
        display_name_key="endpoint_minimax_tts_display",
        request_method="POST",
        request_path_template="/v1/t2a_v2",
        build_backend=_build_minimax_tts,
        requests=_MINIMAX_TTS_REQUESTS,
    ),
    "gemini-tts": EndpointSpec(
        key="gemini-tts",
        media_type="audio",
        family="google",
        display_name_key="endpoint_gemini_tts_display",
        request_method="POST",
        request_path_template="/v1beta/models/{model}:generateContent",
        build_backend=_build_gemini_tts,
        requests=_GEMINI_GENERATE_REQUESTS,
    ),
    **{
        endpoint: EndpointSpec(
            key=endpoint,
            media_type="video",
            family="dashscope",
            display_name_key="endpoint_" + endpoint.replace("-", "_") + "_display",
            request_method="POST",
            request_path_template="/api/v1/services/aigc/video-generation/video-synthesis",
            build_backend=partial(_build_dashscope_async_video, endpoint=endpoint),
            requests=_DASHSCOPE_ASYNC_VIDEO_REQUESTS,
            metering_backend=backend,
            describe_video=backend.describe_video_endpoint,
        )
        for endpoint, backend in DASHSCOPE_VIDEO_BACKENDS.items()
    },
    "minimax-image": EndpointSpec(
        key="minimax-image",
        media_type="image",
        family="minimax",
        display_name_key="endpoint_minimax_image_display",
        request_method="POST",
        request_path_template="/image_generation",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_minimax_image,
        requests=_MINIMAX_IMAGE_REQUESTS,
        metering_backend=MiniMaxImageBackend,
    ),
    "minimax-h3-image": EndpointSpec(
        key="minimax-h3-image",
        media_type="image",
        family="minimax",
        display_name_key="endpoint_minimax_h3_image_display",
        request_method="POST",
        request_path_template=MINIMAX_H3_GENERATIONS_PATH,
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_minimax_h3_image,
        requests=_MINIMAX_H3_IMAGE_REQUESTS,
        metering_backend=MiniMaxH3ImageBackend,
    ),
    **{
        key: EndpointSpec(
            key=key,
            media_type="image",
            family="kling",
            display_name_key="endpoint_" + key.replace("-", "_") + "_display",
            request_method="POST",
            request_path_template="/v1/images/generations",
            image_capabilities=backend.CHANNEL_CAPABILITIES,
            build_backend=partial(_build_kling_image, endpoint=key),
            requests=_KLING_IMAGE_REQUESTS,
            metering_backend=backend,
        )
        for key, backend in KLING_IMAGE_BACKENDS.items()
    },
    **{
        key: EndpointSpec(
            key=key,
            media_type="video",
            family="kling",
            display_name_key="endpoint_" + key.replace("-", "_") + "_display",
            request_method="POST",
            request_path_template="/v1/videos/{text2video,image2video,multi-image2video}",
            build_backend=partial(_build_kling_video, endpoint=key),
            requests=_KLING_VIDEO_REQUESTS,
            metering_backend=backend,
            describe_video=backend.describe_video_endpoint,
        )
        for key, backend in KLING_BACKENDS.items()
    },
    "kling-video-model-path": EndpointSpec(
        key="kling-video-model-path",
        media_type="video",
        family="kling",
        display_name_key="endpoint_kling_video_model_path_display",
        request_method="POST",
        request_path_template="/{text-to-video,image-to-video}/{model}",
        build_backend=_build_kling_model_path_video,
        requests=_KLING_MODEL_PATH_VIDEO_REQUESTS,
        metering_backend=KlingModelPathVideoBackend,
        describe_video=KlingModelPathVideoBackend.describe_video_endpoint,
    ),
}


def validate_video_caps_declaration(spec: EndpointSpec) -> None:
    """校验单条 spec 的参考图上限来源：caps_fn 若声明必须可调用；video endpoint 必须「int cap」
    XOR「caps_fn 非 None」恰一、且 int cap 非负；非 video endpoint 两者皆 None。misconfig（caps_fn
    填成非 callable、多 model 共享端点漏配 caps_fn、同时声明二者、或声明负数 cap）在构造处
    fail-fast，而非等到 request 期 resolver 才抛。

    内置注册表在 import 期逐条过此校验；声明式端点在 declarative_endpoint_spec 里构造完即过这里
    ——各来源的 spec 走同一条不变式，能力判定层因此不必区分 spec 从哪来。
    """
    key = spec.key
    cap = spec.video_max_reference_images
    caps_fn = spec.describe_video
    has_int = cap is not None
    if caps_fn is not None and not callable(caps_fn):
        raise ValueError(f"endpoint {key!r} declares non-callable video_caps_for_model: {caps_fn!r}")
    has_fn = callable(caps_fn)
    if spec.media_type == "video":
        if has_int == has_fn:
            raise ValueError(
                f"video endpoint {key!r} must declare exactly one of video_max_reference_images "
                f"(int) or video_caps_for_model (callable), got "
                f"video_max_reference_images={cap!r}, "
                f"describe_video={caps_fn!r}"
            )
        if cap is not None and cap < 0:
            raise ValueError(f"video endpoint {key!r} declares negative video_max_reference_images: {cap}")
    elif has_int or has_fn:
        raise ValueError(
            f"non-video endpoint {key!r} must not declare video caps, got "
            f"video_max_reference_images={cap!r}, "
            f"describe_video={caps_fn!r}"
        )


_BASE_URL_PLACEHOLDER = re.compile(r"\{\{\s*base_url\s*\}\}")


def declarative_requires_base_url(definition: Mapping[str, Any]) -> bool:
    """这份定义是否要求供应商填了 base_url。

    三节请求 URL 逐个查，不只看提交：提交写死绝对地址、轮询才引用 base_url 的定义是合法的，
    只查提交会让这类端点跑到付费提交成功之后，才在轮询渲染出一个没有协议的相对地址上失败。

    装配闭包与调用方（如测试连接路由的入参校验）共用这一条判定：装配层抛的是不带 i18n key
    的 ValueError，面向用户的那条路要在装配前按本判定给出可翻译的拒绝。
    """
    return any(_BASE_URL_PLACEHOLDER.search(url) for url in request_urls(definition))


def declarative_requires_api_key(definition: Mapping[str, Any]) -> bool:
    """这份定义是否要求调用方提供 api_key。

    校验器保证非空 ``auth`` 至少引用一次 api_key 占位符、且 api_key 只允许出现在 auth 节，
    故判 auth 节是否为空即可。与 :func:`declarative_requires_base_url` 同为凭证必需性的判定缝：
    两者皆否（auth 为空且三节 URL 全写死绝对地址）的定义不需要任何凭证即可调用。
    """
    return bool(definition.get("auth"))


def _require_declarative_base_url(definition: Mapping[str, Any], provider: ProviderConnection) -> None:
    if declarative_requires_base_url(definition) and not provider.base_url:
        raise ValueError("声明式调用端点需要 base_url")


def _build_declarative_video(
    definition: Mapping[str, Any],
) -> BackendBuilder:
    """声明式视频端点的 backend 构造闭包。"""

    def build(
        provider: ProviderConnection, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
    ) -> CustomVideoBackend:
        _require_declarative_base_url(definition, provider)
        delegate = DeclarativeVideoBackend(
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=model_id,
            definition=definition,
            provider=provider.provider_id,
            path_overrides=path_overrides,
        )
        return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)

    return build


def _build_declarative_image(
    definition: Mapping[str, Any],
) -> BackendBuilder:
    """声明式图片端点的 backend 构造闭包。"""

    def build(
        provider: ProviderConnection, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
    ) -> CustomImageBackend:
        _require_declarative_base_url(definition, provider)
        delegate = DeclarativeImageBackend(
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=model_id,
            definition=definition,
            provider=provider.provider_id,
            path_overrides=path_overrides,
        )
        return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)

    return build


_DECLARATIVE_REQUESTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "submit": ("cp_request_submit", ("model",)),
    "poll": ("cp_request_poll", ("model", "task_id")),
    "result": ("cp_request_result", ("model", "result_id", "task_id")),
}
_BASE_URL_PREFIX = re.compile(r"^\s*\{\{\s*base_url\s*\}\}")


def declarative_requests(definition: Mapping[str, Any]) -> tuple[EndpointRequest, ...]:
    """声明式定义里可覆盖路径的请求：URL 以 ``{{ base_url }}`` 开头的那几节。

    写死绝对地址的节没有可替换的路径，不提供请求名（``docs/adr/0101``）；从响应里取得的产物
    地址不是定义里的请求节，本就不在其列。默认路径里已用到的占位符一并放行。
    """
    rule = "strip_version" if strips_base_url_version(definition) else "as_is"
    requests: list[EndpointRequest] = []
    for section in REQUEST_SECTIONS:
        node = definition.get(section)
        if not isinstance(node, Mapping):
            continue
        url = str(node.get("url", ""))
        prefix = _BASE_URL_PREFIX.match(url)
        if prefix is None:
            continue
        default_path = url[prefix.end() :]
        display_name_key, placeholders = _DECLARATIVE_REQUESTS[section]
        requests.append(
            EndpointRequest(
                name=section,
                display_name_key=display_name_key,
                default_path=default_path,
                placeholders=tuple(sorted({*placeholders, *placeholder_names(default_path)})),
                base_url_rule=rule,
            )
        )
    return tuple(requests)


def declarative_endpoint_spec(
    key: str,
    definition: Mapping[str, Any],
    *,
    source: Literal["builtin", "custom"] = "builtin",
) -> EndpointSpec:
    """把一份声明式定义派生成 EndpointSpec。纯函数：不读库、不发请求。

    随版定义（``builtin_endpoints/*.json``）与用户定义（``custom_endpoint`` 表）走同一条投影：
    定义的表达力一样，两份实现只会在能力缺省这类地方悄悄分叉。差别只在 ``source`` 决定的家族归属
    ——随版端点的家族取键首段（协议出处），用户端点的协议由定义自身描述、没有可归属的外部家族。

    媒体类型经 :func:`definition_media_type` 读，与市场索引、镜像列同一读法，并决定 backend 走
    图片还是视频通道。

    能力由定义显式全量声明，与 model 无关。视频走 video_caps_for_model 这条「四字段全量声明」的
    通路（返回同一份常量），而不是只能表达参考图上限的 video_max_reference_images；图片的能力
    与 backend 共读 :func:`image_capabilities_from_definition`，生成前的闸门与这里投影的不会分叉。
    """
    media_type = definition_media_type(definition)
    is_video = media_type == "video"
    description = decode_definition(definition) if media_type in ("image", "video") else None
    spec = EndpointSpec(
        key=key,
        media_type=media_type,
        family=CUSTOM_ENDPOINT_FAMILY if source == "custom" else declarative_family(key),
        display_name_key="",
        source=source,
        request_method=definition["submit"]["method"],
        request_path_template=declarative_request_path(definition),
        build_backend=_build_declarative_video(definition) if is_video else _build_declarative_image(definition),
        image_capabilities=None if is_video else image_capabilities_from_definition(definition),
        describe_image=description if media_type == "image" else None,
        describe_video=(lambda _model_id, _entry: description) if is_video and description is not None else None,
        definition=definition,
        requests=declarative_requests(definition),
    )
    validate_video_caps_declaration(spec)
    return spec


def _build_comfyui_runtime(
    definition: Mapping[str, Any],
) -> BackendBuilder:
    """ComfyUI 端点的 backend 构造闭包，按定义声明的 ``media_type`` 分叉。

    两种 kind 的同一件事：一份 workflow 产图还是产视频只有定义自己知道（键前缀推不出来），而两条
    通道各有自己的 backend 协议与包装类。媒体类型不是这两种时抛带失败码的 ``ComfyuiError``——
    schema 只放行这两个值，走到这里意味着手工改过库；worker 据此把任务落成结构化失败，文案留到
    读侧按语言渲染，落一段裸文本的话非中文用户在任务列表里看到的是一句中文。借
    ``provider_unsupported_media`` 而不另立新码：它说的正是「这个供应商给不出这一类生成」，读侧的
    ``CONFIGURE_PROVIDER`` 指向也对。

    不抛 ``ValueError``：那是本层「端点不认识」的既有含义，沿途多处 ``except ValueError`` 会把它
    降级成「端点已不在」，而实情是端点合法、只是媒体类型不是本层认得的那两个。

    图像那一路的包装保持纯转发（``CustomImageBackend`` 本就只覆写 name / model）：能力注入是视频
    专属的形态（系统判定 ⊕ 用户覆盖），而 ComfyUI 协议的 ``capability_overrides`` 整节关闭
    （``docs/adr/0081``），图像能力只从绑定表推导，没有可叠加的第二个来源。
    """

    def build(
        provider: ProviderConnection, model_id: str, _path_overrides: PathOverrides = NO_PATH_OVERRIDES
    ) -> CustomImageBackend | CustomVideoBackend:
        media_type = str(definition.get("media_type"))
        if media_type not in ("image", "video"):
            raise ComfyuiError("provider_unsupported_media", provider_id=provider.provider_id, media_type=media_type)
        if not provider.base_url:
            raise ValueError("ComfyUI 调用端点需要 base_url")
        if media_type == "image":
            image_delegate = ComfyuiImageBackend(
                provider_id=provider.provider_id,
                model=model_id,
                base_url=provider.base_url,
                api_key=provider.api_key,
                definition=definition,
            )
            return CustomImageBackend(provider_id=provider.provider_id, delegate=image_delegate, model=model_id)
        video_delegate = ComfyuiVideoBackend(
            provider_id=provider.provider_id,
            model=model_id,
            base_url=provider.base_url,
            api_key=provider.api_key,
            definition=definition,
        )
        return CustomVideoBackend(provider_id=provider.provider_id, delegate=video_delegate, model=model_id)

    return build


def comfyui_endpoint_spec(key: str, definition: Mapping[str, Any]) -> EndpointSpec:
    """把一份 ComfyUI 定义派生成 EndpointSpec。纯函数：不读库、不发请求。

    媒体类型经 :func:`definition_media_type` 读定义自身声明的 ``media_type``——一份 workflow 产图
    还是产视频只有它自己知道。能力全部从节点绑定推导（``docs/adr/0082``），不看模型名也不读定义
    里的能力声明（定义里没有那一节）：能力对每个 model 是同一份，因为 workflow 只有一份，模型行
    换名字不改它能做什么。

    实现落在本模块而非 ``comfyui`` 子包：子包受「不依赖声明式运行时」的 import 契约约束，而
    ``EndpointSpec`` 与它的不变式都在这里，子包够到本模块即间接够到声明式 backend。推导本身在
    子包的 ``comfyui.capabilities`` 里——它只依赖绑定表与 workflow，与 ``EndpointSpec`` 无关；
    两种媒体类型的装箱各借对应 backend 模块那一份，backend 自己的能力声明也用它。
    """
    media_type = definition_media_type(definition)
    is_video = media_type == "video"
    description = decode_definition(definition) if media_type in ("image", "video") else None
    spec = EndpointSpec(
        key=key,
        media_type=media_type,
        family=CUSTOM_ENDPOINT_FAMILY,
        display_name_key="",
        source="custom",
        request_method="POST",
        request_path_template="/prompt",
        build_backend=_build_comfyui_runtime(definition),
        image_capabilities=None if is_video else binding_image_capabilities(definition),
        describe_image=description if media_type == "image" else None,
        describe_video=(lambda _model_id, _entry: description) if is_video and description is not None else None,
        definition=definition,
    )
    validate_video_caps_declaration(spec)
    return spec


def merge_builtin_definitions(registry: dict[str, EndpointSpec], directory: Path | None = None) -> None:
    """把随版声明式定义并入注册表。键与 Python 内置同一命名空间，重复即拒。

    定义不合法、目录缺失、键冲突在 import 期一律抛错——装载失败让进程起不来，好过带着一个装不出
    backend 的 endpoint 跑到用户发起生成那一刻。
    """
    definitions = builtin_definitions() if directory is None else load_builtin_definitions(directory)
    for key, definition in definitions.items():
        if key in registry:
            raise BuiltinDefinitionError(f"随版定义 {key}.json 的内置键与已有 endpoint 重复")
        registry[key] = declarative_endpoint_spec(key, definition)


merge_builtin_definitions(ENDPOINT_REGISTRY)


ENDPOINT_KEYS_BY_MEDIA_TYPE: dict[str, tuple[str, ...]] = {
    media_type: tuple(k for k, s in ENDPOINT_REGISTRY.items() if s.media_type == media_type)
    for media_type in {s.media_type for s in ENDPOINT_REGISTRY.values()}
}


def _validate_registry() -> None:
    """import 期校验内置注册表的不变式。"""
    for key, spec in ENDPOINT_REGISTRY.items():
        if is_custom_endpoint(key):
            raise ValueError(f"builtin endpoint key {key!r} must not use the custom endpoint prefix")
        validate_video_caps_declaration(spec)
        if spec.media_type == "image":
            spec.image_description()


_validate_registry()


def get_endpoint_spec(endpoint: str) -> EndpointSpec:
    spec = ENDPOINT_REGISTRY.get(endpoint)
    if spec is None:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    return spec


def endpoint_to_media_type(endpoint: str) -> str:
    return get_endpoint_spec(endpoint).media_type


def static_media_type(endpoint: str) -> str:
    """不读库判定内置端点的媒体类型；``ce-`` 键在此拒绝。

    自定义端点的媒体类型由它那份定义决定（按 ``kind`` 各有取法），键前缀不蕴含媒体类型。模型行
    的 endpoint 列两种键都可能是，按端点分媒体类型的地方一律先
    ``endpoint_resolution.resolve_endpoint_spec`` 取 spec、再读 ``EndpointSpec.media_type``。

    Raises:
        ValueError: 传入 ``ce-`` 键（须读定义），或内置注册表里没有该键。
    """
    from lib.custom_provider import is_custom_endpoint

    if is_custom_endpoint(endpoint):
        raise ValueError(f"custom endpoint media type comes from its definition: {endpoint!r}")
    return endpoint_to_media_type(endpoint)


def endpoint_to_image_capabilities(endpoint: str) -> frozenset[ImageCapability]:
    """返回 image 类 endpoint 的 capability 集合。非 image 类抛 ValueError。"""
    spec = get_endpoint_spec(endpoint)
    if spec.image_capabilities is None:
        raise ValueError(f"endpoint {endpoint!r} is not an image endpoint")
    return spec.image_capabilities


def endpoint_reference_image_limit(spec: EndpointSpec) -> int | None:
    """图片端点静态声明的参考图上限（通道事实），与端点单一能力描述同源。"""
    if spec.media_type != "image":
        return None
    description = spec.image_description()
    limit = description.limits.get("max_reference_images")
    if limit is None:
        limit = description.defaults.get("max_reference_images")
    return int(limit) if isinstance(limit, int) and limit > 0 else None


def endpoint_accepts_explicit_quality(spec: EndpointSpec) -> bool:
    """图片端点是否接受显式画质（通道事实），与端点单一能力描述同源。"""
    return spec.media_type == "image" and "explicit_quality" in spec.image_description().slots


def list_endpoints_by_media_type(media_type: str) -> list[EndpointSpec]:
    return [ENDPOINT_REGISTRY[k] for k in ENDPOINT_KEYS_BY_MEDIA_TYPE.get(media_type, ())]


_ENDPOINT_SPEC_EXCLUDED_FIELDS = frozenset(
    {"build_backend", "describe_image", "describe_video", "metering_backend", "definition"}
)


def endpoint_spec_to_dict(spec: EndpointSpec) -> dict:
    """把 EndpointSpec 转成可序列化的纯数据 dict（剥掉不可 JSON 化的 build_backend 闭包与内部描述）。"""
    from dataclasses import fields

    data = {
        f.name: asdict(value) if is_dataclass(value) and not isinstance(value, type) else value
        for f in fields(spec)
        if f.name not in _ENDPOINT_SPEC_EXCLUDED_FIELDS
        for value in (getattr(spec, f.name),)
    }
    data["requests"] = tuple(asdict(request) for request in spec.requests)
    data["end_image_capable"] = spec.end_image_capable
    data["reference_audio_capable"] = spec.reference_audio_capable
    data["kind"] = spec.kind
    data["display_name"] = spec.display_name
    data["size_fixed"] = spec.size_fixed
    data["duration_fixed"] = spec.duration_fixed
    data["duration_frame_rate_missing"] = spec.duration_frame_rate_missing
    data["duration_tier_empty"] = spec.duration_tier_empty
    data["native_resolution"] = spec.native_resolution
    if spec.image_capabilities is not None:
        data["image_capabilities"] = sorted(c.value for c in spec.image_capabilities)
    else:
        data["image_capabilities"] = None
    return data
