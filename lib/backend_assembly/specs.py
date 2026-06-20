"""内置 provider 的 (provider_id, media_type) → ProviderSpec 声明式表。

镜像自定义侧 ENDPOINT_REGISTRY（lib/custom_provider/endpoints.py）：每条 spec 是 frozen
dataclass，挂一个 build 闭包；闭包读 LoadedConfig 信封 + model_id 拼 backend，不查 DB、不 await。
登记简单族（媒体侧只需 api_key + model + base_url 的内置 provider）的 image/video/audio，
共享一个 _build_simple 闭包；外加 gemini（backend_type 双模式，image/video 对等透传 base_url）与
kling（JWT 双 secret + api_model_name 解耦）两个特例族，各自挂专属 build 闭包。文本（media_type=text）
四条 media_type 同经本表：简单文本（ark/ark-agent-plan/grok）、gemini 文本（aistudio/vertex）、
OpenAI-compat 文本（openai/dashscope/minimax，dashscope/minimax 经 helper 派生 base_url + 透传
provider_name 计费归因）各挂专属闭包；文本侧别名映射（dashscope/minimax → openai registry backend）
并入 spec 的 registry_backend 字段，根除原文本工厂第二份 PROVIDER_ID_TO_BACKEND。表在 import 期校验
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
    media_type: str  # "image" | "video" | "audio" | "text"
    registry_backend: str  # 映射到哪个 media backend registry 名（合并两份 PROVIDER_ID_TO_BACKEND）
    # model_id 可为 None：缺省时由 backend 内部回落各自 DEFAULT_MODEL（与 effective_model 上游一致）
    build_backend: Callable[[LoadedConfig, str | None], Any]


def _media_create_backend(media_type: str) -> Callable[..., Any]:
    """按 media_type 取对应 registry 的 create_backend（运行时取，便于测试 patch 模块属性）。

    text 与 image/video/audio 四条 media_type 共用同一构造缝，文本 backend 注册在
    独立的 lib.text_backends.registry，故在此分流。
    """
    if media_type == "image":
        from lib.image_backends.registry import create_backend
    elif media_type == "video":
        from lib.video_backends.registry import create_backend
    elif media_type == "audio":
        from lib.audio_backends.registry import create_backend
    elif media_type == "text":
        from lib.text_backends.registry import create_backend
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
# image/video 对等透传 base_url：aistudio 用用户配置的自定义 endpoint。api_key 与 base_url 无条件
# 透传（含 None），由 backend 内 resolve_gemini_api_key / normalize_base_url 处理 None（None base_url 省略）；
# vertex 分支走 vertexai=True + 服务账号凭证，结构性忽略 base_url（无注入点），透传的 None 被丢弃。

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
        base_url=config.credentials.get("base_url"),
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
# JWT 直连：双 secret（access_key + secret_key 按列名直取，无条件透传含 None，由 backend 内
# resolve_kling_jwt_credentials 处理）+ auth_mode=jwt。base_url 兜底：db_config 显式填写 > registry
# default_base_url > 不传（KlingBackend 自带 KLING_BASE_URL 兜底）。image/video 共用单一构造 helper，
# 仅 image 侧额外注入 api_model_name 解耦（两栖别名键如 kling-v3-omni-image 读 registry api_model_name
# 发真实 API 名）；video backend 不接受 api_model_name 参数，故该注入仅对 image media_type 生效。

_KLING_REGISTRY_BACKEND = "kling"


def _build_kling(config: LoadedConfig, model_id: str | None, *, media_type: str) -> Any:
    """kling 通用构造：JWT 双 secret + auth_mode=jwt + base_url 兜底；image 侧叠加 api_model_name 注入。"""
    kwargs: dict[str, Any] = {
        "auth_mode": "jwt",
        "access_key": config.credentials.get("access_key"),
        "secret_key": config.credentials.get("secret_key"),
        "model": model_id,
    }
    if media_type == "image":
        # 两栖别名键读 registry api_model_name 发真实 API 名；video backend 不接受该参数，仅 image 注入。
        model_info = config.provider_meta.models.get(model_id) if (config.provider_meta and model_id) else None
        if model_info is not None and model_info.api_model_name:
            kwargs["api_model_name"] = model_info.api_model_name
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend(media_type)(_KLING_REGISTRY_BACKEND, **kwargs)


def _kling_spec(media_type: str) -> ProviderSpec:
    return ProviderSpec(
        provider_id=_KLING_REGISTRY_BACKEND,
        media_type=media_type,
        registry_backend=_KLING_REGISTRY_BACKEND,
        build_backend=partial(_build_kling, media_type=media_type),
    )


# ── 文本族 ────────────────────────────────────────────────────────
# 文本 backend 注册在独立的 lib.text_backends.registry（非 media registry），构造形态与媒体有别：
# api_key/base_url 透传规则、OpenAI-compat 别名映射、provider_name 计费归因透传各不相同，故文本侧
# 不复用 _build_simple，各类形态挂专属闭包。三类：
#   ① 简单文本（ark / ark-agent-plan / grok）—— model + api_key（无条件透传）+ base_url（user >
#      registry default，仅非空才传，对称媒体简单族 base_url 优先级）。
#   ② gemini 文本 —— aistudio（model + api_key + base_url 无条件透传，含 None/空串）/ vertex
#      （model + backend=vertex + gcs_bucket）按 provider_id 分两行，不在闭包内 if。
#   ③ OpenAI-compat（openai / dashscope / minimax）—— 都映射到 "openai" registry backend；openai 直传
#      用户 base_url（无条件，含 None），dashscope/minimax 由各自 helper 从 host 派生 base_url 并透传
#      真实 provider_name，确保 usage 记账与计费查表命中自身（百炼/MiniMax）的 CNY 费率而非 OpenAI USD。


def _build_text_simple(config: LoadedConfig, model_id: str | None, *, registry_backend: str) -> Any:
    """简单文本族：model + api_key（无条件透传）+ base_url（user > registry default，仅非空才传）。

    api_key 无条件透传（含 None）保留迁移前命令式分支语义（db_config.get("api_key") 直写 kwargs）；
    base_url 仅非空才写入，对称媒体简单族 —— ark/ark-agent-plan 有 registry default 回落，grok 无 default
    且用户未配时省略该参数。
    """
    kwargs: dict[str, Any] = {"model": model_id, "api_key": config.credentials.get("api_key")}
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend("text")(registry_backend, **kwargs)


def _text_simple_spec(provider_id: str) -> ProviderSpec:
    """登记一条简单文本 spec：registry_backend 即 provider_id 自身（文本侧无别名映射）。"""
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=provider_id,
        build_backend=partial(_build_text_simple, registry_backend=provider_id),
    )


_TEXT_GEMINI_REGISTRY_BACKEND = "gemini"


def _build_text_gemini_aistudio(config: LoadedConfig, model_id: str | None) -> Any:
    # aistudio 允许用户填自定义 endpoint，无 registry default：base_url 无条件透传用户值（含 None/空串），
    # 由 GeminiTextBackend 内部处理。文本 gemini 不接受 rate_limiter（保留迁移前非对称）。
    return _media_create_backend("text")(
        _TEXT_GEMINI_REGISTRY_BACKEND,
        model=model_id,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
    )


def _build_text_gemini_vertex(config: LoadedConfig, model_id: str | None) -> Any:
    # vertex 走服务账号凭证文件 + gcs_bucket，不传 api_key/base_url（保留迁移前命令式分支）。
    return _media_create_backend("text")(
        _TEXT_GEMINI_REGISTRY_BACKEND,
        model=model_id,
        backend="vertex",
        gcs_bucket=config.credentials.get("gcs_bucket"),
    )


def _text_gemini_spec(provider_id: str, *, build: Callable[[LoadedConfig, str | None], Any]) -> ProviderSpec:
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=_TEXT_GEMINI_REGISTRY_BACKEND,
        build_backend=build,
    )


_TEXT_OPENAI_COMPAT_REGISTRY_BACKEND = "openai"


def _dashscope_text_base_url(configured: str | None) -> str:
    # 运行时 import：免轻量场景（CLI / 迁移）因 import 本表被动拉起 dashscope_shared。
    from lib.dashscope_shared import dashscope_text_base_url

    return dashscope_text_base_url(configured)


def _minimax_text_base_url(configured: str | None) -> str:
    from lib.minimax_shared import minimax_text_base_url

    return minimax_text_base_url(configured)


def _build_text_openai_compat(
    config: LoadedConfig,
    model_id: str | None,
    *,
    derive_base_url: Callable[[str | None], str] | None,
    provider_name: str | None,
) -> Any:
    """OpenAI-compat 文本构造：openai / dashscope / minimax 都用 OpenAITextBackend。

    derive_base_url=None（openai）：直传用户 base_url（无条件，含 None，由 backend 决定 endpoint）。
    derive_base_url 非空（dashscope/minimax）：由 helper 从用户 host 派生 OpenAI 兼容 base，并透传
    provider_name 给 backend，确保 usage 记账与计费查表命中自身 CNY 费率而非 OpenAI USD。
    """
    user_base_url = config.credentials.get("base_url")
    kwargs: dict[str, Any] = {
        "model": model_id,
        "api_key": config.credentials.get("api_key"),
        "base_url": derive_base_url(user_base_url) if derive_base_url else user_base_url,
    }
    if provider_name:
        kwargs["provider_name"] = provider_name
    return _media_create_backend("text")(_TEXT_OPENAI_COMPAT_REGISTRY_BACKEND, **kwargs)


def _text_openai_compat_spec(
    provider_id: str,
    *,
    derive_base_url: Callable[[str | None], str] | None,
    provider_name: str | None,
) -> ProviderSpec:
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=_TEXT_OPENAI_COMPAT_REGISTRY_BACKEND,
        build_backend=partial(_build_text_openai_compat, derive_base_url=derive_base_url, provider_name=provider_name),
    )


# ── PROVIDER_SPEC_REGISTRY 注册表 ──────────────────────────────────
# 键 = (provider_id, media_type)。简单族 = 媒体侧只需 api_key + model + base_url 的内置 provider，
# 共享 _build_simple 闭包。「简单族」按构造形态界定（不是 provider 名白名单），含 ark/ark-agent-plan/
# grok/openai/vidu 与 dashscope/minimax（后两者媒体侧走原生简单构造；其文本侧 OpenAI-compat 形态见下方
# 文本族）。ark-agent-plan 媒体侧复用 Ark image/video backend（registry 同名注册），与 ark 同为简单形态。
# 特例族 = gemini（backend_type 双模式，image/video 对等透传 base_url）与 kling（JWT 双 secret +
# api_model_name 解耦），各挂专属 build 闭包；gemini 的两个 provider_id 各按 backend_type 登记一行（裸
# "gemini" 是死路径——resolver 只产出带后缀 id，按 fail-loud 不登记兜底行）。文本族（media_type=text）按
# 构造形态分三类登记（简单文本 / gemini 文本 / OpenAI-compat 文本，见各闭包），其中 dashscope/minimax 文本
# 映射到 "openai" registry backend，别名映射并入 spec.registry_backend 字段。每对显式登记一行，fail-loud
# （未登记的 provider × media 抛 ValueError，不「缺席即默认」造静默错误 backend）。只登记今天确有注册 backend
# 的对：image/video 简单族七家齐全，audio 仅 dashscope，text 八家（六 provider，gemini 两 id）。

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

# ── 文本族注册 ────────────────────────────────────────────────────
# 简单文本三家（registry_backend = provider_id 自身）；gemini 两个 provider_id 按 backend 分两行
# （aistudio/vertex 各自闭包，registry_backend 同为 "gemini"）；OpenAI-compat 三家都映射到 "openai"
# registry backend，openai 直传用户 base_url，dashscope/minimax 经 helper 派生 + 透传 provider_name 计费归因。
_TEXT_SIMPLE_PROVIDERS = ("ark", "ark-agent-plan", "grok")
PROVIDER_SPEC_REGISTRY.update({(p, "text"): _text_simple_spec(p) for p in _TEXT_SIMPLE_PROVIDERS})
PROVIDER_SPEC_REGISTRY.update(
    {
        ("gemini-aistudio", "text"): _text_gemini_spec("gemini-aistudio", build=_build_text_gemini_aistudio),
        ("gemini-vertex", "text"): _text_gemini_spec("gemini-vertex", build=_build_text_gemini_vertex),
        ("openai", "text"): _text_openai_compat_spec("openai", derive_base_url=None, provider_name=None),
        ("dashscope", "text"): _text_openai_compat_spec(
            "dashscope", derive_base_url=_dashscope_text_base_url, provider_name="dashscope"
        ),
        ("minimax", "text"): _text_openai_compat_spec(
            "minimax", derive_base_url=_minimax_text_base_url, provider_name="minimax"
        ),
    }
)


_VALID_MEDIA_TYPES = frozenset({"image", "video", "audio", "text"})


def _validate_provider_specs() -> None:
    """import 期校验内置表自身不变式，misconfig fail-fast（镜像 endpoints._validate_video_caps_declarations）。

    只做不需 import 后端的内表自洽检查：build 可调用、字典键与 spec 字段一致、media_type 合法。
    「registry 名都在对应后端 registry 里」需 import 全部 lib.{image,video,audio,text}_backends 才能断言，
    为免轻量场景（CLI / 迁移）因 import 本缝而被动拉起全部后端，归入单测，不进 import 期。
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
