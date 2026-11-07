"""ENDPOINT_REGISTRY: sole authority for custom-provider endpoints.

Each EndpointSpec binds media_type, family, HTTP request shapes, and build_backend closures.
Implementations are Python backends or bundled declarative definitions (``builtin_endpoints/*.json``, loaded at import
by builtin_definitions, distinguished through EndpointSpec.definition), sharing one key domain.
factory.create_custom_backend dispatches by endpoint strings;
server.routers.custom_providers exposes GET /custom-providers/endpoints,
fully deriving frontend selectors/path displays from this authority.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlsplit

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, VIDEO_TIER_SHORT_EDGE, short_edge_to_resolution
from arcreel_market_core.endpoint_definition.kinds import COMFYUI_KIND
from arcreel_market_core.endpoint_definition.media_type import definition_media_type
from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.audio_backends.dashscope_speech_synthesizer import (
    SPEECH_SYNTHESIZER_PATH,
    DashScopeSpeechSynthesizerBackend,
)
from lib.backends.audio_backends.gemini import GeminiAudioBackend
from lib.backends.audio_backends.minimax import MiniMaxAudioBackend
from lib.backends.audio_backends.openai import OpenAIAudioBackend
from lib.backends.dashscope_shared import dashscope_native_base_url
from lib.backends.image_backends.ark import SEEDREAM_BACKENDS
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_backends.dashscope import DASHSCOPE_IMAGE_BACKENDS
from lib.backends.image_backends.gemini import GeminiImageBackend
from lib.backends.image_backends.kling import KLING_IMAGE_BACKENDS
from lib.backends.image_backends.minimax import MiniMaxImageBackend
from lib.backends.image_backends.openai import OpenAIImageBackend
from lib.backends.image_backends.openai_chat import OpenAIChatImageBackend
from lib.backends.image_backends.openrouter import IMAGES_PATH as OPENROUTER_IMAGES_PATH
from lib.backends.image_backends.openrouter import MODELS_PATH as OPENROUTER_MODELS_PATH
from lib.backends.image_backends.openrouter import OpenRouterImageBackend
from lib.backends.image_backends.registry import reference_limit_of
from lib.backends.kling_shared import kling_model_path_base_url
from lib.backends.minimax_shared import minimax_text_base_url
from lib.backends.provider_assets import InferenceCredentialRef
from lib.backends.text_backends.anthropic import AnthropicTextBackend
from lib.backends.text_backends.gemini import GeminiTextBackend
from lib.backends.text_backends.openai import OpenAITextBackend
from lib.backends.text_backends.openai_responses import OpenAIResponsesTextBackend
from lib.backends.video_backends.ark import SEEDANCE_BACKENDS
from lib.backends.video_backends.dashscope import DASHSCOPE_VIDEO_BACKENDS, classify_wan_model
from lib.backends.video_backends.gemini import GeminiVideoBackend
from lib.backends.video_backends.gemini_interactions import GeminiInteractionsVideoBackend
from lib.backends.video_backends.kling import KLING_BACKENDS
from lib.backends.video_backends.kling_model_path import KlingModelPathVideoBackend
from lib.backends.video_backends.openai import OpenAIVideoBackend
from lib.backends.video_backends.vidu import VIDU_BACKENDS
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
    declarative_display_name,
    declarative_family,
    declarative_request_path,
    declarative_video_capabilities,
    load_builtin_definitions,
)
from lib.custom_provider.comfyui.capabilities import (
    default_supported_durations,
    duration_is_fixed,
    frame_rate_is_missing,
    native_short_edge,
    size_is_fixed,
)
from lib.custom_provider.comfyui.comfyui_backend import ComfyuiVideoBackend, binding_video_capabilities
from lib.custom_provider.comfyui.comfyui_image_backend import (
    ComfyuiImageBackend,
    binding_image_capabilities,
    binding_reference_limit,
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
    definition_reference_limit,
    image_capabilities_from_definition,
)
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
    from lib.db.models.custom_provider import CustomProvider


#: Catalog family for user endpoints. Built-in family identifies protocol provenance, openai/kling/etc.;
#: user protocols are self-described by definitions without external family ownership.
CUSTOM_ENDPOINT_FAMILY = "custom"


# EndpointSpec types.


class BackendBuilder(Protocol):
    """Endpoint backend-construction closures.

    ``path_overrides`` contains effective model overrides (``docs/adr/0101``), with keys declared in
    :attr:`EndpointSpec.requests`; endpoints without requests always receive empty mappings.
    """

    def __call__(
        self, provider: CustomProvider, model_id: str, path_overrides: PathOverrides = ..., /
    ) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend: ...


@dataclass(frozen=True)
class EndpointSpec:
    """Endpoint metadata + backend-construction closures."""

    key: str  # "openai-chat"
    media_type: str  # "text" | "image" | "video" | "audio"
    family: str  # "openai" | "google" | "newapi"
    # Frontend dashboard i18n keys. Bundled/user declarative names live in definition meta.name,
    # outside locale directories; empty keys use display_name.
    display_name_key: str
    request_method: str  # "POST"
    request_path_template: str  # "/v1/chat/completions", potentially containing {model} placeholders.
    build_backend: BackendBuilder
    # Sources: built-in bundled/uneditable/undeletable, or user-defined custom_endpoint rows.
    source: Literal["builtin", "custom"] = "builtin"
    image_capabilities: frozenset[ImageCapability] | None = None  # Image endpoints only, omitted otherwise.
    # Reference-to-video maxima per call, meaningful for videos only.
    # Explicit int is hard limits; 0 rejects references, executors trim to 0.
    # None means undeclared when one endpoint serves differing models; resolver
    # uses video_caps_for_model by model_id for actual maxima.
    video_max_reference_images: int | None = None
    # With video_max_reference_images=None, resolver calls this pure model_id function for backend
    # capabilities without SDK clients/provider rows. Second arguments are corresponding entries, bindings first/current names otherwise;
    # backends use entry names for channel facts (docs/adr/0104,0105). Integer video_max_reference_images requires this
    # field None because endpoint hard caps suffice. Exactly one per video endpoint, guarded by final invariants.
    video_caps_for_model: Callable[[str, CatalogEntry | None], VideoCapabilities] | None = None
    # Whether delegate.generate() actually reads VideoGenerationRequest.end_image and forwards
    # last-frame constraints. Video only; False silently loses frames despite system/user last_frame=True,
    # so write-side overrides permit last frames only on genuinely capable endpoints.
    end_image_capable: bool = False
    # Like end_image_capable: whether delegate.generate() actually reads
    # VideoGenerationRequest.reference_audio_files into provider requests, meaningful for videos only.
    # False makes direct overrides misleading while execution still omits voice input.
    reference_audio_capable: bool = False
    # Whole declarative JSON: bundled builtin_endpoints/ or user custom_endpoint tables.
    # None for Python implementations. Non-None definitions describe calls; all other metadata
    # mirrors derive from this sole authority.
    definition: Mapping[str, Any] | None = None
    # Overridable requests (docs/adr/0101), restricting override names to this list. Empty tuples mean no
    # path overrides, for ComfyUI/undeclared Python requests.
    requests: tuple[EndpointRequest, ...] = ()
    # Wrapped built-in classes, Python image/video only: pricing reads static metering declarations, video
    # ``metered_usage``, image ``estimate_usage`` (:mod:`lib.custom_provider.metering`). Must match
    # delegates constructed by ``build_backend``, guarded by registry tests. None means definition/generic metering.
    metering_backend: type | None = None

    @property
    def kind(self) -> str:
        """Implementation form: definition ``kind`` if present, ``python`` for Python endpoints.

        ``python`` is not a definition-container kind: it names absence of definitions.
        """
        return "python" if self.definition is None else str(self.definition["kind"])

    @property
    def duration_tier_optional(self) -> bool:
        """Whether endpoint ``supported_durations`` may be empty.

        ComfyUI duration may be wholly undriven (``docs/adr/0082``): unbound ``frames``/missing fps leaves native
        lengths, with empty tiers truthful rather than missing. Other endpoints follow ADR 0018:
        empty means configuration defects, failing loudly during resolution with settings guidance.

        Predicates live on specs, not consumer ``kind`` checks: ``lib.config`` layering cannot
        import ``kind`` constants, but resolved-spec properties require none.
        """
        return self.kind == COMFYUI_KIND

    @property
    def size_fixed(self) -> bool:
        """Whether endpoint-fixed size ignores ratio/resolution choices.

        True only for ComfyUI without both size bindings: disable/explain resolution controls,
        never accept choices then discard during writes. Other endpoints always use request dimensions,
        without fixed-size forms.
        """
        return self.definition is not None and self.kind == COMFYUI_KIND and size_is_fixed(self.definition["bindings"])

    @property
    def duration_fixed(self) -> bool:
        """Whether duration is endpoint-fixed, true for ComfyUI without ``frames`` bindings.

        Unlike :attr:`duration_tier_optional` permitting empty tiers, this addresses user editability.
        Bound ``frames`` without fps also gives empty tiers, but definitions are repairable with
        ``fps`` bindings/manual rates, not inherently fixed workflow durations.
        """
        return (
            self.definition is not None and self.kind == COMFYUI_KIND and duration_is_fixed(self.definition["bindings"])
        )

    @property
    def duration_frame_rate_missing(self) -> bool:
        """Whether empty tiers lack fps: bound ``frames`` without ``fps`` or manual rates.

        Like :attr:`duration_fixed`, selects wording only, never readonly/disabled rules, which use
        :attr:`duration_tier_empty`. Both false with empty tiers means third cases: bound frames/readable fps
        cannot round-trip integer seconds, neither inherently fixed nor missing fps.
        """
        return self.definition is not None and self.kind == COMFYUI_KIND and frame_rate_is_missing(self.definition)

    @property
    def endpoint_durations(self) -> list[int] | None:
        """Endpoint-owned tiers, ``None`` when endpoints do not determine them.

        ComfyUI videos alone derive bindings-based tiers identical across attached model rows;
        other endpoints leave tiers to model rows/registries.

        Empty lists differ from ``None``: explicit answers mean unable to drive dimensions,
        ``None`` means endpoints should not answer that question.
        """
        if self.definition is None or self.kind != COMFYUI_KIND or self.media_type != "video":
            return None
        return default_supported_durations(self.definition)

    @property
    def duration_tier_empty(self) -> bool:
        """Whether no duration tiers exist: true for uninferable ComfyUI native durations.

        Three cases: unbound ``frames`` without literals, bound ``frames`` without readonly ``fps``/manual fps,
        or readable fps without integer-second tiers. UI readonly/disabled predicates use this,
        never :attr:`duration_fixed`, because empty tiers prevent edits. Wording instead uses
        :attr:`duration_fixed` / :attr:`duration_frame_rate_missing` to distinguish inherently fixed,
        missing fps, or present frames/fps without integer-second tiers.
        """
        return self.endpoint_durations == []

    @property
    def native_resolution(self) -> str | None:
        """Actual workflow resolution without selected tiers; None for non-ComfyUI/unreadable literal dimensions."""
        if self.definition is None or self.kind != COMFYUI_KIND:
            return None
        short_edge = native_short_edge(self.definition)
        if short_edge is None:
            return None
        tier_map = IMAGE_TIER_SHORT_EDGE if self.media_type == "image" else VIDEO_TIER_SHORT_EDGE
        return short_edge_to_resolution(short_edge, tier_map=tier_map)

    @property
    def display_name(self) -> str | None:
        """Declarative display ``meta.name``; None for Python built-ins, using display_name_key wording."""
        return None if self.definition is None else declarative_display_name(self.definition)


# Endpoint build_backend closures.


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
    # SDK appends /v1/messages; strip only user-copied endpoint suffixes, without adding/removing /vN.
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
    """Unified OpenRouter /images endpoint, T2I + I2I.

    No /images/edits; both use one JSON endpoint. base_url already includes /api/v1,
    so never append /v1 through ensure_openai_base_url.
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
    # provider_name attributes delegate logs/AudioSynthesisResult.provider to actual providers,
    # matching wrapper .name accounting, never built-in openai.
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
    # Backend derives /api/v1 from host, tolerating suffixes; forward raw base_url without renormalization.
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
    # Backend normalizes {host}/v1, tolerating host-only/version suffixes; forward raw base_url.
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
    # Custom providers use AI Studio only, Vertex credentials bypass base_url; SDK appends api_version.
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
    """Python request declarations: name/display-key/default-path tuples; permit model names and placeholders already in default paths."""
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


