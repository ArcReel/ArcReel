"""Declarative built-in (provider_id, media_type) → ProviderSpec table.

Mirror custom ENDPOINT_REGISTRY (lib/custom_provider/endpoints.py): each spec is a frozen
dataclass with a build closure reading LoadedConfig + model_id, without DB queries/await.
Register image/video/audio for simple built-ins needing api_key + model + base_url,
sharing _build_simple, plus special gemini (backend_type modes, symmetric image/video/audio base_url forwarding) and
kling (JWT key pairs + api_model_name decoupling), each with dedicated closures. Text (media_type=text)
shares this table across all four media types: simple text (ark/ark-agent-plan/grok/agnes; agnes uses OpenAI-compatible
chat/completions but dedicated AgnesTextBackend authentication/base_url, hence simple, not an OpenAI-compat alias), gemini (aistudio/vertex),
and OpenAI-compatible text (openai/dashscope/minimax; dashscope/minimax derive base_url through helpers and forward
provider_name for billing) each have dedicated closures. Text aliases (dashscope/minimax → openai registry backend)
move into registry_backend, eliminating the old factory's second PROVIDER_ID_TO_BACKEND. Validate invariants on import,
except registry-name registration as explained at module end; fail fast on misconfiguration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from lib.backends.kling_shared import kling_auth_mode
from lib.backends.providers import PROVIDER_KLING
from lib.catalog import query as catalog

if TYPE_CHECKING:
    from arcreel_market_core.video_backend_contract import (
        VideoCapabilities,
        VideoGenerationRequest,
        VideoGenerationResult,
    )
    from lib.backends.backend_assembly.loaded_config import LoadedConfig
    from lib.billing.metering import Usage


@dataclass(frozen=True)
class ProviderSpec:
    """Built-in provider backend construction via one built-in endpoint: providers supply credentials, endpoints determine requests."""

    provider_id: str  # Registry/config provider id, such as "ark".
    media_type: str  # "image" | "video" | "audio" | "text"
    registry_backend: str  # Target media registry backend name, merging two PROVIDER_ID_TO_BACKEND mappings.
    # model_id=None occurs only without catalog defaults for this provider/media, handled by backends.
    build_backend: Callable[[LoadedConfig, str | None], Any]
    # Built-in endpoint for this constructor: shared custom-provider endpoints use registry keys; built-in-credential-only endpoints
    # appear in PROVIDER_ONLY_ENDPOINTS.
    endpoint: str


@dataclass(frozen=True)
class ProviderEndpoints:
    """Built-in endpoints available for one provider/media type in declaration order, plus defaults.

    Defaults apply to official models without explicit choices and prefill user-added models when inference fails.
    """

    provider_id: str
    media_type: str
    endpoints: tuple[str, ...]
    default: str


def _media_create_backend(media_type: str) -> Callable[..., Any]:
    """Get registry create_backend by media_type at runtime, permitting test patches to module attributes.

    All four text/image/video/audio types share construction; text backends register independently in
    lib.backends.text_backends.registry, requiring this branch.
    """
    if media_type == "image":
        from lib.backends.image_backends.registry import create_backend
    elif media_type == "video":
        from lib.backends.video_backends.registry import create_backend
    elif media_type == "audio":
        from lib.backends.audio_backends.registry import create_backend
    elif media_type == "text":
        from lib.backends.text_backends.registry import create_backend
    else:
        raise ValueError(f"unknown media_type: {media_type!r}")
    return create_backend


def _resolve_base_url(config: LoadedConfig) -> str | None:
    """base_url priority: explicit user db_config > ProviderMeta.default_base_url > None.

    Simple/kling families share this fallback; callers decide whether None means omitting base_url arguments.
    """
    default = config.provider_meta.default_base_url if config.provider_meta else None
    return config.credentials.get("base_url") or default


def _build_simple(
    config: LoadedConfig,
    model_id: str | None,
    *,
    media_type: str,
    registry_backend: str,
    extra_keys: tuple[str, ...] = (),
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Shared simple construction: api_key + model + base_url plus db_config keys declared by extra_keys.

    Include api_key/base_url in kwargs only when nonempty: explicit None may override SDK environment fallbacks,
    such as OPENAI_API_KEY. Backends handle omissions individually through environment lookup or fail-loud behavior.
    See _resolve_base_url for priority: providers such as grok without configured/default base_url reject that
    argument; even None causes TypeError, so only include nonempty values. extra_keys follow the same rule: names equal constructor parameters,
    sharing construction for provider-specific endpoints such as dashscope wan3_base_url.
    """
    kwargs: dict[str, Any] = {"model": model_id}
    api_key = config.credentials.get("api_key")
    if api_key:
        kwargs["api_key"] = api_key
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    for key in extra_keys:
        value = config.credentials.get(key)
        if value:
            kwargs[key] = value
    kwargs.update(extra_kwargs or {})
    return _media_create_backend(media_type)(registry_backend, **kwargs)


def _build_ark_video(config: LoadedConfig, model_id: str | None, *, endpoint: str) -> Any:
    """Ark video adds the actual inference credential to simple construction, letting asset libraries select bound provider assets.

    References exist only for api_key loaded from credential records: libraries bind records, while directly configured keys lack bindable objects.
    Endpoint declarations govern reference-image asset support (``SeedanceEndpoint.reference_image_asset``), shared with custom providers.
    """
    from lib.backends.provider_assets import InferenceCredentialRef

    api_key = config.credentials.get("api_key")
    extra: dict[str, Any] = {}
    if config.credential_id is not None and api_key:
        extra["inference_credential"] = InferenceCredentialRef(
            credential_type="builtin",
            credential_id=config.credential_id,
            api_key=api_key,
            base_url=config.credentials.get("base_url"),
            default_base_url=config.provider_meta.default_base_url if config.provider_meta else None,
        )
    return _build_simple(config, model_id, media_type="video", registry_backend=endpoint, extra_kwargs=extra)


def _simple_spec(provider_id: str, media_type: str, endpoint: str, *, extra_keys: tuple[str, ...] = ()) -> ProviderSpec:
    """Register simple specs: registry_backend equals provider_id, without media aliases."""
    return ProviderSpec(
        provider_id=provider_id,
        media_type=media_type,
        registry_backend=provider_id,
        build_backend=partial(
            _build_simple, media_type=media_type, registry_backend=provider_id, extra_keys=extra_keys
        ),
        endpoint=endpoint,
    )


