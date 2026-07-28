"""LoadedConfig — 内置 backend 构造缝的 async 装载段产物、sync 构造段唯一输入。

承载三样东西：① 凭证 overlay（db_config 解析出的 api_key / access_key / secret_key /
base_url 等，键名即列名，承 ADR 0037「registry key 名 = 列名 = 构造参数名 = config key」）；
② provider registry meta（default_base_url / api_model_name 来源）；③ 共享 rate_limiter。

构造闭包只读这个信封拼 backend，不查 DB、不 await —— 这条中线是「构造可脱离 DB 直接单测」的
结构保证（手搓一个 LoadedConfig + model_id 即可断言造出的构造参数）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.config.registry import ProviderMeta


@dataclass(frozen=True)
class LoadedConfig:
    """内置侧 build 闭包的唯一输入信封。"""

    credentials: dict[str, str | None]
    provider_meta: ProviderMeta | None
    # 简单族 _build_simple 不消费 rate_limiter（与迁移前 _fill_simple_provider_kwargs 一致：限流由
    # 简单后端各自的 httpx 客户端内建）；信封预留此槽供后续迁入的特例族 build 闭包（如 gemini 媒体）读取。
    rate_limiter: Any | None
