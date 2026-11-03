"""对象存储连接配置 API。

实例至多一条连接（数据库唯一约束保证，并发首次保存时后到的请求按更新那一条处理），首期只支持 TOS。连接只是配置：保存后不改变任何图片的保存位置。密钥只经本接口写入，
读取返回掩码；测试连接的失败原因只带服务端错误码与原文摘要，不带签名或请求地址。

连接上还有正式素材对象时，不能更换桶、Endpoint、区域或删除连接（旧对象会失去可读取的位置），只能更新访问密钥。
另提供远端素材本地工作文件的清理入口，清理不删除对象存储里的任何对象。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import AfterValidator, BaseModel, Field, NonNegativeInt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.object_storage import ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import BadGatewayError, ConflictError, NotFoundError, UnprocessableError
from lib.infra.logging_utils import redact_diagnostic_text
from lib.material.formal_objects import live_object_count
from lib.material.global_library import cleanup_global_work_files
from lib.material.work_files import cleanup_work_cache
from lib.object_storage.client import ObjectStorageError, is_s3_compatible_endpoint
from lib.object_storage.connection import load_object_storage_client
from lib.project.project_manager import get_project_manager

router = APIRouter(prefix="/object-storage", tags=["Object Storage"])


def _strip(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


_Stripped = Annotated[str, AfterValidator(_strip), Field(max_length=2048)]
_StrippedOpt = Annotated[str | None, AfterValidator(_strip), Field(max_length=2048)]


class ObjectStorageConnectionResponse(BaseModel):
    kind: Literal["tos"]
    region: str
    endpoint: str
    bucket: str
    access_key_id_masked: str
    secret_access_key_masked: str
    updated_at: str


class ObjectStorageConnectionEnvelope(BaseModel):
    connection: ObjectStorageConnectionResponse | None


class SaveObjectStorageConnectionRequest(BaseModel):
    """保存连接。已有连接时，两个密钥字段留空表示保留已保存的值。"""

    kind: Literal["tos"] = "tos"
    region: _Stripped
    endpoint: _Stripped
    bucket: _Stripped
    access_key_id: _StrippedOpt = None
    secret_access_key: _StrippedOpt = None


class ObjectStorageTestResponse(BaseModel):
    success: bool


class WorkCacheCleanupRequest(BaseModel):
    """只清理闲置超过 ``min_idle_seconds`` 的工作文件；缺省为 0，正在使用的文件始终保留。"""

    min_idle_seconds: NonNegativeInt = 0


class WorkCacheCleanupResponse(BaseModel):
    removed: int
    freed_bytes: int
    kept_in_use: int


async def _ensure_connection_unused(session: AsyncSession, conn: ObjectStorageConnection) -> None:
    count = await live_object_count(session, conn.id)
    if count:
        raise ConflictError("object_storage_connection_in_use", count=count)


def _relocates(conn: ObjectStorageConnection, body: SaveObjectStorageConnectionRequest) -> bool:
    return (conn.kind, conn.region, conn.endpoint, conn.bucket) != (body.kind, body.region, body.endpoint, body.bucket)


def _to_response(conn: ObjectStorageConnection) -> ObjectStorageConnectionResponse:
    return ObjectStorageConnectionResponse(
        kind="tos",
        region=conn.region,
        endpoint=conn.endpoint,
        bucket=conn.bucket,
        access_key_id_masked=mask_secret(conn.access_key_id),
        secret_access_key_masked=mask_secret(conn.secret_access_key),
        updated_at=dt_to_iso(conn.updated_at) or "",
    )


@router.get("/connection", response_model=ObjectStorageConnectionEnvelope)
async def get_connection(session: AsyncSession = Depends(get_async_session)) -> ObjectStorageConnectionEnvelope:
    conn = await ObjectStorageConnectionRepository(session).get()
    return ObjectStorageConnectionEnvelope(connection=_to_response(conn) if conn else None)


@router.put("/connection", response_model=ObjectStorageConnectionResponse)
async def save_connection(
    body: SaveObjectStorageConnectionRequest, session: AsyncSession = Depends(get_async_session)
) -> ObjectStorageConnectionResponse:
    if not body.region or not body.endpoint or not body.bucket:
        raise UnprocessableError("object_storage_fields_required")
    if is_s3_compatible_endpoint(body.endpoint):
        raise UnprocessableError("object_storage_s3_endpoint_unsupported")
    try:
        conn = await _save_connection(session, body)
    except IntegrityError:
        # 另一请求同时首次保存，已建出唯一的当前连接：回滚后按更新那一条重做一次，校验照常进行。
        await session.rollback()
        conn = await _save_connection(session, body)
    return _to_response(conn)


async def _save_connection(session: AsyncSession, body: SaveObjectStorageConnectionRequest) -> ObjectStorageConnection:
    repo = ObjectStorageConnectionRepository(session)
    existing = await repo.get()
    if existing is None and (not body.access_key_id or not body.secret_access_key):
        raise UnprocessableError("object_storage_keys_required")
    if existing is not None and _relocates(existing, body):
        await _ensure_connection_unused(session, existing)
    conn = await repo.save(
        kind=body.kind,
        region=body.region,
        endpoint=body.endpoint,
        bucket=body.bucket,
        access_key_id=body.access_key_id or None,
        secret_access_key=body.secret_access_key or None,
    )
    await session.commit()
    return conn


@router.delete("/connection", status_code=204)
async def delete_connection(session: AsyncSession = Depends(get_async_session)) -> Response:
    repo = ObjectStorageConnectionRepository(session)
    existing = await repo.get()
    if existing is not None:
        await _ensure_connection_unused(session, existing)
    if not await repo.delete():
        raise NotFoundError("object_storage_not_configured")
    await session.commit()
    return Response(status_code=204)


_TEST_FAILURE_KEYS = {
    "auth": "object_storage_test_auth_failed",
    "denied": "object_storage_test_denied",
    "not_found": "object_storage_test_bucket_not_found",
    "network": "object_storage_test_unreachable",
    "invalid_config": "object_storage_s3_endpoint_unsupported",
    "provider": "object_storage_test_failed",
}


@router.post("/connection/test", response_model=ObjectStorageTestResponse)
async def check_connection(session: AsyncSession = Depends(get_async_session)) -> ObjectStorageTestResponse:
    """用已保存的连接确认凭证有效、桶存在且可以访问。"""
    client = await load_object_storage_client(session)
    if client is None:
        raise NotFoundError("object_storage_not_configured")
    try:
        await client.check()
    except ObjectStorageError as exc:
        diagnostic = {"code": exc.code, "status": exc.status_code, "message": redact_diagnostic_text(exc.message)}
        raise BadGatewayError(_TEST_FAILURE_KEYS[exc.kind]).with_diagnostic(diagnostic) from exc
    return ObjectStorageTestResponse(success=True)


@router.post("/work-cache/cleanup", response_model=WorkCacheCleanupResponse)
async def cleanup_cache(body: WorkCacheCleanupRequest | None = None) -> WorkCacheCleanupResponse:
    """删除远端素材闲置的本地工作文件（项目与全局资产库）；需要时预览与读取会从对象存储重新取回。"""
    request = body or WorkCacheCleanupRequest()
    min_idle = timedelta(seconds=request.min_idle_seconds)
    project_manager = get_project_manager()
    results = (
        await cleanup_work_cache(project_manager.projects_dir, min_idle=min_idle),
        await cleanup_global_work_files(project_manager.data_root, min_idle=min_idle),
    )
    return WorkCacheCleanupResponse(
        removed=sum(result.removed for result in results),
        freed_bytes=sum(result.freed_bytes for result in results),
        kept_in_use=sum(result.kept_in_use for result in results),
    )