#: Seedance-generation built-in endpoints in declaration order; registry names equal endpoint names (``lib.backends.video_backends.ark.SEEDANCE_BACKENDS``).
_SEEDANCE_ENDPOINTS = ("ark-seedance-1", "ark-seedance-2", "ark-seedance-2-5")


def seedance_endpoint_for_name(name: str) -> str | None:
    """Endpoint matching named Seedance generations; ``None`` without 2.0 / 2.5 names.

    ``in`` substrings tolerate doubao- domestic Volcengine / dreamina- international BytePlus prefixes; narrow versions to registered generations.
    Unknown same-generation suffixes (fast/mini/date snapshots) share endpoints. Use only model-addition prefills
    (``lib.custom_provider.endpoints.infer_endpoint``).
    Alembic ``e5b1c9a3d7f2`` freezes identical rules for existing-model backfill.
    """
    lowered = name.lower()
    if "seedance-2-5" in lowered or "seedance-2.5" in lowered:
        return "ark-seedance-2-5"
    if "seedance-2-0" in lowered or "seedance-2.0" in lowered:
        return "ark-seedance-2"
    return None


# Prefill additions by name; running channels read selected endpoints only.
_VIDU_PROFILES = {
    "viduq3-pro": "q3-pro",
    "viduq3-turbo": "q3-turbo",
    "viduq3-pro-fast": "q3-fast",
    "viduq3": "q3-reference",
    "viduq3-mix": "q3-reference",
    "viduq2": "q2",
    "viduq2-pro": "q2-pro",
    "viduq2-pro-fast": "q2-fast",
    "viduq2-turbo": "q2-fast",
    "viduq1": "q1",
    "viduq1-classic": "q1-classic",
    "vidu2.0": "2",
    "viduq4-preview": "q4",
}
_KLING_PROFILES = {
    "kling-v2-5-turbo": "pro-silent",
    "kling-v2-6": "pro-audio",
    "kling-v3": "audio",
    "kling-v3-omni": "audio",
    "kling-video-o1": "silent",
    # Models available only on new APIs with names in paths.
    "kling-4.0": "model-path",
    "kling-4.0-flash": "model-path",
    "kling-3.0-turbo": "model-path",
}
_GROK_ENDPOINTS = ("grok-video-classic", "grok-video-mixed")
_VIDU_ENDPOINTS = (
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
)
_KLING_ENDPOINTS = (
    "kling-video-pro-silent",
    "kling-video-pro-audio",
    "kling-video-audio",
    "kling-video-silent",
    "kling-video-model-path",
)


def request_profile_endpoint_for_name(provider_id: str, name: str) -> str | None:
    """Name-based Grok/Vidu/Kling video endpoints for addition prefills only; migrations freeze identical rules."""
    if provider_id == "grok":
        return "grok-video-classic" if name == "grok-imagine-video" else None
    if provider_id == "vidu":
        profile = _VIDU_PROFILES.get(name)
        return f"vidu-video-{profile}" if profile else None
    if provider_id == "kling":
        profile = _KLING_PROFILES.get(name.replace(":", "/").rsplit("/", 1)[-1].strip().lower())
        return f"kling-video-{profile}" if profile else None
    return None


def builtin_declarative_video_diagnostics(
    provider_id: str, model_id: str, config: Mapping[str, str]
) -> tuple[Mapping[str, Any], str, str] | None:
    """``(definition, base_url, api_key)`` for connection diagnostics on declaratively assembled built-in model rows.

    Return None for Python-backend (provider, model) pairs. Endpoint resolution (:func:`builtin_model_spec`) and credential fallbacks
    share :func:`_build_minimax_declarative_video`: diagnostic rendered requests must match actual requests.
    Callers first validate nonempty ``api_key`` through their credential gates; empty values fail loudly here.
    """
    if provider_id != "minimax":
        return None
    from lib.backends.minimax_shared import MINIMAX_BASE_URL, resolve_minimax_api_key

    definition = _declarative_definition(builtin_model_spec(provider_id, "video", model_id))
    if definition is None:
        return None
    return definition, config.get("base_url") or MINIMAX_BASE_URL, resolve_minimax_api_key(config.get("api_key"))


def _declarative_definition(spec: ProviderSpec) -> Mapping[str, Any] | None:
    """Bundled definitions for declaratively implemented endpoints; ``None`` for Python backends."""
    if spec.registry_backend != "declarative":
        return None
    from lib.custom_provider.endpoints import get_endpoint_spec

    return get_endpoint_spec(spec.endpoint).definition


def _build_minimax_declarative_video(config: LoadedConfig, model_id: str | None, *, endpoint: str) -> Any:
    from lib.backends.minimax_shared import MINIMAX_BASE_URL, resolve_minimax_api_key
    from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
    from lib.custom_provider.endpoints import get_endpoint_spec

    model = model_id or "MiniMax-H3"
    definition = get_endpoint_spec(endpoint).definition
    if definition is None:
        raise ValueError(f"builtin endpoint {endpoint!r} is not declarative")
    return DeclarativeVideoBackend(
        api_key=resolve_minimax_api_key(config.credentials.get("api_key")),
        base_url=_resolve_base_url(config) or MINIMAX_BASE_URL,
        model=model,
        definition=definition,
        provider="minimax",
    )


# Special gemini family.
# gemini-aistudio / gemini-vertex both map to "gemini" media backends,
# differing only by backend_type (aistudio | vertex), declared in separate provider spec rows, never closure branches.
# Forward base_url symmetrically across image/video/audio: aistudio uses configured endpoints. Unconditionally forward api_key/base_url,
# including None, handled by resolve_gemini_api_key / normalize_base_url within backends (None base_url omitted).
# Vertex uses vertexai=True + service accounts, structurally ignoring base_url without injection points; forwarded None is discarded.

_GEMINI_REGISTRY_BACKEND = "gemini"


