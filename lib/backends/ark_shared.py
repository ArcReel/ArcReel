"""
Ark (火山方舟) 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- ARK_BASE_URL — 火山方舟 API 基础 URL
- resolve_ark_api_key — API Key 解析（缺失即 raise，不再走 env fallback）
- ark_base_url — 归一化用户输入（strip + 去尾斜杠），缺省回落 ARK_BASE_URL
- create_ark_client — Ark 客户端工厂
"""

from __future__ import annotations

import httpx

from lib.backends.transport_layer import TransportLayer, layer_sync_client

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def resolve_ark_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Ark API Key")
    return api_key.strip()


def ark_base_url(configured: str | None = None) -> str:
    """归一化用户填入的 base_url：strip + 去尾斜杠，缺省回落 ARK_BASE_URL。

    不像 dashscope/minimax/agnes 那样按已知后缀做 host 派生再重建——ark 的 backend 会传入
    非标准变体路径（如 ark-agent-plan 用的 /api/plan/v3），按后缀重建会把这类路径拼坏，
    所以只做保守的空白/尾斜杠归一化，不改写路径结构。
    """
    # 先 strip 再判空：纯空白串（"   "）是真值会绕过 or，回落必须在 strip 之后。
    base = (configured or "").strip()
    return base.rstrip("/") if base else ARK_BASE_URL


def create_ark_client(
    *, api_key: str | None = None, base_url: str | None = None, transport_layer: TransportLayer | None = None
):
    """创建 Ark 客户端；base_url 缺省走 ARK_BASE_URL（即 /api/v3），经 ark_base_url 归一化。

    SDK 是同步客户端，``transport_layer`` 叠在同步 httpx 客户端之上。
    """
    from volcenginesdkarkruntime import Ark

    base = ark_base_url(base_url)
    key = resolve_ark_api_key(api_key)
    if transport_layer is None:
        return Ark(base_url=base, api_key=key)
    return Ark(
        base_url=base, api_key=key, http_client=layer_sync_client(httpx.Client(follow_redirects=True), transport_layer)
    )
