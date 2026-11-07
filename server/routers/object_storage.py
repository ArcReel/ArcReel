"""Object-storage connection configuration API.

At most one connection per instance, enforced by a database uniqueness constraint; concurrent initial saves update the
winning connection. Initially only TOS is supported. Saving connection configuration never changes image storage locations.
Only these endpoints write secrets; reads return masks. Connection-test failures expose only server error codes and original
message summaries, never signatures or request URLs.

New-image storage location (``material_storage_target``) is a separate global setting, local by default. Object storage requires
an existing connection, which cannot be deleted while new images target it. Changes affect only subsequently submitted image
content versions; existing versions retain their own read locations.

While the connection has formal material objects that may still be read (unconfirmed uploads, saved objects or referenced objects),
its bucket, Endpoint and region cannot change and it cannot be deleted, or old objects would lose readable locations. Only access
keys may change. Unreferenced objects awaiting compensating deletion do not block changes: detach them from the connection,
stop automatic deletion and show counts in connection settings alongside relay copies.
Also provide cleanup for local working files of remote materials, without deleting any object-storage objects."""

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
from lib.material.formal_objects import (
    NewContentTarget,
    connection_change_lock,
    count_unreleased_objects,
    detach_orphaned_objects,
    holding_object_count,
    new_content_target,
    set_new_content_target,
)
from lib.material.global_library import cleanup_global_work_files
from lib.material.work_files import cleanup_work_cache
from lib.object_storage.client import ObjectStorageError, is_s3_compatible_endpoint
from lib.object_storage.connection import load_object_storage_client
from lib.object_storage.relay import count_retained_relay_objects
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
    #: Storage location for new image versions; configuring the connection alone does not change it.
    new_image_target: NewContentTarget
    #: Uncleared relay-copy count; changing bucket/Endpoint stops automatic deletion of these copies in the old bucket.
    retained_relay_objects: int
    #: Undeleted image objects with no references, mostly left by failed saves; changing bucket/Endpoint also stops their automatic deletion.
    unreleased_image_objects: int


class NewImageTargetBody(BaseModel):
    target: NewContentTarget


class SaveObjectStorageConnectionRequest(BaseModel):
    """Save the connection. For existing connections, empty secret fields preserve stored values."""

    kind: Literal["tos"] = "tos"
    region: _Stripped
    endpoint: _Stripped
    bucket: _Stripped
    access_key_id: _StrippedOpt = None
    secret_access_key: _StrippedOpt = None


class ObjectStorageTestResponse(BaseModel):
    success: bool


class WorkCacheCleanupRequest(BaseModel):
    """Clean only working files idle longer than ``min_idle_seconds``, default 0; always retain files in use."""

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
        retained_relay_objects=await count_retained_relay_objects(session),
        unreleased_image_objects=await count_unreleased_objects(session),
    )


@router.put("/new-image-target", response_model=NewImageTargetBody)
async def save_new_image_target(
    body: NewImageTargetBody, session: AsyncSession = Depends(get_async_session)
) -> NewImageTargetBody:
    """Choose local or object storage for subsequently submitted image versions; do not move existing images or historical versions."""
    # Serialize with connection deletion: checking existence and committing settings together prevents an object-storage target without a connection.
    async with connection_change_lock():
        if body.target == "object_storage" and await ObjectStorageConnectionRepository(session).get() is None:
            raise ConflictError("material_storage_target_needs_connection")
        await set_new_content_target(session, body.target)
        await session.commit()
    return body


@router.put("/connection", response_model=ObjectStorageConnectionResponse)
async def save_connection(
    body: SaveObjectStorageConnectionRequest, session: AsyncSession = Depends(get_async_session)
) -> ObjectStorageConnectionResponse:
    if not body.region or not body.endpoint or not body.bucket:
        raise UnprocessableError("object_storage_fields_required")
    if is_s3_compatible_endpoint(body.endpoint):
        raise UnprocessableError("object_storage_s3_endpoint_unsupported")
    # Serialize with initial formal-object registration: check for occupying objects and commit changes together; active submissions recheck against the new connection.
    async with connection_change_lock():
        try:
            conn = await _save_connection(session, body)
        except IntegrityError:
            # Another concurrent initial save created the unique connection; roll back and retry as an update with normal validation.
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
        await detach_orphaned_objects(session, existing, endpoint=body.endpoint, bucket=body.bucket)
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
    async with connection_change_lock():
        repo = ObjectStorageConnectionRepository(session)
        existing = await repo.get()
        if existing is not None:
            await _ensure_connection_unused(session, existing)
            if await new_content_target(session) == "object_storage":
                # Deletion would fail every image submission; first switch new images back to local storage.
                raise ConflictError("object_storage_connection_is_new_image_target")
            await detach_orphaned_objects(session, existing)
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
    """Use the saved connection to verify credentials and confirm the bucket exists and is accessible."""
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
    """Delete idle local working files for remote materials in projects/global assets; previews and reads retrieve them from object storage when needed."""
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
