"""真实 botocore 传输层契约测试：不替换 create_sdk_client，在 URLLib3Session.send 边界核验真实请求与签名。"""

from __future__ import annotations

import io
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from botocore.awsrequest import AWSPreparedRequest, AWSResponse
from botocore.exceptions import EndpointConnectionError
from botocore.httpsession import URLLib3Session

from lib.object_storage.client import (
    ObjectStorageClient,
    ObjectStorageConfig,
    ObjectStorageError,
    normalize_endpoint_url,
)
from lib.object_storage.probe import PROBE_PREFIX, probe_connection

AK = "TEST-CONTRACT-AK-0001"
SK = "TEST-CONTRACT-SK-0001"


class _CapturedTransport:
    """在 botocore HTTP 传输层拦截真实 AWSPreparedRequest，返回规范 AWSResponse。"""

    def __init__(self) -> None:
        self.requests: list[AWSPreparedRequest] = []
        self.objects: dict[tuple[str, str], tuple[bytes, str | None]] = {}
        self.next_response: AWSResponse | Exception | None = None

    def send(self, request: AWSPreparedRequest) -> AWSResponse:
        self.requests.append(request)
        if isinstance(self.next_response, Exception):
            raise self.next_response
        if self.next_response is not None:
            return self.next_response
        parsed = urlsplit(request.url)
        key = (parsed.netloc, parsed.path)
        if request.method == "PUT":
            body = request.body
            if isinstance(body, bytes):
                raw = body
            elif hasattr(body, "read"):
                raw = body.read()
            else:
                raw = b""
            ctype = request.headers.get("Content-Type")
            content_type = ctype.decode("utf-8") if isinstance(ctype, bytes) else ctype
            self.objects[key] = (raw, content_type)
            return _aws_response(request.url, 200, {"ETag": '"etag-put"'}, b"")
        if request.method == "HEAD":
            segments = [part for part in parsed.path.split("/") if part]
            if not segments or (len(segments) == 1 and key not in self.objects):
                return _aws_response(request.url, 200, {}, b"")
            if key not in self.objects:
                return _aws_response(request.url, 404, {}, b"")
            data, content_type = self.objects[key]
            headers = {"Content-Length": str(len(data)), "ETag": '"etag-put"'}
            if content_type:
                headers["Content-Type"] = content_type
            return _aws_response(request.url, 200, headers, b"")
        if request.method == "GET":
            if key not in self.objects:
                return _xml_error(request.url, 404, "NoSuchKey", "not found")
            data, content_type = self.objects[key]
            headers = {"Content-Length": str(len(data)), "ETag": '"etag-put"'}
            if content_type:
                headers["Content-Type"] = content_type
            return _aws_response(request.url, 200, headers, data)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return _aws_response(request.url, 204, {}, b"")
        return _aws_response(request.url, 405, {}, b"")


class _RawStream(io.BytesIO):
    """兼容 botocore AWSResponse.raw 的最小流包装（提供 ``stream()`` 迭代块）。"""

    def stream(self, amt: int | None = None, decode_content: bool | None = None):
        data = self.getvalue()
        if data:
            yield data


def _aws_response(url: str, status: int, headers: dict[str, str], body: bytes) -> AWSResponse:
    return AWSResponse(url, status, headers, _RawStream(body))


def _xml_error(url: str, status: int, code: str, message: str) -> AWSResponse:
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>{message}</Message></Error>'.encode()
    )
    return _aws_response(url, status, {"Content-Type": "application/xml"}, xml)


def _header(request: AWSPreparedRequest, name: str) -> str | None:
    target = name.lower()
    for key, value in request.headers.items():
        if key.lower() == target:
            return value.decode("utf-8") if isinstance(value, bytes) else str(value)
    return None


@pytest.fixture
def transport(monkeypatch) -> _CapturedTransport:
    captured = _CapturedTransport()
    monkeypatch.setattr(URLLib3Session, "send", lambda _session, request: captured.send(request))
    return captured


