"""
Ark (火山方舟) 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- ARK_BASE_URL — 火山方舟 API 基础 URL
- resolve_ark_api_key — API Key 解析（含环境变量 fallback）
- create_ark_client — Ark 客户端工厂
"""

from __future__ import annotations

import os

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def resolve_ark_api_key(api_key: str | None = None) -> str:
    """解析 Ark API Key，支持环境变量 fallback。"""
    resolved = api_key or os.environ.get("ARK_API_KEY")
    if not resolved:
        raise ValueError("Ark API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。")
    return resolved


def create_ark_client(*, api_key: str | None = None):
    """创建 Ark 客户端，统一校验 api_key 并构造。"""
    from volcenginesdkarkruntime import Ark

    return Ark(base_url=ARK_BASE_URL, api_key=resolve_ark_api_key(api_key))