def _build_gemini_image(config: LoadedConfig, model_id: str | None, *, backend_type: str) -> Any:
    return _media_create_backend("image")(
        _GEMINI_REGISTRY_BACKEND,
        backend_type=backend_type,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
        rate_limiter=config.rate_limiter,
        image_model=model_id,
        **({"credentials_path": config.credentials.get("credentials_path")} if backend_type == "vertex" else {}),
    )


def _build_gemini_video(config: LoadedConfig, model_id: str | None, *, backend_type: str, registry_backend: str) -> Any:
    return _media_create_backend("video")(
        registry_backend,
        backend_type=backend_type,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
        rate_limiter=config.rate_limiter,
        video_model=model_id,
        **({"credentials_path": config.credentials.get("credentials_path")} if backend_type == "vertex" else {}),
    )


def _build_gemini_audio(config: LoadedConfig, model_id: str | None, *, backend_type: str) -> Any:
    return _media_create_backend("audio")(
        _GEMINI_REGISTRY_BACKEND,
        backend_type=backend_type,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
        model=model_id,
        **({"credentials_path": config.credentials.get("credentials_path")} if backend_type == "vertex" else {}),
    )


_GEMINI_BUILDS = {"image": _build_gemini_image, "audio": _build_gemini_audio}


_GEMINI_ENDPOINTS = {"image": "gemini-image", "audio": "gemini-tts"}


#: Video protocol → backend_type → credential-specific endpoint. Veo long tasks default (AI Studio Veo registers in video
#: registry as bare "gemini", other names match registry names); Gemini Omni uses Interactions.
_GEMINI_VIDEO_ENDPOINTS: Mapping[str, Mapping[str, str]] = {
    "veo": {"aistudio": "gemini-video", "vertex": "gemini-vertex-video"},
    "interactions": {"aistudio": "gemini-interactions-video", "vertex": "gemini-vertex-interactions-video"},
}


def _gemini_spec(provider_id: str, media_type: str, *, backend_type: str) -> ProviderSpec:
    build = _GEMINI_BUILDS[media_type]
    return ProviderSpec(
        provider_id=provider_id,
        media_type=media_type,
        registry_backend=_GEMINI_REGISTRY_BACKEND,
        build_backend=partial(build, backend_type=backend_type),
        endpoint=_GEMINI_ENDPOINTS[media_type],
    )


def _gemini_video_spec(provider_id: str, endpoint: str, *, backend_type: str) -> ProviderSpec:
    registry_backend = _GEMINI_REGISTRY_BACKEND if endpoint == "gemini-video" else endpoint
    return ProviderSpec(
        provider_id=provider_id,
        media_type="video",
        registry_backend=registry_backend,
        build_backend=partial(_build_gemini_video, backend_type=backend_type, registry_backend=registry_backend),
        endpoint=endpoint,
    )


# Special kling family.
# Two authentication modes, api_key taking priority when both filled, centralized in lib.backends.kling_shared.kling_auth_mode:
# backend_assembly/providers._test_kling share it to prevent duplicated dispatch drift.
# - Nonempty api_key → auth_mode=bearer, official API Key for every model.
# - Otherwise auth_mode=jwt, access_key + secret_key, officially for 3.0 and earlier only;
#   read both secrets by column names, forward unconditionally including None for backend resolve_kling_jwt_credentials.
# base_url: explicit db_config > registry default_base_url > omitted (KlingBackend's
# KLING_BASE_URL fallback). Image/video share construction; only images inject api_model_name decoupling,
# letting dual-modality alias keys such as kling-v3-omni-image send registry API names. Video backends reject
# api_model_name, so inject only for image media_type.

_KLING_REGISTRY_BACKEND = "kling"


def _build_kling(config: LoadedConfig, model_id: str | None, *, media_type: str, endpoint: str | None = None) -> Any:
    """Shared Kling construction: credential-shaped auth_mode (api_key before key pairs), plus image api_model_name injection."""
    if kling_auth_mode(config.credentials) == "bearer":
        kwargs: dict[str, Any] = {
            "auth_mode": "bearer",
            "api_key": config.credentials.get("api_key"),
            "model": model_id,
        }
    else:
        kwargs = {
            "auth_mode": "jwt",
            "access_key": config.credentials.get("access_key"),
            "secret_key": config.credentials.get("secret_key"),
            "model": model_id,
        }
    if media_type == "image":
        # Dual-modality aliases send catalog api_model_name; videos reject it, so inject only for images.
        model_info = catalog.get_model(PROVIDER_KLING, model_id) if model_id else None
        if model_info is not None and model_info.api_model_name:
            kwargs["api_model_name"] = model_info.api_model_name
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend(media_type)(endpoint or _KLING_REGISTRY_BACKEND, **kwargs)


def _kling_spec(media_type: str, *, endpoint: str | None = None) -> ProviderSpec:
    return ProviderSpec(
        provider_id=_KLING_REGISTRY_BACKEND,
        media_type=media_type,
        registry_backend=endpoint or _KLING_REGISTRY_BACKEND,
        build_backend=partial(_build_kling, media_type=media_type, endpoint=endpoint),
        endpoint=endpoint or f"kling-{media_type}",
    )


# Text families.
# Text backends have independent lib.backends.text_backends.registry, not media registries, with distinct construction:
# api_key/base_url forwarding, OpenAI aliases, and provider_name billing attribution differ, so text cannot
# reuse _build_simple; each shape uses dedicated closures. Three families:
#   ① Simple text: ark / ark-agent-plan / grok / agnes; model + unconditional api_key + base_url (user >
#      registry default, nonempty only, matching simple-media priority). Agnes uses compatible
#      chat/completions but dedicated AgnesTextBackend authentication/base_url normalization, hence this family, not ③.
#   ② gemini: aistudio forwards model + api_key + base_url unconditionally, including None/empty strings; vertex
#      uses model + backend=vertex + gcs_bucket. Separate provider rows, never closure branches.
#   ③ OpenAI-compat: openai / dashscope / minimax map to "openai" registry backends; openai directly forwards
#      user base_url unconditionally, including None; dashscope/minimax derive base_url from hosts via helpers and forward
#      actual provider_name so usage/pricing selects their own Bailian/MiniMax CNY rates, never OpenAI USD.


