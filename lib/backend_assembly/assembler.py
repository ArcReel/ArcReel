"""assemble_backend — 「provider config + model → backend」统一构造入口。

按 is_custom_provider 单分支分流到内置 / 自定义两族（一道门、门后两个对称房间，见 ADR 0039）。
构造拆两段：async 装载（查 DB/config，产出 LoadedConfig 信封）/ sync 纯闭包构造。缝无状态、纯构造，
backend 实例缓存留在调用方编排层、不进缝。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lib.backend_assembly.loaded_config import LoadedConfig
from lib.backend_assembly.specs import get_provider_spec
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider import is_custom_provider

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver


async def _load_builtin_config(resolver: ConfigResolver, provider_id: str, rate_limiter: Any | None) -> LoadedConfig:
    """内置侧 async 装载段：查 DB/config 产出 LoadedConfig 信封。

    凭证 overlay 来自 db_config（resolver.provider_config）；registry meta 提供 default_base_url /
    api_model_name 来源；rate_limiter 由调用方注入（共享实例）。这一段是唯一的 await/DB 触点，
    之后 sync 构造闭包只读信封。
    """
    db_config = await resolver.provider_config(provider_id)
    return LoadedConfig(
        credentials=dict(db_config),
        provider_meta=PROVIDER_REGISTRY.get(provider_id),
        rate_limiter=rate_limiter,
    )


async def assemble_backend(
    *,
    provider_id: str,
    media_type: str,
    model_id: str | None,
    resolver: ConfigResolver,
    rate_limiter: Any | None = None,
) -> Any:
    """统一构造入口。按 provider_id 是否自定义分流；未登记的内置 provider × media fail-loud。"""
    if is_custom_provider(provider_id):
        from lib.custom_provider.loader import load_custom_backend

        # 借 resolver 的 session（bound 时复用其事务上下文）：自定义装载与简单族装载共用同一
        # session_factory，调用方在 resolver.session() 内构造时复用同一连接，避免另开 factory。
        async with resolver._open_session() as (session, _):
            return await load_custom_backend(
                session=session, provider_id=provider_id, model_id=model_id, media_type=media_type
            )
    spec = get_provider_spec(provider_id, media_type)  # 未登记 → ValueError（fail-loud）
    config = await _load_builtin_config(resolver, provider_id, rate_limiter)
    return spec.build_backend(config, model_id)
