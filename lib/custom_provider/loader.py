"""自定义供应商 backend 的 DB 装载。

查 provider、校验请求的 model（存在 / 启用 / endpoint 推算 media_type 相符），失效则回退该
media_type × 任务类型桶上唯一的默认启用 model，最后委托现成 create_custom_backend
（ENDPOINT_REGISTRY 不改）。所对应条目已下线的 model 不回退、直接报下线错误，默认 model 也跳过已下线的
（``lib.custom_provider.default_models``）。
装载落在 lib 让媒体路径与文本工厂共用一份自定义解析，且 lib 不反向依赖 server。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lib.custom_provider import parse_provider_id
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.default_models import requested_model, resolve_default_model
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.factory import create_custom_backend
from lib.custom_provider.model_facts import text_model_facts
from lib.db.repositories.custom_provider_repo import CustomProviderRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

logger = logging.getLogger(__name__)


async def custom_model_max_output_tokens(*, session: AsyncSession, provider_id: str, model_id: str) -> int | None:
    """自定义供应商模型的最大输出长度：能力覆盖 > 对应条目；都没有时 None。"""
    row = await CustomProviderRepository(session).get_model_by_ids(parse_provider_id(provider_id), model_id)
    if row is None:
        return None
    return text_model_facts(model_id, row.capability_overrides, entry_binding=row.entry_binding).max_output_tokens


async def select_custom_model(
    *,
    session: AsyncSession,
    provider_id: str,
    model_id: str | None,
    media_type: str,
    generation_type: str | None = None,
) -> tuple[CustomProvider, CustomProviderModel]:
    """这次调用实际使用的自定义供应商与模型行，不构造 backend。

    请求 model 不存在 / 已禁用 / 媒体类型不符 → 视为失效并回退该 media_type 的默认启用 model；
    请求 model 所对应的条目已下线 → 报下线错误，不回退（停用的也一样）。生命周期按当天日期判定：复用 backend
    实例的调用方在取缓存前也经它检查（``lib.backends.backend_assembly.callable_model_key``）。

    ``generation_type`` 是调用点所属的任务类型桶，只作用于回退：默认模型按桶分槽（image 的 t2i
    与 i2i 各有一个默认），带桶才挑得出唯一那一行，见 ``lib.custom_provider.default_models``。
    调用点不承诺桶时传 None、不过滤。

    Raises:
        ModelDiscontinuedError: 请求 model 所对应的条目已下线。
        ValueError: provider 不存在，或该 media_type × 桶上未下线的默认启用 model 不唯一（零个或多个）。
    """
    db_id = parse_provider_id(provider_id)
    provider = await CustomProviderRepository(session).get_provider(db_id)
    if provider is None:
        raise ValueError(f"自定义供应商 {provider_id} 不存在")

    if model_id:
        model = await requested_model(
            session, provider_id=provider_id, db_id=db_id, model_id=model_id, media_type=media_type
        )
        if model is not None:
            return provider, model
        logger.warning(
            "自定义模型 %s/%s 已不存在 / 已禁用 / 媒体类型不符（期望 %s），回退到默认模型",
            provider_id,
            model_id,
            media_type,
        )
    default_model = await resolve_default_model(
        session,
        provider_id=provider_id,
        db_id=db_id,
        media_type=media_type,
        generation_type=generation_type,
    )
    return provider, default_model


async def load_custom_backend(
    *,
    session: AsyncSession,
    provider_id: str,
    model_id: str | None,
    media_type: str,
    generation_type: str | None = None,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend:
    """装载并构造自定义供应商 backend：按 :func:`select_custom_model` 定下供应商与模型行，再委托工厂构造。

    media_type 用于校验请求 model 的 endpoint 是否相符、以及回退默认时分组；实际派发以 model.endpoint
    为准。

    Raises:
        ModelDiscontinuedError: 请求 model 所对应的条目已下线。
        ValueError: provider 不存在，或该 media_type × 桶上未下线的默认启用 model 不唯一（零个或多个）。
    """
    from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

    provider, model = await select_custom_model(
        session=session,
        provider_id=provider_id,
        model_id=model_id,
        media_type=media_type,
        generation_type=generation_type,
    )
    spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    return create_custom_backend(
        provider=provider,
        model_id=model.model_id,
        endpoint=model.endpoint,
        capability_overrides=model.capability_overrides,
        path_overrides=model.path_overrides,
        endpoint_spec=spec,
        entry_binding=model.entry_binding,
    )
