"""自定义调用端点管理 API。

定义本体的 CRUD 与保存前确认。零封套：请求体与导出内容都是定义 JSON 原样，导入即
``POST``，导出即 ``GET`` 后由客户端存盘——没有独立的 import / export 接口，也没有外层信封。

``POST /validate`` 是单段、服务端无状态的确认：与保存共用同一个校验器，额外回同作者同名的既有端点、提示
回显与版本档位，让客户端在创建之前就能决定新建副本、覆盖既有还是取消。

``validate`` 与创建这两个导入入口按载荷形状分流：带 ``kind`` 的是端点定义，其余按 ComfyUI 的
workflow 收——用户手上最常见的文件是 ComfyUI 自己导出的 workflow，而不是端点定义。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.custom_provider import make_endpoint_key
from lib.custom_provider.comfyui.import_shapes import ImportShape, route_import_payload, ui_workflow_refusal
from lib.custom_provider.discovery_formats import endpoint_attachment_holds
from lib.custom_provider.endpoint_definition import (
    DefinitionDiagnostics,
    SchemaVersionLevel,
    VersionRelation,
    current_schema_version,
    meets_min_app_version,
    parse_semver,
    schema_version_level,
    validate_definition,
    version_relation,
)
from lib.custom_provider.endpoint_resolution import derive_mirror_columns
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository, EndpointReference
from lib.infra.api_errors import ConflictError, NotFoundError, UnprocessableError
from server.i18n import Translator
from server.routers import comfyui_inference, endpoint_tests
from server.routers._market_installations import (
    EndpointInstallationResponse,
    endpoint_installation,
    endpoint_installations,
)
from server.routers.system_config import get_app_version_reader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-endpoints", tags=["Custom Endpoints"])

# 端点测试与节点绑定推断的路由必须先于本模块的 ``/{endpoint_id}`` 注册：FastAPI 按注册序匹配，
# 路径参数解析失败不会往后回退——``/custom-endpoints/trial-runs`` 撞上 ``endpoint_id: int`` 只会
# 直接 422。
router.include_router(endpoint_tests.router)
router.include_router(comfyui_inference.router)

#: 请求体即定义 JSON 原样。刻意不声明成 ``dict``：非对象的输入（数组、裸串）也要经共享校验器
#: 产出定位到字段的诊断，而不是撞上 FastAPI 自己的一套 422 形状。
DefinitionBody = Annotated[Any, Body()]

#: 粘进来的是原始 workflow 时，由导入方指定它产图还是产视频——workflow 本身不声明这件事。
ImportMediaType = Literal["image", "video"]


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
    installation: EndpointInstallationResponse | None = None


class CustomEndpointListResponse(BaseModel):
    endpoints: list[CustomEndpointResponse]


class DuplicateDescriptor(BaseModel):
    """与待导入定义同作者同名（``meta.author`` + ``meta.name``）的既有端点。"""

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


class MinAppVersionInfo(BaseModel):
    """``meta.min_app_version`` 与当前应用版本的比对。不满足只是提示，不拦导入。"""

    required: str
    current: str
    satisfied: bool


class ValidateResponse(BaseModel):
    errors: list[dict[str, str]]
    warnings: list[dict[str, str]]
    duplicates: list[DuplicateDescriptor]
    # meta.hints 原样回显（base_url 与建议模型）；只展示，不复合创建供应商。
    hints: dict[str, Any] | None = None
    schema_version: SchemaVersionInfo
    # 定义未声明（或声明值不是 semver）、或应用版本读不出时为 null。
    min_app_version: MinAppVersionInfo | None = None
    # 这份载荷被当成什么收的：端点定义、ComfyUI 的 API workflow，还是提交不了的 UI 格式。
    import_shape: ImportShape
    # 原始 API workflow 的包装结果；另两种形状为 null，客户端继续用自己手上那份。
    wrapped_definition: dict[str, Any] | None = None


class EndpointReferenceDescriptor(BaseModel):
    """引用该端点的模型行。删除被拒时随 409 下发，让用户知道去哪里解除引用。"""

    provider_id: int
    provider_display_name: str
    model_id: str
    model_display_name: str


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def endpoint_response(
    row: CustomEndpoint, installation: EndpointInstallationResponse | None = None
) -> CustomEndpointResponse:
    return CustomEndpointResponse(
        installation=installation,
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


def _routed_import(body: object, media_type: str) -> tuple[ImportShape, Any, DefinitionDiagnostics]:
    """按载荷形状分流，产出「待校验的定义 + 它的诊断」。

    UI 格式的 workflow 不进校验器：它没有 ``class_type``，拿定义 schema 去判只会报出一整屏与
    「导错了菜单项」无关的字段错误，一条结构化的拒绝才说得清该怎么办。
    """
    shape, definition = route_import_payload(body, media_type=media_type)
    if shape is ImportShape.COMFYUI_UI_WORKFLOW:
        return shape, definition, ui_workflow_refusal()
    return shape, definition, validate_definition(definition)


def _accepted(definition: object, diagnostics: DefinitionDiagnostics, _t: Translator) -> dict[str, Any]:
    """错误即 422 + 结构化诊断；通过后才是可落库的定义。

    保存与 ``validate`` 走同一个 :func:`validate_definition`，两处不可能给出不同判定——
    「validate 说行、保存说不行」正是共用校验器要消灭的分裂。警告不拦保存。
    """
    if diagnostics.errors or not isinstance(definition, dict):
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(diagnostics.to_payload(_t))
    return definition


def _accepted_definition(body: object, _t: Translator) -> dict[str, Any]:
    """整份替换走的入口：只认端点定义，不做导入分流。

    用一份原始 workflow 覆盖既有端点会把已确认的节点绑定一并抹掉，这不该是静默发生的事。
    """
    return _accepted(body, validate_definition(body), _t)


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
    """按 ``meta.author + meta.name`` 找同作者同名的既有端点。

    匹配只认这两项：键由系统分配、分享文件不带键，显示名也没有唯一约束，作者加名字是文件里
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


