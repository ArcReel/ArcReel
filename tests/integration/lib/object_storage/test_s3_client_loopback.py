from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from lib.object_storage.client import (
    ObjectStorageClient,
    ObjectStorageConfig,
    ObjectStorageError,
)
from lib.object_storage.probe import PROBE_PREFIX, probe_connection

AK = "TEST-CONTRACT-AK-0001"
SK = "TEST-CONTRACT-SK-0001"


@pytest.mark.local_port
async def test_real_loopback_http_roundtrip_including_unauthenticated_presigned_get(
    monkeypatch: pytest.MonkeyPatch,
):
    """在本地回环 HTTP 服务器上跑通真实 botocore PUT/HEAD/无额外请求头预签名 GET/DELETE。"""
    for env_key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    store: dict[str, tuple[bytes, str | None]] = {}
    seen_headers: list[tuple[str, dict[str, str]]] = []
    seen_get_queries: list[str] = []

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
            seen_get_queries.append(parsed.query)
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
        async with httpx.AsyncClient(trust_env=False) as http:
            resp = await http.get(presigned)
        assert resp.status_code == 200
        assert resp.content == payload

        put_headers = next(hdrs for method, hdrs in seen_headers if method == "PUT")
        assert "x-amz-checksum-crc32" not in put_headers
        assert "x-amz-sdk-checksum-algorithm" not in put_headers
        assert "aws-chunked" not in put_headers.get("content-encoding", "")

        get_headers = next(hdrs for method, hdrs in seen_headers if method == "GET")
        assert "authorization" not in get_headers
        assert "X-Amz-Signature=" in seen_get_queries[0]

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
