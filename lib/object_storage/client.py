"""用户自有对象存储的统一 S3 兼容客户端。

业务方只经 :class:`ObjectStorageClient` 存取对象，不各自实现对象存储协议。底层统一基于标准 Python S3 SDK
（``boto3`` / ``botocore``），采用 SigV4 签名并配置按需计算与校验校验和（``when_required``）以兼容主流非 AWS S3
服务商；同步调用整体放进工作线程。连接配置的密钥只在构造 SDK 客户端时使用，不进日志与异常文本；签名下载地址只返回给
调用方，不作为长期存储定位。
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

ObjectStorageKind = Literal["s3"]
OBJECT_STORAGE_KIND_S3: ObjectStorageKind = "s3"

AddressingStyle = Literal["auto", "virtual", "path"]
ADDRESSING_STYLES: tuple[AddressingStyle, ...] = ("auto", "virtual", "path")
DEFAULT_ADDRESSING_STYLE: AddressingStyle = "auto"

ObjectStorageErrorKind = Literal["auth", "not_found", "denied", "network", "invalid_config", "provider"]


@dataclass(frozen=True)
class ObjectStorageConfig:
    kind: str
    region: str
    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    addressing_style: str = DEFAULT_ADDRESSING_STYLE


class ObjectStorageError(Exception):
    """对象存储请求失败。``code`` / ``message`` 是服务端原文，``kind`` 是给界面选文案用的分类。"""

    def __init__(
        self, kind: ObjectStorageErrorKind, *, code: str = "", message: str = "", status_code: int | None = None
    ) -> None:
        super().__init__(f"{kind}: {code} {message}".strip())
        self.kind: ObjectStorageErrorKind = kind
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    etag: str
    content_type: str | None


_AUTH_CODES = frozenset(
    {
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "AccessKeyDisabled",
        "InvalidSecurityToken",
        "ExpiredToken",
        "InvalidToken",
        "AuthFailure",
        "InvalidClientTokenId",
    }
)
_NOT_FOUND_CODES = frozenset({"NoSuchBucket", "NoSuchKey", "NotFound", "404"})
_DENIED_CODES = frozenset({"AccessDenied", "AccountProblem", "AllAccessDisabled", "Forbidden", "403"})
_INVALID_CONFIG_CODES = frozenset(
    {
        "InvalidBucketName",
        "InvalidURI",
        "InvalidEndpoint",
        "InvalidLocationConstraint",
        "IllegalLocationConstraintException",
    }
)


def classify_s3_error(status_code: int | None, code: str) -> ObjectStorageErrorKind:
    """将 S3 服务端错误码与 HTTP 状态码映射为统一的错误分类。"""
    if code in _AUTH_CODES or status_code == 401:
        return "auth"
    if code in _NOT_FOUND_CODES or status_code == 404:
        return "not_found"
    if code in _DENIED_CODES or status_code == 403:
        return "denied"
    if code in _INVALID_CONFIG_CODES:
        return "invalid_config"
    return "provider"


def normalize_endpoint_url(endpoint: str) -> str:
    """规范化并校验 S3 Endpoint URL；无 scheme 的裸主机默认补 ``https://``。"""
    raw = endpoint.strip()
    if not raw or any(ord(ch) <= 32 or ord(ch) == 127 for ch in raw):
        raise ObjectStorageError("invalid_config", code="InvalidEndpoint")
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ObjectStorageError("invalid_config", code="InvalidEndpoint") from exc
    if parsed.scheme not in ("http", "https"):
        raise ObjectStorageError("invalid_config", code="InvalidEndpoint")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ObjectStorageError("invalid_config", code="InvalidEndpoint")
    if parsed.query or parsed.fragment:
        raise ObjectStorageError("invalid_config", code="InvalidEndpoint")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{netloc}{path}"


