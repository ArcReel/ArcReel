"""对象存储连接配置 API。

TOS SDK 基于 requests，不经 httpx；对象存储的网络边界在 ``create_sdk_client`` 一处替换为内存替身。
"""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from tos.exceptions import TosServerError

from lib.db import get_async_session
from lib.object_storage.client import ObjectStorageClient, ObjectStorageConfig
from server.error_handlers import register_error_handlers
from server.routers import object_storage
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import use_object_storage_sdk

AK = "TEST-TOS-AK-0001"
SK = "TEST-TOS-SECRET-0001"
_BODY = {
    "region": "cn-beijing",
    "endpoint": "tos-cn-beijing.volces.com",
    "bucket": "arcreel-relay",
    "access_key_id": AK,
    "secret_access_key": SK,
}


class FakeTos:
    """TOS SDK 客户端替身：记录构造参数与调用，``fail`` 设为异常时 head_bucket 抛出它。"""

    instances: ClassVar[list[FakeTos]] = []

    def __init__(self, config: ObjectStorageConfig, fail: Exception | None = None) -> None:
        self.config = config
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []
        FakeTos.instances.append(self)

    def head_bucket(self, **kwargs):
        self.calls.append(("head_bucket", kwargs))
        if self.fail:
            raise self.fail
        return SimpleNamespace(region=self.config.region)

    def pre_signed_url(self, **kwargs):
        self.calls.append(("pre_signed_url", kwargs))
        return SimpleNamespace(signed_url=f"https://{kwargs['bucket']}.example/{kwargs['key']}?sig", signed_header={})


def _server_error(status: int, code: str) -> TosServerError:
    resp = SimpleNamespace(request_id="req-1", headers={}, status=status)
    return TosServerError(resp, "denied by policy", code, "host", "/arcreel-relay")


@pytest.fixture
def fake_tos(monkeypatch):
    FakeTos.instances = []
    state: dict[str, Exception | None] = {"fail": None}
    use_object_storage_sdk(monkeypatch, lambda config: FakeTos(config, state["fail"]))
    return state


@pytest.fixture
def storage_api(db_engine: AsyncEngine) -> Generator[TestClient]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    app = FastAPI()

    async def _session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    override_auth(app)
    app.include_router(object_storage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    with TestClient(app) as c:
        yield c


def test_local_only_instance_has_no_connection(storage_api: TestClient):
    assert storage_api.get("/api/v1/object-storage/connection").json() == {"connection": None}
    assert storage_api.post("/api/v1/object-storage/connection/test").status_code == 404


def test_save_masks_secrets_and_keeps_them_when_left_blank(storage_api: TestClient, fake_tos):
    first = {k: v for k, v in _BODY.items() if not k.endswith("_key") and k != "access_key_id"}
    assert storage_api.put("/api/v1/object-storage/connection", json=first).status_code == 422

    saved = storage_api.put("/api/v1/object-storage/connection", json=_BODY).json()
    assert saved["access_key_id_masked"] == f"{AK[:4]}…{AK[-4:]}"
    assert AK not in str(saved)
    assert SK not in str(saved)

    storage_api.put("/api/v1/object-storage/connection", json={**first, "bucket": "arcreel-relay-2"})
    assert storage_api.post("/api/v1/object-storage/connection/test").json() == {"success": True}
    (tos,) = FakeTos.instances
    assert (tos.config.bucket, tos.config.access_key_id, tos.config.secret_access_key) == ("arcreel-relay-2", AK, SK)
    assert tos.calls == [("head_bucket", {"bucket": "arcreel-relay-2"})]


def test_rejects_s3_compatible_endpoint(storage_api: TestClient):
    resp = storage_api.put(
        "/api/v1/object-storage/connection", json={**_BODY, "endpoint": "https://tos-s3-cn-beijing.volces.com"}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("error", "expected"),
    [(_server_error(403, "AccessDenied"), "没有这个桶的权限"), (_server_error(404, "NoSuchBucket"), "找不到这个桶")],
)
def test_connection_test_failure_explains_reason(storage_api: TestClient, fake_tos, error, expected):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    fake_tos["fail"] = error

    resp = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"})

    assert resp.status_code == 502
    assert expected in resp.json()["detail"]
    assert resp.json()["diagnostic"]["code"] == error.code
    assert SK not in resp.text


def test_delete_returns_instance_to_local_only(storage_api: TestClient):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    assert storage_api.delete("/api/v1/object-storage/connection").status_code == 204
    assert storage_api.get("/api/v1/object-storage/connection").json() == {"connection": None}


async def test_presigned_download_address_is_a_url_string(fake_tos):
    """SDK 返回的是带 ``signed_url`` 的对象，客户端交给调用方的是地址本身。"""
    config = ObjectStorageConfig("tos", "cn-beijing", "tos-cn-beijing.volces.com", "b", AK, SK)
    url = await ObjectStorageClient(config).presign_get("relay/a.png", expires_seconds=600)
    assert url == "https://b.example/relay/a.png?sig"
    assert FakeTos.instances[0].calls[0][1]["expires"] == 600
