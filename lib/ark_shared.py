"""
Ark (火山方舟) 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- ARK_BASE_URL — 火山方舟 API 基础 URL
- create_ark_client — Ark 客户端工厂
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def create_ark_client(*, api_key: str | None = None):
    """创建 Ark 客户端，统一校验 api_key 并构造。"""
    from volcenginesdkarkruntime import Ark

    resolved_key = api_key or os.environ.get("ARK_API_KEY")
    if not resolved_key:
        raise ValueError("Ark API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。")
    return Ark(base_url=ARK_BASE_URL, api_key=resolved_key)
