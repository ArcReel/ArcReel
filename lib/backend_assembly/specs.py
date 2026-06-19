"""内置 provider 的 (provider_id, media_type) → ProviderSpec 声明式表。

镜像自定义侧 ENDPOINT_REGISTRY（lib/custom_provider/endpoints.py）：每条 spec 是 frozen
dataclass，挂一个 build 闭包；闭包读 LoadedConfig 信封 + model_id 拼 backend，不查 DB、不 await。
登记简单族（媒体侧只需 api_key + model + base_url 的内置 provider）的 image/video/audio，
共享一个 _build_simple 闭包；外加 gemini（backend_type 双模式 + image/video base_url 非对称）与
kling（JWT 双 secret + api_model_name 解耦）两个特例族，各自挂专属 build 闭包。表在 import 期校验
不变式（registry 名已注册除外，见模块末尾说明），misconfig fail-fast。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.backend_assembly.loaded_config import LoadedConfig


@dataclass(frozen=True)
class ProviderSpec:
    """单条内置 (provider, media) 的 backend 构造规格。"""

    provider_id: str  # registry / config provider id，如 "ark"
    media_type: str  # "image" | "video" | "audio"
    registry_backend: str  # 映射到哪个 media backend registry 名（合并两份 PROVIDER_ID_TO_BACKEND）
    # model_id 可为 None：缺省时由 backend 内部回落各自 DEFAULT_MODEL（与 effective_model 上游一致）
    build_backend: Callable[[LoadedConfig, str | None], Any]


def _media_create_backend(media_type: str) -> Callable[..., Any]:
    """按 media_type 取对应 registry 的 create_backend（运行时取，便于测试 patch 模块属性）。"""
    if media_type == "image":
        from lib.image_backends.registry import create_backend
    elif media_type == "video":
        from lib.video_backends.registry import create_backend
    elif media_type == "audio":
        from lib.audio_backends.registry import create_backend
    else:
        raise ValueError(f"unknown media_type: {media_type!r}")
    return create_backend


def _resolve_base_url(config: LoadedConfig) -> str | None:
    """base_url 优先级：用户在 db_config 显式填写 > ProviderMeta.default_base_url > None。

    简单族与 kling 共用此兜底语义；调用方各自决定 None 时是否省略 base_url 参数。
    """
    default = config.provider_meta.default_base_url if config.provider_meta else None
    return config.credentials.get("base_url") or default


def _build_simple(config: LoadedConfig, model_id: str | None, *, media_type: str, registry_backend: str) -> Any:
    """简单族通用构造：api_key + model + base_url。

    api_key 与 base_url 同遵「仅非空才写入 kwargs」：显式传 None 可能覆盖底层 SDK 的环境变量兜底
    （如 OpenAI SDK 读 OPENAI_API_KEY），缺省由 backend 各自处理（要么读环境变量、要么 fail-loud）。
    base_url 优先级见 _resolve_base_url —— grok 等无 default 且用户未配的 provider 不接受 base_url
    参数，传 None 会触发 TypeError，故仅非空才写入。
    """
    kwargs: dict[str, Any] = {"model": model_id}
    api_key = config.credentials.get("api_key")
    if api_key:
        kwargs["api_key"] = api_key
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend(media_type)(registry_backend, **kwargs)


def _simple_spec(provider_id: str, media_type: str) -> ProviderSpec:
    """登记一条简单族 spec：registry_backend 即 provider_id 自身（媒体侧无别名映射）。"""
    return ProviderSpec(
        provider_id=provider_id,
        media_type=media_type,
        registry_backend=provider_id,
        build_backend=partial(_build_simple, media_type=media_type, registry_backend=provider_id),
    )


# ── gemini 特例族 ──────────────────────────────────────────────────
# gemini-aistudio / gemini-vertex 两个 provider_id 都映射到同一个 "gemini" media backend，
# 差异只在 backend_type（aistudio | vertex）—— 由 spec 行声明（每个 provider_id 各一行），不在闭包内 if。
# image 设 base_url，video 不设（保留迁移前的非对称：GeminiVideoBackend 虽接受 base_url 但 video 路径
# 历来不传）。api_key 与 image 的 base_url 无条件透传（含 None）：迁移前命令式分支即无条件 db_config.get，
# 由 backend 内 resolve_gemini_api_key / normalize_base_url 处理 None（vertex 读凭证文件、None base_url 省略）。

_GEMINI_REGISTRY_BACKEND = "gemini"


def _build_gemini_image(config: LoadedConfig, model_id: str | None, *, backend_type: str) -> Any:
    return _media_create_backend("image")(
        _GEMINI_REGISTRY_BACKEND,
        backend_type=backend_type,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
        rate_limiter=config.rate_limiter,
        image_model=model_id,
    )


def _build_gemini_video(config: LoadedConfig, model_id: str | None, *, backend_type: str) -> Any:
    return _media_create_backend("video")(
        _GEMINI_REGISTRY_BACKEND,
        backend_type=backend_type,
        api_key=config.credentials.get("api_key"),
        rate_limiter=config.rate_limiter,
        video_model=model_id,
    )


def _gemini_spec(provider_id: str, media_type: str, *, backend_type: str) -> ProviderSpec:
    build = _build_gemini_image if media_type == "image" else _build_gemini_video
    return ProviderSpec(
        provider_id=provider_id,
        media_type=media_type,
        registry_backend=_GEMINI_REGISTRY_BACKEND,
        build_backend=partial(build, backend_type=backend_type),
    )


# ── kling 特例族 ──────────────────────────────────────────────────
# JWT 直连：双 secret（access_key + secret_key，按 ADR 0037 列名直取，无条件透传含 None，由 backend 内
# resolve_kling_jwt_credentials 处理）+ auth_mode=jwt。base_url 兜底：db_config 显式填写 > registry
# default_base_url > 不传（KlingBackend 自带 KLING_BASE_URL 兜底）。image 侧额外做 api_model_name 解耦
# （两栖别名键如 kling-v3-omni-image 读 registry api_model_name 发真实 API 名）；video backend 不接受
# api_model_name 参数，video 闭包不传（保留迁移前非对称）。

_KLING_REGISTRY_BACKEND = "kling"


def _build_kling_image(config: LoadedConfig, model_id: str | None) -> Any:
    kwargs: dict[str, Any] = {
        "auth_mode": "jwt",
        "access_key": config.credentials.get("access_key"),
        "secret_key": config.credentials.get("secret_key"),
        "model": model_id,
    }
    model_info = config.provider_meta.models.get(model_id) if (config.provider_meta and model_id) else None
    if model_info is not None and model_info.api_model_name:
        kwargs["api_model_name"] = model_info.api_model_name
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend("image")(_KLING_REGISTRY_BACKEND, **kwargs)


def _build_kling_video(config: LoadedConfig, model_id: str | None) -> Any:
    kwargs: dict[str, Any] = {
        "auth_mode": "jwt",
        "access_key": config.credentials.get("access_key"),
        "secret_key": config.credentials.get("secret_key"),
        "model": model_id,
    }
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend("video")(_KLING_REGISTRY_BACKEND, **kwargs)


def _kling_spec(media_type: str) -> ProviderSpec:
    build = _build_kling_image if media_type == "image" else _build_kling_video
    return ProviderSpec(
        provider_id=_KLING_REGISTRY_BACKEND,
        media_type=media_type,
        registry_backend=_KLING_REGISTRY_BACKEND,
        build_backend=build,
    )


# ── PROVIDER_SPEC_REGISTRY 注册表 ──────────────────────────────────
# 键 = (provider_id, media_type)。简单族 = 媒体侧只需 api_key + model + base_url 的内置 provider，
# 共享 _build_simple 闭包。「简单族」按构造形态界定（不是 provider 名白名单），含 ark/ark-agent-plan/
# grok/openai/vidu 与 dashscope/minimax（后两者媒体侧走原生简单构造；其文本侧 OpenAI-compat 特例由
# 文本工厂另行处理）。ark-agent-plan 媒体侧复用 Ark image/video backend（registry 同名注册），与 ark
# 同为简单形态。特例族 = gemini（backend_type 双模式 + image/video base_url 非对称）与 kling（JWT 双
# secret + api_model_name 解耦），各挂专属 build 闭包；gemini 的两个 provider_id 各按 backend_type 登记
# 一行（裸 "gemini" 是死路径——resolver 只产出带后缀 id，按 fail-loud 不登记兜底行）。每对显式登记一行，
# fail-loud（未登记的 provider × media 抛 ValueError，不「缺席即默认」造静默错误 backend）。只登记今天
# 确有注册 backend 的对：image/video 简单族七家齐全，audio 仅 dashscope。

_SIMPLE_IMAGE_VIDEO_PROVIDERS = ("ark", "ark-agent-plan", "grok", "openai", "vidu", "dashscope", "minimax")
_SIMPLE_MEDIA_PAIRS: list[tuple[str, str]] = [
    *((p, "image") for p in _SIMPLE_IMAGE_VIDEO_PROVIDERS),
    *((p, "video") for p in _SIMPLE_IMAGE_VIDEO_PROVIDERS),
    ("dashscope", "audio"),
]

# gemini 两个 provider_id → backend_type，每个 × image/video 登记一行。
_GEMINI_BACKEND_TYPES: dict[str, str] = {"gemini-aistudio": "aistudio", "gemini-vertex": "vertex"}

PROVIDER_SPEC_REGISTRY: dict[tuple[str, str], ProviderSpec] = {
    (provider_id, media_type): _simple_spec(provider_id, media_type) for provider_id, media_type in _SIMPLE_MEDIA_PAIRS
}
PROVIDER_SPEC_REGISTRY.update(
    {
        (provider_id, media_type): _gemini_spec(provider_id, media_type, backend_type=backend_type)
        for provider_id, backend_type in _GEMINI_BACKEND_TYPES.items()
        for media_type in ("image", "video")
    }
)
PROVIDER_SPEC_REGISTRY.update(
    {(_KLING_REGISTRY_BACKEND, media_type): _kling_spec(media_type) for media_type in ("image", "video")}
)


_VALID_MEDIA_TYPES = frozenset({"image", "video", "audio"})


def _validate_provider_specs() -> None:
    """import 期校验内置表自身不变式，misconfig fail-fast（镜像 endpoints._validate_video_caps_declarations）。

    只做不需 import 媒体后端的内表自洽检查：build 可调用、字典键与 spec 字段一致、media_type 合法。
    「registry 名都在媒体后端 registry 里」需 import 全部 lib.{image,video,audio}_backends 才能断言，
    为免轻量场景（CLI / 迁移）因 import 本缝而被动拉起全部后端，归入单测，不进 import 期（见 ADR 0039）。
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


_validate_provider_specs()


def get_provider_spec(provider_id: str, media_type: str) -> ProviderSpec:
    spec = PROVIDER_SPEC_REGISTRY.get((provider_id, media_type))
    if spec is None:
        raise ValueError(f"no builtin ProviderSpec for provider={provider_id!r} media={media_type!r}")
    return spec