# Requests emitted by raw httpx/Ark SDK; response download URLs expose no request names (docs/adr/0101).
_ARK_SEEDANCE_REQUESTS = _python_requests(
    "ensure_api_v3",
    ("submit", "cp_request_submit", "/contents/generations/tasks"),
    ("poll", "cp_request_poll", "/contents/generations/tasks/{{ task_id }}"),
)
# Ark Seedream text/multi-reference share JSON endpoints with references in image, without /images/edits.
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
# New Kling model paths omit /v1; task ids enter query task_ids, preserved when path overrides lack queries.
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
# Per-generation catalog queries read model parameter declarations under shared base_url; relays override both paths together.
# Unlike provider-level discovery excluded from overrides in docs/adr/0101, these are overridable too.
_OPENROUTER_IMAGES_REQUESTS = _python_requests(
    "as_is",
    ("generate", "cp_request_generate", OPENROUTER_IMAGES_PATH),
    ("model_catalog", "cp_request_model_catalog", OPENROUTER_MODELS_PATH),
)
_MINIMAX_TTS_REQUESTS = _python_requests("ensure_v1", ("generate", "cp_request_generate", "/t2a_v2"))
# SDK appends /v1beta after stripping /vN from base_url.
_GEMINI_GENERATE_REQUESTS = _python_requests(
    "strip_version", ("generate", "cp_request_generate", "/v1beta/models/{{ model }}:generateContent")
)
_GEMINI_VIDEO_REQUESTS = _python_requests(
    "strip_version",
    ("submit", "cp_request_submit", "/v1beta/models/{{ model }}:predictLongRunning"),
    # Operation names models/<model>/operations/<id> include /, expanded as multisegment paths.
    ("poll", "cp_request_poll", "/v1beta/{{ task_id }}"),
    # SDK extracts artifact file ids from uri, composing downloads from base_url, so downloads are relative too.
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
    # Placeholders never cross ``:``: capture slash-containing operation names whole, retaining SDK suffix distinctions :predictLongRunning/:download.
    return path_override_layer(
        requests, path_overrides, base_url, ensure_google_base_url(base_url), model_id, placeholder="[^:]+"
    )


# OpenAI SDK defaults are relative to ensure_openai_base_url-added /v1.
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
# Anthropic SDK appends /v1/messages; strip trailing slashes/copied /v1/messages only.
_ANTHROPIC_MESSAGES_REQUESTS = _python_requests(
    "strip_messages", ("generate", "cp_request_generate", ANTHROPIC_MESSAGES_PATH)
)


def _ensure_url_path_suffix(base_url: str | None, suffix: str) -> str | None:
    """Append known protocol mounts (ark /api/v3, vidu /ent/v2, kling /v1),
    trusting existing matching paths without duplication; shared by ark/vidu/kling closures.

    Schemaless hosts such as ``relay.example.com`` parse entirely as paths;
    add ``https://`` first or host-only configurations receive neither schemes nor mounts.
    """
    s = (base_url or "").strip().rstrip("/")
    if not s:
        return None
    normalized = s if "://" in s else f"https://{s}"
    if urlsplit(normalized).path.rstrip("/").endswith(suffix):
        return normalized
    return normalized + suffix


def _custom_inference_credential(provider) -> InferenceCredentialRef | None:
    """Custom-provider inference-credential references; unsaved providers without record IDs lack bindable records.

    Asset bindings use records/original configured Base URLs (``lib.asset_library.bindings``),
    never request addresses after adding ``/api/v3``.
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
    """Shared Seedance construction: ``endpoint`` selects generation-specific request/metering channels."""
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
    # Wrap ArkImageBackend directly, sharing size/reference-limit/encoding/metering with built-in Ark.
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
    # Backend derives /api/v1 from host, tolerating suffixes; forward raw base_url without renormalization.
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
    # Backend normalizes {host}/v1, tolerating host/version suffixes; forward raw base_url.
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


def _build_kling_image(
    provider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES, *, endpoint: str
) -> CustomImageBackend:
    # Native Kling image relays use bearer/static api_key, bypassing JWT, targeting native async images.
    # Host-only bases receive /v1 mounts, trusting explicit paths; forward native model_name without alias decoupling.
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
    # Native Kling video relays use bearer/static api_key, bypassing JWT, targeting native async video.
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
    # New APIs accept API Key only; strip trailing versions often copied from old /v1 addresses.
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


# ENDPOINT_REGISTRY.


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
        # References dispatch /generations versus /edits; braces express both paths.
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
        # Same API as openai-chat, separate keys by media, excluded from infer_endpoint automatic inference.
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
        # Unified OpenRouter T2I/I2I /images, without /images/edits.
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
        # OpenAI Sora input_reference accepts one first frame.
        video_max_reference_images=1,
    ),
    # Seedance 1/2/2.5 share creation/polling, differing in bodies/metering, hence separate endpoints (ark.SeedanceEndpoint).
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
            video_caps_for_model=backend_class.video_capabilities_for_model,
            end_image_capable=True,
            # _create_task converts every reference_audio_files clip into audio_url + role:reference_audio.
            reference_audio_capable=True,
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
            # Text/reference generation share /images/generations, Ark references in JSON image.
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
            video_caps_for_model=backend.video_capabilities_for_model,
            end_image_capable=True,
            # Only reference endpoints writing reference_audio_files into sounds, Q4, send audio; read the same backend
            # segment declarations without separate endpoint lists.
            reference_audio_capable=backend.endpoint.max_reference_audio > 0,
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
    # qwen-audio-*-tts SpeechSynthesizer differs from multimodal in bodies/voices/usage.
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
        # Read model reference/audio forms through pure backend capability functions, without clients.
        video_caps_for_model=GeminiVideoBackend.video_capabilities_for_model,
        # generate() forwards end_image as config.last_frame.
        end_image_capable=True,
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
        video_caps_for_model=GeminiInteractionsVideoBackend.video_capabilities_for_model,
        end_image_capable=True,
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
            video_caps_for_model=backend.video_capabilities_for_model,
            end_image_capable=True,
            reference_audio_capable=True,
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
            video_caps_for_model=backend.video_capabilities_for_model,
            end_image_capable=True,
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
        video_caps_for_model=KlingModelPathVideoBackend.video_capabilities_for_model,
        end_image_capable=True,
    ),
}


def validate_video_caps_declaration(spec: EndpointSpec) -> None:
    """Validate reference-limit sources: declared caps_fn must be callable; video endpoints require exactly one of integer cap
    XOR non-None caps_fn, with nonnegative caps. Nonvideo endpoints require both None. Misconfiguration, including
    noncallables, shared-model endpoints missing caps_fn, both declarations, or negative caps, fails
    at construction rather than request-time resolvers.

    Built-in registries validate each spec at import; declarative_endpoint_spec validates immediately after construction.
    Shared invariants keep capability layers independent of spec provenance.
    """
    key = spec.key
    cap = spec.video_max_reference_images
    caps_fn = spec.video_caps_for_model
    has_int = cap is not None
    # Resolver calls caps_fn(model_id), requiring callable. Nonempty strings/integers/noncallables
    # fail at import, never delayed resolver requests, matching fail-fast intent.
    if caps_fn is not None and not callable(caps_fn):
        raise ValueError(f"endpoint {key!r} declares non-callable video_caps_for_model: {caps_fn!r}")
    has_fn = callable(caps_fn)
    if spec.media_type != "video" and spec.reference_audio_capable:
        raise ValueError(f"non-video endpoint {key!r} must not declare reference_audio_capable")
    if spec.media_type == "video":
        if has_int == has_fn:
            raise ValueError(
                f"video endpoint {key!r} must declare exactly one of video_max_reference_images "
                f"(int) or video_caps_for_model (callable), got "
                f"video_max_reference_images={cap!r}, "
                f"video_caps_for_model={caps_fn!r}"
            )
        if cap is not None and cap < 0:
            # Negative integer caps would produce references[:-1], dropping last images rather than
            # trimming to 0; reject at construction so resolver integers are always nonnegative.
            raise ValueError(f"video endpoint {key!r} declares negative video_max_reference_images: {cap}")
    elif has_int or has_fn:
        raise ValueError(
            f"non-video endpoint {key!r} must not declare video caps, got "
            f"video_max_reference_images={cap!r}, "
            f"video_caps_for_model={caps_fn!r}"
        )


#: Recognize placeholders only; literal URLs containing "base_url", such as https://api.test/base_url/video,
#: do not require configured provider addresses.
_BASE_URL_PLACEHOLDER = re.compile(r"\{\{\s*base_url\s*\}\}")


def declarative_requires_base_url(definition: Mapping[str, Any]) -> bool:
    """Whether definitions require configured base_url.

    Inspect all three URLs, not submission only: hardcoded submissions/base_url polling are valid,
    and submission-only checks would fail on schemaless relative polling URLs after successful paid creation.

    Assembly/callers such as connection-route validation share this predicate: assembly raises ValueError without i18n keys,
    so user-facing flows must reject locally with translatable guidance beforehand.
    """
    return any(_BASE_URL_PLACEHOLDER.search(url) for url in request_urls(definition))


def declarative_requires_api_key(definition: Mapping[str, Any]) -> bool:
    """Whether callers must supply api_key.

    Validators guarantee nonempty ``auth`` references api_key at least once, exclusively there,
    so test auth emptiness. Like :func:`declarative_requires_base_url`, this is credential-necessity authority:
    when both false, empty auth/all-hardcoded URLs permit calls without credentials.
    """
    return bool(definition.get("auth"))


def _require_declarative_base_url(definition: Mapping[str, Any], provider: CustomProvider) -> None:
    if declarative_requires_base_url(definition) and not provider.base_url:
        raise ValueError("声明式调用端点需要 base_url")


def _build_declarative_video(
    definition: Mapping[str, Any],
) -> BackendBuilder:
    """Declarative video backend closures."""

    def build(
        provider: CustomProvider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
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
    """Declarative image backend closures."""

    def build(
        provider: CustomProvider, model_id: str, path_overrides: PathOverrides = NO_PATH_OVERRIDES
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


#: Declarative section display i18n keys/allowed override placeholders, limited to values already
#: available: submission models, polling task ids, secondary retrieval result ids.
_DECLARATIVE_REQUESTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "submit": ("cp_request_submit", ("model",)),
    "poll": ("cp_request_poll", ("model", "task_id")),
    "result": ("cp_request_result", ("model", "result_id", "task_id")),
}
_BASE_URL_PREFIX = re.compile(r"^\s*\{\{\s*base_url\s*\}\}")


def declarative_requests(definition: Mapping[str, Any]) -> tuple[EndpointRequest, ...]:
    """Overridable declarative sections whose URLs begin ``{{ base_url }}``.

    Hardcoded absolute sections expose no replaceable paths/request names (``docs/adr/0101``); response artifact
    URLs are not definition request sections. Permit placeholders already used in default paths.
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
    """Pure definition → EndpointSpec projections, without DB/network.

    Bundled ``builtin_endpoints/*.json`` and user ``custom_endpoint`` definitions share projections:
    equal expressiveness, avoiding divergent capability defaults. Only ``source`` determines family membership:
    bundled keys' first segments identify protocol provenance; user definitions describe their own protocols without external families.

    Read media through :func:`definition_media_type`, shared with market indexes/mirrors, selecting
    image/video backends.

    Definitions explicitly declare full model-independent capabilities. Video uses complete four-field video_caps_for_model
    constant returns, not reference-only video_max_reference_images. Images share backend
    :func:`image_capabilities_from_definition`, keeping pre-generation gates/projections aligned.
    """
    media_type = definition_media_type(definition)
    is_video = media_type == "video"
    caps = declarative_video_capabilities(definition) if is_video else None
    spec = EndpointSpec(
        key=key,
        media_type=media_type,
        family=CUSTOM_ENDPOINT_FAMILY if source == "custom" else declarative_family(key),
        # Declarative display names use meta.name, outside i18n (EndpointSpec.display_name).
        display_name_key="",
        source=source,
        request_method=definition["submit"]["method"],
        request_path_template=declarative_request_path(definition),
        build_backend=_build_declarative_video(definition) if is_video else _build_declarative_image(definition),
        image_capabilities=None if is_video else image_capabilities_from_definition(definition),
        video_caps_for_model=(lambda _model_id, _entry=None: caps) if caps is not None else None,
        end_image_capable=caps.last_frame if caps is not None else False,
        reference_audio_capable=caps is not None and caps.reference_audio_mode is not ReferenceAudioMode.NONE,
        definition=definition,
        requests=declarative_requests(definition),
    )
    # Built-ins validate invariants at import (_validate_registry); on-demand user definitions lack
    # that phase, so validate at construction, keeping capability interpretation source-independent.
    validate_video_caps_declaration(spec)
    return spec


def _build_comfyui_runtime(
    definition: Mapping[str, Any],
) -> BackendBuilder:
    """ComfyUI construction by definition ``media_type``.

    Same issue across kinds: definitions alone know image/video workflows, never key prefixes. Each channel has backend/wrapper
    protocols. Unsupported media raises coded ``ComfyuiError``: schemas permit two values only,
    so this implies manual DB edits. Workers persist structured failures for localized reads,
    avoiding Chinese raw text in other-language task lists. Reuse
    ``provider_unsupported_media`` because providers cannot generate this media, with correct
    ``CONFIGURE_PROVIDER`` guidance.

    Never ``ValueError``, already meaning unknown endpoints here; ``except ValueError`` along paths would make it endpoint-absent;
    actual endpoints are valid, only media unsupported by this layer.

    Images remain pure forwarding through ``CustomImageBackend`` name/model overrides. Capability injection is video-specific
    system ⊕ user composition; ComfyUI disables entire ``capability_overrides``
    (``docs/adr/0081``), deriving images from bindings without secondary sources.
    """

    def build(
        provider: CustomProvider, model_id: str, _path_overrides: PathOverrides = NO_PATH_OVERRIDES
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
    """Pure ComfyUI definition → EndpointSpec, without DB/network.

    :func:`definition_media_type` reads definition ``media_type``, sole authority on image/video outputs.
    All capabilities derive from bindings (``docs/adr/0082``), without names or absent capability sections:
    one workflow gives every model identical capabilities, unchanged by model renaming.

    Projection lives here, not ``comfyui``, whose forbidden declarative-runtime imports cannot reach
    ``EndpointSpec``/invariants without indirectly reaching declarative backends. Derivation lives in
    ``comfyui.capabilities``, depending only on bindings/workflows, never ``EndpointSpec``.
    Media boxing reuses corresponding backend functions, shared by backend declarations.
    """
    media_type = definition_media_type(definition)
    is_video = media_type == "video"
    # Pregeneration gates read backend declarations, not projected caps; duplicate implementations would conflict,
    # so both media projections reuse backend boxing functions.
    caps = binding_video_capabilities(definition) if is_video else None
    spec = EndpointSpec(
        key=key,
        media_type=media_type,
        family=CUSTOM_ENDPOINT_FAMILY,
        # ComfyUI display meta.name like declarative endpoints, EndpointSpec.display_name.
        display_name_key="",
        source="custom",
        # Fixed submission: whole workflows POST /prompt, without model-varying paths/configurable methods.
        # Neither is a definition-selectable value, so declare in projections rather than read definitions.
        request_method="POST",
        request_path_template="/prompt",
        build_backend=_build_comfyui_runtime(definition),
        image_capabilities=None if is_video else binding_image_capabilities(definition),
        video_caps_for_model=(lambda _model_id, _entry=None: caps) if caps is not None else None,
        # Derive two transport flags from caps like declarative endpoints, without duplicate predicates.
        end_image_capable=caps.last_frame if caps is not None else False,
        definition=definition,
    )
    validate_video_caps_declaration(spec)
    return spec


def merge_builtin_definitions(registry: dict[str, EndpointSpec], directory: Path | None = None) -> None:
    """Merge bundled definitions into Python-shared key namespaces, rejecting duplicates.

    Invalid definitions/missing directories/key conflicts fail at import, preventing startup rather than
    reaching user generation with unconstructible endpoints.
    """
    for key, definition in load_builtin_definitions(directory).items():
        if key in registry:
            raise BuiltinDefinitionError(f"随版定义 {key}.json 的内置键与已有 endpoint 重复")
        registry[key] = declarative_endpoint_spec(key, definition)


merge_builtin_definitions(ENDPOINT_REGISTRY)


ENDPOINT_KEYS_BY_MEDIA_TYPE: dict[str, tuple[str, ...]] = {
    media_type: tuple(k for k, s in ENDPOINT_REGISTRY.items() if s.media_type == media_type)
    for media_type in {s.media_type for s in ENDPOINT_REGISTRY.values()}
}


def _validate_registry() -> None:
    """Validate built-in registry invariants at import."""
    for key, spec in ENDPOINT_REGISTRY.items():
        # ce- prefixes are DB-assigned custom endpoint ids; built-in occupation destroys unique
        # prefix routing. This invariant keeps shared-domain namespaces disjoint.
        if is_custom_endpoint(key):
            raise ValueError(f"builtin endpoint key {key!r} must not use the custom endpoint prefix")
        validate_video_caps_declaration(spec)


_validate_registry()


def _validate_builtin_provider_endpoints() -> None:
    """At import, validate built-in-provider endpoint options against this registry: shared names are built-ins here with matching media."""
    from lib.backends.backend_assembly.specs import validate_endpoint_declarations

    validate_endpoint_declarations()


_validate_builtin_provider_endpoints()


# Helpers.


def get_endpoint_spec(endpoint: str) -> EndpointSpec:
    spec = ENDPOINT_REGISTRY.get(endpoint)
    if spec is None:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    return spec


def endpoint_to_media_type(endpoint: str) -> str:
    return get_endpoint_spec(endpoint).media_type


def static_media_type(endpoint: str) -> str:
    """Built-in media types without DB; reject ``ce-`` keys.

    Custom media comes from ``kind``-specific definitions, never key prefixes. Model endpoint columns may hold either;
    media-dependent callers always resolve specs through
    ``endpoint_resolution.resolve_endpoint_spec``, then read ``EndpointSpec.media_type``.

    Raises:
        ValueError: ``ce-`` keys requiring definition reads, or missing built-in keys.
    """
    # Deferred imports: builtin_definitions consumes registries here, so module imports would cycle.
    from lib.custom_provider import is_custom_endpoint

    if is_custom_endpoint(endpoint):
        raise ValueError(f"custom endpoint media type comes from its definition: {endpoint!r}")
    return endpoint_to_media_type(endpoint)


def endpoint_to_image_capabilities(endpoint: str) -> frozenset[ImageCapability]:
    """Image endpoint capabilities; nonimages raise ValueError."""
    spec = get_endpoint_spec(endpoint)
    if spec.image_capabilities is None:
        raise ValueError(f"endpoint {endpoint!r} is not an image endpoint")
    return spec.image_capabilities


def endpoint_reference_image_limit(spec: EndpointSpec) -> int | None:
    """Static endpoint reference limits (channel facts), sharing constructed-backend ``reference_image_limit()`` sources.

    ComfyUI uses binding slot counts; declarative endpoints explicit declarations; Python wrapped channels use
    ``CHANNEL_REFERENCE_LIMIT``. ``None`` means unlimited channels. Runtime-query channels such as OpenRouter expose endpoint
    caps here, potentially narrowing during execution.
    """
    if spec.definition is not None:
        if spec.kind == COMFYUI_KIND:
            return binding_reference_limit(spec.definition)
        return definition_reference_limit(spec.definition)
    if spec.metering_backend is None:
        return None
    return reference_limit_of(spec.metering_backend)


def list_endpoints_by_media_type(media_type: str) -> list[EndpointSpec]:
    return [ENDPOINT_REGISTRY[k] for k in ENDPOINT_KEYS_BY_MEDIA_TYPE.get(media_type, ())]


def endpoint_spec_to_dict(spec: EndpointSpec) -> dict:
    """Serialize EndpointSpec into pure dicts, stripping non-JSON build_backend closures."""
    data = asdict(spec)
    data.pop("build_backend", None)
    data.pop("video_caps_for_model", None)  # Like build_backend, strip non-JSON callables.
    data.pop("metering_backend", None)  # Class objects for pricing/metering only.
    # Catalogs omit inline definitions; GET /custom-providers/endpoints/{key}/definition fetches JSON separately.
    data.pop("definition", None)
    data["kind"] = spec.kind
    data["display_name"] = spec.display_name
    # Parameter projections drive disabled size/duration controls/placeholders, sharing execution predicates.
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


# Heuristics: model_id + discovery_format → endpoint defaults.


_IMAGE_PATTERN = re.compile(r"image|dall|img|imagen|flux|seedream|jimeng|viduq[12](?:[-_].*)?", re.IGNORECASE)
_VIDEO_PATTERN = re.compile(
    r"video|sora|kling|wan|seedance|cog|mochi|veo|pika|runway|"
    r"vidu2(?:\.0)?(?:[-_].*)?|viduq[34](?:[-_].*)?",
    re.IGNORECASE,
)
# TTS ids: tts-1/gpt-4o-mini-tts/speech-1.5/cosyvoice/etc.
# Deliberately exclude bare "audio", which would misclassify chat audio models such as gpt-4o-audio-preview.
_AUDIO_PATTERN = re.compile(r"tts|speech|cosyvoice", re.IGNORECASE)
# Qwen TTS qwen-tts/qwen3-tts-flash uses native Bailian synchronous APIs, not /v1/audio/speech.
_QWEN_TTS_PATTERN = re.compile(r"qwen3?-tts", re.IGNORECASE)
# qwen-audio-<版本>-tts, such as qwen-audio-3.1-tts-flash, uses Bailian SpeechSynthesizer.
_QWEN_AUDIO_TTS_PATTERN = re.compile(r"qwen-audio-[\d.]+-tts", re.IGNORECASE)
# MiniMax speech-2.8-hd/speech-02-turbo exact final namespace segments use t2a_v2.
# Require -hd/-turbo suffixes; Fish Audio speech-1.5 and same-prefix ids remain /v1/audio/speech.
_MINIMAX_TTS_PATTERN = re.compile(r"speech-[\d.]+-(?:hd|turbo)")
# Bare speech collides with ASR ids; exclude recognition by content, never default them to TTS.
_ASR_PATTERN = re.compile(r"transcribe|speech.?to.?text|recognition", re.IGNORECASE)

#: Exact restricted MiniMax models, lowercase after namespace removal. Fast requires I2V,
#: S2V references; aliases would receive unverified input requirements, so official exact ids only
#: for addition prefills; historical migrations preserve upgrade rules. S2V also accepts minimax-s2v-01,
#: common vendor-prefixed aggregator aliases with identical exact-model semantics.
_MINIMAX_FAST_MODELS = frozenset({"minimax-hailuo-2.3-fast"})
_MINIMAX_S2V_MODELS = frozenset({"s2v-01", "minimax-s2v-01"})


#: Within known media, vendor tokens select corresponding family endpoints (docs/adr/0105).
#: Include only makers whose names span media, dual-modality or media-ambiguous models; others already
#: yield unique media through name rules.
_FAMILY_ENDPOINTS: tuple[tuple[str, Mapping[str, str]], ...] = (
    ("kling", {"image": "kling-image", "video": "kling-video-pro-silent"}),
    ("gemini", {"text": "gemini-generate", "image": "gemini-image", "audio": "gemini-tts"}),
    # Seedance generations further distinguish by name, _seedance_endpoint.
    ("seedance", {"video": "ark-seedance-1"}),
    ("seedream", {"image": "ark-seedream-4"}),
    ("viduq", {"video": "vidu-video-q3-turbo"}),
    ("vidu2", {"video": "vidu-video-2"}),
    ("hailuo", {"video": "minimax-hailuo-v1"}),
)

#: Discovery-media defaults: google uses native Gemini, others compatible OpenAI.
_DEFAULT_ENDPOINTS: Mapping[str, tuple[str, str]] = {
    "text": ("gemini-generate", "openai-chat"),
    "image": ("gemini-image", "openai-images"),
    "video": ("gemini-video", "openai-video"),
    "audio": ("gemini-tts", "openai-tts"),
}


def _request_profile_endpoint(provider: str, model_id: str) -> str:
    from lib.backends.backend_assembly.specs import request_profile_endpoint_for_name
    from lib.catalog.query import find_entry

    entry = find_entry(model_id, media_type="video")
    names = (entry.name, *entry.aliases) if entry is not None else (model_id,)
    for name in names:
        endpoint = request_profile_endpoint_for_name(provider, name)
        if endpoint is not None:
            return endpoint
    return {
        "vidu": "vidu-video-generic",
        "kling": "kling-video-silent",
    }[provider]


def _seedance_endpoint(model_id: str) -> str:
    """Seedance-video name prefills: explicit 2.5/2.0 choose corresponding generations, otherwise 1.x."""
    from lib.backends.backend_assembly.specs import seedance_endpoint_for_name

    return seedance_endpoint_for_name(model_id) or "ark-seedance-1"


def infer_endpoint(model_id: str, discovery_format: str, media_type: str | None = None) -> str:
    """Infer defaults; ``media_type`` is known matching-entry media, otherwise infer alongside names.

    Given media, use name-inferred endpoints only when matching types, otherwise choose family within that media
    (:data:`_FAMILY_ENDPOINTS`), falling back to discovery-protocol media defaults (docs/adr/0105).
    """
    by_name = _infer_endpoint_by_name(model_id, discovery_format)
    if media_type is None or endpoint_to_media_type(by_name) == media_type:
        return by_name
    lowered = model_id.lower()
    for token, endpoints in _FAMILY_ENDPOINTS:
        if token in lowered and media_type in endpoints:
            if endpoints[media_type] in SEEDANCE_BACKENDS:
                return _seedance_endpoint(model_id)
            if endpoints[media_type] in SEEDREAM_BACKENDS:
                from lib.backends.backend_assembly.specs import image_endpoint_for_name

                return image_endpoint_for_name("ark", model_id)
            if media_type == "video" and ("kling" in token or "vidu" in token):
                return _request_profile_endpoint("kling" if "kling" in token else "vidu", model_id)
            return endpoints[media_type]
    google_default, other_default = _DEFAULT_ENDPOINTS[media_type]
    return google_default if discovery_format == "google" else other_default


def infer_media_type(model_id: str, discovery_format: str) -> str:
    """Name-inferred media, from name-inferred endpoint types."""
    return endpoint_to_media_type(_infer_endpoint_by_name(model_id, discovery_format))


def _infer_endpoint_by_name(model_id: str, discovery_format: str) -> str:
    """Infer endpoints by model id/discovery_format, content first.

    Model contents precede discovery_format: relays commonly use discovery_format="openai" but expose
    native gemini-* names, requiring Google correction rather than openai-chat/openai-images
    and repeated manual fixes.

    1) Bailian video: happyhorse/wan2.7/wan3 families, including hyphenated wan-2.7-xxx / wan-3-xxx and
       image-to-video aliases, use native asynchronous Bailian video endpoints. happyhorse is absent from
       _VIDEO_PATTERN and requires explicit handling; Wan precedes generic is_video. True images do not auto-select dashscope
       because relays may use OpenAI: qwen-image / wan2.7-image / wan-2.7-image / wan3.0-video-image
       and version/date-suffixed equivalents follow image inference. wan-3-turbo-image-to-video /
       wan3-image2video remain video, like 2.5 kling-image2video below. classify_wan_model is_image_to_video
       recognizes this exact syntax without assumptions about image-variant endings/other naming forms.
       Native routes recognize 2.7 (including dotted "wan2.x", see classify_wan_model below) and wan3 only;
       other hyphen/underscore 2.x forms, wan-2.1 / wan_2.2-s2v, use generic video in 4).
       Unimplemented 2.7 videoedit, wan2.7-videoedit, also stays outside native routes
       (classify_wan_model is_videoedit).
    2) Native MiniMax tokens: Hailuo Fast/non-Fast separate keys, S2V, and H3 use respective declarative endpoints;
       image-01 uses "minimax-image", before generic
       is_video/is_image because s2v is absent from _VIDEO_PATTERN and image-01 otherwise becomes generic image.
       Restricted Fast/S2V require exact model names after removing relay namespace prefixes; nonexact Hailuo aliases
       use generic Hailuo, other s2v forms generic video 4).
    2.5) Kling tokens: video semantics first use "kling-video", including kling-image2video, which contains image
       but means video; remaining image semantics use "kling-image", otherwise "kling-video". Kling also matches
       _VIDEO_PATTERN, so precedes generic is_video to avoid openai-video. Shared v3-omni names
       default video, images require manual selection.
    3) gemini-omni → "gemini-interactions-video"; other native nonvideo gemini → TTS "gemini-tts",
       images "gemini-image", otherwise text "gemini-generate", independent of discovery_format.
    4) Videos: Seedance named generations → "ark-seedance-2-5" / "ark-seedance-2", unspecified
       "ark-seedance-1"; viduq3 / viduq4→"vidu-video", Veo with discovery_format=google→"gemini-video", otherwise "openai-video".
    5) Images: Seedream→"ark-seedream-3" / "ark-seedream-4" regardless of discovery_format; others google→
       "gemini-image", otherwise "openai-images"; imagen follows generic image inference.
    6) TTS (tts/speech/cosyvoice): qwen-audio-<版本>-tts → "dashscope-speech-synthesizer",
       qwen-tts / qwen3-tts → "dashscope-tts"; MiniMax
       speech-<版本>-hd|turbo → "minimax-tts"; others "openai-tts",
       independent of discovery_format and before text defaults.
    7) Default text: discovery_format=google → "gemini-generate", otherwise "openai-chat".
    """
    lowered = model_id.lower()
    is_image = bool(_IMAGE_PATTERN.search(model_id))
    # Native Bailian Wan/HappyHorse family matches include images/videos, consumed separately for routing/is_video exclusion.
    # Family membership/separators/boundaries/image-to-video syntax/videoedit exclusions
    # classify once through classify_wan_model, lib.backends.video_backends.dashscope. This function,
    # DashScopeVideoBackend._profile_for_model, and duration_presets.infer_supported_durations
    # consume conclusions only, never independent model_id regexes, avoiding divergent breadth and
    # native routing without compatible capabilities.
    classification = classify_wan_model(model_id)
    is_wan_family = classification.family is not None and classification.family != "happyhorse"
    # Wan image-to-video aliases wan-3-turbo-image-to-video/wan3-image2video contain image
    # but remain videos, like kling-image2video below; broad is_image would misclassify them.
    # Conversely image-ending-only checks miss true wan3.0-image-edit/wan-3-turbo-image-preview/
    # wan3.0-video-image-20260801 aliases, wrongly video.
    # Isolate only image-to-video continuation, image immediately followed by to/2 then video, as exceptions;
    # all other image semantics mean variants, without assumptions about naming forms.
    #
    # Do not require strict is_wan_family boundaries: _VIDEO_PATTERN wan is unbounded,
    # so swan2.7-image/vendorwan2.7-image still match _VIDEO_PATTERN;
    # requiring family membership for exclusion would falsely classify them video instead of image inference.
    # Therefore match wan substring breadth like _VIDEO_PATTERN, without family boundaries;
    # strict boundaries govern native Bailian async routing eligibility only.
    contains_wan_token = "wan" in lowered
    # Confirmed wan2.7 video t2v/i2v/r2v/s2v/v2v/videoedit, including unimplemented bodies,
    # per classify_wan_model is_known_video_modality, already establishes video semantics.
    # Unrelated image in other id regions, such as image-proxy/wan-2.7-s2v namespaces,
    # cannot override it. Other names without known modalities, wan2.7-image, conservatively mean images.
    # Never apply to wan3, whose single profile covers video and true image-edit aliases;
    # doing so would wrongly exclude genuine wan3.0-image-edit images, as above.
    # See the image-variant explanation above.
    #
    # Likewise never gate this predicate by is_wan_family, matching wan_image_variant scope:
    # wan-2.2-image-to-video violates strict family boundaries due to hyphens,
    # but classify_wan_model still recognizes boundary-valid continuation syntax, is_image_to_video.
    # Requiring family membership would misclassify explicit I2V names as images.
    wan_video_continuation = contains_wan_token and (
        classification.is_image_to_video
        or (classification.family == "wan2.7" and classification.is_known_video_modality)
    )
    wan_image_variant = contains_wan_token and is_image and not wan_video_continuation
    # Unimplemented wan2.7-videoedit/s2v/v2v never use native endpoints despite family matches,
    # per classify_wan_model has_known_modality; use generic video 4) below,
    # avoiding bodies this backend cannot construct.
    wan_unsupported_modality = is_wan_family and not classification.has_known_modality

    # Intercept Bailian video before generic is_video into native async routes.
    if classification.family == "happyhorse":
        from lib.backends.backend_assembly.specs import dashscope_video_endpoint_for_name

        return dashscope_video_endpoint_for_name(model_id)
    if is_wan_family and not wan_image_variant and not wan_unsupported_modality:
        from lib.backends.backend_assembly.specs import dashscope_video_endpoint_for_name

        return dashscope_video_endpoint_for_name(model_id)

    # MiniMax native secondary routes: Hailuo/minimax-hailuo/S2V/H3 → two-/single-step video retrieval;
    # image-01 → single-step images. Precede generic is_video/is_image because s2v/h3 lack _VIDEO_PATTERN,
    # and image-01 otherwise becomes generic images. Match minimax-h3, not bare h3,
    # too short and liable to collide with unrelated vendors.
    #
    # Restricted Fast/S2V use exact models, _MINIMAX_FAST_MODELS/_MINIMAX_S2V_MODELS.
    # MiniMax-Hailuo-2.3-Fast/MiniMax-Hailuo-2.3 prefix collisions make substring matching unsafe,
    # imposing required-first-frame Fast definitions on non-Fast/unknown relay aliases. Remove namespace prefixes,
    # taking final segments even in openrouter/minimax/MiniMax-Hailuo-2.3-Fast. Namespaces are merely
    # resale wrappers; final segments must equal official ids literally. Nonexact Hailuo aliases use generic
    # Hailuo without input requirements, while other s2v shapes such as
    # wan2.7-s2v use generic video 4), never misclassified MiniMax S2V.
    canonical = re.split(r"[/:]", lowered)[-1]
    if "hailuo" in lowered:
        # Fast accepts I2V only, requiring distinct capability-defined keys, never shared 2.3 definitions.
        return "minimax-hailuo-v1-fast" if canonical in _MINIMAX_FAST_MODELS else "minimax-hailuo-v1"
    if canonical in _MINIMAX_S2V_MODELS:
        return "minimax-s2v-01"
    if "minimax-h3" in lowered:
        return "minimax-h3"
    if "image-01" in lowered:
        return "minimax-image"

    # Native Kling secondary routing: kling matches _VIDEO_PATTERN and image-bearing
    # _IMAGE_PATTERN, requiring explicit dispatch before generic is_video/is_image. Prefer video:
    # kling-image2video/kling-img2video contain image yet are video. is_image alone would choose
    # kling-image, so intercept video first for kling-video; other image semantics
    # use kling-image, otherwise kling-video. Shared kling-v3-omni image/video names cannot be token-disambiguated,
    # default video/manual image, independent of discovery_format because Kling endpoints are unique.
    if "kling" in lowered:
        if "video" in lowered:
            return _request_profile_endpoint("kling", model_id)
        return "kling-image" if is_image else _request_profile_endpoint("kling", model_id)

    # wan2.x-image/wan3.0-video-image include wan and falsely match video; exclude to
    # image inference through shared wan_image_variant, already excluding continuation aliases.
    is_video = bool(_VIDEO_PATTERN.search(model_id)) and not wan_image_variant

    if "gemini-omni" in lowered:
        return "gemini-interactions-video"
    if "gemini" in lowered and not is_video:
        if "tts" in lowered:
            return "gemini-tts"
        return "gemini-image" if is_image else "gemini-generate"
    if is_video:
        if "seedance" in lowered:
            return _seedance_endpoint(model_id)
        if "viduq3" in lowered or "viduq4" in lowered:
            return _request_profile_endpoint("vidu", model_id)
        # Veo compatible OpenAI uses /v1/videos; only google discovery selects native predictLongRunning.
        if "veo" in lowered and discovery_format == "google":
            return "gemini-video"
        return "openai-video"
    if is_image:
        # Ark Seedream lacks /images/edits, references also use /images/generations, regardless of discovery_format.
        if "seedream" in lowered:
            from lib.backends.backend_assembly.specs import image_endpoint_for_name

            return image_endpoint_for_name("ark", model_id)
        return "gemini-image" if discovery_format == "google" else "openai-images"
    if _AUDIO_PATTERN.search(model_id) and not _ASR_PATTERN.search(model_id):
        if _QWEN_AUDIO_TTS_PATTERN.search(model_id):
            return "dashscope-speech-synthesizer"
        if _QWEN_TTS_PATTERN.search(model_id):
            return "dashscope-tts"
        return "minimax-tts" if _MINIMAX_TTS_PATTERN.fullmatch(canonical) else "openai-tts"
    return "gemini-generate" if discovery_format == "google" else "openai-chat"