def _build_text_simple(config: LoadedConfig, model_id: str | None, *, registry_backend: str) -> Any:
    """Simple text family: model + unconditional api_key + base_url (user > registry default, nonempty only).

    Unconditional api_key, including None, preserves pre-migration imperative semantics (db_config.get("api_key") directly in kwargs).
    Nonempty-only base_url mirrors simple media: ark/ark-agent-plan have registry fallbacks; grok omits it without
    user configuration/defaults.
    """
    kwargs: dict[str, Any] = {"model": model_id, "api_key": config.credentials.get("api_key")}
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend("text")(registry_backend, **kwargs)


def _text_simple_spec(provider_id: str, endpoint: str) -> ProviderSpec:
    """Register simple text specs: registry_backend equals provider_id without text aliases."""
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=provider_id,
        build_backend=partial(_build_text_simple, registry_backend=provider_id),
        endpoint=endpoint,
    )


_TEXT_GEMINI_REGISTRY_BACKEND = "gemini"


def _build_text_gemini_aistudio(config: LoadedConfig, model_id: str | None) -> Any:
    # Aistudio accepts configured endpoints without registry defaults; forward user base_url unconditionally, including None/empty,
    # handled inside GeminiTextBackend. Text gemini lacks rate_limiter, preserving pre-migration asymmetry.
    return _media_create_backend("text")(
        _TEXT_GEMINI_REGISTRY_BACKEND,
        model=model_id,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
    )


def _build_text_gemini_vertex(config: LoadedConfig, model_id: str | None) -> Any:
    # Vertex uses service-account files + gcs_bucket, without api_key/base_url, preserving old imperative branches.
    return _media_create_backend("text")(
        _TEXT_GEMINI_REGISTRY_BACKEND,
        model=model_id,
        backend="vertex",
        gcs_bucket=config.credentials.get("gcs_bucket"),
        credentials_path=config.credentials.get("credentials_path"),
    )


def _text_gemini_spec(provider_id: str, *, build: Callable[[LoadedConfig, str | None], Any]) -> ProviderSpec:
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=_TEXT_GEMINI_REGISTRY_BACKEND,
        build_backend=build,
        endpoint="gemini-generate",
    )


_TEXT_OPENAI_COMPAT_REGISTRY_BACKEND = "openai"
# Built-in openai text uses Responses, supporting Responses-only o3-pro / gpt-5.5-pro models.
_TEXT_OPENAI_RESPONSES_REGISTRY_BACKEND = "openai-responses"


def _dashscope_text_base_url(configured: str | None) -> str:
    # Runtime import prevents lightweight CLI/migrations from indirectly importing dashscope_shared through this table.
    from lib.backends.dashscope_shared import dashscope_text_base_url

    return dashscope_text_base_url(configured)


def _minimax_text_base_url(configured: str | None) -> str:
    from lib.backends.minimax_shared import minimax_text_base_url

    return minimax_text_base_url(configured)


def _build_text_openai_compat(
    config: LoadedConfig,
    model_id: str | None,
    *,
    derive_base_url: Callable[[str | None], str] | None,
    provider_name: str | None,
    registry_backend: str = _TEXT_OPENAI_COMPAT_REGISTRY_BACKEND,
) -> Any:
    """OpenAI-protocol text: dashscope/minimax use Chat Completions (OpenAITextBackend),
    openai uses Responses (OpenAIResponsesTextBackend); constructor arguments match, differentiated by registry_backend.

    derive_base_url=None (openai): forward user base_url unconditionally, including None, leaving endpoint decisions to backends.
    Nonempty derive_base_url (dashscope/minimax): derive compatible bases from user hosts and forward
    provider_name, ensuring usage/billing selects their own CNY rates, never OpenAI USD.
    """
    user_base_url = config.credentials.get("base_url")
    kwargs: dict[str, Any] = {
        "model": model_id,
        "api_key": config.credentials.get("api_key"),
        "base_url": derive_base_url(user_base_url) if derive_base_url else user_base_url,
    }
    if provider_name:
        kwargs["provider_name"] = provider_name
    return _media_create_backend("text")(registry_backend, **kwargs)


def _text_openai_compat_spec(
    provider_id: str,
    *,
    derive_base_url: Callable[[str | None], str] | None,
    provider_name: str | None,
    registry_backend: str = _TEXT_OPENAI_COMPAT_REGISTRY_BACKEND,
) -> ProviderSpec:
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=registry_backend,
        build_backend=partial(
            _build_text_openai_compat,
            derive_base_url=derive_base_url,
            provider_name=provider_name,
            registry_backend=registry_backend,
        ),
        # Chat Completions / Responses each define request structures matching same-named custom-provider endpoints.
        endpoint="openai-responses" if registry_backend == _TEXT_OPENAI_RESPONSES_REGISTRY_BACKEND else "openai-chat",
    )


# Built-in endpoint registry.
# Provider models first select attached built-in endpoints, then construct backends: providers supply credentials (Vertex accounts, Kling key pairs,
# Ark inference credentials, etc., handled in dedicated closures), endpoints determine requests. Keys = (provider_id, endpoint).
#
# Built-in/custom endpoints share namespaces: identical request structures use same-named
# lib.custom_provider.endpoints.ENDPOINT_REGISTRY keys. Structures requiring built-in credentials, such as xAI SDK,
# Ark SDK/Agnes, register in PROVIDER_ONLY_ENDPOINTS and cannot attach to custom providers.
#
# Simple family means built-in media needing api_key + model + base_url, sharing _build_simple. Defined by construction shapes,
# never provider allowlists; includes ark/ark-agent-plan/grok/openai/vidu/dashscope/minimax. ark-agent-plan media reuses
# same-named Ark image/video registrations, equally simple. Special gemini (backend_type modes) and kling
# (JWT pairs + api_model_name) have dedicated closures; gemini providers register per backend_type (bare
# "gemini" is unreachable because resolver emits suffixed ids, so no silent fallback row). Three text construction families:
# simple/gemini/OpenAI-compatible, per closures. Unregistered provider×media raises ValueError, never defaults to incorrect backends.

