"""对象存储连接配置 API。

实例至多一条连接，首期只支持 TOS。连接只是配置：保存后不改变任何图片的保存位置。密钥只经本接口写入，
读取返回掩码；测试连接的失败原因只带服务端错误码与原文摘要，不带签名或请求地址。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.object_storage import ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import BadGatewayError, NotFoundError, UnprocessableError
from lib.infra.logging_utils import redact_diagnostic_text
from lib.object_storage.client import ObjectStorageError, is_s3_compatible_endpoint
from lib.object_storage.connection import load_object_storage_client

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
    repo = ObjectStorageConnectionRepository(session)
    if await repo.get() is None and (not body.access_key_id or not body.secret_access_key):
        raise UnprocessableError("object_storage_keys_required")
    conn = await repo.save(
        kind=body.kind,
        region=body.region,
        endpoint=body.endpoint,
        bucket=body.bucket,
        access_key_id=body.access_key_id or None,
        secret_access_key=body.secret_access_key or None,
    )
    await session.commit()
    return _to_response(conn)


@router.delete("/connection", status_code=204)
async def delete_connection(session: AsyncSession = Depends(get_async_session)) -> Response:
    if not await ObjectStorageConnectionRepository(session).delete():
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
