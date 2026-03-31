"""
Grok (xAI) 共享工具模块

供 text_backends / image_backends / video_backends 复用。

包含：
- create_grok_client — xAI AsyncClient 客户端工厂
"""

from __future__ import annotations

import logging

import xai_sdk

logger = logging.getLogger(__name__)


def create_grok_client(*, api_key: str) -> xai_sdk.AsyncClient:
    """创建 xAI AsyncClient，统一校验和构造。"""
    if not api_key:
        raise ValueError("XAI_API_KEY 未设置\n请在系统配置页中配置 xAI API Key")
    return xai_sdk.AsyncClient(api_key=api_key)
