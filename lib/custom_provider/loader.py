"""自定义供应商 backend 的 DB 装载。

查 provider、校验请求的 model（存在 / 启用 / endpoint 推算 media_type 相符），失效则回退该
media_type × 任务类型桶上唯一的默认启用 model，最后委托现成 create_custom_backend
（ENDPOINT_REGISTRY 不改）。
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
from lib.custom_provider.default_models import resolve_default_model
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
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
    generation_type: str | None = None,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend:
    """装载并构造自定义供应商 backend。

    media_type 用于校验请求 model 的 endpoint 是否相符、以及回退默认时分组；实际派发以 model.endpoint
    为准。请求 model 不存在 / 已禁用 / 媒体类型不符 → 视为失效并回退该 media_type 的默认启用 model。

    ``generation_type`` 是调用点所属的任务类型桶，只作用于回退：默认模型按桶分槽（image 的 t2i
    与 i2i 各有一个默认），带桶才挑得出唯一那一行，见 ``lib.custom_provider.default_models``。
    调用点不承诺桶时传 None、不过滤。

    Raises:
        ValueError: provider 不存在，或该 media_type × 桶上的默认启用 model 不唯一（零个或多个）。
    """
    repo = CustomProviderRepository(session)
    from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

    endpoint_repo = CustomEndpointRepository(session)
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
        if candidate:
            try:
                candidate_spec = await resolve_endpoint_spec(candidate.endpoint, endpoint_repo.get)
            except ValueError:
                candidate_spec = None
            if candidate_spec is not None and candidate_spec.media_type == media_type:
                model = candidate
        if model is None:
            logger.warning(
                "自定义模型 %s/%s 已不存在 / 已禁用 / 媒体类型不符（期望 %s），回退到默认模型",
                provider_id,
                model_id,
                media_type,
            )
            model_id = None

    if model is None:
        default_model = await resolve_default_model(
            session,
            provider_id=provider_id,
            db_id=db_id,
            media_type=media_type,
            generation_type=generation_type,
        )
        model = default_model
        model_id = default_model.model_id

    if model_id is None:
        raise ValueError(f"自定义供应商 {provider_id} 解析后仍缺少 model_id")
    spec = await resolve_endpoint_spec(model.endpoint, endpoint_repo.get)
    return create_custom_backend(
        provider=provider,
        model_id=model_id,
        endpoint=model.endpoint,
        capability_overrides=model.capability_overrides,
        endpoint_spec=spec,
    )