@pytest.mark.parametrize(
    ("style", "endpoint", "region", "bucket", "expected_host", "expected_path"),
    [
        (
            "virtual",
            "https://tos-s3-cn-beijing.volces.com",
            "cn-beijing",
            "studio-assets",
            "studio-assets.tos-s3-cn-beijing.volces.com",
            "/arcreel-materials/sha256/ab/abcd.png",
        ),
        (
            "path",
            "http://127.0.0.1:9000",
            "us-east-1",
            "studio-assets",
            "127.0.0.1:9000",
            "/studio-assets/arcreel-materials/sha256/ab/abcd.png",
        ),
        (
            "auto",
            "https://account123.r2.cloudflarestorage.com",
            "auto",
            "studio-assets",
            "account123.r2.cloudflarestorage.com",
            "/studio-assets/arcreel-materials/sha256/ab/abcd.png",
        ),
    ],
)
async def test_botocore_transport_contract_headers_addressing_and_checksums(
    transport: _CapturedTransport,
    style: Any,
    endpoint: str,
    region: str,
    bucket: str,
    expected_host: str,
    expected_path: str,
):
    config = ObjectStorageConfig(
        kind="s3",
        region=region,
        endpoint=endpoint,
        bucket=bucket,
        access_key_id=AK,
        secret_access_key=SK,
        addressing_style=style,
    )
    client = ObjectStorageClient(config)
    key = "arcreel-materials/sha256/ab/abcd.png"
    payload = b"\x89PNG\r\n\x1a\ncontract-bytes"

    await client.check()
    etag = await client.put_bytes(key, payload, content_type="image/png")
    info = await client.head(key)
    downloaded = await client.get_bytes(key)
    presigned = await client.presign_get(key, expires_seconds=900)
    await client.delete(key)

    assert etag == '"etag-put"'
    assert (info.size, info.etag, info.content_type) == (len(payload), '"etag-put"', "image/png")
    assert downloaded == payload

    put_req = next(req for req in transport.requests if req.method == "PUT")
    parsed_put = urlsplit(put_req.url)
    assert parsed_put.netloc == expected_host
    assert parsed_put.path == expected_path
    assert _header(put_req, "Content-Type") == "image/png"
    auth = _header(put_req, "Authorization") or ""
    assert auth.startswith("AWS4-HMAC-SHA256 ")
    assert f"/{region}/s3/aws4_request" in auth
    assert _header(put_req, "x-amz-checksum-crc32") is None
    assert _header(put_req, "x-amz-sdk-checksum-algorithm") is None
    assert "aws-chunked" not in (_header(put_req, "Content-Encoding") or "")
    assert "STREAMING-AWS4-HMAC-SHA256-PAYLOAD" not in (_header(put_req, "x-amz-content-sha256") or "")

    parsed_presign = urlsplit(presigned)
    query = parse_qs(parsed_presign.query)
    assert parsed_presign.netloc == expected_host
    assert parsed_presign.path == expected_path
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["X-Amz-Expires"] == ["900"]
    assert f"/{region}/s3/aws4_request" in query["X-Amz-Credential"][0]
    assert "X-Amz-Checksum-Mode" not in query
    assert "x-amz-checksum-crc32" not in query


def test_sync_put_and_head_contract(transport: _CapturedTransport):
    config = ObjectStorageConfig(
        kind="s3",
        region="cn-beijing",
        endpoint="tos-s3-cn-beijing.volces.com",
        bucket="sync-bucket",
        access_key_id=AK,
        secret_access_key=SK,
        addressing_style="virtual",
    )
    client = ObjectStorageClient(config)
    key = "arcreel-materials/sha256/00/0000.png"
    payload = b"sync-bytes"

    assert client.put_bytes_sync(key, payload, content_type="image/png") == '"etag-put"'
    info = client.head_sync(key)
    assert info.size == len(payload)
    assert info.content_type == "image/png"
    put_req = next(req for req in transport.requests if req.method == "PUT")
    assert urlsplit(put_req.url).netloc == "sync-bucket.tos-s3-cn-beijing.volces.com"


@pytest.mark.parametrize(
    ("status", "code", "expected_kind"),
    [
        (403, "InvalidAccessKeyId", "auth"),
        (403, "SignatureDoesNotMatch", "auth"),
        (401, "Unauthorized", "auth"),
        (403, "AccessDenied", "denied"),
        (404, "NoSuchBucket", "not_found"),
        (404, "NoSuchKey", "not_found"),
        (400, "InvalidBucketName", "invalid_config"),
        (503, "ServiceUnavailable", "provider"),
    ],
)
async def test_s3_error_classification_from_real_botocore_xml_responses(
    transport: _CapturedTransport, status: int, code: str, expected_kind: str
):
    client = ObjectStorageClient(
        ObjectStorageConfig("s3", "cn-beijing", "https://tos-s3-cn-beijing.volces.com", "b", AK, SK, "virtual")
    )
    transport.next_response = _xml_error("https://b.tos-s3-cn-beijing.volces.com/k", status, code, "server message")
    with pytest.raises(ObjectStorageError) as exc_info:
        await client.get_bytes("k")
    assert exc_info.value.kind == expected_kind
    assert exc_info.value.code == code
    assert exc_info.value.status_code == status


