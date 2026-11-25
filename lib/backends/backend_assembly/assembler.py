"""内置供应商的异步凭证装载段。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.config.registry import PROVIDER_REGISTRY

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver


async def load_builtin_config(resolver: ConfigResolver, provider_id: str, rate_limiter: Any | None) -> LoadedConfig:
    """内置侧 async 装载段：查 DB/config 产出 LoadedConfig 信封。

    凭证 overlay 来自 db_config（resolver.provider_config）；registry meta 提供 default_base_url /
    api_model_name 来源；rate_limiter 由调用方注入（共享实例）。这一段是唯一的 await/DB 触点，
    之后 sync 构造只读信封。
    """
    db_config, credential_id = await resolver.provider_config_with_credential(provider_id)
    return LoadedConfig(
        credentials=dict(db_config),
        provider_meta=PROVIDER_REGISTRY.get(provider_id),
        rate_limiter=rate_limiter,
        credential_id=credential_id,
    )
