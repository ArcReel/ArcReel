"""Client for user-owned object storage.

Business callers access objects only through :class:`ObjectStorageClient`, never reimplementing
object-storage protocols. The initial TOS implementation uses the official Python SDK (``tos``,
synchronous, requests-based), moving each entire call into a thread. Connection secrets are used
only to construct SDK clients, never in logs/exception text; signed download URLs return only to
callers and never serve as durable storage locators.

Endpoints/regions: https://docs.volcengine.com/docs/6349/107356 ; BytePlus: https://docs.byteplus.com/en/docs/tos/docs-region-and-endpoint
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
    """Object-storage request failure: ``code`` / ``message`` preserve server text; ``kind`` selects UI copy."""

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
    """TOS S3-compatible endpoints (``tos-s3-*``) support S3 clients only; the TOS SDK rejects them."""
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
        # Client-error causes are usually connection failures; original text may include request URLs, so do not extract it.
        return ObjectStorageError("network", message=type(getattr(exc, "cause", None) or exc).__name__)
    return ObjectStorageError("provider", message=type(exc).__name__)


class ObjectStorageClient:
    """Object operations over one storage connection.

    Async methods run SDK calls in threads; ``*_sync`` methods serve synchronous transactions already
    in worker threads (formal commits upload within version locks). Never call them on event-loop threads.
    """

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

    def _call(self, fn_name: str, /, **kwargs: Any) -> Any:
        try:
            return getattr(self._client(), fn_name)(bucket=self.config.bucket, **kwargs)
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    async def _run(self, fn_name: str, /, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._call, fn_name, **kwargs)

    async def check(self) -> None:
        """Verify valid credentials and an existing, accessible bucket."""
        await self._run("head_bucket")

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        """Write an object and return its ETag."""
        output = await self._run("put_object", key=key, content=data, content_type=content_type)
        return str(getattr(output, "etag", "") or "")

    async def get_bytes(self, key: str) -> bytes:
        def read(output: Any) -> bytes:
            return output.read()

        output = await self._run("get_object", key=key)
        return await asyncio.to_thread(read, output)

    async def head(self, key: str) -> ObjectInfo:
        return _object_info(await self._run("head_object", key=key))

    def put_bytes_sync(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        output = self._call("put_object", key=key, content=data, content_type=content_type)
        return str(getattr(output, "etag", "") or "")

    def head_sync(self, key: str) -> ObjectInfo:
        return _object_info(self._call("head_object", key=key))

    async def delete(self, key: str) -> None:
        await self._run("delete_object", key=key)

    async def presign_get(self, key: str, *, expires_seconds: int) -> str:
        """Issue an expiring object download URL only to the image consumer; never log it or use it as a storage locator."""
        from tos import HttpMethodType

        output = await self._run(
            "pre_signed_url", http_method=HttpMethodType.Http_Method_Get, key=key, expires=expires_seconds
        )
        return str(output.signed_url)


def _object_info(output: Any) -> ObjectInfo:
    return ObjectInfo(
        size=int(getattr(output, "content_length", 0) or 0),
        etag=str(getattr(output, "etag", "") or ""),
        content_type=getattr(output, "content_type", None),
    )


#: Image suffix to Content-Type. Avoid mimetypes: its first call reads system files, blocking the event loop.
IMAGE_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def create_sdk_client(config: ObjectStorageConfig) -> Any:
    """Construct the TOS SDK client; tests replace the object-storage network boundary here."""
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
