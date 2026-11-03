"""用户自有对象存储的客户端。

业务方只经 :class:`ObjectStorageClient` 存取对象，不各自实现对象存储协议。首期实现是 TOS，基于官方
Python SDK（``tos``，同步、基于 requests），每个调用整体放进线程。连接配置的密钥只在构造 SDK 客户端时
使用，不进日志与异常文本；签名下载地址只返回给调用方，不作为长期存储定位。

endpoint 与区域：https://docs.volcengine.com/docs/6349/107356 ；BytePlus 站点：https://docs.byteplus.com/en/docs/tos/docs-region-and-endpoint
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

ObjectStorageErrorKind = Literal["auth", "not_found", "denied", "network", "invalid_config", "provider"]


@dataclass(frozen=True)
class ObjectStorageConfig:
    kind: str
    region: str
    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str


class ObjectStorageError(Exception):
    """对象存储请求失败。``code`` / ``message`` 是服务端原文，``kind`` 是给界面选文案用的分类。"""

    def __init__(
        self, kind: ObjectStorageErrorKind, *, code: str = "", message: str = "", status_code: int | None = None
    ) -> None:
        super().__init__(f"{kind}: {code} {message}".strip())
        self.kind = kind
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    etag: str
    content_type: str | None


_AUTH_CODES = frozenset({"InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessKeyDisabled", "InvalidSecurityToken"})


def is_s3_compatible_endpoint(endpoint: str) -> bool:
    """TOS 的 S3 兼容 endpoint（``tos-s3-*``）只供 S3 客户端使用，TOS SDK 不接受。"""
    host = endpoint.strip().lower().removeprefix("https://").removeprefix("http://")
    return host.startswith("tos-s3-")


def _classify(exc: Exception) -> ObjectStorageError:
    from tos.exceptions import TosClientError, TosServerError

    if isinstance(exc, TosServerError):
        code = str(getattr(exc, "code", "") or "")
        status = getattr(exc, "status_code", None)
        message = str(getattr(exc, "message", "") or "")
        if code in _AUTH_CODES:
            kind: ObjectStorageErrorKind = "auth"
        elif status == 404:
            kind = "not_found"
        elif status == 403:
            kind = "denied"
        else:
            kind = "provider"
        return ObjectStorageError(kind, code=code, message=message, status_code=status)
    if isinstance(exc, TosClientError):
        # 客户端错误的 cause 多为连接失败；原文可能带请求地址，不取出。
        return ObjectStorageError("network", message=type(getattr(exc, "cause", None) or exc).__name__)
    return ObjectStorageError("provider", message=type(exc).__name__)


class ObjectStorageClient:
    """一条对象存储连接上的对象操作；方法都是异步的，SDK 调用在线程中执行。"""

    def __init__(self, config: ObjectStorageConfig) -> None:
        if config.kind != "tos":
            raise ObjectStorageError("invalid_config", code="UnsupportedKind")
        if is_s3_compatible_endpoint(config.endpoint):
            raise ObjectStorageError("invalid_config", code="S3EndpointUnsupported")
        self.config = config
        self._sdk: Any = None

    def _client(self) -> Any:
        if self._sdk is None:
            self._sdk = create_sdk_client(self.config)
        return self._sdk

    async def _run(self, fn_name: str, /, **kwargs: Any) -> Any:
        def call() -> Any:
            return getattr(self._client(), fn_name)(bucket=self.config.bucket, **kwargs)

        try:
            return await asyncio.to_thread(call)
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    async def check(self) -> None:
        """确认凭证有效、桶存在且可访问。"""
        await self._run("head_bucket")

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        """写入对象，返回 ETag。"""
        output = await self._run("put_object", key=key, content=data, content_type=content_type)
        return str(getattr(output, "etag", "") or "")

    async def get_bytes(self, key: str) -> bytes:
        def read(output: Any) -> bytes:
            return output.read()

        output = await self._run("get_object", key=key)
        return await asyncio.to_thread(read, output)

    async def head(self, key: str) -> ObjectInfo:
        output = await self._run("head_object", key=key)
        return ObjectInfo(
            size=int(getattr(output, "content_length", 0) or 0),
            etag=str(getattr(output, "etag", "") or ""),
            content_type=getattr(output, "content_type", None),
        )

    async def delete(self, key: str) -> None:
        await self._run("delete_object", key=key)

    async def presign_get(self, key: str, *, expires_seconds: int) -> str:
        """为对象签发有时效的下载地址。地址只交给需要取图的一方，不记录、不当作存储定位。"""
        from tos import HttpMethodType

        output = await self._run(
            "pre_signed_url", http_method=HttpMethodType.Http_Method_Get, key=key, expires=expires_seconds
        )
        return str(output.signed_url)


def create_sdk_client(config: ObjectStorageConfig) -> Any:
    """构造 TOS SDK 客户端；测试在这一处替换对象存储的网络边界。"""
    import tos

    return tos.TosClientV2(
        ak=config.access_key_id.strip(),
        sk=config.secret_access_key.strip(),
        endpoint=config.endpoint.strip(),
        region=config.region.strip(),
        max_retry_count=1,
        connection_time=10,
        socket_timeout=30,
    )
