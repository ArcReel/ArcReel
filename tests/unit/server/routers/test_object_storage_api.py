"""对象存储连接配置 API。

boto3 / botocore 基于 urllib3，不经 httpx；对象存储的网络边界在 ``create_sdk_client`` 一处替换为内存替身。
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any, ClassVar

import httpx
import pytest
import respx
from botocore.exceptions import EndpointConnectionError, ParamValidationError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from lib.db import get_async_session
from lib.db.models.object_storage import RelayObject
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.object_storage import probe
from lib.object_storage.client import ObjectStorageClient, ObjectStorageConfig
from lib.object_storage.probe import PROBE_PREFIX
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
    """S3 SDK 客户端替身：在内存中支持分级探针的全部操作，可按方法注入失败。"""

    instances: ClassVar[list[FakeS3]] = []

    def __init__(
        self,
        config: ObjectStorageConfig,
        failures: dict[str, Exception],
        objects: dict[tuple[str, str], tuple[bytes, str | None]],
        *,
        head_size_override: int | None = None,
    ) -> None:
        self.config = config
        self.failures = failures
        self.objects = objects
        self.head_size_override = head_size_override
        self.calls: list[tuple[str, dict[str, Any]]] = []
        FakeS3.instances.append(self)

    def _maybe_fail(self, op: str) -> None:
        exc = self.failures.get(op)
        if exc is not None:
            raise exc

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_bucket", kwargs))
        self._maybe_fail("head_bucket")
        return {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        self._maybe_fail("put_object")
        body = kwargs["Body"]
        raw = body if isinstance(body, bytes) else body.read()
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = (raw, kwargs.get("ContentType"))
        self._maybe_fail("put_response")
        return {"ETag": '"fake-etag"'}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_object", kwargs))
        self._maybe_fail("head_object")
        item = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
        if item is None:
            raise s3_client_error(404, "NoSuchKey", "not found")
        raw, content_type = item
        return {
            "ContentLength": self.head_size_override if self.head_size_override is not None else len(raw),
            "ETag": '"fake-etag"',
            "ContentType": content_type,
        }

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete_object", kwargs))
        self._maybe_fail("delete_object")
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)
        return {}

    def generate_presigned_url(self, client_method: str, *, Params: dict[str, Any], ExpiresIn: int) -> str:
        self.calls.append(
            ("generate_presigned_url", {"method": client_method, "Params": Params, "ExpiresIn": ExpiresIn})
        )
        self._maybe_fail("generate_presigned_url")
        return (
            f"https://{Params['Bucket']}.example/{Params['Key']}"
            f"?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=deadbeefsecret999"
        )


@pytest.fixture
def fake_s3(monkeypatch):
    FakeS3.instances = []
    state: dict[str, Any] = {"failures": {}, "objects": {}, "head_size_override": None}
    use_object_storage_sdk(
        monkeypatch,
        lambda config: FakeS3(
            config,
            state["failures"],
            state["objects"],
            head_size_override=state["head_size_override"],
        ),
    )
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

    without_style = {key: value for key, value in without_keys.items() if key != "addressing_style"}
    rotated = storage_api.put(
        "/api/v1/object-storage/connection",
        json={**without_style, "access_key_id": NEW_AK},
    )
    assert rotated.json()["addressing_style"] == "virtual"
    rotated = storage_api.put(
        "/api/v1/object-storage/connection",
        json={**without_keys, "addressing_style": "auto"},
    )
    assert rotated.status_code == 200
    assert rotated.json()["addressing_style"] == "auto"
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

    with respx.mock(assert_all_called=False) as http_mock:
        http_mock.get(url__regex=r"^https://arcreel-relay-2\.example/.*").mock(
            side_effect=lambda req: httpx.Response(
                200,
                content=fake_s3["objects"][("arcreel-relay-2", req.url.path.lstrip("/"))][0],
            )
        )
        probe = storage_api.post("/api/v1/object-storage/connection/test").json()
    assert probe["success"] is True
    assert [(step["step"], step["status"]) for step in probe["steps"]] == [
        ("head_bucket", "passed"),
        ("put_object", "passed"),
        ("head_object", "passed"),
        ("presigned_get", "passed"),
        ("delete_object", "passed"),
    ]
    assert probe["residual_object_key"] is None
    assert fake_s3["objects"] == {}
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
    assert [op for op, _ in s3.calls] == [
        "head_bucket",
        "put_object",
        "head_object",
        "generate_presigned_url",
        "delete_object",
    ]
    probe_key = s3.calls[1][1]["Key"]
    assert probe_key.startswith(PROBE_PREFIX)
    assert s3.calls[4][1]["Key"] == probe_key


def test_null_credentials_only_reuse_keys_for_the_saved_target(storage_api: TestClient):
    url = "/api/v1/object-storage/connection"
    saved = storage_api.put(url, json=_BODY)
    assert saved.status_code == 200
    null_keys = {**_BODY, "access_key_id": None, "secret_access_key": None}

    changed = storage_api.put(url, json={**null_keys, "bucket": "another-bucket"}, headers={"Accept-Language": "zh"})
    assert changed.status_code == 422
    assert "更换桶或 Endpoint" in changed.json()["detail"]

    same = storage_api.put(url, json=null_keys)
    assert same.status_code == 200
    for field in ("access_key_id_masked", "secret_access_key_masked"):
        assert same.json()[field] == saved.json()[field]


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
    ("error", "expected_kind", "expected"),
    [
        (s3_client_error(403, "AccessDenied", "denied by policy"), "denied", "没有这个桶的权限"),
        (s3_client_error(403, "SignatureDoesNotMatch", "bad sig"), "auth", "拒绝了访问凭证"),
        (s3_client_error(404, "NoSuchBucket", "missing"), "not_found", "找不到这个桶"),
        (ParamValidationError(report="invalid bucket"), "invalid_config", "连接配置无效"),
    ],
)
def test_connection_test_head_bucket_failure_skips_later_steps(
    storage_api: TestClient, fake_s3, error, expected_kind, expected
):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    fake_s3["failures"]["head_bucket"] = error

    resp = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["residual_object_key"] is None
    assert [(step["step"], step["status"]) for step in body["steps"]] == [
        ("head_bucket", "failed"),
        ("put_object", "skipped"),
        ("head_object", "skipped"),
        ("presigned_get", "skipped"),
        ("delete_object", "skipped"),
    ]
    assert body["steps"][0]["error_kind"] == expected_kind
    assert expected in body["steps"][0]["message"]
    assert "TOS" not in body["steps"][0]["message"]
    assert SK not in resp.text


def test_connection_test_put_denied_stops_without_cleanup(storage_api: TestClient, fake_s3):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    fake_s3["failures"]["put_object"] = s3_client_error(403, "AccessDenied", "put denied")

    resp = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert [(step["step"], step["status"]) for step in body["steps"]] == [
        ("head_bucket", "passed"),
        ("put_object", "failed"),
        ("head_object", "skipped"),
        ("presigned_get", "skipped"),
        ("delete_object", "skipped"),
    ]
    assert "PutObject" in body["steps"][1]["message"]
    assert body["residual_object_key"] is None


@pytest.mark.parametrize(("code", "message"), [("NoSuchKey", "临时探针对象"), ("NoSuchBucket", "找不到这个桶")])
def test_probe_reports_missing_object_or_bucket(storage_api, fake_s3, code, message):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    fake_s3["failures"]["head_object"] = s3_client_error(404, code)
    result = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"}).json()
    step = next(step for step in result["steps"] if step["step"] == "head_object")
    assert step["error_kind"] == "not_found"
    assert message in step["message"]


def test_connection_test_head_mismatch_still_cleans_up_probe(storage_api: TestClient, fake_s3):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    fake_s3["head_size_override"] = 9999

    resp = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert [(step["step"], step["status"]) for step in body["steps"]] == [
        ("head_bucket", "passed"),
        ("put_object", "passed"),
        ("head_object", "failed"),
        ("presigned_get", "skipped"),
        ("delete_object", "passed"),
    ]
    assert body["steps"][2]["error_kind"] == "metadata_mismatch"
    assert body["residual_object_key"] is None
    assert fake_s3["objects"] == {}


def test_connection_test_presigned_get_digest_mismatch_and_redacts_query(storage_api: TestClient, fake_s3):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    seen_headers: list[dict[str, str]] = []

    with respx.mock(assert_all_called=False) as http_mock:

        def _tampered(req: httpx.Request) -> httpx.Response:
            seen_headers.append(dict(req.headers))
            return httpx.Response(200, content=b"tampered-bytes")

        http_mock.get(url__regex=r"^https://arcreel-relay\.example/.*").mock(side_effect=_tampered)
        resp = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert [(step["step"], step["status"]) for step in body["steps"]] == [
        ("head_bucket", "passed"),
        ("put_object", "passed"),
        ("head_object", "passed"),
        ("presigned_get", "failed"),
        ("delete_object", "passed"),
    ]
    assert body["steps"][3]["error_kind"] == "digest_mismatch"
    assert "摘要" in body["steps"][3]["message"]
    assert "authorization" not in {k.lower() for k in seen_headers[0]}
    assert "deadbeefsecret999" not in resp.text
    assert "X-Amz-Signature" not in resp.text
    assert fake_s3["objects"] == {}


def test_connection_test_presigned_get_network_error_strips_presigned_query(storage_api: TestClient, fake_s3):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)

    with respx.mock(assert_all_called=False) as http_mock:

        def _network_fail(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"failed connecting to {req.url}", request=req)

        http_mock.get(url__regex=r"^https://arcreel-relay\.example/.*").mock(side_effect=_network_fail)
        resp = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["steps"][3]["status"] == "failed"
    assert body["steps"][3]["error_kind"] == "network"
    assert body["steps"][4]["status"] == "passed"
    assert "deadbeefsecret999" not in resp.text
    assert "X-Amz-Signature" not in resp.text


@pytest.mark.parametrize("step", ["head_bucket", "presigned_get"])
def test_probe_rejects_credential_bearing_provider_codes(storage_api, fake_s3, step):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    code = "https://s3.example/key?X-Amz-Signature=secret-value"
    if step == "head_bucket":
        fake_s3["failures"]["head_bucket"] = s3_client_error(403, code)
    with respx.mock(assert_all_called=False) as http_mock:
        http_mock.get(url__regex=r"^https://arcreel-relay[.]example/.*").mock(
            return_value=httpx.Response(403, text=f"<Error><Code>{code}</Code></Error>")
        )
        response = storage_api.post("/api/v1/object-storage/connection/test")
    failed = next(entry for entry in response.json()["steps"] if entry["step"] == step)
    assert failed["diagnostic"]["code"] == "HTTP_403"
    assert "secret-value" not in response.text


async def test_connection_test_delete_failure_reports_residual_and_keeps_endpoint_review(
    storage_api: TestClient, storage_sessions: async_sessionmaker, fake_s3
):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    async with storage_sessions() as session:
        repo = ObjectStorageConnectionRepository(session)
        conn = await repo.get()
        assert conn is not None
        conn.needs_endpoint_review = True
        await session.commit()

    fake_s3["failures"]["delete_object"] = s3_client_error(403, "AccessDenied", "delete denied")
    with respx.mock(assert_all_called=False) as http_mock:
        http_mock.get(url__regex=r"^https://arcreel-relay\.example/.*").mock(
            side_effect=lambda req: httpx.Response(
                200,
                content=fake_s3["objects"][("arcreel-relay", req.url.path.lstrip("/"))][0],
            )
        )
        failed = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"}).json()

    assert failed["success"] is False
    assert [(step["step"], step["status"]) for step in failed["steps"]] == [
        ("head_bucket", "passed"),
        ("put_object", "passed"),
        ("head_object", "passed"),
        ("presigned_get", "passed"),
        ("delete_object", "failed"),
    ]
    residual = failed["residual_object_key"]
    assert isinstance(residual, str)
    assert residual.startswith(PROBE_PREFIX)
    assert ("arcreel-relay", residual) in fake_s3["objects"]
    assert storage_api.get("/api/v1/object-storage/connection").json()["connection"]["needs_endpoint_review"] is True

    fake_s3["failures"].clear()
    with respx.mock(assert_all_called=False) as http_mock:
        http_mock.get(url__regex=r"^https://arcreel-relay\.example/.*").mock(
            side_effect=lambda req: httpx.Response(
                200,
                content=fake_s3["objects"][("arcreel-relay", req.url.path.lstrip("/"))][0],
            )
        )
        passed = storage_api.post("/api/v1/object-storage/connection/test", headers={"Accept-Language": "zh"}).json()

    assert passed["success"] is True
    assert passed["residual_object_key"] is None
    assert storage_api.get("/api/v1/object-storage/connection").json()["connection"]["needs_endpoint_review"] is False


def test_delete_returns_instance_to_local_only(storage_api: TestClient):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    assert storage_api.delete("/api/v1/object-storage/connection").status_code == 200
    assert storage_api.get("/api/v1/object-storage/connection").json() == _LOCAL_ONLY


async def test_presigned_download_address_is_a_url_string(fake_s3):
    """客户端签发的预签名 GET 返回下载地址字符串。"""
    config = ObjectStorageConfig("s3", "cn-beijing", "https://tos-s3-cn-beijing.volces.com", "b", AK, SK, "virtual")
    url = await ObjectStorageClient(config).presign_get("relay/a.png", expires_seconds=600)
    assert url.startswith("https://b.example/relay/a.png?")
    assert FakeS3.instances[0].calls[0][1]["ExpiresIn"] == 600


@pytest.mark.parametrize("cleanup_status", [None, 404, 403])
def test_ambiguous_probe_put_cleans_up_and_reports_possible_residue(storage_api, fake_s3, cleanup_status):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    fake_s3["failures"]["put_response"] = EndpointConnectionError(endpoint_url=_BODY["endpoint"])
    if cleanup_status:
        code = "NoSuchKey" if cleanup_status == 404 else "AccessDenied"
        fake_s3["failures"]["delete_object"] = s3_client_error(cleanup_status, code)
    response = storage_api.post("/api/v1/object-storage/connection/test")
    assert response.status_code == 200
    result = response.json()
    assert [step["status"] for step in result["steps"]] == [
        "passed",
        "failed",
        "skipped",
        "skipped",
        "failed" if cleanup_status == 403 else "passed",
    ]
    [client] = FakeS3.instances
    assert [op for op, _ in client.calls] == ["head_bucket", "put_object", "delete_object"]
    key = client.calls[1][1]["Key"]
    assert client.calls[2][1]["Key"] == key
    assert result["residual_object_key"] == (key if cleanup_status == 403 else None)
    if cleanup_status != 404:
        assert ((_BODY["bucket"], key) in fake_s3["objects"]) == (cleanup_status == 403)


async def test_presigned_timeout_still_deletes_probe(fake_s3):
    config = ObjectStorageConfig(**_BODY)
    deadline = asyncio.timeout(None)

    async def slow_response(request):
        deadline.reschedule(asyncio.get_running_loop().time())
        await asyncio.Event().wait()
        raise AssertionError("the probe deadline should interrupt the request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_response)) as http_client:
        result = await probe.probe_connection(
            ObjectStorageClient(config), http_client=http_client, timeout_factory=lambda seconds: deadline
        )
    assert deadline.expired()
    steps = {step.step: step for step in result.steps}
    assert steps["presigned_get"].error_kind == "network"
    assert steps["presigned_get"].error_code == "TimeoutError"
    assert steps["delete_object"].status == "passed"
    assert not fake_s3["objects"]


async def test_config_rotation_during_probe_preserves_review(storage_api, storage_sessions, fake_s3):
    storage_api.put("/api/v1/object-storage/connection", json=_BODY)
    async with storage_sessions() as session:
        current = await ObjectStorageConnectionRepository(session).get()
        assert current is not None
        current.needs_endpoint_review = True
        await session.commit()

    async def rotate_during_get(request):
        async with storage_sessions() as session:
            current = await ObjectStorageConnectionRepository(session).get()
            assert current is not None
            current.secret_access_key = NEW_SK
            await session.commit()
        return httpx.Response(200, content=fake_s3["objects"][(_BODY["bucket"], request.url.path.lstrip("/"))][0])

    with respx.mock() as http_mock:
        http_mock.get(url__regex=r"^https://arcreel-relay[.]example/.*").mock(side_effect=rotate_during_get)
        result = storage_api.post("/api/v1/object-storage/connection/test").json()
    assert result["success"]
    assert storage_api.get("/api/v1/object-storage/connection").json()["connection"]["needs_endpoint_review"]


async def test_stale_save_preserves_replacement_connection_objects(storage_api, storage_sessions, fake_s3):
    url = "/api/v1/object-storage/connection"
    old_id = storage_api.put(url, json=_BODY).json()["id"]
    replacement = storage_api.put(url, json={**_BODY, "bucket": "replacement"}).json()
    key = "arcreel-relay/replacement.png"
    fake_s3["objects"][("replacement", key)] = (b"replacement", None)
    async with storage_sessions() as session:
        session.add(
            RelayObject(
                connection_id=replacement["id"], endpoint=replacement["endpoint"], bucket="replacement", object_key=key
            )
        )
        await session.commit()
    response = storage_api.put(url, json={**_BODY, "expected_connection_id": old_id})
    assert response.status_code == 409
    assert storage_api.get(url).json()["connection"]["id"] == replacement["id"]
    assert ("replacement", key) in fake_s3["objects"]
    assert not any(op == "delete_object" for client in FakeS3.instances for op, _ in client.calls)
