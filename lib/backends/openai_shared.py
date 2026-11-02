"""
OpenAI 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- OPENAI_RETRYABLE_ERRORS — 可重试错误类型
- create_openai_client — AsyncOpenAI 客户端工厂
- OPENAI_IMAGE_QUALITY_MAP — image_size 档位 → quality 映射，供 image_backends.openai 消费。
  尺寸不再用静态 (image_size, aspect_ratio) → "WxH" 表，改由 arcreel_market_core.aspect_size 按比例精确计算
  （比例优先、清晰度其次），见 docs/adr/0011。
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from lib.backends.proxy_bypass import openai_async_proxy_http_client
from lib.backends.transport_layer import TransportLayer, layer_async_client
from lib.config.url_utils import OFFICIAL_OPENAI_BASE_URL

logger = logging.getLogger(__name__)

OPENAI_RETRYABLE_ERRORS: tuple[type[Exception], ...] = ()

OPENAI_IMAGE_QUALITY_MAP: dict[str, str] = {
    "512px": "low",
    "1K": "medium",
    "2K": "high",
    "4K": "high",
}

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    OPENAI_RETRYABLE_ERRORS = (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
except ImportError:
    pass  # openai 是必装依赖，此分支仅作防御性保护；回退到空 tuple


def create_openai_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    max_retries: int | None = None,
    transport_layer: TransportLayer | None = None,
) -> AsyncOpenAI:
    """创建 AsyncOpenAI 客户端，统一处理 api_key 和 base_url。

    base_url 为空（None/空白）时显式回填官方端点：AsyncOpenAI 对空 base_url
    会回落读取 OPENAI_BASE_URL 环境变量，环境残留将静默覆盖 DB 配置。base_url
    的唯一来源是 DB，此处兜死显式值断掉该回落路径。

    base_url 判为内网时内层 http_client 挂绕系统代理的 mounts：AsyncOpenAI 自建的 httpx
    client 默认读系统代理（如 Clash 127.0.0.1:7890），发往内网兼容端点（如
    192.168.5.149:8001）会被劫持到代理上回 502。绕代理判定与连接须用同一个回填后的
    effective_base_url；公网 base_url（含回填的官方端点）用 SDK 默认客户端。
    ``transport_layer`` 叠在内层客户端之上。
    """
    effective_base_url = (base_url or "").strip() or OFFICIAL_OPENAI_BASE_URL
    kwargs: dict = {"base_url": effective_base_url}
    if api_key:
        kwargs["api_key"] = api_key
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    # http_client 只在需要时下发：内网 base_url 挂绕系统代理的 mounts，或 transport_layer 叠在客户端之上；
    # 公网 base_url（含回填的官方端点）且无 transport_layer 时不传 http_client，维持 SDK 默认走系统代理。
    proxy_client = openai_async_proxy_http_client(effective_base_url)
    if proxy_client is not None or transport_layer is not None:
        inner_client = proxy_client or DefaultAsyncHttpxClient()
        kwargs["http_client"] = layer_async_client(inner_client, transport_layer)
    return AsyncOpenAI(**kwargs)
