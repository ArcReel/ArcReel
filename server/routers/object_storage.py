"""对象存储连接配置 API。

实例至多一条连接（数据库唯一约束保证，并发首次保存时后到的请求按更新那一条处理），统一采用标准 S3 兼容协议
（``kind="s3"``）。连接只是配置：保存后不改变任何图片的保存位置。密钥只经本接口写入，读取返回掩码；
测试连接的失败原因只带服务端错误码与原文摘要，不带签名或请求地址。

新增图片的保存位置（``material_storage_target``）是另一项全局设置，缺省本地，只能在已有连接时设为对象存储；
新增图片保存到对象存储期间不能删除连接。设置只影响之后提交的图片内容版本，已有版本照旧按各自的定位读取。

连接上还有仍可能被读取的正式素材对象（上传未确认，或仍被项目、全局资产库等非入库引用方持有）时，不能更换桶、Endpoint、
区域或删除连接（旧对象会失去可读取的位置），只能更新访问密钥或寻址风格。没有引用或只被入库任务持有的对象不阻止变更。
真正更换桶或 Endpoint 时分配新的连接 ID，且要求显式填写新目标的访问密钥；离开旧桶时，待删除对象按用户选择删除或放弃，
记录一并清除。另提供远端素材本地工作文件的清理入口，清理不删除对象存储里的任何对象。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from pydantic import AfterValidator, BaseModel, Field, NonNegativeInt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.object_storage import ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import ConflictError, NotFoundError, UnprocessableError
from lib.infra.async_thread import run_noninterruptible_async
from lib.material.formal_objects import (
    NewContentTarget,
    connection_change_lock,
    holding_object_count,
    new_content_target,
    set_new_content_target,
)
from lib.material.global_library import cleanup_global_work_files
from lib.material.work_files import cleanup_work_cache
from lib.object_storage.client import (
    DEFAULT_ADDRESSING_STYLE,
    OBJECT_STORAGE_KIND_S3,
    AddressingStyle,
    ObjectStorageClient,
    ObjectStorageError,
    ObjectStorageKind,
    normalize_endpoint_url,
)
from lib.object_storage.connection import config_from_connection
from lib.object_storage.disposal import dispose_connection_objects, pending_ingest_count
from lib.object_storage.probe import (
    PROBE_STEP_ORDER,
    ObjectStorageProbeResult,
    ProbeErrorKind,
    ProbeStepName,
    ProbeStepResult,
    ProbeStepStatus,
    probe_connection,
    sanitize_probe_diagnostic,
)
from lib.project.project_manager import get_project_manager
from server.i18n import Translator

router = APIRouter(prefix="/object-storage", tags=["Object Storage"])


def _strip(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


_Stripped = Annotated[str, AfterValidator(_strip), Field(max_length=2048)]
_StrippedOpt = Annotated[str | None, AfterValidator(_strip), Field(max_length=2048)]


class ObjectStorageConnectionResponse(BaseModel):
    kind: ObjectStorageKind
    region: str
    endpoint: str
    bucket: str
    addressing_style: AddressingStyle
    needs_endpoint_review: bool = False
    access_key_id_masked: str
    secret_access_key_masked: str
    updated_at: str


class ObjectStorageConnectionEnvelope(BaseModel):
    connection: ObjectStorageConnectionResponse | None
    new_image_target: NewContentTarget
    pending_ingest_tasks: int


class NewImageTargetBody(BaseModel):
    target: NewContentTarget


class SaveObjectStorageConnectionRequest(BaseModel):
    """保存连接。同目标（Endpoint 与桶未变）时两个密钥字段留空表示保留已保存的值；换桶或换 Endpoint 必须显式提供新凭证。"""

    kind: ObjectStorageKind = OBJECT_STORAGE_KIND_S3
    region: _Stripped
    endpoint: _Stripped
    bucket: _Stripped
    addressing_style: AddressingStyle = DEFAULT_ADDRESSING_STYLE
    access_key_id: _StrippedOpt = None
    secret_access_key: _StrippedOpt = None
    delete_remote_objects: bool = True


class ConnectionCleanupResponse(BaseModel):
    cleanup_failures: dict[str, int] = Field(default_factory=dict)


class SaveConnectionResponse(ObjectStorageConnectionResponse, ConnectionCleanupResponse):
    pass


class ObjectStorageProbeStepResponse(BaseModel):
    step: ProbeStepName
    status: ProbeStepStatus
    error_kind: ProbeErrorKind | None = None
    message: str | None = None
    diagnostic: dict[str, Any] | None = None


class ObjectStorageTestResponse(BaseModel):
    success: bool
    steps: list[ObjectStorageProbeStepResponse]
    residual_object_key: str | None = None


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


def _same_endpoint(existing_endpoint: str, raw_endpoint: str, normalized_endpoint: str) -> bool:
    if existing_endpoint == raw_endpoint:
        return True
    try:
        return normalize_endpoint_url(existing_endpoint) == normalized_endpoint
    except ObjectStorageError:
        return False


def _to_response(conn: ObjectStorageConnection) -> ObjectStorageConnectionResponse:
    return ObjectStorageConnectionResponse(
        kind=OBJECT_STORAGE_KIND_S3,
        region=conn.region,
        endpoint=conn.endpoint,
        bucket=conn.bucket,
        addressing_style=cast(AddressingStyle, conn.addressing_style),
        needs_endpoint_review=bool(conn.needs_endpoint_review),
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
    try:
        normalized_endpoint = normalize_endpoint_url(body.endpoint)
    except ObjectStorageError as exc:
        raise UnprocessableError("object_storage_invalid_config") from exc

    async def apply_change() -> SaveConnectionResponse:
        async with connection_change_lock():
            try:
                conn, failures = await _save_connection(session, body, normalized_endpoint)
            except IntegrityError:
                await session.rollback()
                conn, failures = await _save_connection(session, body, normalized_endpoint)
        return SaveConnectionResponse(**_to_response(conn).model_dump(), cleanup_failures=failures)

    return await run_noninterruptible_async(apply_change())


async def _save_connection(
    session: AsyncSession, body: SaveObjectStorageConnectionRequest, normalized_endpoint: str
) -> tuple[ObjectStorageConnection, dict[str, int]]:
    async with AsyncExitStack() as stack:
        repo = ObjectStorageConnectionRepository(session)
        existing = await repo.get()
        failures: dict[str, int] = {}
        if existing is None:
            if not body.access_key_id or not body.secret_access_key:
                raise UnprocessableError("object_storage_keys_required")
            next_endpoint = normalized_endpoint
            needs_review = False
        else:
            same_endpoint = _same_endpoint(existing.endpoint, body.endpoint, normalized_endpoint)
            target_changed = (not same_endpoint) or (existing.bucket != body.bucket)
            if target_changed:
                if not body.access_key_id or not body.secret_access_key:
                    raise UnprocessableError("object_storage_target_keys_required")
                await _ensure_connection_unused(session, existing)
                failures = await stack.enter_async_context(
                    dispose_connection_objects(session, existing, delete_remote=body.delete_remote_objects)
                )
                next_endpoint = normalized_endpoint
                needs_review = False
            else:
                if (existing.kind, existing.region) != (body.kind, body.region):
                    await _ensure_connection_unused(session, existing)
                next_endpoint = existing.endpoint
                needs_review = bool(existing.needs_endpoint_review)
        conn = await repo.save(
            kind=body.kind,
            region=body.region,
            endpoint=next_endpoint,
            bucket=body.bucket,
            addressing_style=body.addressing_style,
            access_key_id=body.access_key_id or None,
            secret_access_key=body.secret_access_key or None,
            needs_endpoint_review=needs_review,
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
                    raise ConflictError("object_storage_connection_is_new_image_target")
                failures = await stack.enter_async_context(
                    dispose_connection_objects(session, existing, delete_remote=delete_remote_objects)
                )
            if not await repo.delete():
                raise NotFoundError("object_storage_not_configured")
            await session.commit()
        return ConnectionCleanupResponse(cleanup_failures=failures)

    return await run_noninterruptible_async(apply_change())


_TEST_FAILURE_KEYS: dict[ProbeErrorKind, str] = {
    "auth": "object_storage_test_auth_failed",
    "denied": "object_storage_test_denied",
    "not_found": "object_storage_test_bucket_not_found",
    "network": "object_storage_test_unreachable",
    "invalid_config": "object_storage_invalid_config",
    "provider": "object_storage_test_failed",
    "metadata_mismatch": "object_storage_probe_metadata_mismatch",
    "digest_mismatch": "object_storage_probe_digest_mismatch",
}

_STEP_DENIED_KEYS: dict[ProbeStepName, str] = {
    "head_bucket": "object_storage_test_denied",
    "put_object": "object_storage_probe_put_denied",
    "head_object": "object_storage_probe_head_denied",
    "presigned_get": "object_storage_probe_presigned_denied",
    "delete_object": "object_storage_probe_delete_denied",
}


def _probe_step_message_key(step: ProbeStepResult) -> str:
    if step.error_kind == "denied":
        return _STEP_DENIED_KEYS[step.step]
    if step.error_kind is None:
        return "object_storage_test_failed"
    return _TEST_FAILURE_KEYS[step.error_kind]


def _probe_step_response(step: ProbeStepResult, _t: Translator) -> ObjectStorageProbeStepResponse:
    if step.status != "failed":
        return ObjectStorageProbeStepResponse(step=step.step, status=step.status)
    diagnostic: dict[str, Any] | None = None
    if step.error_code or step.status_code is not None or step.error_message:
        diagnostic = {
            "code": step.error_code,
            "status": step.status_code,
            "message": step.error_message,
        }
    return ObjectStorageProbeStepResponse(
        step=step.step,
        status=step.status,
        error_kind=step.error_kind,
        message=_t(_probe_step_message_key(step)),
        diagnostic=diagnostic,
    )


def _probe_result_response(result: ObjectStorageProbeResult, _t: Translator) -> ObjectStorageTestResponse:
    return ObjectStorageTestResponse(
        success=result.success,
        steps=[_probe_step_response(step, _t) for step in result.steps],
        residual_object_key=result.residual_object_key,
    )


@router.post("/connection/test", response_model=ObjectStorageTestResponse)
async def check_connection(
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ObjectStorageTestResponse:
    """用已保存的连接执行分级探针（桶探测、写入、元信息、无登录态预签名下载校验与探针清理）。"""
    repo = ObjectStorageConnectionRepository(session)
    conn = await repo.get()
    if conn is None:
        raise NotFoundError("object_storage_not_configured")
    try:
        client = ObjectStorageClient(config_from_connection(conn))
    except ObjectStorageError as exc:
        failed_head = ProbeStepResult(
            step="head_bucket",
            status="failed",
            error_kind=exc.kind,
            error_code=exc.code,
            status_code=exc.status_code,
            error_message=sanitize_probe_diagnostic(exc.message),
        )
        steps = (failed_head, *(ProbeStepResult(step=name, status="skipped") for name in PROBE_STEP_ORDER[1:]))
        return _probe_result_response(ObjectStorageProbeResult(success=False, steps=steps), _t)

    result = await probe_connection(client)
    if result.success and conn.needs_endpoint_review:
        await repo.clear_endpoint_review(conn.id)
        await session.commit()
    return _probe_result_response(result, _t)


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