#: Built-in-credential-only endpoints → media types; all other built-in endpoints use endpoint-registry keys.
PROVIDER_ONLY_ENDPOINTS: Mapping[str, str] = {
    "ark-chat": "text",
    "grok-chat": "text",
    "agnes-chat": "text",
    "grok-image": "image",
    "vidu-image": "image",
    "agnes-image-pixels": "image",
    "agnes-image-tier": "image",
    **dict.fromkeys(_GROK_ENDPOINTS, "video"),
    "agnes-video-2": "video",
    "agnes-video-2-5": "video",
    "gemini-vertex-video": "video",
    "gemini-vertex-interactions-video": "video",
}

#: Simple image/video/audio: (provider_id, media_type) → endpoints.
_SIMPLE_MEDIA_ENDPOINTS: Mapping[tuple[str, str], str] = {
    ("grok", "image"): "grok-image",
    ("openai", "image"): "openai-images",
    ("vidu", "image"): "vidu-image",
    ("minimax", "image"): "minimax-image",
    ("openai", "video"): "openai-video",
    ("minimax", "audio"): "minimax-tts",
}

# Two gemini provider ids → backend_type, each with image/video/audio rows.
_GEMINI_BACKEND_TYPES: dict[str, str] = {"gemini-aistudio": "aistudio", "gemini-vertex": "vertex"}

#: Four MiniMax declarative video endpoints, each a bundled request-shape/capabilities definition; Hailuo 2.3 defaults.
_MINIMAX_VIDEO_ENDPOINTS = ("minimax-hailuo-v1", "minimax-hailuo-v1-fast", "minimax-s2v-01", "minimax-h3")

_specs: list[ProviderSpec] = [
    _simple_spec(provider_id, media_type, endpoint)
    for (provider_id, media_type), endpoint in _SIMPLE_MEDIA_ENDPOINTS.items()
]
_IMAGE_ENDPOINTS = {
    "ark": ("ark-seedream-3", "ark-seedream-4"),
    "ark-agent-plan": ("ark-seedream-3", "ark-seedream-4"),
    "dashscope": ("dashscope-qwen-image", "dashscope-wan-image", "dashscope-qwen-image-edit"),
    "agnes": ("agnes-image-pixels", "agnes-image-tier"),
}
_specs += [
    ProviderSpec(
        provider_id=provider_id,
        media_type="image",
        registry_backend=endpoint,
        build_backend=partial(_build_simple, media_type="image", registry_backend=endpoint),
        endpoint=endpoint,
    )
    for provider_id, endpoints in _IMAGE_ENDPOINTS.items()
    for endpoint in endpoints
]
#: Two Bailian audio endpoints: default multimodal-generation (qwen3-tts) and SpeechSynthesizer (qwen-audio-*-tts),
#: registry names equal endpoints (``lib.backends.audio_backends.DASHSCOPE_AUDIO_BACKENDS``).
_DASHSCOPE_AUDIO_ENDPOINTS = ("dashscope-tts", "dashscope-speech-synthesizer")
_specs += [
    ProviderSpec(
        provider_id="dashscope",
        media_type="audio",
        registry_backend=endpoint,
        build_backend=partial(_build_simple, media_type="audio", registry_backend=endpoint),
        endpoint=endpoint,
    )
    for endpoint in _DASHSCOPE_AUDIO_ENDPOINTS
]
_specs += [
    _gemini_spec(provider_id, media_type, backend_type=backend_type)
    for provider_id, backend_type in _GEMINI_BACKEND_TYPES.items()
    for media_type in ("image", "audio")
]
_specs += [
    _gemini_video_spec(provider_id, by_backend_type[backend_type], backend_type=backend_type)
    for provider_id, backend_type in _GEMINI_BACKEND_TYPES.items()
    for by_backend_type in _GEMINI_VIDEO_ENDPOINTS.values()
]
_specs += [_kling_spec("image"), _kling_spec("image", endpoint="kling-image-single")]
_specs += [
    ProviderSpec(
        provider_id=provider,
        media_type="video",
        registry_backend=endpoint,
        build_backend=(
            partial(_build_kling, media_type="video", endpoint=endpoint)
            if provider == "kling"
            else partial(_build_simple, media_type="video", registry_backend=endpoint)
        ),
        endpoint=endpoint,
    )
    for provider, endpoints in (("grok", _GROK_ENDPOINTS), ("vidu", _VIDU_ENDPOINTS), ("kling", _KLING_ENDPOINTS))
    for endpoint in endpoints
]
# Both Ark providers attach three Seedance endpoints; video lanes additionally report inference credentials (_build_ark_video).
_specs += [
    ProviderSpec(
        provider_id=ark_provider,
        media_type="video",
        registry_backend=endpoint,
        build_backend=partial(_build_ark_video, endpoint=endpoint),
        endpoint=endpoint,
    )
    for ark_provider in ("ark", "ark-agent-plan")
    for endpoint in _SEEDANCE_ENDPOINTS
]
# dashscope video extends simple construction with wan3_base_url: Wan 3.0 has separate MaaS hosts containing region/workspace, not inferable.
# Without it, backends fall back to generic base_url. Image/audio ignore it and remain simple.
for _provider, _endpoints in (
    (
        "dashscope",
        (
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
        ),
    ),
    ("agnes", ("agnes-video-2", "agnes-video-2-5")),
):
    for _endpoint in _endpoints:
        _specs.append(
            ProviderSpec(
                provider_id=_provider,
                media_type="video",
                registry_backend=_endpoint,
                endpoint=_endpoint,
                build_backend=partial(
                    _build_simple,
                    media_type="video",
                    registry_backend=_endpoint,
                    extra_keys=("wan3_base_url",) if _provider == "dashscope" else (),
                ),
            )
        )
