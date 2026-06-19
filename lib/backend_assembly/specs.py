"""内置 provider 的 (provider_id, media_type) → ProviderSpec 声明式表。

镜像自定义侧 ENDPOINT_REGISTRY（lib/custom_provider/endpoints.py）：每条 spec 是 frozen
dataclass，挂一个 build 闭包；闭包读 LoadedConfig 信封 + model_id 拼 backend，不查 DB、不 await。
本切片登记简单族（媒体侧只需 api_key + model + base_url 的内置 provider）的 image/video/audio，
共享一个 _build_simple 闭包。表在 import 期校验不变式（registry 名已注册除外，见模块末尾说明），
misconfig fail-fast。
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


def _build_simple(config: LoadedConfig, model_id: str | None, *, media_type: str, registry_backend: str) -> Any:
    """简单族通用构造：api_key + model + base_url。

    api_key 与 base_url 同遵「仅非空才写入 kwargs」：显式传 None 可能覆盖底层 SDK 的环境变量兜底
    （如 OpenAI SDK 读 OPENAI_API_KEY），缺省由 backend 各自处理（要么读环境变量、要么 fail-loud）。
    base_url 优先级：用户在 db_config 显式填写 > ProviderMeta.default_base_url > 不传 —— grok 等无
    default 且用户未配的 provider 不接受 base_url 参数，传 None 会触发 TypeError。
    """
    kwargs: dict[str, Any] = {"model": model_id}
    api_key = config.credentials.get("api_key")
    if api_key:
        kwargs["api_key"] = api_key
    default_base_url = config.provider_meta.default_base_url if config.provider_meta else None
    base_url = config.credentials.get("base_url") or default_base_url
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


# ── PROVIDER_SPEC_REGISTRY 注册表 ──────────────────────────────────
# 键 = (provider_id, media_type)。简单族 = 媒体侧只需 api_key + model + base_url 的内置 provider，
# 共享 _build_simple 闭包。「简单族」按构造形态界定（不是 provider 名白名单），含 ark/ark-agent-plan/
# grok/openai/vidu 与 dashscope/minimax（后两者媒体侧走原生简单构造；其文本侧 OpenAI-compat 特例由
# 文本工厂另行处理）。ark-agent-plan 媒体侧复用 Ark image/video backend（registry 同名注册），与 ark
# 同为简单形态。每对显式登记一行，fail-loud（未登记的 provider × media 抛 ValueError，不「缺席即默认」
# 造静默错误 backend）。只登记今天确有注册 backend 的对：image/video 简单族七家齐全，audio 仅 dashscope。

_SIMPLE_IMAGE_VIDEO_PROVIDERS = ("ark", "ark-agent-plan", "grok", "openai", "vidu", "dashscope", "minimax")
_SIMPLE_MEDIA_PAIRS: list[tuple[str, str]] = [
    *((p, "image") for p in _SIMPLE_IMAGE_VIDEO_PROVIDERS),
    *((p, "video") for p in _SIMPLE_IMAGE_VIDEO_PROVIDERS),
    ("dashscope", "audio"),
]

PROVIDER_SPEC_REGISTRY: dict[tuple[str, str], ProviderSpec] = {
    (provider_id, media_type): _simple_spec(provider_id, media_type) for provider_id, media_type in _SIMPLE_MEDIA_PAIRS
}


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