def _min_app_version_of(definition: object, read_app_version: Callable[[], str]) -> MinAppVersionInfo | None:
    if not isinstance(definition, Mapping):
        return None
    meta = definition.get("meta")
    required = meta.get("min_app_version") if isinstance(meta, Mapping) else None
    if not isinstance(required, str) or parse_semver(required) is None:
        return None
    try:
        current = read_app_version()
    except Exception:
        logger.exception("Failed to read app version")
        return None
    return MinAppVersionInfo(required=required, current=current, satisfied=meets_min_app_version(required, current))


def _reference_descriptors(references: list[EndpointReference]) -> list[dict[str, Any]]:
    return [EndpointReferenceDescriptor(**ref._asdict()).model_dump() for ref in references]


async def _invalidate_backend_cache() -> None:
    """定义原地更新立即对新任务生效：清掉按 provider / model 缓存的 backend 实例。

    已在轮询的任务持有构造时的 spec，改动只影响新任务——这是接受的行为，不做在途版本锁。
    """
    from server.services.tasks.generation_context import invalidate_backend_cache

    invalidate_backend_cache()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_endpoint(
    body: DefinitionBody,
    _t: Translator,
    media_type: ImportMediaType = "video",
    session: AsyncSession = Depends(get_async_session),
) -> CustomEndpointResponse:
    """创建（即导入）一条自定义调用端点。请求体是定义 JSON 原样，键由系统分配。

    请求体也可以是一份 ComfyUI 的原始 API workflow，此时服务端按 ``media_type`` 把它包成
    ComfyUI 端点定义——包装结果的节点绑定是空的，因此会被「提示词与产物必须绑定」挡下。
    """
    _, routed, diagnostics = _routed_import(body, media_type)
    definition = _accepted(routed, diagnostics, _t)
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
    return endpoint_response(row, await endpoint_installation(session, row.id))


@router.get("")
async def list_endpoints(session: AsyncSession = Depends(get_async_session)) -> CustomEndpointListResponse:
    """列出全部自定义调用端点（含定义本体）。"""
    rows = await CustomEndpointRepository(session).list_all()
    installations = await endpoint_installations(session)
    return CustomEndpointListResponse(endpoints=[endpoint_response(row, installations.get(row.id)) for row in rows])


