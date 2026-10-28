"""供应商模型 API：按供应商列出、读取单个、保存供应商设置、启用与停用、移除；内置供应商另可按 ID 添加（docs/adr/0099）。

内置与自定义供应商共用。读取单个与保存对应供应商模型 Sheet 的「供应商设置」：官方收录的模型只有启用；用户负责的
模型（自定义供应商的模型、内置供应商下用户添加的模型）另有模型 ID、供应商模型倍率与能力覆盖；自定义供应商的模型
另有默认、调用端点、请求地址与默认分辨率。

Sheet 下段「模型信息」是对应到的模型条目，由所有使用它的供应商共用：读取时带上条目由谁维护、官方值与共用它的
其他供应商；保存时与供应商设置在同一个请求里原子写入，修改存为整个条目的用户副本（docs/adr/0099）。用户负责的模型
另可「关联其他模型」（存为条目绑定，docs/adr/0105），或手动填写目录里没有的模型的信息（新建条目并绑定）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Annotated, Any, Literal, assert_never, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import (
    MEDIA_TYPES,
    AudioFacts,
    CatalogEntry,
    ImageFacts,
    MediaType,
    TextFacts,
    VideoFacts,
    dump_entry,
)
from lib.billing.multipliers import get_provider_multiplier
from lib.catalog import builtin_discovery, builtin_models
from lib.catalog import query as catalog
from lib.catalog.composition import EntrySource
from lib.catalog.price_summary import summarize_price
from lib.catalog.reload_order import LatestReadWins
from lib.catalog.user_entries import (
    UNSET,
    EntryEditRejected,
    create_user_entry,
    edited_entry,
    reload_user_entries,
    save_user_entry,
)
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.retired_model_ids import GLOBAL_MODEL_SETTING_KEYS, PROJECT_MODEL_SETTING_KEYS
from lib.config.service import ConfigService
from lib.custom_provider import is_custom_provider, make_provider_id, parse_provider_id
from lib.custom_provider.capability_view import (
    CapabilityView,
    OverrideRejected,
    builtin_capability_view,
    check_user_added_overrides,
    user_added_runtime_overrides,
)
from lib.custom_provider.discovery_formats import is_comfyui_protocol
from lib.custom_provider.duration_presets import infer_supported_durations
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.model_facts import entry_summary, model_entry
from lib.db import get_async_session
from lib.db.models.custom_provider import CustomProviderModel
from lib.db.repositories.builtin_provider_model_repo import BuiltinProviderModelRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.i18n import Translator
from server.routers.catalog import NewEntryBody, build_new_entry
from server.routers.custom_providers import (
    CUSTOM_MODEL_SETTING_FIELDS,
    EndpointRequestDescriptor,
    add_custom_model,
    cleanup_project_model_refs,
    custom_model_settings,
    discover_custom_provider_models,
    invalidate_provider_caches,
    remove_custom_model,
    rename_project_model_refs,
    update_custom_model,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["Providers"])

ModelFlag = Literal["discontinued", "unpriced", "modified"]


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
      两级倍率之积为 0 时不计费，不标）；``modified`` 用户改过对应的条目，不再随官方更新。
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
    """``discoverable``：供应商可以「获取模型」（自定义供应商的 ComfyUI 协议与没有模型列表接口的内置供应商不行）。"""

    models: list[ProviderModelItem]
    discoverable: bool = False


class ProviderModelEnabledUpdate(BaseModel):
    """启用或停用一个供应商模型；自定义供应商的模型停用时同时取消默认。"""

    model_id: str
    enabled: bool


class AddProviderModelRequest(BaseModel):
    """按 ID 添加：``media_type`` 用来区分两栖模型，以及给对应不上条目的模型指定媒体类型；``entry_binding`` 是
    用户为它指定的条目（「模型厂商/官方模型名」）。``endpoint`` 是自定义供应商模型的调用端点，不给时按名字推断
    （docs/adr/0105）；内置供应商不接受。"""

    model_id: Annotated[str, Field(min_length=1, max_length=128)]
    media_type: MediaType | None = None
    entry_binding: str | None = None
    endpoint: str | None = None


class DiscoveredModelItem(BaseModel):
    """获取到的一个模型。

    - ``recognized``：自定义供应商为名字对应上了条目；内置供应商为对上了清单里官方收录的模型（API 模型名精确匹配），
      ``model_key`` 是它的模型键，对不上时为 ``null``。
    - ``media_type``：对应上的取条目或官方收录模型的，对应不上的按名字推断；添加时作为用户添加的模型的媒体类型。
    - ``matched_entry``：对应到的条目摘要。
    - ``endpoint`` 与 ``endpoint_by_media_type``：自定义供应商的默认调用端点，以及改为对应其他媒体类型的条目时
      各媒体类型下的默认端点；内置供应商为 ``null``。
    """

    model_id: str
    display_name: str
    media_type: MediaType
    recognized: bool
    model_key: str | None = None
    matched_entry: dict[str, Any] | None = None
    endpoint: str | None = None
    endpoint_by_media_type: dict[str, str] | None = None
    is_default: bool = False
    is_enabled: bool = True
    max_output_tokens: int | None = None


class DiscoverProviderModelsResponse(BaseModel):
    """获取模型的结果。``not_applicable`` 为真时这个供应商没有模型列表接口，``reason`` 说明原因。"""

    models: list[DiscoveredModelItem] = []
    not_applicable: bool = False
    reason: str | None = None


class AddProviderModelResponse(BaseModel):
    """``official`` 为真时对上了官方收录的模型，添加即启用它。"""

    model: ProviderModelItem
    official: bool


class CapabilityViewResponse(BaseModel):
    """「这个供应商支持的参数」：每个维度的原值（没有能力覆盖时运行时采用的值，``null`` 表示不加约束）、生效的能力
    覆盖与生效值。``editable`` 是可以修改的维度，按界面顺序；官方收录的模型为空。"""

    media_type: str
    editable: list[str]
    base: dict[str, Any]
    overrides: dict[str, Any]
    effective: dict[str, Any]


class OfficialEntryValues(BaseModel):
    """用户改过的条目在官方数据里的值，供逐项比对与「采用」。"""

    display_name: str
    facts: dict[str, Any]
    price: dict[str, Any] | None


class SharedProviderResponse(BaseModel):
    provider_id: str
    display_name: str


class EntryInfoResponse(BaseModel):
    """供应商模型对应到的条目（合成后），供 Sheet 下段「模型信息」。

    - ``price``：价目表（目录数据的格式，可直接作为 ``EntryUpdate.price`` 回写）；没有价目表时为 ``null``。
    - ``source``：``official`` 跟随官方；``modified`` 用户改过，不再随官方更新；``user_created`` 用户新建、由用户填写。
    - ``official``：用户改过、或用户新建的条目日后被官方数据收录时，官方数据里的值（可「恢复官方数据」「改用官方数据」）；
      其余情况为 ``null``。
    - ``shared_with``：同样使用这个条目的其他供应商（已配置密钥的内置供应商与自定义供应商中启用的模型），
      修改与恢复官方数据会一起影响它们。
    """

    maker: str
    name: str
    display_name: str
    media_type: str
    aliases: list[str]
    discontinued: bool
    context_length: int | None
    facts: dict[str, Any]
    price: dict[str, Any] | None
    source: EntrySource
    official: OfficialEntryValues | None
    shared_with: list[SharedProviderResponse]


class ProviderModelDetail(BaseModel):
    """供应商模型 Sheet 的内容。

    - ``multiplier``：供应商模型倍率；官方收录的模型随官方数据下发、界面不显示，为 ``null``。
    - ``provider_multiplier``：供应商倍率，界面据此提示两级相乘后的最终倍率。
    - ``entry``：对应到的条目；对应不上时为 ``null``，调用费用记为未定价。
    - ``entry_binding``：用户指定的条目绑定（「模型厂商/官方模型名」）；没有绑定、按名字对应时为 ``null``。官方收录的
      模型没有绑定。
    - 自定义供应商的模型另有 ``endpoint``、``resolution``、``path_overrides`` 与 ``requests``（调用端点可覆盖路径的
      请求）；内置供应商为空。
    """

    model: ProviderModelItem
    multiplier: float | None
    provider_multiplier: float
    entry: EntryInfoResponse | None
    entry_binding: str | None = None
    capabilities: CapabilityViewResponse
    endpoint: str | None = None
    resolution: str | None = None
    path_overrides: dict[str, str] | None = None
    requests: list[EndpointRequestDescriptor] = []


class EntryUpdate(BaseModel):
    """Sheet 下段「模型信息」的修改。``maker``、``name``、``media_type`` 是 Sheet 读到的条目身份，须与这个供应商模型
    当前对应的条目一致；``display_name``、``facts`` 与 ``price`` 没有传的不改，``facts`` 只给要改的模型事实。
    ``price`` 是整份价目表（见 ``EntryInfoResponse.price``），整份替换；显式传 ``null`` 清除价目表，调用费用记为未定价。"""

    maker: str
    name: str
    media_type: MediaType
    display_name: str | None = None
    facts: dict[str, Any] | None = None
    price: dict[str, Any] | None = None


class ProviderModelSettingsUpdate(BaseModel):
    """保存供应商模型 Sheet 的供应商设置与模型信息；没有传的字段不改。

    - 所有供应商模型：``enabled``，以及 ``entry``（修改对应的条目，存为用户副本，条目从此不再随官方更新）。
    - 用户负责的模型：``model_id``（改模型 ID）、``multiplier``、``capability_overrides``（整份替换，``null`` 或
      空对象表示清除）、``entry_binding``（关联到目录里的另一个模型，须是同媒体类型的现有条目；``null`` 解除，改回
      按名字对应）、``new_entry``（目录里找不到时手动填写：新建条目并绑定到它，目录里已有同名模型时 422；与
      ``entry_binding`` 同传时以 ``new_entry`` 为准）。条目与绑定都随这一次保存写入，任一步被拒则都不落库。
    - 自定义供应商的模型：``is_default``、``endpoint``、``path_overrides``、``resolution``。
    """

    enabled: bool | None = None
    is_default: bool | None = None
    model_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    multiplier: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    capability_overrides: dict[str, Any] | None = None
    endpoint: str | None = None
    path_overrides: dict[str, str] | None = None
    resolution: str | None = None
    entry: EntryUpdate | None = None
    entry_binding: str | None = None
    new_entry: NewEntryBody | None = None


class ProjectReference(BaseModel):
    name: str
    title: str | None


class ProviderModelReferences(BaseModel):
    """引用这个供应商模型的全局设置键与项目；移除前提示用。"""

    settings: list[str]
    projects: list[ProjectReference]


_USER_ADDED_RELOADS = LatestReadWins()


async def reload_user_added_models(session: AsyncSession) -> None:
    """从内置供应商模型表装入用户添加的模型，交给目录查询。应用启动时与每次写入提交之后调用。

    读目录的代码（接口、生成 worker、Agent 会话的工具）都跑在应用进程里，进程内替换即对全部读点生效；并发的两次
    装入按开始读的先后生效。
    """
    await _USER_ADDED_RELOADS.reload(
        lambda: builtin_models.list_user_added_rows(session),
        lambda rows: catalog.install_user_added_models(
            catalog.UserAddedModel(
                provider_id=row.provider_id,
                model_id=row.model_id,
                media_type=cast("MediaType", row.media_type),
                multiplier=row.multiplier,
                entry_binding=row.entry_binding,
                fallback_durations=tuple(infer_supported_durations(row.model_id)) if row.media_type == "video" else (),
                capability_overrides=user_added_runtime_overrides(
                    row.media_type, row.capability_overrides, model_id=row.model_id
                ),
            )
            for row in rows
            if row.media_type in MEDIA_TYPES
        ),
    )


def _flags(entry: CatalogEntry | None, multiplier: float) -> list[ModelFlag]:
    flags: list[ModelFlag] = []
    if entry is not None and entry.discontinued:
        flags.append("discontinued")
    priced = entry is not None and entry.price is not None and entry.price.is_priceable
    if not priced and multiplier != 0:
        flags.append("unpriced")
    if entry is not None and catalog.entry_source(entry.key) == "modified":
        flags.append("modified")
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
        provider = await CustomProviderRepository(session).get_provider(db_id)
        return ProviderModelsResponse(
            models=await _custom_items(session, provider_id, db_id),
            discoverable=provider is not None and not is_comfyui_protocol(provider.discovery_format),
        )
    _require_builtin(provider_id, _t)
    return ProviderModelsResponse(
        models=await _builtin_items(session, provider_id),
        discoverable=builtin_discovery.is_discoverable(provider_id),
    )


@router.put("/{provider_id}/models/enabled", response_model=ProviderModelItem)
async def set_provider_model_enabled(
    provider_id: str,
    body: ProviderModelEnabledUpdate,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelItem:
    """启用或停用一个供应商模型，立即生效。

    自定义供应商开放哪些媒体类型由已启用的模型决定，启停后与其他模型改动一样清缓存、刷新 worker 的容量表。
    """
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
        await invalidate_provider_caches(request)
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
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> AddProviderModelResponse:
    """按 ID 添加模型。

    内置供应商：对上官方收录的模型即启用它，对不上的作为用户添加的模型。自定义供应商：没给调用端点时按名字推断，
    这个媒体类型还没有默认模型时新模型成为默认。
    """
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        row = await add_custom_model(
            session,
            db_id,
            body.model_id,
            endpoint=body.endpoint,
            media_type=body.media_type,
            entry_binding=body.entry_binding,
            _t=_t,
        )
        model_id = row.model_id
        await session.commit()
        await invalidate_provider_caches(request)
        item = next((i for i in await _custom_items(session, provider_id, db_id) if i.model_id == model_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
        return AddProviderModelResponse(model=item, official=False)
    _require_builtin(provider_id, _t)
    if body.endpoint is not None:
        raise HTTPException(
            status_code=422,
            detail=_t("provider_model_setting_not_applicable", model=body.model_id, setting="endpoint"),
        )
    try:
        added = await builtin_models.add_builtin_model(
            session,
            provider_id,
            body.model_id,
            media_type=body.media_type,
            entry_binding=body.entry_binding,
            inferred_media_type=builtin_discovery.name_media_type(provider_id, body.model_id.strip()),
        )
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    await session.commit()
    await reload_user_added_models(session)
    return AddProviderModelResponse(
        model=await _one_builtin_item(session, provider_id, added.model_id, _t), official=added.official
    )


#: 内置供应商获取模型时等待模型列表接口的上限。
_DISCOVERY_TIMEOUT_SECONDS = 30


@router.post("/{provider_id}/models/discover", response_model=DiscoverProviderModelsResponse)
async def discover_provider_models(
    provider_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> DiscoverProviderModelsResponse:
    """获取模型：调用供应商的模型列表接口，返回全部模型 ID 与对应结果，由界面排除已添加的模型。

    自定义供应商按名字对应条目并给出默认调用端点；内置供应商按清单中的 API 模型名精确匹配。获取结果不用来判断
    模型是否可用。
    """
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        found = await discover_custom_provider_models(session, db_id, _t)
        return DiscoverProviderModelsResponse(
            models=[DiscoveredModelItem.model_validate(m) for m in found.models],
            not_applicable=found.not_applicable,
            reason=found.reason,
        )
    _require_builtin(provider_id, _t)
    if not builtin_discovery.is_discoverable(provider_id):
        return DiscoverProviderModelsResponse(not_applicable=True, reason=_t("discovery_not_applicable_builtin"))
    config = await _builtin_discovery_config(session, provider_id)
    if config is None:
        raise HTTPException(status_code=422, detail=_t("missing_credentials"))
    try:
        model_ids = await asyncio.wait_for(
            asyncio.to_thread(builtin_discovery.list_model_ids, provider_id, config),
            timeout=_DISCOVERY_TIMEOUT_SECONDS,
        )
    except builtin_discovery.BuiltinDiscoveryConfigError as exc:
        raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc
    except Exception as exc:
        err_msg = str(exc) or type(exc).__name__
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("获取模型失败 [%s]: %s", provider_id, err_msg)
        raise HTTPException(status_code=502, detail=_t("discovery_failed", err_msg=err_msg)) from exc
    return DiscoverProviderModelsResponse(
        models=[_discovered_builtin_item(m) for m in builtin_discovery.classify_model_ids(provider_id, model_ids)]
    )


async def _builtin_discovery_config(session: AsyncSession, provider_id: str) -> dict[str, str] | None:
    """叠加了激活凭证的供应商配置；没有凭证时为 ``None``。"""
    cred = await CredentialRepository(session).get_active(provider_id)
    if cred is None:
        return None
    config = await ConfigService(session).get_provider_config(provider_id)
    # 叠加凭证时会探测 Vertex 凭证文件是否存在，放进线程
    await asyncio.to_thread(cred.overlay_config, config)
    if not config.get("base_url") and (meta := PROVIDER_REGISTRY.get(provider_id)) and meta.default_base_url:
        config["base_url"] = meta.default_base_url
    return config


def _discovered_builtin_item(model: builtin_discovery.DiscoveredBuiltinModel) -> DiscoveredModelItem:
    return DiscoveredModelItem(
        model_id=model.model_id,
        display_name=model.entry.display_name if model.entry is not None else model.model_id,
        media_type=cast("MediaType", model.media_type),
        recognized=model.recognized,
        model_key=model.model_key,
        matched_entry=entry_summary(model.entry) if model.entry is not None else None,
    )


@router.delete("/{provider_id}/models/{model_id:path}", status_code=204)
async def remove_provider_model(
    provider_id: str,
    model_id: str,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """移除用户负责的模型：自定义供应商的模型（同时清空全局设置与项目里指向它的引用），或内置供应商下用户添加的模型。
    官方收录的模型只能停用。"""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        await remove_custom_model(session, db_id, model_id, _t)
        await session.commit()
        await invalidate_provider_caches(request)
        await cleanup_project_model_refs(db_id, {model_id})
        return Response(status_code=204)
    _require_builtin(provider_id, _t)
    try:
        await builtin_models.remove_builtin_model(session, provider_id, model_id)
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    await session.commit()
    await reload_user_added_models(session)
    await invalidate_provider_caches(request)
    return Response(status_code=204)


def _capabilities_response(view: CapabilityView | None, media_type: str) -> CapabilityViewResponse:
    if view is None:
        return CapabilityViewResponse(media_type=media_type, editable=[], base={}, overrides={}, effective={})
    return CapabilityViewResponse(
        media_type=view.media_type,
        editable=list(view.editable),
        base=view.base,
        overrides=view.overrides,
        effective=view.effective,
    )


def _facts_json(entry: CatalogEntry) -> dict[str, Any]:
    facts = entry.facts
    match facts:
        case TextFacts():
            return {
                "image_input": facts.image_input,
                "structured_output": facts.structured_output,
                "max_output_tokens": facts.max_output_tokens,
            }
        case ImageFacts():
            return {
                "text_to_image": facts.text_to_image,
                "image_to_image": facts.image_to_image,
                "resolutions": list(facts.resolutions),
            }
        case VideoFacts():
            constraints = facts.duration_constraints
            return {
                "durations": list(facts.durations),
                "resolutions": list(facts.resolutions),
                "duration_constraints": {
                    "by_resolution": {res: list(allowed) for res, allowed in constraints.by_resolution.items()},
                    "with_reference_images": list(constraints.with_reference_images),
                },
            }
        case AudioFacts():
            return {}
        case _:
            assert_never(facts)


def _price_json(entry: CatalogEntry) -> dict[str, Any] | None:
    return cast("dict[str, Any] | None", dump_entry(entry).get("price"))


async def _custom_model_entry(row: CustomProviderModel, endpoint_repo: CustomEndpointRepository) -> CatalogEntry | None:
    try:
        media_type = (await resolve_endpoint_spec(row.endpoint, endpoint_repo.get)).media_type
    except ValueError:
        return None
    return model_entry(row.model_id, media_type, row.entry_binding) if media_type in MEDIA_TYPES else None


async def _providers_sharing(
    session: AsyncSession, entry: CatalogEntry, provider_id: str
) -> list[SharedProviderResponse]:
    """同样使用这个条目的其他供应商：已配置密钥的内置供应商与自定义供应商，只算启用的模型。"""
    shared: list[SharedProviderResponse] = []
    configured = await CredentialRepository(session).get_active_credentials_bulk()
    for builtin_id, meta in PROVIDER_REGISTRY.items():
        if builtin_id == provider_id or builtin_id not in configured:
            continue
        items = await builtin_models.list_builtin_models(session, builtin_id)
        if any(i.enabled and i.view.entry is not None and i.view.entry.key == entry.key for i in items):
            shared.append(SharedProviderResponse(provider_id=builtin_id, display_name=meta.display_name))
    endpoint_repo = CustomEndpointRepository(session)
    for provider, rows in await CustomProviderRepository(session).list_providers_with_models():
        custom_id = make_provider_id(provider.id)
        if custom_id == provider_id:
            continue
        for row in rows:
            if row.is_enabled and (found := await _custom_model_entry(row, endpoint_repo)) and found.key == entry.key:
                shared.append(SharedProviderResponse(provider_id=custom_id, display_name=provider.display_name))
                break
    return shared


async def _entry_info(session: AsyncSession, entry: CatalogEntry | None, provider_id: str) -> EntryInfoResponse | None:
    if entry is None:
        return None
    source = catalog.entry_source(entry.key)
    official = catalog.official_entry(entry.key) if source != "official" else None
    return EntryInfoResponse(
        maker=entry.maker,
        name=entry.name,
        display_name=entry.display_name,
        media_type=entry.media_type,
        aliases=list(entry.aliases),
        discontinued=entry.discontinued,
        context_length=entry.context_length,
        facts=_facts_json(entry),
        price=_price_json(entry),
        source=source,
        official=(
            OfficialEntryValues(
                display_name=official.display_name, facts=_facts_json(official), price=_price_json(official)
            )
            if official is not None
            else None
        ),
        shared_with=await _providers_sharing(session, entry, provider_id),
    )


async def _builtin_binding(session: AsyncSession, provider_id: str, model_id: str, *, official: bool) -> str | None:
    """内置供应商下用户添加的模型的条目绑定；官方收录的模型没有。"""
    if official:
        return None
    row = await BuiltinProviderModelRepository(session).get(provider_id, model_id)
    return row.entry_binding if row is not None else None


async def _builtin_detail(
    session: AsyncSession, provider_id: str, model_id: str, _t: Translator
) -> ProviderModelDetail:
    provider_multiplier = await get_provider_multiplier(session, provider_id)
    items = await builtin_models.list_builtin_models(session, provider_id)
    item = next((i for i in items if i.view.model_id == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    view = item.view
    capabilities = builtin_capability_view(
        provider_id,
        model_id,
        base_model=view.base_model or view.model,
        official=view.official,
        overrides=view.capability_overrides,
    )
    return ProviderModelDetail(
        model=_builtin_item(item, provider_multiplier),
        multiplier=None if view.official else view.multiplier,
        provider_multiplier=provider_multiplier,
        entry=await _entry_info(session, view.entry, provider_id),
        entry_binding=await _builtin_binding(session, provider_id, model_id, official=view.official),
        capabilities=_capabilities_response(capabilities, view.model.media_type),
    )


async def _custom_detail(
    session: AsyncSession, provider_id: str, db_id: int, model_id: str, _t: Translator
) -> ProviderModelDetail:
    row = await CustomProviderRepository(session).get_model_by_ids(db_id, model_id)
    item = next((i for i in await _custom_items(session, provider_id, db_id) if i.model_id == model_id), None)
    if row is None or item is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    settings = await custom_model_settings(session, row)
    return ProviderModelDetail(
        model=item,
        multiplier=row.multiplier,
        provider_multiplier=await get_provider_multiplier(session, provider_id),
        entry=await _entry_info(session, model_entry(row.model_id, item.media_type, row.entry_binding), provider_id),
        entry_binding=row.entry_binding,
        capabilities=_capabilities_response(settings.capabilities, item.media_type),
        endpoint=settings.endpoint,
        resolution=settings.resolution,
        path_overrides=settings.path_overrides,
        requests=settings.requests,
    )


@router.get("/{provider_id}/models/{model_id:path}", response_model=ProviderModelDetail)
async def get_provider_model(
    provider_id: str,
    model_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelDetail:
    """读取单个供应商模型：列表行、供应商设置、对应条目，以及「这个供应商支持的参数」的生效值与原值。"""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        return await _custom_detail(session, provider_id, db_id, model_id, _t)
    _require_builtin(provider_id, _t)
    return await _builtin_detail(session, provider_id, model_id, _t)


_BUILTIN_SETTING_FIELDS = frozenset(
    {"enabled", "model_id", "multiplier", "capability_overrides", "entry", "entry_binding", "new_entry"}
)


async def _current_entry(session: AsyncSession, provider_id: str, model_id: str, _t: Translator) -> CatalogEntry | None:
    """供应商模型当前对应到的条目（合成后）；供应商模型不存在时 404。"""
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        row = await CustomProviderRepository(session).get_model_by_ids(db_id, model_id)
        if row is None:
            raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
        return await _custom_model_entry(row, CustomEndpointRepository(session))
    items = await builtin_models.list_builtin_models(session, provider_id)
    item = next((i for i in items if i.view.model_id == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    return item.view.entry


async def _save_entry(
    session: AsyncSession, provider_id: str, model_id: str, update: EntryUpdate, _t: Translator
) -> bool:
    """把「模型信息」的修改存为条目的用户副本；返回是否写入（与当前条目相同时不写，条目照旧跟随官方）。"""
    current = await _current_entry(session, provider_id, model_id, _t)
    if current is None or (current.maker, current.name, current.media_type) != (
        update.maker,
        update.name,
        update.media_type,
    ):
        raise HTTPException(status_code=409, detail=_t("provider_model_entry_mismatch", model=model_id))
    try:
        entry = edited_entry(
            current,
            display_name=update.display_name,
            facts=update.facts,
            price=update.price if "price" in update.model_fields_set else UNSET,
        )
    except EntryEditRejected as exc:
        raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc
    if entry == current:
        return False
    await save_user_entry(session, entry)
    return True


@dataclass(frozen=True)
class _BindingChange:
    """这次保存对条目绑定的修改：``ref`` 是新的绑定（``None`` 为解除），``created`` 是随之新建的条目。"""

    ref: str | None
    created: CatalogEntry | None


def _plan_binding(body: ProviderModelSettingsUpdate, _t: Translator) -> _BindingChange | None:
    """请求里的「关联其他模型」或「手动填写」；没有传时为 ``None``。新条目在这里校验，写入见 :func:`_apply_binding`。"""
    if body.new_entry is not None:
        created = build_new_entry(body.new_entry, _t)
        return _BindingChange(created.key.ref, created)
    if "entry_binding" in body.model_fields_set:
        return _BindingChange((body.entry_binding or "").strip() or None, None)
    return None


async def _apply_binding(session: AsyncSession, change: _BindingChange, media_type: str, _t: Translator) -> None:
    """按保存后的媒体类型核对绑定：绑定到现有条目要求条目存在且同媒体类型，新条目要求媒体类型一致；通过后写入新条目。"""
    if change.created is not None:
        if change.created.media_type != media_type:
            raise HTTPException(
                status_code=422, detail=_t("provider_model_binding_not_found", binding=change.created.key.ref)
            )
        await create_user_entry(session, change.created)
    elif change.ref is not None and catalog.find_bound_entry(change.ref, media_type) is None:
        raise HTTPException(status_code=422, detail=_t("provider_model_binding_not_found", binding=change.ref))


async def _save_builtin_settings(
    session: AsyncSession,
    provider_id: str,
    model_id: str,
    body: ProviderModelSettingsUpdate,
    binding: _BindingChange | None,
    _t: Translator,
) -> str:
    fields = body.model_fields_set
    if unsupported := sorted(fields - _BUILTIN_SETTING_FIELDS):
        raise HTTPException(
            status_code=422,
            detail=_t("provider_model_setting_not_applicable", model=model_id, setting=unsupported[0]),
        )
    kwargs: dict[str, Any] = {}
    if "capability_overrides" in fields:
        item = next(
            (i for i in await builtin_models.list_builtin_models(session, provider_id) if i.view.model_id == model_id),
            None,
        )
        if item is not None and not item.view.official:
            try:
                kwargs["capability_overrides"] = check_user_added_overrides(
                    provider_id, model_id, item.view.model.media_type, body.capability_overrides
                )
            except OverrideRejected as exc:
                raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc
        else:
            kwargs["capability_overrides"] = body.capability_overrides
    if binding is not None:
        kwargs["entry_binding"] = binding.ref
    try:
        saved = await builtin_models.update_builtin_model(
            session,
            provider_id,
            model_id,
            enabled=body.enabled,
            new_model_id=body.model_id,
            multiplier=body.multiplier,
            **kwargs,
        )
    except builtin_models.BuiltinModelError as exc:
        raise _raise_for(exc, _t) from exc
    if binding is not None:
        row = await BuiltinProviderModelRepository(session).get(provider_id, saved)
        if row is not None and row.media_type is not None:
            await _apply_binding(session, binding, row.media_type, _t)
    return saved


@router.patch("/{provider_id}/models/{model_id:path}", response_model=ProviderModelDetail)
async def update_provider_model(
    provider_id: str,
    model_id: str,
    body: ProviderModelSettingsUpdate,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelDetail:
    """保存供应商模型 Sheet 的供应商设置与模型信息，一次请求原子保存；返回保存后的 Sheet 内容。"""
    if not is_custom_provider(provider_id):
        _require_builtin(provider_id, _t)
    # 先按保存前的对应关系写条目副本：Sheet 读到的就是这个条目，同一请求里改模型 ID 不影响改哪个条目
    entry_saved = body.entry is not None and await _save_entry(session, provider_id, model_id, body.entry, _t)
    binding = _plan_binding(body, _t)
    if is_custom_provider(provider_id):
        db_id = await _custom_db_id(session, provider_id, _t)
        renames = {"enabled": "is_enabled"}
        changes = {
            renames.get(name, name): getattr(body, name)
            for name in body.model_fields_set
            if renames.get(name, name) in CUSTOM_MODEL_SETTING_FIELDS
        }
        if binding is not None:
            changes["entry_binding"] = binding.ref
        row = await update_custom_model(session, db_id, model_id, changes, _t)
        saved_model_id = row.model_id
        # 换调用端点可能换了媒体类型：没改绑定时也按新的媒体类型核对原绑定
        if binding is None and "endpoint" in changes and row.entry_binding:
            binding = _BindingChange(ref=row.entry_binding, created=None)
        if binding is not None:
            media_type = (await resolve_endpoint_spec(row.endpoint, CustomEndpointRepository(session).get)).media_type
            await _apply_binding(session, binding, media_type, _t)
        await session.commit()
        if entry_saved or (binding is not None and binding.created is not None):
            await reload_user_entries(session)
        await invalidate_provider_caches(request)
        await rename_project_model_refs(db_id, model_id, saved_model_id)
        return await _custom_detail(session, provider_id, db_id, saved_model_id, _t)
    saved_model_id = await _save_builtin_settings(session, provider_id, model_id, body, binding, _t)
    await session.commit()
    if entry_saved or (binding is not None and binding.created is not None):
        await reload_user_entries(session)
    await reload_user_added_models(session)
    await invalidate_provider_caches(request)
    return await _builtin_detail(session, provider_id, saved_model_id, _t)


def _project_references(reference: str) -> list[ProjectReference]:
    """读每个项目的 project.json，挑出模型设置项引用了 ``reference`` 的项目；读不了的项目跳过。"""
    from lib.config.resolver import get_project_manager

    pm = get_project_manager()
    found: list[ProjectReference] = []
    for name in pm.list_projects():
        try:
            project = pm.load_project(name)
        except (OSError, ValueError):
            continue
        if any(project.get(key) == reference for key in PROJECT_MODEL_SETTING_KEYS):
            title = project.get("title")
            found.append(ProjectReference(name=name, title=title if isinstance(title, str) and title else None))
    return found


@router.get("/{provider_id}/model-references", response_model=ProviderModelReferences)
async def get_provider_model_references(
    provider_id: str,
    model_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderModelReferences:
    """引用这个供应商模型的全局设置项与项目，供「从供应商移除」的确认框提示。"""
    if is_custom_provider(provider_id):
        await _custom_db_id(session, provider_id, _t)
    else:
        _require_builtin(provider_id, _t)
    reference = f"{provider_id}/{model_id}"
    all_settings = await ConfigService(session).get_all_settings()
    settings = [key for key in GLOBAL_MODEL_SETTING_KEYS if all_settings.get(key) == reference]
    projects = await asyncio.to_thread(_project_references, reference)
    return ProviderModelReferences(settings=settings, projects=projects)
