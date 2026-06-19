"""自定义供应商 backend 的 DB 装载。

查 provider、校验请求的 model（存在 / 启用 / endpoint 推算 media_type 相符），失效则回退该
media_type 的默认启用 model，最后委托现成 create_custom_backend（ENDPOINT_REGISTRY 不改）。
装载落在 lib 让媒体路径与文本工厂共用一份自定义解析，且 lib 不反向依赖 server。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from lib.custom_provider import parse_provider_id
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.endpoints import endpoint_to_media_type
from lib.custom_provider.factory import create_custom_backend
from lib.db.models.custom_provider import CustomProviderModel
from lib.db.repositories.custom_provider_repo import CustomProviderRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def load_custom_backend(
    *,
    session: AsyncSession,
    provider_id: str,
    model_id: str | None,
    media_type: str,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend:
    """装载并构造自定义供应商 backend。

    media_type 用于校验请求 model 的 endpoint 是否相符、以及回退默认时分组；实际派发以 model.endpoint
    为准。请求 model 不存在 / 已禁用 / 媒体类型不符 → 视为失效并回退该 media_type 的默认启用 model。

    Raises:
        ValueError: provider 不存在，或该 media_type 无默认启用 model。
    """
    repo = CustomProviderRepository(session)
    db_id = parse_provider_id(provider_id)
    provider = await repo.get_provider(db_id)
    if provider is None:
        raise ValueError(f"自定义供应商 {provider_id} 不存在")

    model = None
    if model_id:
        stmt = select(CustomProviderModel).where(
            CustomProviderModel.provider_id == db_id,
            CustomProviderModel.model_id == model_id,
            CustomProviderModel.is_enabled,
        )
        result = await session.execute(stmt)
        candidate = result.scalar_one_or_none()
        if candidate and endpoint_to_media_type(candidate.endpoint) == media_type:
            model = candidate
        else:
            logger.warning(
                "自定义模型 %s/%s 已不存在 / 已禁用 / 媒体类型不符（期望 %s），回退到默认模型",
                provider_id,
                model_id,
                media_type,
            )
            model_id = None

    if model is None:
        default_model = await repo.get_default_model(db_id, media_type)
        if default_model is None:
            raise ValueError(f"自定义供应商 {provider_id} 没有默认 {media_type} 模型")
        model = default_model
        model_id = default_model.model_id

    if model_id is None:
        raise ValueError(f"自定义供应商 {provider_id} 解析后仍缺少 model_id")
    return create_custom_backend(provider=provider, model_id=model_id, endpoint=model.endpoint)
