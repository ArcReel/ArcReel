"""文本 backend 工厂。

provider/model 解析仍在此（resolver.text_backend_for_task），backend 构造收口到统一缝
assemble_backend（media_type=text）：内置文本 provider 全部经 ProviderSpec 表，
自定义 provider 经下移到 lib 的 load_custom_backend，文本侧不再各写一份命令式构造与自定义解析。
"""

from __future__ import annotations

from lib.backend_assembly import assemble_backend
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.text_backends.base import TextBackend, TextTaskType


async def create_text_backend_for_task(
    task_type: TextTaskType,
    project_name: str | None = None,
) -> TextBackend:
    """从 DB 配置创建文本 backend。"""
    resolver = ConfigResolver(async_session_factory)

    async with resolver.session() as r:
        provider_id, model_id = await r.text_backend_for_task(task_type, project_name)
        backend = await assemble_backend(
            provider_id=provider_id,
            media_type="text",
            model_id=model_id,
            resolver=r,
        )
    return backend