_specs += [
    ProviderSpec(
        provider_id="minimax",
        media_type="video",
        registry_backend="declarative",
        build_backend=partial(_build_minimax_declarative_video, endpoint=endpoint),
        endpoint=endpoint,
    )
    for endpoint in _MINIMAX_VIDEO_ENDPOINTS
]
# Text: four simple providers (registry_backend=provider_id; Agnes uses compatible chat/completions but dedicated authentication/base_url
# normalization in AgnesTextBackend). Two gemini ids use separate backend rows; three OpenAI-compatible providers
# share constructors: dashscope/minimax → "openai" Chat Completions with helper-derived base_url + provider_name
# attribution; openai → "openai-responses" Responses, directly forwarding user base_url.
_specs += [
    _text_simple_spec("ark", "ark-chat"),
    _text_simple_spec("ark-agent-plan", "ark-chat"),
    _text_simple_spec("grok", "grok-chat"),
    _text_simple_spec("agnes", "agnes-chat"),
    _text_gemini_spec("gemini-aistudio", build=_build_text_gemini_aistudio),
    _text_gemini_spec("gemini-vertex", build=_build_text_gemini_vertex),
    _text_openai_compat_spec(
        "openai", derive_base_url=None, provider_name=None, registry_backend=_TEXT_OPENAI_RESPONSES_REGISTRY_BACKEND
    ),
    _text_openai_compat_spec("dashscope", derive_base_url=_dashscope_text_base_url, provider_name="dashscope"),
    _text_openai_compat_spec("minimax", derive_base_url=_minimax_text_base_url, provider_name="minimax"),
]

#: (provider_id, endpoint) → construction specs for that provider/endpoint.
PROVIDER_ENDPOINT_SPECS: dict[tuple[str, str], ProviderSpec] = {
    (spec.provider_id, spec.endpoint): spec for spec in _specs
}


def _declare_endpoints(provider_id: str, media_type: str, default: str) -> ProviderEndpoints:
    endpoints = tuple(
        spec.endpoint
        for spec in PROVIDER_ENDPOINT_SPECS.values()
        if spec.provider_id == provider_id and spec.media_type == media_type
    )
    return ProviderEndpoints(provider_id=provider_id, media_type=media_type, endpoints=endpoints, default=default)


#: Provider/media pairs with multiple endpoints → defaults; single options are themselves defaults.
_MULTI_ENDPOINT_DEFAULTS: Mapping[tuple[str, str], str] = {
    ("ark", "image"): "ark-seedream-4",
    ("ark-agent-plan", "image"): "ark-seedream-4",
    ("dashscope", "image"): "dashscope-qwen-image",
    ("gemini-aistudio", "video"): "gemini-video",
    ("gemini-vertex", "video"): "gemini-vertex-video",
    ("agnes", "image"): "agnes-image-tier",
    ("minimax", "video"): "minimax-hailuo-v1",
    ("grok", "video"): "grok-video-classic",
    ("vidu", "video"): "vidu-video-q3-turbo",
    ("kling", "image"): "kling-image",
    ("kling", "video"): "kling-video-pro-silent",
    ("dashscope", "video"): "dashscope-always-on-video",
    ("dashscope", "audio"): "dashscope-tts",
    ("agnes", "video"): "agnes-video-2-5",
    # Ark default video models are Seedance 2.0.
    ("ark", "video"): "ark-seedance-2",
    ("ark-agent-plan", "video"): "ark-seedance-2",
}

#: (provider_id, media_type) → available/default built-in endpoints; defaults are actual channels for official models without explicit choices.
PROVIDER_ENDPOINTS: dict[tuple[str, str], ProviderEndpoints] = {
    (spec.provider_id, spec.media_type): _declare_endpoints(
        spec.provider_id,
        spec.media_type,
        _MULTI_ENDPOINT_DEFAULTS.get((spec.provider_id, spec.media_type), spec.endpoint),
    )
    for spec in PROVIDER_ENDPOINT_SPECS.values()
}

#: (provider_id, media_type) → default construction specs when no other endpoint is selected.
PROVIDER_SPEC_REGISTRY: dict[tuple[str, str], ProviderSpec] = {
    key: PROVIDER_ENDPOINT_SPECS[(declared.provider_id, declared.default)]
    for key, declared in PROVIDER_ENDPOINTS.items()
}


_VALID_MEDIA_TYPES = frozenset({"image", "video", "audio", "text"})


def endpoint_media_type(endpoint: str) -> str | None:
    """Media type for built-in endpoints; ``None`` otherwise."""
    if endpoint in PROVIDER_ONLY_ENDPOINTS:
        return PROVIDER_ONLY_ENDPOINTS[endpoint]
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    spec = ENDPOINT_REGISTRY.get(endpoint)
    return spec.media_type if spec is not None and spec.source == "builtin" else None


def _validate_provider_specs() -> None:
    """Validate built-in-table invariants on import, failing fast (mirroring endpoints._validate_video_caps_declarations).

    Check internal consistency without importing backends: callable builds, dict/spec key agreement, valid media types, and every
    provider/media default among its options, all options having same-media constructors. Endpoint names/media agreement
    (:func:`validate_endpoint_declarations`) requires endpoint registries, validated when those import; backend registry-name
    registration belongs to unit tests. Both avoid lightweight CLI/migrations importing all backends via this seam.
    """
    for key, spec in PROVIDER_SPEC_REGISTRY.items():
        if not callable(spec.build_backend):
            raise ValueError(f"ProviderSpec {key!r} declares non-callable build_backend: {spec.build_backend!r}")
        if (spec.provider_id, spec.media_type) != key:
            raise ValueError(
                f"PROVIDER_SPEC_REGISTRY key {key!r} does not match spec fields "
                f"(provider_id={spec.provider_id!r}, media_type={spec.media_type!r})"
            )
        if spec.media_type not in _VALID_MEDIA_TYPES:
            raise ValueError(f"ProviderSpec {key!r} declares unknown media_type: {spec.media_type!r}")
    for key, declared in PROVIDER_ENDPOINTS.items():
        if declared.default not in declared.endpoints:
            raise ValueError(f"default endpoint {declared.default!r} of {key!r} is not among {declared.endpoints!r}")
        for endpoint in declared.endpoints:
            spec = PROVIDER_ENDPOINT_SPECS.get((declared.provider_id, endpoint))
            if spec is None or spec.media_type != declared.media_type:
                raise ValueError(f"endpoint {endpoint!r} of {key!r} has no {declared.media_type} build")


def validate_endpoint_declarations() -> None:
    """Every built-in provider endpoint is built-in and matches its declaring provider/media type."""
    for key, declared in PROVIDER_ENDPOINTS.items():
        for endpoint in declared.endpoints:
            media_type = endpoint_media_type(endpoint)
            if media_type != declared.media_type:
                raise ValueError(
                    f"endpoint {endpoint!r} of {key!r} is a {media_type or 'unknown'} endpoint, "
                    f"expected {declared.media_type}"
                )


