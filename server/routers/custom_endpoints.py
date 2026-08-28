"""自定义调用端点管理 API。

定义本体的 CRUD 与保存前确认。零封套：请求体与导出内容都是定义 JSON 原样，导入即
``POST``，导出即 ``GET`` 后由客户端存盘——没有独立的 import / export 接口，也没有外层信封。

``POST /validate`` 是单段、服务端无状态的确认：与保存共用同一个校验器，额外回重复血统、提示
回显与版本档位，让客户端在创建之前就能决定新建副本、覆盖既有还是取消。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.api_errors import ConflictError, NotFoundError, UnprocessableError
from lib.custom_provider import make_endpoint_key
from lib.custom_provider.endpoint_definition import (
    CURRENT_SCHEMA_VERSION,
    SchemaVersionLevel,
    VersionRelation,
    schema_version_level,
    validate_definition,
    version_relation,
)
from lib.custom_provider.endpoint_resolution import derive_mirror_columns
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository, EndpointReference
from lib.i18n import Translator

router = APIRouter(prefix="/custom-endpoints", tags=["Custom Endpoints"])

#: 请求体即定义 JSON 原样。刻意不声明成 ``dict``：非对象的输入（数组、裸串）也要经共享校验器
#: 产出定位到字段的诊断，而不是撞上 FastAPI 自己的一套 422 形状。
DefinitionBody = Annotated[Any, Body()]


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class CustomEndpointResponse(BaseModel):
    """一条自定义调用端点。``definition`` 即导出内容，客户端直接存成文件。"""

    id: int
    key: str  # ce-<id>，系统分配、不透明
    display_name: str
    kind: str
    schema_version: str
    media_type: str
    definition: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None


class CustomEndpointListResponse(BaseModel):
    endpoints: list[CustomEndpointResponse]


class DuplicateDescriptor(BaseModel):
    """与待导入定义同血统（``meta.author`` + ``meta.name``）的既有端点。"""

    id: int
    key: str
    display_name: str
    version: str
    # 既有定义相对待导入文件的新旧
    relation: VersionRelation


class SchemaVersionInfo(BaseModel):
    """文件版本与当前版本的比对结果。``level`` 只是提示信号，闸门始终是 schema 校验器。"""

    file: str | None
    current: str
    level: SchemaVersionLevel


class ValidateResponse(BaseModel):
    errors: list[dict[str, str]]
    warnings: list[dict[str, str]]
    duplicates: list[DuplicateDescriptor]
    # meta.hints 原样回显（base_url 与建议模型）；只展示，不复合创建供应商。
    hints: dict[str, Any] | None = None
    schema_version: SchemaVersionInfo


class EndpointReferenceDescriptor(BaseModel):
    """引用该端点的模型行。删除被拒时随 409 下发，让用户知道去哪里解除引用。"""

    provider_id: int
    provider_display_name: str
    model_id: str
    model_display_name: str


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_response(row: CustomEndpoint) -> CustomEndpointResponse:
    return CustomEndpointResponse(
        id=row.id,
        key=make_endpoint_key(row.id),
        display_name=row.display_name,
        kind=row.kind,
        schema_version=row.schema_version,
        media_type=row.media_type,
        definition=row.definition,
        created_at=dt_to_iso(row.created_at),
        updated_at=dt_to_iso(row.updated_at),
    )


def _accepted_definition(body: object, _t: Translator) -> dict[str, Any]:
    """过共享校验器，错误即 422 + 结构化诊断；通过后才是可落库的定义。

    保存与 ``validate`` 走同一个 :func:`validate_definition`，两处不可能给出不同判定——
    「validate 说行、保存说不行」正是共用校验器要消灭的分裂。警告不拦保存。
    """
    diagnostics = validate_definition(body)
    if diagnostics.errors or not isinstance(body, dict):
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(diagnostics.to_payload(_t))
    return body


def _lineage(definition: object) -> tuple[str | None, str | None, str | None]:
    """从任意形状的输入里取 ``(author, name, version)``。

    库里的行已过校验，但请求体在本函数被调用时可能尚未过——重复检测要在有错的定义上照样能跑，
    否则「名字打错一处就看不到自己已经导过一份」。
    """
    if not isinstance(definition, Mapping):
        return None, None, None
    meta = definition.get("meta")
    if not isinstance(meta, Mapping):
        return None, None, None
    author, name, version = meta.get("author"), meta.get("name"), meta.get("version")
    return (
        author if isinstance(author, str) else None,
        name if isinstance(name, str) else None,
        version if isinstance(version, str) else None,
    )


async def _duplicates_of(
    repo: CustomEndpointRepository, definition: object, exclude_id: int | None
) -> list[DuplicateDescriptor]:
    """按 ``meta.author + meta.name`` 找同血统的既有端点。

    血统只认这两项：键由系统分配、分享文件不带键，显示名也没有唯一约束，作者加名字是文件里
    仅有的、能跨实例指认「同一份定义」的信息。版本只用来说明新旧，不参与配对。
    """
    author, name, version = _lineage(definition)
    if author is None or name is None:
        return []
    duplicates: list[DuplicateDescriptor] = []
    for row in await repo.list_all():
        if row.id == exclude_id:
            continue
        row_author, row_name, row_version = _lineage(row.definition)
        if row_author != author or row_name != name:
            continue
        duplicates.append(
            DuplicateDescriptor(
                id=row.id,
                key=make_endpoint_key(row.id),
                display_name=row.display_name,
                version=row_version or "",
                relation=version_relation(row_version, version),
            )
        )
    return duplicates


def _hints_of(definition: object) -> dict[str, Any] | None:
    if not isinstance(definition, Mapping):
        return None
    meta = definition.get("meta")
    if not isinstance(meta, Mapping):
        return None
    hints = meta.get("hints")
    return dict(hints) if isinstance(hints, Mapping) else None


def _schema_version_of(definition: object) -> str | None:
    if not isinstance(definition, Mapping):
        return None
    value = definition.get("schema_version")
    return value if isinstance(value, str) else None


def _reference_descriptors(references: list[EndpointReference]) -> list[dict[str, Any]]:
    return [EndpointReferenceDescriptor(**ref._asdict()).model_dump() for ref in references]


async def _invalidate_backend_cache() -> None:
    """定义原地更新立即对新任务生效：清掉按 provider / model 缓存的 backend 实例。

    已在轮询的任务持有构造时的 spec，改动只影响新任务——这是接受的行为，不做在途版本锁。
    """
    from server.services.generation_context import invalidate_backend_cache

    invalidate_backend_cache()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_endpoint(
    body: DefinitionBody,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CustomEndpointResponse:
    """创建（即导入）一条自定义调用端点。请求体是定义 JSON 原样，键由系统分配。"""
    definition = _accepted_definition(body, _t)
    mirror = derive_mirror_columns(definition)
    repo = CustomEndpointRepository(session)
    row = await repo.create(
        definition=definition,
        kind=mirror.kind,
        schema_version=mirror.schema_version,
        media_type=mirror.media_type,
        display_name=mirror.display_name,
    )
    await session.commit()
    await _invalidate_backend_cache()
    await session.refresh(row)
    return _to_response(row)


@router.get("")
async def list_endpoints(session: AsyncSession = Depends(get_async_session)) -> CustomEndpointListResponse:
    """列出全部自定义调用端点（含定义本体）。"""
    rows = await CustomEndpointRepository(session).list_all()
    return CustomEndpointListResponse(endpoints=[_to_response(row) for row in rows])


@router.post("/validate")
async def validate_endpoint_definition(
    body: DefinitionBody,
    _t: Translator,
    exclude_id: int | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> ValidateResponse:
    """保存前的单段确认：校验诊断 + 重复血统 + 提示回显 + 版本档位。服务端不留任何状态。

    ``exclude_id`` 供编辑既有端点时排除自身，否则它总会把自己报成重复。
    """
    diagnostics = validate_definition(body)
    payload = diagnostics.to_payload(_t)
    file_version = _schema_version_of(body)
    return ValidateResponse(
        errors=payload["errors"],
        warnings=payload["warnings"],
        duplicates=await _duplicates_of(CustomEndpointRepository(session), body, exclude_id),
        hints=_hints_of(body),
        schema_version=SchemaVersionInfo(
            file=file_version,
            current=CURRENT_SCHEMA_VERSION,
            level=schema_version_level(file_version, CURRENT_SCHEMA_VERSION),
        ),
    )


@router.get("/{endpoint_id}")
async def get_endpoint(
    endpoint_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CustomEndpointResponse:
    """读取单条端点。导出即取本响应的 ``definition`` 存成文件，服务端不参与下载。"""
    row = await CustomEndpointRepository(session).get(endpoint_id)
    if row is None:
        raise NotFoundError("custom_endpoint_not_found")
    return _to_response(row)


@router.put("/{endpoint_id}")
async def update_endpoint(
    endpoint_id: int,
    body: DefinitionBody,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CustomEndpointResponse:
    """整份替换定义。原地更新：键与模型行挂接不变，新任务立即用新定义。"""
    definition = _accepted_definition(body, _t)
    mirror = derive_mirror_columns(definition)
    repo = CustomEndpointRepository(session)
    row = await repo.update(
        endpoint_id,
        definition=definition,
        kind=mirror.kind,
        schema_version=mirror.schema_version,
        media_type=mirror.media_type,
        display_name=mirror.display_name,
    )
    if row is None:
        raise NotFoundError("custom_endpoint_not_found")
    await session.commit()
    await _invalidate_backend_cache()
    await session.refresh(row)
    return _to_response(row)


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(
    endpoint_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """删除端点；被模型行引用时拒删并回引用清单。

    不级联删除引用它的模型行——模型行承载用户手工配置（定价、能力覆盖），级联会丢用户劳动；
    也不留悬空引用，那只会把错误推迟到生成时才爆。
    """
    repo = CustomEndpointRepository(session)
    row = await repo.get(endpoint_id)
    if row is None:
        raise NotFoundError("custom_endpoint_not_found")
    references = await repo.list_references(make_endpoint_key(endpoint_id))
    if references:
        raise ConflictError("custom_endpoint_referenced_by_models", count=len(references)).with_diagnostic(
            {"references": _reference_descriptors(references)}
        )
    await repo.delete(endpoint_id)
    await session.commit()
    await _invalidate_backend_cache()
