"""文本 backend 工厂。"""
from __future__ import annotations

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.text_backends.base import TextBackend, TextTaskType
from lib.text_backends.registry import create_backend

PROVIDER_ID_TO_BACKEND: dict[str, str] = {
    "gemini-aistudio": "gemini",
    "gemini-vertex": "gemini",
    "ark": "ark",
    "grok": "grok",
}


async def create_text_backend_for_task(
    task_type: TextTaskType,
    project_name: str | None = None,
) -> TextBackend:
    """从 DB 配置创建文本 backend。"""
    resolver = ConfigResolver(async_session_factory)
    provider_id, model_id = await resolver.text_backend_for_task(task_type, project_name)
    provider_config = await resolver.provider_config(provider_id)

    backend_name = PROVIDER_ID_TO_BACKEND.get(provider_id, provider_id)
    kwargs: dict = {"model": model_id}

    if provider_id == "gemini-vertex":
        kwargs["backend"] = "vertex"
        kwargs["gcs_bucket"] = provider_config.get("gcs_bucket")
    else:
        kwargs["api_key"] = provider_config.get("api_key")
        if provider_id == "gemini-aistudio":
            kwargs["base_url"] = provider_config.get("base_url")

    return create_backend(backend_name, **kwargs)