_validate_provider_specs()


def get_provider_endpoints(provider_id: str, media_type: str) -> ProviderEndpoints:
    """Available/default built-in endpoints for provider/media; raise ``ValueError`` without channels for that media."""
    declared = PROVIDER_ENDPOINTS.get((provider_id, media_type))
    if declared is None:
        raise ValueError(f"no builtin ProviderSpec for provider={provider_id!r} media={media_type!r}")
    return declared


def get_provider_spec(provider_id: str, media_type: str, endpoint: str | None = None) -> ProviderSpec:
    """Construction spec for provider through ``endpoint``, default if omitted; ``ValueError`` for unavailable endpoints."""
    declared = get_provider_endpoints(provider_id, media_type)
    name = declared.default if endpoint is None else endpoint
    if name not in declared.endpoints:
        raise ValueError(f"endpoint {name!r} is not available for provider={provider_id!r} media={media_type!r}")
    return PROVIDER_ENDPOINT_SPECS[(provider_id, name)]


def image_endpoint_for_name(provider_id: str, model_id: str) -> str:
    """Image-endpoint name rules, used only for new-model prefills."""
    lowered = model_id.lower()
    if provider_id in ("ark", "ark-agent-plan"):
        return "ark-seedream-3" if "seedream-3" in lowered else "ark-seedream-4"
    if provider_id == "dashscope":
        if lowered.startswith("wan"):
            return "dashscope-wan-image"
        return "dashscope-qwen-image-edit" if "qwen-image-edit" in lowered else "dashscope-qwen-image"
    return "agnes-image-tier" if model_id.startswith("agnes-image-2.5") else "agnes-image-pixels"


def dashscope_video_endpoint_for_name(name: str) -> str:
    """Prefill Bailian request contracts by name; execution reads selected endpoints only."""
    import re

    normalized = name.strip().lower()
    if re.search(r"(?<![a-z0-9])wan[-_]?3(?![a-z0-9])", normalized):
        return "dashscope-wan3-video"
    wan27 = re.search(r"(?<![a-z0-9])wan[-_]?2\.7(?![a-z0-9])", normalized)
    if wan27:
        suffix = normalized[wan27.start() :]
        if re.search(r"(?<![a-z0-9])(?:video[-_]?edit|[sv]2v)(?![a-z0-9])", suffix):
            return "dashscope-video"
        suffix = re.sub(r"wan[-_]?2\.7[_-]", "wan2.7-", suffix)
        suffix = re.sub(r"(?<![a-z0-9])image[-_]?(?:to|2)[-_]?video(?![a-z0-9])", "i2v", suffix)
        normalized = normalized[: wan27.start()] + suffix
    if re.search(r"(?<![a-z0-9])wan2\.7-r2v(?![a-z0-9])", normalized):
        return "dashscope-reference-voice-video"
    if re.search(r"(?<![a-z0-9])happyhorse-1\.[01]-r2v(?![a-z0-9])", normalized):
        return "dashscope-reference-video"
    if re.search(r"(?<![a-z0-9])(?:wan2\.7|happyhorse-1\.[01])-(?:t2v|i2v)(?![a-z0-9])", normalized):
        return "dashscope-always-on-video"
    return "dashscope-video"


def agnes_video_endpoint_for_name(name: str) -> str | None:
    """Prefill Agnes contracts by known generations; unknown uses provider defaults."""
    if name in {"agnes-video-2.5", "agnes-video-2.5-flash"}:
        return "agnes-video-2-5"
    if name == "agnes-video-v2.0":
        return "agnes-video-2"
    return None


def gemini_video_endpoint_for_name(provider_id: str, name: str) -> str | None:
    """Prefill Gemini video protocols by name: Omni uses Interactions, others provider defaults."""
    if "gemini-omni" not in name.strip().lower():
        return None
    return _GEMINI_VIDEO_ENDPOINTS["interactions"][_GEMINI_BACKEND_TYPES[provider_id]]


def builtin_model_endpoint(provider_id: str, media_type: str, model_id: str | None) -> str:
    """Official models read catalog declarations, user-added models saved choices; undeclared uses provider defaults.

    Raise ``ValueError`` if declared endpoints are unavailable for provider/media, preventing incompatible request structures.
    """
    chosen = catalog.provider_model_endpoint(provider_id, model_id, media_type) if model_id else None
    return get_provider_spec(provider_id, media_type, chosen).endpoint


def builtin_model_spec(provider_id: str, media_type: str, model_id: str | None) -> ProviderSpec:
    """Built-in provider-model construction from endpoints resolved by :func:`builtin_model_endpoint`."""
    return PROVIDER_ENDPOINT_SPECS[(provider_id, builtin_model_endpoint(provider_id, media_type, model_id))]


def infer_builtin_endpoint(provider_id: str, media_type: str, model_id: str) -> str:
    """Prefill added built-in-provider models by name using custom-provider rules; inferred unavailable endpoints fall back to defaults."""
    if media_type == "image" and provider_id in _IMAGE_ENDPOINTS:
        return image_endpoint_for_name(provider_id, model_id)
    from lib.custom_provider.endpoints import infer_endpoint

    declared = get_provider_endpoints(provider_id, media_type)
    if media_type == "video" and provider_id == "grok":
        entry = catalog.find_entry(model_id, media_type="video")
        names = (entry.name, *entry.aliases) if entry is not None else (model_id,)
        for name in names:
            endpoint = request_profile_endpoint_for_name(provider_id, name)
            if endpoint is not None:
                return endpoint
        return declared.default
    if media_type == "video" and provider_id in _GEMINI_BACKEND_TYPES:
        return gemini_video_endpoint_for_name(provider_id, model_id) or declared.default
    discovery_format = "google" if provider_id in _GEMINI_BACKEND_TYPES else "openai"
    inferred = (
        agnes_video_endpoint_for_name(model_id) if provider_id == "agnes" and media_type == "video" else None
    ) or infer_endpoint(model_id, discovery_format, media_type)
    return inferred if inferred in declared.endpoints else declared.default