def _classify(exc: Exception) -> ObjectStorageError:
    from botocore.exceptions import (
        BotoCoreError,
        ClientError,
        EndpointVariantError,
        HTTPClientError,
        IncompleteReadError,
        InvalidRegionError,
        NoCredentialsError,
        NoRegionError,
        ParamValidationError,
        PartialCredentialsError,
        ResponseStreamingError,
        UnknownEndpointError,
        UnsupportedS3ConfigurationError,
    )
    from botocore.exceptions import (
        ConnectionError as BotoConnectionError,
    )

    if isinstance(exc, ClientError):
        response = exc.response or {}
        error = response.get("Error") or {}
        code = str(error.get("Code") or "")
        message = str(error.get("Message") or "")
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        kind = classify_s3_error(status, code)
        return ObjectStorageError(kind, code=code, message=message, status_code=status)
    config_errors = (
        ParamValidationError,
        InvalidRegionError,
        NoRegionError,
        NoCredentialsError,
        PartialCredentialsError,
        UnknownEndpointError,
        EndpointVariantError,
        UnsupportedS3ConfigurationError,
        ValueError,
    )
    if isinstance(exc, config_errors):
        return ObjectStorageError("invalid_config", code=type(exc).__name__)
    network_errors = (
        BotoConnectionError,
        HTTPClientError,
        IncompleteReadError,
        ResponseStreamingError,
        TimeoutError,
        OSError,
    )
    if isinstance(exc, network_errors):
        return ObjectStorageError("network", code=type(exc).__name__, message=type(exc).__name__)
    if isinstance(exc, BotoCoreError):
        return ObjectStorageError("provider", code=type(exc).__name__, message=type(exc).__name__)
    return ObjectStorageError("provider", message=type(exc).__name__)


class ObjectStorageClient:
    """一条对象存储连接上的对象操作。

    异步方法把 SDK 调用放进线程；``*_sync`` 方法供已经在工作线程里的同步事务直接调用（正式提交在版本锁内
    上传），不得在事件循环线程上调用。
    """

    def __init__(self, config: ObjectStorageConfig) -> None:
        if config.kind != OBJECT_STORAGE_KIND_S3:
            raise ObjectStorageError("invalid_config", code="UnsupportedKind")
        if config.addressing_style not in ADDRESSING_STYLES:
            raise ObjectStorageError("invalid_config", code="InvalidAddressingStyle")
        normalize_endpoint_url(config.endpoint)
        self.config = config
        self._sdk: Any = None

    def _client(self) -> Any:
        if self._sdk is None:
            self._sdk = create_sdk_client(self.config)
        return self._sdk

    def _call(self, fn_name: str, /, **kwargs: Any) -> Any:
        try:
            return getattr(self._client(), fn_name)(Bucket=self.config.bucket, **kwargs)
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    async def _run(self, fn_name: str, /, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._call, fn_name, **kwargs)

    async def check(self) -> None:
        """确认凭证有效、桶存在且可访问。"""
        await self._run("head_bucket")

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        """写入对象，返回 ETag。"""
        kwargs: dict[str, Any] = {"Key": key, "Body": data}
        if content_type is not None:
            kwargs["ContentType"] = content_type
        output = await self._run("put_object", **kwargs)
        return str((output or {}).get("ETag") or "")

    def _get_bytes_sync(self, key: str) -> bytes:
        try:
            output = self._client().get_object(Bucket=self.config.bucket, Key=key)
            body = output["Body"]
            with closing(body):
                return body.read()
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    async def get_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get_bytes_sync, key)

    async def head(self, key: str) -> ObjectInfo:
        return _object_info(await self._run("head_object", Key=key))

    def put_bytes_sync(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        kwargs: dict[str, Any] = {"Key": key, "Body": data}
        if content_type is not None:
            kwargs["ContentType"] = content_type
        output = self._call("put_object", **kwargs)
        return str((output or {}).get("ETag") or "")

    def head_sync(self, key: str) -> ObjectInfo:
        return _object_info(self._call("head_object", Key=key))

    async def delete(self, key: str) -> None:
        await self._run("delete_object", Key=key)

    def _presign_get_sync(self, key: str, expires_seconds: int) -> str:
        try:
            url = self._client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
            return str(url)
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    async def presign_get(self, key: str, *, expires_seconds: int) -> str:
        """为对象签发有时效的下载地址。地址只交给需要取图的一方，不记录、不当作存储定位。"""
        return await asyncio.to_thread(self._presign_get_sync, key, expires_seconds)


def _object_info(output: Any) -> ObjectInfo:
    mapping = output if isinstance(output, dict) else {}
    return ObjectInfo(
        size=int(mapping.get("ContentLength") or 0),
        etag=str(mapping.get("ETag") or ""),
        content_type=mapping.get("ContentType"),
    )


IMAGE_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def create_sdk_client(config: ObjectStorageConfig) -> Any:
    """构造标准 S3 SDK 客户端；测试在这一处替换对象存储的网络边界。"""
    from boto3.session import Session
    from botocore.config import Config

    endpoint_url = normalize_endpoint_url(config.endpoint)
    session = Session(
        aws_access_key_id=config.access_key_id.strip(),
        aws_secret_access_key=config.secret_access_key.strip(),
        region_name=config.region.strip(),
    )
    return session.client(
        "s3",
        endpoint_url=endpoint_url,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": config.addressing_style},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )
