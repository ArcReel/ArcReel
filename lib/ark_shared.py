"""
Ark (火山方舟) 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- ARK_BASE_URL — 火山方舟 API 基础 URL
- resolve_ark_api_key — API Key 解析（缺失即 raise，不再走 env fallback）
- create_ark_client — Ark 客户端工厂
"""

from __future__ import annotations

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def resolve_ark_api_key(api_key: str | None = None) -> str:
    if not api_key:
        raise ValueError("请到系统配置页填写 Ark API Key")
    return api_key


def create_ark_client(*, api_key: str | None = None):
    """创建 Ark 客户端，统一校验 api_key 并构造。"""
    from volcenginesdkarkruntime import Ark

    return Ark(base_url=ARK_BASE_URL, api_key=resolve_ark_api_key(api_key))
