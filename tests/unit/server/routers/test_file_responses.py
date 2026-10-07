"""公开媒体文件响应的缓存头与条件请求（304）。"""

from __future__ import annotations

from email.utils import formatdate
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from server.routers._file_responses import (
    IMMUTABLE_CACHE_CONTROL,
    REVALIDATE_CACHE_CONTROL,
    cached_file_response,
)

_BODY = b"0123456789" * 10


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.png"
    path.write_bytes(_BODY)
    return path


@pytest.fixture
def media_client(media_file: Path) -> TestClient:
    app = FastAPI()

    @app.get("/media")
    def media(request: Request):
        immutable = bool(request.query_params.get("v"))
        return cached_file_response(
            media_file,
            media_file.stat(),
            request,
            immutable=immutable,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return TestClient(app)


def test_cache_control_follows_version_key(media_client: TestClient):
    plain = media_client.get("/media")
    versioned = media_client.get("/media?v=1")

    assert plain.headers["cache-control"] == REVALIDATE_CACHE_CONTROL
    assert versioned.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL
    assert plain.headers["x-content-type-options"] == "nosniff"
    assert plain.content == _BODY


def test_matching_if_none_match_returns_304(media_client: TestClient):
    etag = media_client.get("/media").headers["etag"]

    resp = media_client.get("/media", headers={"If-None-Match": etag})

    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["etag"] == etag
    assert resp.headers["cache-control"] == REVALIDATE_CACHE_CONTROL


def test_weak_etag_in_list_also_matches(media_client: TestClient):
    etag = media_client.get("/media").headers["etag"]

    resp = media_client.get("/media", headers={"If-None-Match": f'"other", W/{etag}'})

    assert resp.status_code == 304


def test_stale_etag_returns_full_body(media_client: TestClient):
    resp = media_client.get("/media", headers={"If-None-Match": '"stale"'})

    assert resp.status_code == 200
    assert resp.content == _BODY


def test_if_modified_since_returns_304_when_not_newer(media_client: TestClient, media_file: Path):
    last_modified = media_client.get("/media").headers["last-modified"]

    assert media_client.get("/media", headers={"If-Modified-Since": last_modified}).status_code == 304
    older = formatdate(media_file.stat().st_mtime - 3600, usegmt=True)
    assert media_client.get("/media", headers={"If-Modified-Since": older}).status_code == 200


def test_if_none_match_takes_precedence_over_if_modified_since(media_client: TestClient):
    last_modified = media_client.get("/media").headers["last-modified"]

    resp = media_client.get("/media", headers={"If-None-Match": '"stale"', "If-Modified-Since": last_modified})

    assert resp.status_code == 200


def test_range_requests_still_return_partial_content(media_client: TestClient):
    resp = media_client.get("/media", headers={"Range": "bytes=0-9"})

    assert resp.status_code == 206
    assert resp.content == _BODY[:10]
    assert resp.headers["content-range"] == f"bytes 0-9/{len(_BODY)}"