def builtin_video_capabilities_for_model(
    provider_id: str, model_id: str, *, endpoint: str | None = None
) -> VideoCapabilities:
    """Effective built-in video capabilities: jointly evaluate channel declarations/model entries, adding user-added model overrides (docs/adr/0104).

    Overrides replace whole dimensions at highest priority; meaningless combined audio dimensions mean unsupported audio references, matching custom providers.
    Given ``endpoint``, evaluate through it, aligning execution capabilities and constructed backend endpoints.
    """
    return effective_builtin_video_capabilities(
        declared_builtin_video_capabilities(provider_id, model_id, endpoint=endpoint), provider_id, model_id
    )


def two_sided_builtin_video_capabilities(
    channel: VideoCapabilities, provider_id: str, model_id: str
) -> VideoCapabilities:
    """Jointly evaluate channel/model capabilities without overrides; settings show these base values.

    Use corresponding entries: official models use manifest references, user-added models bindings/current name lookup.
    """
    from lib.backends.video_capabilities import effective_video_capabilities, entry_video_facts

    entry = catalog.provider_model_entry(provider_id, model_id, "video")
    return effective_video_capabilities(channel, entry_video_facts(entry))


def effective_builtin_video_capabilities(
    channel: VideoCapabilities, provider_id: str, model_id: str
) -> VideoCapabilities:
    """Effective built-in video capabilities: jointly evaluate ``channel``/entries, then user-added-model overrides (docs/adr/0104).

    Callers supply bases: context-free declarations for planning, tier-resolved capabilities for execution gates, with identical evaluation rules.
    """
    return apply_user_added_video_overrides(
        two_sided_builtin_video_capabilities(channel, provider_id, model_id), provider_id, model_id
    )


def apply_user_added_video_overrides(caps: VideoCapabilities, provider_id: str, model_id: str) -> VideoCapabilities:
    """Apply whole-dimension overrides for user-added built-in-provider models to jointly evaluated capabilities (docs/adr/0104)."""
    overrides = catalog.user_added_capability_overrides(provider_id, model_id)
    if not overrides:
        return caps
    from lib.custom_provider.capabilities import (
        CHANNEL_FACT_OVERRIDE_KEYS,
        enforce_audio_capability_invariant,
        merge_overrides,
    )

    channel = {key: value for key, value in overrides.items() if key in CHANNEL_FACT_OVERRIDE_KEYS}
    return enforce_audio_capability_invariant(merge_overrides(caps, channel), endpoint=provider_id, model_id=model_id)


def _builtin_video_spec(provider_id: str, model_id: str, endpoint: str | None) -> ProviderSpec:
    """Built-in video channel for supplied endpoints or currently attached model endpoints."""
    if endpoint is not None:
        return get_provider_spec(provider_id, "video", endpoint)
    return builtin_model_spec(provider_id, "video", model_id)


def declared_builtin_video_capabilities(
    provider_id: str, model_id: str, *, endpoint: str | None = None
) -> VideoCapabilities:
    """Built-in channel declarations only, without entries/user overrides; declarative endpoints read bundled definitions, others backend registries.

    Resolve model-attached endpoints (:func:`builtin_model_endpoint`); supplied ``endpoint`` supports pre-save checks of changed-endpoint overrides.
    """
    spec = _builtin_video_spec(provider_id, model_id, endpoint)
    if spec.registry_backend == "declarative":
        from lib.custom_provider.builtin_definitions import declarative_video_capabilities

        definition = _declarative_definition(spec)
        if definition is None:
            raise ValueError(f"builtin declarative definition missing for {provider_id}/{model_id}")
        return declarative_video_capabilities(definition)
    from lib.backends.video_backends.registry import video_capabilities_for_model

    # Channels identify request shapes (audio/prompt limits, etc.) only through corresponding entry names, not input capabilities: official models use manifest
    # entries; user-added models use bindings/current name lookup.
    return video_capabilities_for_model(
        spec.registry_backend, model_id, catalog.provider_model_entry(provider_id, model_id, "video")
    )


def builtin_video_metered_usage(
    provider_id: str,
    model_id: str,
    request: VideoGenerationRequest,
    result: VideoGenerationResult | None = None,
) -> Usage:
    """Built-in video-channel request measurements without instance construction; declarative providers read bundled definitions.

    ``result`` = ``None`` means presubmission estimation; otherwise match settlement instance measurements. Requests without capabilities receive
    user-overridden capabilities from execution's source, metering relaxed dimensions according to actual sent requests.
    """
    spec = builtin_model_spec(provider_id, "video", model_id)
    if spec.registry_backend == "declarative":
        from lib.custom_provider.declarative_backend import declarative_video_usage

        definition = _declarative_definition(spec)
        if definition is None:
            raise ValueError(f"builtin declarative definition missing for {provider_id}/{model_id}")
        return declarative_video_usage(definition, request, result)
    from lib.backends.video_backends.registry import metered_usage_for_model

    if request.video_capabilities is None:
        request = replace(request, video_capabilities=builtin_video_capabilities_for_model(provider_id, model_id))
    # Same entry as capability declarations/execution assembly: official models use manifest entries, user additions bindings/current name lookup.
    return metered_usage_for_model(
        spec.registry_backend,
        model_id,
        request,
        result,
        entry=catalog.provider_model_entry(provider_id, model_id, "video"),
    )


def builtin_effective_generate_audio_for_model(provider_id: str, model_id: str, *, endpoint: str | None = None) -> bool:
    spec = _builtin_video_spec(provider_id, model_id, endpoint)
    if spec.registry_backend == "declarative":
        # Pricing shares capability sources: definitions declaring always-silent outputs cannot produce speech under default execution tiers.
        from arcreel_market_core.video_backend_contract import VideoAudioMode

        return (
            builtin_video_capabilities_for_model(provider_id, model_id, endpoint=endpoint).audio_track
            is not VideoAudioMode.ALWAYS_OFF
        )
    from lib.backends.video_backends.registry import effective_generate_audio_for_model

    return effective_generate_audio_for_model(
        spec.registry_backend, model_id, catalog.provider_model_entry(provider_id, model_id, "video")
    )
