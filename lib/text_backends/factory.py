"""文本 backend 工厂。"""

from __future__ import annotations

from lib.config.resolver import ConfigResolver
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.db import async_session_factory
from lib.providers import PROVIDER_OPENAI
from lib.text_backends.base import TextBackend, TextTaskType
from lib.text_backends.registry import create_backend

PROVIDER_ID_TO_BACKEND: dict[str, str] = {
    "gemini-aistudio": "gemini",
    "gemini-vertex": "gemini",
    "ark": "ark",
    "grok": "grok",
    "openai": "openai",
}


async def create_text_backend_for_task(
    task_type: TextTaskType,
    project_name: str | None = None,
) -> TextBackend:
    """从 DB 配置创建文本 backend。"""
    resolver = ConfigResolver(async_session_factory)

    async with resolver.session() as r:
        provider_id, model_id = await r.text_backend_for_task(task_type, project_name)

        # Custom providers use a separate factory path
        if is_custom_provider(provider_id):
            from sqlalchemy import select

            from lib.custom_provider.endpoints import endpoint_to_media_type
            from lib.custom_provider.factory import create_custom_backend
            from lib.db.models.custom_provider import CustomProviderModel
            from lib.db.repositories.custom_provider_repo import CustomProviderRepository

            async with r._open_session() as (session, _):
                repo = CustomProviderRepository(session)
                db_id = parse_provider_id(provider_id)
                provider = await repo.get_provider(db_id)
                if provider is None:
                    raise ValueError("配置的自定义供应商已被删除，请到项目设置中重新选择文本模型")
                name = provider.display_name
                model = None
                # 校验 model_id 仍存在、已启用、endpoint 推算 media_type=text
                if model_id:
                    stmt = select(CustomProviderModel).where(
                        CustomProviderModel.provider_id == db_id,
                        CustomProviderModel.model_id == model_id,
                        CustomProviderModel.is_enabled == True,  # noqa: E712
                    )
                    result = await session.execute(stmt)
                    candidate = result.scalar_one_or_none()
                    if candidate and endpoint_to_media_type(candidate.endpoint) == "text":
                        model = candidate
                    else:
                        model_id = None
                if model is None:
                    default_model = await repo.get_default_model(db_id, "text")
                    if default_model is None:
                        raise ValueError(f"供应商「{name}」没有可用的文本模型，请到项目设置中重新选择")
                    model = default_model
                    model_id = default_model.model_id
                return create_custom_backend(provider=provider, model_id=model_id, endpoint=model.endpoint)

        provider_config = await r.provider_config(provider_id)

    backend_name = PROVIDER_ID_TO_BACKEND.get(provider_id, provider_id)
    kwargs: dict = {"model": model_id}

    if provider_id == "gemini-vertex":
        kwargs["backend"] = "vertex"
        kwargs["gcs_bucket"] = provider_config.get("gcs_bucket")
    else:
        kwargs["api_key"] = provider_config.get("api_key")
        if provider_id in ("gemini-aistudio", PROVIDER_OPENAI):
            kwargs["base_url"] = provider_config.get("base_url")

    return create_backend(backend_name, **kwargs)
