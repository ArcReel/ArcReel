"""对象存储连接配置 API。

boto3 / botocore 基于 urllib3，不经 httpx；对象存储的网络边界在 ``create_sdk_client`` 一处替换为内存替身。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, ClassVar

import pytest
from botocore.exceptions import ParamValidationError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from lib.db import get_async_session
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.object_storage.client import ObjectStorageClient, ObjectStorageConfig
from server.error_handlers import register_error_handlers
from server.routers import object_storage
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import s3_client_error, use_object_storage_sdk

AK = "TEST-S3-AK-0001"
SK = "TEST-S3-SECRET-0001"
NEW_AK = "TEST-S3-AK-0002"
NEW_SK = "TEST-S3-SECRET-0002"
_BODY = {
    "kind": "s3",
    "region": "cn-beijing",
    "endpoint": "https://tos-s3-cn-beijing.volces.com",
    "bucket": "arcreel-relay",
    "addressing_style": "virtual",
    "access_key_id": AK,
    "secret_access_key": SK,
}


class FakeS3:
    """S3 SDK 客户端替身：记录构造参数与调用，``fail`` 设为异常时 head_bucket 抛出它。"""

    instances: ClassVar[list[FakeS3]] = []

    def __init__(self, config: ObjectStorageConfig, fail: Exception | None = None) -> None:
        self.config = config
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []
        FakeS3.instances.append(self)

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_bucket", kwargs))
        if self.fail:
            raise self.fail
        return {}

    def generate_presigned_url(self, client_method: str, *, Params: dict[str, Any], ExpiresIn: int) -> str:
        self.calls.append(
            ("generate_presigned_url", {"method": client_method, "Params": Params, "ExpiresIn": ExpiresIn})
        )
        return f"https://{Params['Bucket']}.example/{Params['Key']}?sig"


@pytest.fixture
def fake_s3(monkeypatch):
    FakeS3.instances = []
    state: dict[str, Exception | None] = {"fail": None}
    use_object_storage_sdk(monkeypatch, lambda config: FakeS3(config, state["fail"]))
    return state


@pytest.fixture
def storage_sessions(db_engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def storage_api(storage_sessions: async_sessionmaker) -> Generator[TestClient]:
    app = FastAPI()

    async def _session():
        async with storage_sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    override_auth(app)
    app.include_router(object_storage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    with TestClient(app) as c:
        yield c


_LOCAL_ONLY = {
    "connection": None,
    "new_image_target": "local",
    "pending_ingest_tasks": 0,
}


def test_local_only_instance_has_no_connection(storage_api: TestClient):
    assert storage_api.get("/api/v1/object-storage/connection").json() == _LOCAL_ONLY
    assert storage_api.post("/api/v1/object-storage/connection/test").status_code == 404


async def test_save_masks_secrets_rotates_in_place_and_allocates_new_id_on_target_change(
    storage_api: TestClient, storage_sessions: async_sessionmaker, fake_s3
):
    without_keys = {k: v for k, v in _BODY.items() if k not in ("access_key_id", "secret_access_key")}
    assert storage_api.put("/api/v1/object-storage/connection", json=without_keys).status_code == 422

    saved = storage_api.put("/api/v1/object-storage/connection", json=_BODY).json()
    assert saved["kind"] == "s3"
    assert saved["addressing_style"] == "virtual"
    assert saved["access_key_id_masked"] == f"{AK[:4]}…{AK[-4:]}"
    assert AK not in str(saved)
    assert SK not in str(saved)

    async with storage_sessions() as session:
        first_conn = await ObjectStorageConnectionRepository(session).get()
        assert first_conn is not None
        first_id = first_conn.id

    rotated = storage_api.put(
        "/api/v1/object-storage/connection",
        json={**without_keys, "addressing_style": "path", "access_key_id": NEW_AK},
    )
    assert rotated.status_code == 200
    assert rotated.json()["addressing_style"] == "path"
    async with storage_sessions() as session:
        rotated_conn = await ObjectStorageConnectionRepository(session).get()
        assert rotated_conn is not None
        assert rotated_conn.id == first_id
        assert (rotated_conn.access_key_id, rotated_conn.secret_access_key) == (NEW_AK, SK)

    blank_on_new_bucket = storage_api.put(
        "/api/v1/object-storage/connection",
        json={**without_keys, "bucket": "arcreel-relay-2"},
        headers={"Accept-Language": "zh"},
    )
    assert blank_on_new_bucket.status_code == 422
    assert "更换桶或 Endpoint" in blank_on_new_bucket.json()["detail"]

    blank_on_new_endpoint = storage_api.put(
        "/api/v1/object-storage/connection",
        json={**without_keys, "endpoint": "https://oss-cn-hangzhou.aliyuncs.com"},
    )
    assert blank_on_new_endpoint.status_code == 422

    relocated = storage_api.put(
        "/api/v1/object-storage/connection",
        json={
            **without_keys,
            "endpoint": "https://123456.r2.cloudflarestorage.com",
            "region": "auto",
            "bucket": "arcreel-relay-2",
            "addressing_style": "auto",
            "access_key_id": NEW_AK,
            "secret_access_key": NEW_SK,
        },
    )
    assert relocated.status_code == 200
    async with storage_sessions() as session:
        second_conn = await ObjectStorageConnectionRepository(session).get()
        assert second_conn is not None
        assert second_conn.id > first_id

    assert storage_api.post("/api/v1/object-storage/connection/test").json() == {"success": True}
    (s3,) = FakeS3.instances
    assert (
        s3.config.region,
        s3.config.bucket,
        s3.config.addressing_style,
        s3.config.access_key_id,
        s3.config.secret_access_key,
    ) == (
        "auto",
        "arcreel-relay-2",
        "auto",
        NEW_AK,
        NEW_SK,
    )
    assert s3.calls == [("head_bucket", {"Bucket": "arcreel-relay-2"})]


def test_accepts_tos_s3_endpoint_and_rejects_legacy_tos_kind_or_bad_endpoint(storage_api: TestClient):
    ok = storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    assert ok.status_code == 200
    assert ok.json()["endpoint"] == "https://tos-s3-cn-beijing.volces.com"

    bad_kind = storage_api.put("/api/v1/object-storage/connection", json={**_BODY, "kind": "tos"})
    assert bad_kind.status_code == 422

    bad_endpoint = storage_api.put(
        "/api/v1/object-storage/connection",
        json={**_BODY, "endpoint": "ftp://invalid.example.com"},
        headers={"Accept-Language": "zh"},
    )
    assert bad_endpoint.status_code == 422
    assert "配置无效" in bad_endpoint.json()["detail"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (s3_client_error(403, "AccessDenied", "denied by policy"), "没有这个桶的权限"),
        (s3_client_error(403, "SignatureDoesNotMatch", "bad sig"), "拒绝了访问凭证"),
        (s3_client_error(404, "NoSuchBucket", "missing"), "找不到这个桶"),
        (ParamValidationError(report="invalid bucket"), "连接配置无效"),
    ],
)
def test_connection_test_failure_explains_reason(storage_api: TestClient, fake_s3, error, expected):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    fake_s3["fail"] = error

    resp = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"})

    assert resp.status_code == 502
    assert expected in resp.json()["detail"]
    assert "TOS" not in resp.json()["detail"]
    assert SK not in resp.text


def test_delete_returns_instance_to_local_only(storage_api: TestClient):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    assert storage_api.delete("/api/v1/object-storage/connection").status_code == 200
    assert storage_api.get("/api/v1/object-storage/connection").json() == _LOCAL_ONLY


async def test_presigned_download_address_is_a_url_string(fake_s3):
    """客户端签发的预签名 GET 返回下载地址字符串。"""
    config = ObjectStorageConfig("s3", "cn-beijing", "https://tos-s3-cn-beijing.volces.com", "b", AK, SK, "virtual")
    url = await ObjectStorageClient(config).presign_get("relay/a.png", expires_seconds=600)
    assert url == "https://b.example/relay/a.png?sig"
    assert FakeS3.instances[0].calls[0][1]["ExpiresIn"] == 600
