"""自定义供应商调用哪一个模型：请求的模型能否直接使用、默认模型的按桶解析 —— 默认回退与解析期能力闸共用一份过滤。

默认模型是按任务类型桶分槽的：保存期的唯一性检查（``server/routers/custom_providers.py``
``_check_unique_defaults``）按 image 能力集判互斥，t2i 与 i2i 各设一个默认是放行的，故
「provider + media_type」只定位到一组候选行，挑哪一行须再带上桶。桶归属一律经
``lib.backends.generation_type_buckets.custom_model_buckets`` 判，不在此另写一份能力解读。

桶未知（视频侧不定桶解析、text / audio 无桶）时不过滤：这些 media_type 的默认在保存期就是
同一 media_type 至多一个，候选恒不超过一行。

生命周期与内置供应商共用一个读模型（``lib.catalog.lifecycle``，docs/adr/0103）：供应商模型按条目绑定或名字对应到
条目（``lib.custom_provider.model_facts.model_entry``），条目已下线的模型不作默认、不进候选；显式引用了它就报下线错误，
不回退到默认模型（:func:`requested_model`）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.backends.generation_type_buckets import (
    BUCKETS_BY_MEDIA_TYPE,
    GenerationTypeBucket,
    custom_model_buckets,
)
from lib.catalog import query as catalog
from lib.catalog.lifecycle import Lifecycle, entry_lifecycle
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.model_facts import model_entry
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from lib.db.models.custom_provider import CustomProviderModel


def model_lifecycle(model: CustomProviderModel, media_type: str) -> Lifecycle:
    """供应商模型所对应条目在今天的生命周期；``media_type`` 取调用端点的媒体类型。对应不上条目时为可用。"""
    return entry_lifecycle(model_entry(model.model_id, media_type, model.entry_binding))


def callable_models(models: list[CustomProviderModel], media_type: str) -> list[CustomProviderModel]:
    """滤掉条目已下线的模型行，保持入参顺序：默认模型与候选只来自其余的行。"""
    return [model for model in models if not model_lifecycle(model, media_type).discontinued]


async def requested_model(
    session: AsyncSession,
    *,
    provider_id: str,
    db_id: int,
    model_id: str,
    media_type: str,
    submitted_job: bool = False,
) -> CustomProviderModel | None:
    """请求的模型行可以直接用于 ``media_type`` 时返回该行；不存在、端点解析不出或媒体类型不符、已停用时返回 ``None``，
    由调用方回退该媒体类型（与桶）的默认模型。

    对应的条目已下线时抛 ``ModelDiscontinuedError``（带下线日期与后继），停用的也一样：显式引用了已下线的模型，
    回退等于替用户换模型，报错让用户自己重新选择。``submitted_job`` 为真表示续跑已提交给厂商的任务（只轮询、
    不发起新的生成），不做这项检查，其余判定不变。

    Raises:
        ModelDiscontinuedError: 模型所对应的条目今天已下线（``submitted_job`` 为假时）。
    """
    model = await CustomProviderRepository(session).get_model_by_ids(db_id, model_id)
    if model is None:
        return None
    try:
        endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        return None
    if endpoint_spec.media_type != media_type:
        return None
    if not submitted_job:
        catalog.reject_discontinued_entry(
            provider_id, model.model_id, media_type, model_entry(model.model_id, media_type, model.entry_binding)
        )
    return model if model.is_enabled else None


async def model_buckets(session: AsyncSession, model: CustomProviderModel) -> frozenset[GenerationTypeBucket]:
    """模型行具备的任务类型桶；端点已下线 / 解析不出时空集（候选宁缺勿滥）。"""
    try:
        endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        endpoint_spec = None
    return custom_model_buckets(
        endpoint=model.endpoint,
        model_id=model.model_id,
        capability_overrides=model.capability_overrides,
        endpoint_spec=endpoint_spec,
        entry_binding=model.entry_binding,
    )


#: 桶 → 所属媒体类型，由 ``BUCKETS_BY_MEDIA_TYPE`` 反转得到，不另列一份。
_MEDIA_TYPE_BY_BUCKET: dict[str, str] = {
    bucket: media_type for media_type, buckets in BUCKETS_BY_MEDIA_TYPE.items() for bucket in buckets
}


async def lacks_bucket(session: AsyncSession, model: CustomProviderModel, generation_type: str) -> bool:
    """模型行确实产该媒体、但不具备该桶 —— 解析期能力闸的判据。

    端点解析不出、或端点已改成别的媒体类型时返回 False：那是引用失效，由默认回退接手，不是能力缺失。
    """
    buckets = await model_buckets(session, model)
    same_media = BUCKETS_BY_MEDIA_TYPE.get(_MEDIA_TYPE_BY_BUCKET.get(generation_type, ""), ())
    return bool(buckets & frozenset(same_media)) and generation_type not in buckets


async def filter_by_bucket(
    session: AsyncSession,
    models: list[CustomProviderModel],
    generation_type: str | None,
) -> list[CustomProviderModel]:
    """保留具备该桶的模型行，保持入参顺序；``generation_type`` 为 None 时原样返回。"""
    if generation_type is None:
        return models
    return [model for model in models if generation_type in await model_buckets(session, model)]


async def resolve_default_model(
    session: AsyncSession,
    *,
    provider_id: str,
    db_id: int,
    media_type: str,
    generation_type: str | None = None,
) -> CustomProviderModel:
    """取该供应商在该 media_type × 桶上唯一的默认已启用模型；条目已下线的默认模型跳过，按没有设默认处理。

    零行与多行都 fail loud：挑不出唯一那一行时静默取其中之一等于静默换模型，而多行只在
    「该桶配了两个默认」这种保存期本该拦住的状态下出现。

    Raises:
        ValueError: 没有默认模型，或该桶有多个默认模型。
    """
    candidates = await CustomProviderRepository(session).list_default_models(db_id, media_type)
    candidates = await filter_by_bucket(session, callable_models(candidates, media_type), generation_type)
    label = f"{generation_type} {media_type}" if generation_type is not None else media_type
    if not candidates:
        raise ValueError(f"自定义供应商 {provider_id} 没有默认 {label} 模型")
    if len(candidates) > 1:
        conflict = ", ".join(model.model_id for model in candidates)
        raise ValueError(f"自定义供应商 {provider_id} 有多个默认 {label} 模型：{conflict}")
    return candidates[0]
