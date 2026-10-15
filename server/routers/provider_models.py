"""供应商模型 API：按供应商列出供应商模型，启用与停用；内置供应商另可按 ID 添加与移除（docs/adr/0099）。

列表与启停对内置与自定义供应商共用。自定义供应商模型的其余字段仍经自定义供应商接口整表保存。
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import MEDIA_TYPES, CatalogEntry, MediaType
from lib.billing.multipliers import get_provider_multiplier
from lib.catalog import builtin_models
from lib.catalog import query as catalog
from lib.catalog.price_summary import summarize_price
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.custom_provider.duration_presets import infer_supported_durations
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.db import get_async_session
from lib.db.models.custom_provider import CustomProviderModel
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.i18n import Translator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["Providers"])

ModelFlag = Literal["discontinued", "unpriced"]


class PriceSummaryItemResponse(BaseModel):
    """一个计费项的单价范围；``per`` 是单价对应的计数量（每百万 token 为 1000000，每张、每秒为 1）。"""

    meter: str
    min_price: float
    max_price: float
    per: int


class PriceSummaryResponse(BaseModel):
    currency: str
    items: list[PriceSummaryItemResponse]


class ProviderModelItem(BaseModel):
    """供应商详情模型列表的一行。

    - ``model_id``：设置与项目里引用这个模型的模型键；``api_model_name`` 是发给供应商的模型名。
    - ``display_name``：对应条目的显示名（自定义供应商取用户填写的名字）；没有时为 ``null``，界面显示模型 ID。
    - ``official``：内置供应商官方收录的模型，只能启用、停用；用户添加的与自定义供应商的模型为 ``false``。
    - ``flags``：``discontinued`` 条目已下架；``unpriced`` 调用费用会记为未定价（对应不上条目或条目没有价格，
      两级倍率之积为 0 时不计费，不标）。
    - ``endpoint``：自定义供应商模型的调用端点键；内置供应商为 ``null``。
    """

    model_id: str
    api_model_name: str
    display_name: str | None
    media_type: MediaType
    enabled: bool
    is_default: bool
    official: bool
    flags: list[ModelFlag]
    price: PriceSummaryResponse | None
    endpoint: str | None = None


class ProviderModelsResponse(BaseModel):
    models: list[ProviderModelItem]


class ProviderModelEnabledUpdate(BaseModel):
    """启用或停用一个供应商模型；自定义供应商的模型停用时同时取消默认。"""

    model_id: str
    enabled: bool


class AddProviderModelRequest(BaseModel):
    """按 ID 添加：``media_type`` 用来区分两栖模型，以及给对应不上条目的模型指定媒体类型；``entry_binding`` 是
    用户为它指定的条目（「模型厂商/官方模型名」）。"""

    model_id: Annotated[str, Field(min_length=1, max_length=128)]
    media_type: MediaType | None = None
    entry_binding: str | None = None


class AddProviderModelResponse(BaseModel):
    """``official`` 为真时对上了官方收录的模型，添加即启用它。"""

    model: ProviderModelItem
    official: bool


async def reload_user_added_models(session: AsyncSession) -> None:
    """从内置供应商模型表装入用户添加的模型，交给目录查询。应用启动时与每次写入提交之后调用。

    读目录的代码（接口、生成 worker、Agent 会话的工具）都跑在应用进程里，进程内替换即对全部读点生效。
    """
    catalog.install_user_added_models(
        catalog.UserAddedModel(
            provider_id=row.provider_id,
            model_id=row.model_id,
            media_type=cast("MediaType", row.media_type),
            multiplier=row.multiplier,
            entry_binding=row.entry_binding,
            fallback_durations=tuple(infer_supported_durations(row.model_id)) if row.media_type == "video" else (),
        )
        for row in await builtin_models.list_user_added_rows(session)
        if row.media_type in MEDIA_TYPES
    )


def _flags(entry: CatalogEntry | None, multiplier: float) -> list[ModelFlag]:
    flags: list[ModelFlag] = []
    if entry is not None and entry.discontinued:
        flags.append("discontinued")
    priced = entry is not None and entry.price is not None and entry.price.is_priceable
    if not priced and multiplier != 0:
        flags.append("unpriced")
    return flags


def _price(entry: CatalogEntry | None) -> PriceSummaryResponse | None:
    summary = summarize_price(entry.price, entry.media_type) if entry is not None else None
    if summary is None:
        return None
    return PriceSummaryResponse(
        currency=summary.currency,
        items=[
            PriceSummaryItemResponse(meter=i.meter, min_price=i.min_price, max_price=i.max_price, per=i.per)
            for i in summary.items
        ],
    )


def _builtin_item(item: builtin_models.BuiltinModelItem, provider_multiplier: float) -> ProviderModelItem:
    view = item.view
    return ProviderModelItem(
        model_id=view.model_id,
        api_model_name=view.api_model_name,
        display_name=view.entry.display_name if view.entry is not None else None,
        media_type=cast("MediaType", view.model.media_type),
        enabled=item.enabled,
        is_default=item.is_default,
        official=view.official,
        flags=_flags(view.entry, provider_multiplier * view.multiplier),
        price=_price(view.entry),
    )


async def _custom_items(session: AsyncSession, provider_id: str, db_id: int) -> list[ProviderModelItem]:
    provider_multiplier = await get_provider_multiplier(session, provider_id)
    endpoint_repo = CustomEndpointRepository(session)
    items: list[ProviderModelItem] = []
    for row in await CustomProviderRepository(session).list_models(db_id):
        try:
            media_type = (await resolve_endpoint_spec(row.endpoint, endpoint_repo.get)).media_type
        except ValueError:
            # 调用端点已被删除：这一行调用不了，也无从归组，不在列表里出现；修复经自定义供应商表单。
            logger.warning("自定义供应商 %s 的模型 %s 的调用端点 %s 不存在", provider_id, row.model_id, row.endpoint)
            continue
        if media_type in MEDIA_TYPES:
            items.append(_custom_item(row, cast("MediaType", media_type), provider_multiplier))
    return items


def _custom_item(row: CustomProviderModel, media_type: MediaType, provider_multiplier: float) -> ProviderModelItem:
    entry = catalog.find_entry(
        row.model_id, media_type=media_type, binding=catalog.binding_key(row.entry_binding, media_type)
    )
    display_name = row.display_name.strip() if row.display_name and row.display_name.strip() != row.model_id else None
    return ProviderModelItem(
        model_id=row.model_id,
        api_model_name=row.model_id,
        display_name=display_name or (entry.display_name if entry is not None else None),
        media_type=media_type,
        enabled=row.is_enabled,
        is_default=row.is_enabled and row.is_default,
        official=False,
        flags=_flags(entry, provider_multiplier * row.multiplier),
        price=_price(entry),
        endpoint=row.endpoint,
    )


async def _custom_db_id(session: AsyncSession, provider_id: str, _t: Translator) -> int:
    try:
        db_id = parse_provider_id(provider_id)
    except ValueError:
        db_id = None
    if db_id is None or await CustomProviderRepository(session).get_provider(db_id) is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return db_id


def _require_builtin(provider_id: str, _t: Translator) -> None:
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))


def _raise_for(exc: builtin_models.BuiltinModelError, _t: Translator) -> HTTPException:
    return HTTPException(status_code=404 if exc.not_found else 422, detail=_t(exc.code, **exc.params))


async def _builtin_items(session: AsyncSession, provider_id: str) -> list[ProviderModelItem]:
    provider_multiplier = await get_provider_multiplier(session, provider_id)
    items = await builtin_models.list_builtin_models(session, provider_id)
    return [_builtin_item(item, provider_multiplier) for item in items]


@router.get("/{provider_id}/models", response_model=ProviderModelsResponse)
async def list_provider_models(
    provider_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelsResponse:
    """供应商的全部供应商模型，带启用状态、状态标记与价格摘要。"""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        return ProviderModelsResponse(models=await _custom_items(session, provider_id, db_id))
    _require_builtin(provider_id, _t)
    return ProviderModelsResponse(models=await _builtin_items(session, provider_id))


@router.put("/{provider_id}/models/enabled", response_model=ProviderModelItem)
async def set_provider_model_enabled(
    provider_id: str,
    body: ProviderModelEnabledUpdate,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelItem:
    """启用或停用一个供应商模型，立即生效。"""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        repo = CustomProviderRepository(session)
        row = await repo.get_model_by_ids(db_id, body.model_id)
        if row is None:
            raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=body.model_id))
        row.is_enabled = body.enabled
        if not body.enabled:
            row.is_default = False
        await session.commit()
        item = next((i for i in await _custom_items(session, provider_id, db_id) if i.model_id == body.model_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=body.model_id))
        return item
    _require_builtin(provider_id, _t)
    try:
        await builtin_models.set_builtin_model_enabled(session, provider_id, body.model_id, body.enabled)
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    await session.commit()
    return await _one_builtin_item(session, provider_id, body.model_id, _t)


async def _one_builtin_item(
    session: AsyncSession, provider_id: str, model_id: str, _t: Translator
) -> ProviderModelItem:
    item = next((i for i in await _builtin_items(session, provider_id) if i.model_id == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    return item


@router.post("/{provider_id}/models", response_model=AddProviderModelResponse, status_code=201)
async def add_provider_model(
    provider_id: str,
    body: AddProviderModelRequest,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> AddProviderModelResponse:
    """内置供应商按 ID 添加模型：对上官方收录的模型即启用它，对不上的作为用户添加的模型。"""
    _require_builtin(provider_id, _t)
    try:
        added = await builtin_models.add_builtin_model(
            session, provider_id, body.model_id, media_type=body.media_type, entry_binding=body.entry_binding
        )
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    await session.commit()
    await reload_user_added_models(session)
    return AddProviderModelResponse(
        model=await _one_builtin_item(session, provider_id, added.model_id, _t), official=added.official
    )


@router.delete("/{provider_id}/models/{model_id:path}", status_code=204)
async def remove_provider_model(
    provider_id: str,
    model_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """移除内置供应商下用户添加的模型；官方收录的模型只能停用。"""
    _require_builtin(provider_id, _t)
    try:
        await builtin_models.remove_builtin_model(session, provider_id, model_id)
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    await session.commit()
    await reload_user_added_models(session)
    return Response(status_code=204)