@router.post("/validate")
async def validate_endpoint_definition(
    body: DefinitionBody,
    _t: Translator,
    read_app_version: Annotated[Callable[[], str], Depends(get_app_version_reader)],
    exclude_id: int | None = None,
    media_type: ImportMediaType = "video",
    session: AsyncSession = Depends(get_async_session),
) -> ValidateResponse:
    """保存前的单段确认：形状分流 + 校验诊断 + 同作者同名的既有端点 + 提示回显 + 版本档位 + 应用版本门槛。服务端不留任何状态。

    ``exclude_id`` 供编辑既有端点时排除自身，否则它总会把自己报成重复。载荷是原始 API workflow
    时，回的每一项都算在包装结果上——客户端接着要带走的就是它。
    """
    shape, definition, diagnostics = _routed_import(body, media_type)
    payload = diagnostics.to_payload(_t)
    file_version = _schema_version_of(definition)
    current_version = current_schema_version(definition)
    return ValidateResponse(
        errors=payload["errors"],
        warnings=payload["warnings"],
        duplicates=await _duplicates_of(CustomEndpointRepository(session), definition, exclude_id),
        hints=_hints_of(definition),
        schema_version=SchemaVersionInfo(
            file=file_version,
            current=current_version,
            level=schema_version_level(file_version, current_version),
        ),
        min_app_version=_min_app_version_of(definition, read_app_version),
        import_shape=shape,
        wrapped_definition=definition if shape is ImportShape.COMFYUI_API_WORKFLOW else None,
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
    return endpoint_response(row, await endpoint_installation(session, row.id))


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
    current = await repo.get(endpoint_id)
    if current is None:
        raise NotFoundError("custom_endpoint_not_found")
    if mirror.kind != current.kind:
        await _check_kind_change_keeps_attachments(repo, endpoint_id, mirror.kind, _t)
    if mirror.media_type != current.media_type:
        await _check_media_type_change_has_no_attachments(repo, endpoint_id, mirror.media_type)
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
    return endpoint_response(row, await endpoint_installation(session, row.id))


async def _check_kind_change_keeps_attachments(
    repo: CustomEndpointRepository,
    endpoint_id: int,
    new_kind: str,
    _t: Callable[..., str],
) -> None:
    """换了容器类型的定义不得把在用的挂接弄坏。

    「端点 × 供应商协议」的双向配对原先只在供应商侧写入时判（``docs/adr/0081``），而整份替换会
    原地改掉 ``kind``、键与模型行引用不变：一份 ComfyUI 定义因此能盖掉挂在 OpenAI 协议供应商上的
    声明式端点，反向亦然，两边在保存期都毫无征兆。判据与供应商侧同一个谓词，错挂在两条写入路径上
    都拒得住。先改模型行的挂接、再替换定义，两步各自仍然可行。
    """
    for attachment in await repo.list_attachments(make_endpoint_key(endpoint_id)):
        if endpoint_attachment_holds(endpoint_kind=new_kind, discovery_format=attachment.discovery_format):
            continue
        raise UnprocessableError(
            "custom_endpoint_kind_conflicts_with_attachment",
            model_id=attachment.model_id,
            provider=attachment.provider_display_name,
        )


async def _check_media_type_change_has_no_attachments(
    repo: CustomEndpointRepository,
    endpoint_id: int,
    new_media_type: str,
) -> None:
    """换了媒体类型的定义不得原地改掉在用模型行的所属媒体。

    模型行不自带媒体类型，它归哪一路由端点说了算。一份产图的 ComfyUI 定义被一份产视频的盖掉后，
    挂着它的模型行全部原地改判为视频行：本来图像与视频各一个默认模型的供应商，替换后成了两个视频
    默认，取默认模型时一次查出两行，生成期才炸。时长档位的归一同样按媒体类型走，也会一并错位。

    与换 ``kind`` 那条不同，这里没有「还成立」的情形可言：改的不是配对关系，而是模型行的归属。
    先摘掉挂接、再替换定义，两步各自仍然可行。
    """
    attachments = await repo.list_attachments(make_endpoint_key(endpoint_id))
    if attachments:
        raise UnprocessableError(
            "custom_endpoint_media_type_conflicts_with_attachment",
            media_type=new_media_type,
            model_id=attachments[0].model_id,
            provider=attachments[0].provider_display_name,
        )


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
