"""从已装载的凭证和调用端点构造内置视频通道。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from lib.backends.kling_shared import kling_auth_mode
from lib.backends.minimax_shared import MINIMAX_BASE_URL, resolve_minimax_api_key
from lib.backends.provider_assets import InferenceCredentialRef
from lib.custom_provider.builtin_definitions import builtin_definitions

if TYPE_CHECKING:
    from arcreel_market_core.video_backend_contract import VideoBackend, VideoGenerationRequest, VideoGenerationResult
    from lib.backends.audio_backends.base import AudioBackend
    from lib.backends.backend_assembly.loaded_config import LoadedConfig
    from lib.backends.backend_assembly.specs import ProviderSpec
    from lib.backends.image_backends.base import ImageBackend
    from lib.backends.text_backends.base import TextBackend
    from lib.billing.metering import Usage


def build_builtin_text(config: LoadedConfig, model: str | None, provider_id: str, registry: str) -> TextBackend:
    from lib.backends.dashscope_shared import dashscope_text_base_url
    from lib.backends.minimax_shared import minimax_text_base_url
    from lib.backends.text_backends import registry as text_backends

    credentials = config.credentials
    default_base_url = config.provider_meta.default_base_url if config.provider_meta else None
    if provider_id == "gemini-aistudio":
        return text_backends.create_backend(
            registry,
            model=model,
            api_key=credentials.get("api_key"),
            base_url=credentials.get("base_url"),
        )
    if provider_id == "gemini-vertex":
        return text_backends.create_backend(
            registry,
            model=model,
            backend="vertex",
            gcs_bucket=credentials.get("gcs_bucket"),
            credentials_path=credentials.get("credentials_path"),
        )
    if provider_id in {"openai", "dashscope", "minimax"}:
        user_base_url = credentials.get("base_url")
        if provider_id == "dashscope":
            base_url: str | None = dashscope_text_base_url(user_base_url)
            provider_name: str | None = "dashscope"
        elif provider_id == "minimax":
            base_url = minimax_text_base_url(user_base_url)
            provider_name = "minimax"
        else:
            base_url = user_base_url
            provider_name = None
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": credentials.get("api_key"),
            "base_url": base_url,
        }
        if provider_name:
            kwargs["provider_name"] = provider_name
        return text_backends.create_backend(registry, **kwargs)
    kwargs = {"model": model, "api_key": credentials.get("api_key")}
    base_url = credentials.get("base_url") or default_base_url
    if base_url:
        kwargs["base_url"] = base_url
    return text_backends.create_backend(registry, **kwargs)


def build_builtin_audio(config: LoadedConfig, model: str | None, provider_id: str, registry: str) -> AudioBackend:
    from lib.backends.audio_backends import registry as audio_backends

    credentials = config.credentials
    api_key = credentials.get("api_key")
    default_base_url = config.provider_meta.default_base_url if config.provider_meta else None
    if provider_id in {"gemini-aistudio", "gemini-vertex"}:
        vertex = provider_id == "gemini-vertex"
        return audio_backends.create_backend(
            registry,
            backend_type="vertex" if vertex else "aistudio",
            api_key=api_key,
            base_url=credentials.get("base_url"),
            model=model,
            **({"credentials_path": credentials.get("credentials_path")} if vertex else {}),
        )
    if provider_id == "ark-agent-plan":
        kwargs: dict[str, Any] = {"model": model}
        if api_key:
            kwargs["api_key"] = api_key
        return audio_backends.create_backend(registry, **kwargs)
    kwargs = {"model": model}
    if api_key:
        kwargs["api_key"] = api_key
    base_url = credentials.get("base_url") or default_base_url
    if base_url:
        kwargs["base_url"] = base_url
    return audio_backends.create_backend(registry, **kwargs)


def build_builtin_image(
    config: LoadedConfig,
    model: str | None,
    provider_id: str,
    registry: str,
    *,
    api_model_name: str | None = None,
) -> ImageBackend:
    from lib.backends.image_backends import registry as image_backends

    credentials = config.credentials
    api_key = credentials.get("api_key")
    default_base_url = config.provider_meta.default_base_url if config.provider_meta else None
    base_url = credentials.get("base_url") or default_base_url
    if provider_id in {"gemini-aistudio", "gemini-vertex"}:
        vertex = provider_id == "gemini-vertex"
        return image_backends.create_backend(
            registry,
            backend_type="vertex" if vertex else "aistudio",
            api_key=api_key,
            base_url=credentials.get("base_url"),
            rate_limiter=config.rate_limiter,
            image_model=model,
            **({"credentials_path": credentials.get("credentials_path")} if vertex else {}),
        )
    kwargs: dict[str, Any] = {"model": model}
    if provider_id == "kling":
        auth_mode = kling_auth_mode(credentials)
        kwargs["auth_mode"] = auth_mode
        if auth_mode == "bearer":
            kwargs["api_key"] = api_key
        else:
            kwargs.update(access_key=credentials.get("access_key"), secret_key=credentials.get("secret_key"))
        if api_model_name:
            kwargs["api_model_name"] = api_model_name
    elif api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return image_backends.create_backend(registry, **kwargs)


def builtin_declarative_credentials(config: LoadedConfig) -> tuple[str, str]:
    credentials = config.credentials
    default_base_url = config.provider_meta.default_base_url if config.provider_meta else None
    base_url = credentials.get("base_url") or default_base_url or MINIMAX_BASE_URL
    return base_url, resolve_minimax_api_key(credentials.get("api_key"))


def build_builtin_video(
    config: LoadedConfig, model: str | None, provider_id: str, registry: str, definition: Mapping[str, Any] | None
) -> VideoBackend:
    from lib.backends.video_backends import registry as video_backends
    from lib.custom_provider.declarative_backend import DeclarativeVideoBackend

    credentials = config.credentials
    api_key = credentials.get("api_key")
    default_base_url = config.provider_meta.default_base_url if config.provider_meta else None
    base_url = credentials.get("base_url") or default_base_url
    if definition is not None:
        decl_base_url, decl_api_key = builtin_declarative_credentials(config)
        return DeclarativeVideoBackend(
            api_key=decl_api_key,
            base_url=decl_base_url,
            model=model or "MiniMax-H3",
            definition=definition,
            provider=provider_id,
        )
    if provider_id in {"gemini-aistudio", "gemini-vertex"}:
        vertex = provider_id == "gemini-vertex"
        return video_backends.create_backend(
            registry,
            backend_type="vertex" if vertex else "aistudio",
            api_key=api_key,
            base_url=credentials.get("base_url"),
            rate_limiter=config.rate_limiter,
            video_model=model,
            **({"credentials_path": credentials.get("credentials_path")} if vertex else {}),
        )
    kwargs: dict[str, Any] = {"model": model}
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    if provider_id == "kling":
        kwargs["auth_mode"] = kling_auth_mode(credentials)
        if kwargs["auth_mode"] != "bearer":
            kwargs.pop("api_key", None)
            kwargs.update(access_key=credentials.get("access_key"), secret_key=credentials.get("secret_key"))
    if provider_id in {"ark", "ark-agent-plan"} and config.credential_id is not None and api_key:
        kwargs["inference_credential"] = InferenceCredentialRef(
            credential_type="builtin",
            credential_id=config.credential_id,
            api_key=api_key,
            base_url=credentials.get("base_url"),
            default_base_url=default_base_url,
        )
    if provider_id == "dashscope" and credentials.get("wan3_base_url"):
        kwargs["wan3_base_url"] = credentials["wan3_base_url"]
    return video_backends.create_backend(registry, **kwargs)


def declarative_definition(spec: ProviderSpec) -> Mapping[str, Any] | None:
    """经随版声明式定义调用的端点的定义；Python backend 实现的端点为 ``None``。"""
    if spec.registry_backend != "declarative":
        return None
    return builtin_definitions()[spec.endpoint]


def build_builtin_backend(spec: ProviderSpec, config: LoadedConfig, model: str | None) -> Any:
    """按内置端点规格与已装载凭证构造对应媒体的内置调用通道。"""
    if spec.media_type == "text":
        return build_builtin_text(config, model, spec.provider_id, spec.registry_backend)
    if spec.media_type == "audio":
        return build_builtin_audio(config, model, spec.provider_id, spec.registry_backend)
    if spec.media_type == "image":
        api_model_name: str | None = None
        if spec.provider_id == "kling" and model:
            from lib.catalog import query as catalog

            model_info = catalog.get_model("kling", model)
            if model_info is not None and model_info.api_model_name:
                api_model_name = model_info.api_model_name
        return build_builtin_image(
            config, model, spec.provider_id, spec.registry_backend, api_model_name=api_model_name
        )
    if spec.media_type == "video":
        return build_builtin_video(config, model, spec.provider_id, spec.registry_backend, declarative_definition(spec))
    raise ValueError(f"unsupported media_type: {spec.media_type!r}")


def builtin_video_metered_usage(
    provider_id: str,
    model_id: str,
    request: VideoGenerationRequest,
    result: VideoGenerationResult | None = None,
) -> Usage:
    """内置供应商的视频调用通道对一个请求上报的计量，不构造实例；声明式供应商从随版定义读。

    ``result`` 为 ``None`` 时是提交前预估；给出时与结算时调用通道实例上报的计量相同。请求没带能力时补上
    叠加用户覆盖后的能力，与执行时请求携带的那份同源，放宽的维度按实际发出的请求计量。
    """
    from lib.backends.backend_assembly.specs import builtin_model_spec, builtin_video_capabilities_for_model

    spec = builtin_model_spec(provider_id, "video", model_id)
    if spec.registry_backend == "declarative":
        from lib.custom_provider.declarative_backend import declarative_video_usage

        definition = declarative_definition(spec)
        if definition is None:
            raise ValueError(f"builtin declarative definition missing for {provider_id}/{model_id}")
        return declarative_video_usage(definition, request, result)
    from lib.backends.video_backends.registry import metered_usage_for_model
    from lib.catalog import query as catalog

    if request.video_capabilities is None:
        request = replace(request, video_capabilities=builtin_video_capabilities_for_model(provider_id, model_id))
    return metered_usage_for_model(
        spec.registry_backend,
        model_id,
        request,
        result,
        entry=catalog.provider_model_entry(provider_id, model_id, "video"),
    )