async def test_network_and_config_error_classification(transport: _CapturedTransport):
    client = ObjectStorageClient(
        ObjectStorageConfig("s3", "cn-beijing", "https://tos-s3-cn-beijing.volces.com", "b", AK, SK, "virtual")
    )
    transport.next_response = EndpointConnectionError(endpoint_url="https://tos-s3-cn-beijing.volces.com")
    with pytest.raises(ObjectStorageError) as exc_info:
        await client.check()
    assert exc_info.value.kind == "network"

    with pytest.raises(ObjectStorageError) as bad_kind:
        ObjectStorageClient(ObjectStorageConfig("tos", "cn-beijing", "https://s3.example.com", "b", AK, SK))
    assert bad_kind.value.kind == "invalid_config"

    with pytest.raises(ObjectStorageError) as bad_endpoint:
        normalize_endpoint_url("ftp://bad.example.com")
    assert bad_endpoint.value.kind == "invalid_config"


@pytest.mark.local_port
async def test_real_loopback_http_roundtrip_including_unauthenticated_presigned_get():
    """在本地回环 HTTP 服务器上跑通真实 botocore PUT/HEAD/无额外请求头预签名 GET/DELETE。"""
    store: dict[str, tuple[bytes, str | None]] = {}
    seen_headers: list[tuple[str, dict[str, str]]] = []

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_PUT(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            seen_headers.append(("PUT", {k.lower(): v for k, v in self.headers.items()}))
            store[urlsplit(self.path).path] = (body, self.headers.get("Content-Type"))
            self.send_response(200)
            self.send_header("ETag", '"loopback-etag"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_HEAD(self) -> None:
            path = urlsplit(self.path).path
            if path == "/local-bucket":
                self.send_response(200)
                self.end_headers()
                return
            if path not in store:
                self.send_response(404)
                self.end_headers()
                return
            body, ctype = store[path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"loopback-etag"')
            if ctype:
                self.send_header("Content-Type", ctype)
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            seen_headers.append(("GET", {k.lower(): v for k, v in self.headers.items()}))
            if parsed.path not in store:
                self.send_response(404)
                self.end_headers()
                return
            body, ctype = store[parsed.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            if ctype:
                self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:
            store.pop(urlsplit(self.path).path, None)
            self.send_response(204)
            self.end_headers()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    except OSError as exc:
        pytest.skip(f"loopback bind unavailable: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        endpoint = f"http://127.0.0.1:{port}"
        client = ObjectStorageClient(
            ObjectStorageConfig("s3", "us-east-1", endpoint, "local-bucket", AK, SK, addressing_style="path")
        )
        key = "arcreel-materials/sha256/11/1111.png"
        payload = b"\x89PNG\r\n\x1a\nloopback-bytes"

        await client.put_bytes(key, payload, content_type="image/png")
        info = await client.head(key)
        assert info.size == len(payload)

        presigned = await client.presign_get(key, expires_seconds=300)
        async with httpx.AsyncClient() as http:
            resp = await http.get(presigned)
        assert resp.status_code == 200
        assert resp.content == payload

        put_headers = next(hdrs for method, hdrs in seen_headers if method == "PUT")
        assert "x-amz-checksum-crc32" not in put_headers
        assert "x-amz-sdk-checksum-algorithm" not in put_headers
        assert "aws-chunked" not in put_headers.get("content-encoding", "")

        get_headers = next(hdrs for method, hdrs in seen_headers if method == "GET")
        assert "authorization" not in get_headers

        await client.delete(key)
        with pytest.raises(ObjectStorageError) as missing:
            await client.head(key)
        assert missing.value.kind == "not_found"

        probe_result = await probe_connection(client)
        assert probe_result.success is True
        assert [step.status for step in probe_result.steps] == ["passed"] * 5
        assert probe_result.residual_object_key is None
        assert not any(PROBE_PREFIX in path for path in store)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        with socket.socket() as _unused:
            pass
