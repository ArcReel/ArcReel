"""
text_client.py — 文本生成 GeminiClient 工厂

集中管理文本生成的供应商优先级逻辑：gemini-vertex > gemini-aistudio。
从数据库加载供应商配置，自管理 DB session。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from lib.config.service import ConfigService
from lib.db import async_session_factory
from lib.gemini_client import GeminiClient
from lib.system_config import resolve_vertex_credentials_path

logger = logging.getLogger(__name__)


async def create_text_client() -> GeminiClient:
    """异步创建文本生成用的 GeminiClient。

    供应商优先级：gemini-vertex > gemini-aistudio。
    从数据库加载供应商配置，自管理 DB session。
    """
    async with async_session_factory() as session:
        svc = ConfigService(session)
        configs = await svc.get_all_provider_configs()

    # 1. 优先尝试 Vertex AI
    vertex_config = configs.get("gemini-vertex", {})
    if vertex_config.get("credentials_path"):
        creds_path = resolve_vertex_credentials_path(Path(__file__).parent.parent)
        if creds_path is not None:
            logger.info("文本生成使用 Vertex AI 后端")
            return GeminiClient(
                backend="vertex",
                gcs_bucket=vertex_config.get("gcs_bucket"),
            )
        else:
            logger.warning("Vertex AI credentials_path 已配置但凭证文件不存在，回退到 AI Studio")

    # 2. 回退到 AI Studio
    aistudio_config = configs.get("gemini-aistudio", {})
    api_key = aistudio_config.get("api_key")
    if api_key:
        logger.info("文本生成使用 AI Studio 后端")
        return GeminiClient(
            api_key=api_key,
            base_url=aistudio_config.get("base_url"),
        )

    # 3. 都没有
    raise ValueError(
        "未配置任何 Gemini 文本生成供应商。"
        "请在「全局设置 → 供应商」页面配置 Vertex AI 或 AI Studio。"
    )


def create_text_client_sync() -> GeminiClient:
    """同步版本，供 CLI 脚本使用。内部 asyncio.run()。

    注意：仅限在无事件循环的同步入口（如 __main__）中调用。
    若在已有 asyncio 事件循环内调用会抛 RuntimeError。
    """
    return asyncio.run(create_text_client())
