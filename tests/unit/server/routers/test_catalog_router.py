"""模型目录搜索接口：按名称与媒体类型过滤，需登录。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.error_handlers import register_error_handlers
from server.routers import catalog
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


def _client(*, authenticated: bool = True) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    if authenticated:
        override_auth(app)
    app.include_router(catalog.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def test_search_filters_by_name_and_media_type() -> None:
    with _client() as client:
        resp = client.get("/api/v1/catalog/entries", params={"q": "seedance", "media_type": "video"})

    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert entries
    assert {e["media_type"] for e in entries} == {"video"}
    assert all("seedance" in (e["name"] + e["display_name"]).lower() for e in entries)
    assert set(entries[0]) == {"maker", "name", "media_type", "display_name", "discontinued"}


def test_unknown_media_type_is_rejected() -> None:
    with _client() as client:
        resp = client.get("/api/v1/catalog/entries", params={"media_type": "hologram"})

    assert resp.status_code == 422


def test_requires_login() -> None:
    with _client(authenticated=False) as client:
        resp = client.get("/api/v1/catalog/entries")

    assert resp.status_code == 401
