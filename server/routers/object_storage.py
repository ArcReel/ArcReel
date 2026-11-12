"""对象存储连接配置 API。

实例至多一条连接（数据库唯一约束保证，并发首次保存时后到的请求按更新那一条处理），首期只支持 TOS。连接只是配置：保存后不改变任何图片的保存位置。密钥只经本接口写入，
读取返回掩码；测试连接的失败原因只带服务端错误码与原文摘要，不带签名或请求地址。

新增图片的保存位置（``material_storage_target``）是另一项全局设置，缺省本地，只能在已有连接时设为对象存储；
新增图片保存到对象存储期间不能删除连接。设置只影响之后提交的图片内容版本，已有版本照旧按各自的定位读取。

连接上还有仍可能被读取的正式素材对象（上传未确认，或仍被项目、全局资产库等非入库引用方持有）时，不能更换桶、Endpoint、
区域或删除连接（旧对象会失去可读取的位置），只能更新访问密钥。没有引用或只被入库任务持有的对象不阻止变更。
离开旧桶时，待删除对象按用户选择删除或放弃，记录一并清除。
另提供远端素材本地工作文件的清理入口，清理不删除对象存储里的任何对象。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import AfterValidator, BaseModel, Field, NonNegativeInt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.object_storage import ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import BadGatewayError, ConflictError, NotFoundError, UnprocessableError
from lib.infra.async_thread import run_noninterruptible_async
from lib.infra.logging_utils import redact_diagnostic_text
from lib.material.formal_objects import (
    NewContentTarget,
    connection_change_lock,
    holding_object_count,
    new_content_target,
    set_new_content_target,
)
from lib.material.global_library import cleanup_global_work_files
from lib.material.work_files import cleanup_work_cache
from lib.object_storage.client import ObjectStorageError, is_s3_compatible_endpoint
from lib.object_storage.connection import load_object_storage_client
from lib.object_storage.disposal import dispose_connection_objects, pending_ingest_count
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
    #: 新增图片内容版本的保存位置；只配置连接不改变它。
    new_image_target: NewContentTarget
    pending_ingest_tasks: int


class NewImageTargetBody(BaseModel):
    target: NewContentTarget


class SaveObjectStorageConnectionRequest(BaseModel):
    """保存连接。已有连接时，两个密钥字段留空表示保留已保存的值。"""

    kind: Literal["tos"] = "tos"
    region: _Stripped
    endpoint: _Stripped
    bucket: _Stripped
    access_key_id: _StrippedOpt = None
    secret_access_key: _StrippedOpt = None
    delete_remote_objects: bool = True


class ConnectionCleanupResponse(BaseModel):
    cleanup_failures: dict[str, int] = Field(default_factory=dict)


class SaveConnectionResponse(ObjectStorageConnectionResponse, ConnectionCleanupResponse):
    pass


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
    count = await holding_object_count(session, conn.id)
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
    return ObjectStorageConnectionEnvelope(
        connection=_to_response(conn) if conn else None,
        new_image_target=await new_content_target(session),
        pending_ingest_tasks=await pending_ingest_count(session, conn.id) if conn else 0,
    )


@router.put("/new-image-target", response_model=NewImageTargetBody)
async def save_new_image_target(
    body: NewImageTargetBody, session: AsyncSession = Depends(get_async_session)
) -> NewImageTargetBody:
    """选择之后新提交的图片内容版本保存在本地还是对象存储；已有图片与历史版本不搬迁。"""
    # 与删除连接串行：「连接存在」的核对与设置的提交在同一段里，不会留下目标为对象存储而连接已删除的状态
    async with connection_change_lock():
        if body.target == "object_storage" and await ObjectStorageConnectionRepository(session).get() is None:
            raise ConflictError("material_storage_target_needs_connection")
        await set_new_content_target(session, body.target)
        await session.commit()
    return body


@router.put("/connection", response_model=SaveConnectionResponse)
async def save_connection(
    body: SaveObjectStorageConnectionRequest, session: AsyncSession = Depends(get_async_session)
) -> SaveConnectionResponse:
    if not body.region or not body.endpoint or not body.bucket:
        raise UnprocessableError("object_storage_fields_required")
    if is_s3_compatible_endpoint(body.endpoint):
        raise UnprocessableError("object_storage_s3_endpoint_unsupported")

    async def apply_change() -> SaveConnectionResponse:
        # 与正式对象的首次登记串行：核对连接上没有仍占用它的正式对象与提交改动在同一段里完成，进行中的提交随后按新连接复核。
        async with connection_change_lock():
            try:
                conn, failures = await _save_connection(session, body)
            except IntegrityError:
                # 另一请求同时首次保存，已建出唯一的当前连接：回滚后按更新那一条重做一次，校验照常进行。
                await session.rollback()
                conn, failures = await _save_connection(session, body)
        return SaveConnectionResponse(**_to_response(conn).model_dump(), cleanup_failures=failures)

    return await run_noninterruptible_async(apply_change())


async def _save_connection(
    session: AsyncSession, body: SaveObjectStorageConnectionRequest
) -> tuple[ObjectStorageConnection, dict[str, int]]:
    async with AsyncExitStack() as stack:
        repo = ObjectStorageConnectionRepository(session)
        existing = await repo.get()
        if existing is None and (not body.access_key_id or not body.secret_access_key):
            raise UnprocessableError("object_storage_keys_required")
        failures: dict[str, int] = {}
        if existing is not None and _relocates(existing, body):
            await _ensure_connection_unused(session, existing)
            if (existing.endpoint, existing.bucket) != (body.endpoint, body.bucket):
                failures = await stack.enter_async_context(
                    dispose_connection_objects(session, existing, delete_remote=body.delete_remote_objects)
                )
        conn = await repo.save(
            kind=body.kind,
            region=body.region,
            endpoint=body.endpoint,
            bucket=body.bucket,
            access_key_id=body.access_key_id or None,
            secret_access_key=body.secret_access_key or None,
        )
        await session.commit()
        return conn, failures


@router.delete("/connection", response_model=ConnectionCleanupResponse)
async def delete_connection(
    delete_remote_objects: bool = True, session: AsyncSession = Depends(get_async_session)
) -> ConnectionCleanupResponse:
    async def apply_change() -> ConnectionCleanupResponse:
        async with connection_change_lock(), AsyncExitStack() as stack:
            repo = ObjectStorageConnectionRepository(session)
            existing = await repo.get()
            failures: dict[str, int] = {}
            if existing is not None:
                await _ensure_connection_unused(session, existing)
                if await new_content_target(session) == "object_storage":
                    # 删除后每次图片提交都会失败：先把新增图片改回本地保存
                    raise ConflictError("object_storage_connection_is_new_image_target")
                failures = await stack.enter_async_context(
                    dispose_connection_objects(session, existing, delete_remote=delete_remote_objects)
                )
            if not await repo.delete():
                raise NotFoundError("object_storage_not_configured")
            await session.commit()
        return ConnectionCleanupResponse(cleanup_failures=failures)

    return await run_noninterruptible_async(apply_change())


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
