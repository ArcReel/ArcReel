"""上传图片到供应商素材库：入库任务、内容版本关联、状态查询与中转副本清理。

真实编排：资产图经文件路由上传成版本，入库经关联路由创建，后台处理直接调用 ``ingest.run_once``。
只替换两个外部边界：控制面请求经 respx 拦截（按 Action 分派），TOS SDK 在 ``create_sdk_client`` 换成内存桶。
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tos.exceptions import TosServerError

from lib.asset_library import ingest
from lib.asset_library.links import account_scope, selected_link
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import RelayObject
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.object_storage import client as storage_client
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import files, provider_asset_links, versions
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import use_project_manager
from tests.http_capture import capture_http, request_json
from tests.integration.server.derivative_sheet_support import solid_png_bytes

VOLC_HOST = "https://ark.cn-beijing.volcengineapi.com/"
SK = "controlplane-secret-access-key-0003"
LINKS = "/api/v1/projects/demo/provider-asset-links"


class MemoryTos:
    """TOS SDK 客户端替身：对象存在内存里，可以让写入或删除失败。"""

    def __init__(self, bucket_state: dict[str, Any]) -> None:
        self.state = bucket_state

    def put_object(self, *, bucket: str, key: str, content: bytes, content_type: str | None = None):
        if self.state["fail_put"]:
            raise _server_error(503, "ServiceUnavailable")
        self.state["objects"][(bucket, key)] = content
        return SimpleNamespace(etag="etag")

    def pre_signed_url(self, *, bucket: str, key: str, expires: int, **_kwargs):
        self.state["presigned"].append((key, expires))
        return SimpleNamespace(signed_url=f"https://{bucket}.tos.example/{key}?X-Tos-Signature=sig", signed_header={})

    def delete_object(self, *, bucket: str, key: str):
        self.state["delete_calls"].append(key)
        if self.state["fail_delete"]:
            raise _server_error(500, "InternalError")
        self.state["objects"].pop((bucket, key), None)


def _server_error(status: int, code: str) -> TosServerError:
    resp = SimpleNamespace(request_id="req-1", headers={}, status=status)
    return TosServerError(resp, "failed", code, "host", "/bucket")


@pytest.fixture
def bucket(monkeypatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "objects": {},
        "presigned": [],
        "delete_calls": [],
        "fail_put": False,
        "fail_delete": False,
    }
    monkeypatch.setattr(storage_client, "create_sdk_client", lambda config: MemoryTos(state))
    return state


@dataclass
class ControlPlane:
    """按 Action 应答的控制面替身；``asset_status`` 依次作为 GetAsset 的结果。"""

    asset_status: list[dict[str, Any]] = field(default_factory=list)
    create_error: Exception | dict[str, Any] | None = None
    listed_assets: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def actions(self, name: str) -> list[dict[str, Any]]:
        return [body for action, body in self.calls if action == name]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        action = request.url.params["Action"]
        body = request_json(request)
        self.calls.append((action, body))
        if action == "ListAssetGroups":
            return httpx.Response(200, json={"Result": {"Items": []}})
        if action == "CreateAssetGroup":
            return httpx.Response(200, json={"Result": {"Id": "group-arcreel"}})
        if action == "CreateAsset":
            if isinstance(self.create_error, Exception):
                raise self.create_error
            if isinstance(self.create_error, dict):
                return httpx.Response(400, json={"ResponseMetadata": {"Error": self.create_error}})
            return httpx.Response(200, json={"Result": {"Id": "asset-20261103-a"}})
        if action == "GetAsset":
            status = self.asset_status.pop(0) if self.asset_status else {"Status": "Processing"}
            return httpx.Response(200, json={"Result": {"Id": body["Id"], **status}})
        if action == "ListAssets":
            return httpx.Response(200, json={"Result": {"Items": self.listed_assets, "TotalCount": 1}})
        return httpx.Response(404, json={"ResponseMetadata": {"Error": {"Code": "UnknownAction"}}})


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "Alice", "银发少女")
    use_project_manager(monkeypatch, pm)
    return pm


@pytest.fixture
def api(sessions, project) -> Generator[TestClient]:
    app = FastAPI()

    async def _session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    for router in (files.router, versions.router, provider_asset_links.router):
        app.include_router(router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    with TestClient(app) as client:
        yield client


async def _configure(sessions: async_sessionmaker[AsyncSession], *, object_storage: bool = True) -> int:
    async with sessions() as session:
        if object_storage:
            await ObjectStorageConnectionRepository(session).save(
                kind="tos",
                region="cn-beijing",
                endpoint="tos-cn-beijing.volces.com",
                bucket="relay-bucket",
                access_key_id="TEST-TOS-AK-0003",
                secret_access_key="tos-secret-0003",
            )
        account = await AssetLibraryRepository(session).create_account(
            site="volcengine",
            name="主账号",
            region="cn-beijing",
            project_name="film",
            access_key_id="TEST-CONTROL-PLANE-AK-0003",
            secret_access_key=SK,
        )
        await session.commit()
        return account.id


def _upload_sheet(api: TestClient, content: bytes) -> None:
    resp = api.post(
        "/api/v1/projects/demo/upload/character?name=Alice", files={"file": ("alice.png", content, "image/png")}
    )
    assert resp.status_code == 200, resp.text


def _panel(api: TestClient) -> dict[str, Any]:
    resp = api.get(LINKS, params={"resource_type": "characters", "resource_id": "Alice"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _start_upload(api: TestClient, account_id: int) -> dict[str, Any]:
    resp = api.post(LINKS, json={"resource_type": "characters", "resource_id": "Alice", "account_id": account_id})
    assert resp.status_code == 202, resp.text
    return resp.json()


async def _link(sessions, link_id: int) -> ProviderAssetLink:
    async with sessions() as session:
        link = await session.get(ProviderAssetLink, link_id)
        assert link is not None
        return link


async def _relays(sessions) -> list[RelayObject]:
    async with sessions() as session:
        return list((await session.execute(select(RelayObject).order_by(RelayObject.id))).scalars())


async def _run(sessions, control: ControlPlane, at: datetime) -> None:
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        await ingest.run_once(sessions, now=at)


async def test_upload_reaches_active_link_bound_to_the_frozen_version(api, sessions, bucket, caplog):
    """完整入库：中转上传 A 的内容 → CreateAsset → 处理中 → 可用；期间改选 B，结果仍归属 A。"""
    account_id = await _configure(sessions)
    image_a = solid_png_bytes((200, 10, 10), size=(400, 400))
    image_b = solid_png_bytes((10, 200, 10), size=(400, 400))
    _upload_sheet(api, image_a)
    version_a = _panel(api)["content_version"]

    job = _start_upload(api, account_id)
    assert job["state"] == "pending"
    assert job["usable"] is False
    assert job["content_version_id"] == version_a["id"]
    # 取得素材 ID 之前，选定的入库任务已可查询
    assert [link["state"] for link in _panel(api)["links"]] == ["pending"]

    # 入库期间改选到 B：B 不继承 A 的入库任务
    _upload_sheet(api, image_b)
    panel_b = _panel(api)
    assert panel_b["content_version"]["id"] != version_a["id"]
    assert panel_b["links"] == []

    control = ControlPlane(
        asset_status=[{"Status": "Processing"}, {"Status": "Active", "Name": "x", "AssetType": "Image"}]
    )
    t0 = utc_now()
    await _run(sessions, control, t0)

    # 中转上传的是提交时冻结的 A，不是此刻选中的 B
    assert list(bucket["objects"].values()) == [image_a]
    (relay_key, expires) = bucket["presigned"][0]
    assert relay_key.startswith("arcreel-relay/")
    assert expires == 24 * 3600
    create = control.actions("CreateAsset")
    assert len(create) == 1
    assert create[0]["GroupId"] == "group-arcreel"
    assert create[0]["AssetType"] == "Image"
    assert create[0]["ProjectName"] == "film"
    assert create[0]["URL"].startswith(f"https://relay-bucket.tos.example/{relay_key}")
    assert create[0]["Name"].startswith("Alice-arc-")
    assert control.actions("CreateAssetGroup")[0]["GroupType"] == "AIGC"
    link = await _link(sessions, job["id"])
    assert (link.state, link.provider_asset_id) == ("processing", "asset-20261103-a")

    # 刚取得素材 ID：中转副本不能删
    await _run(sessions, control, t0 + timedelta(minutes=1))
    assert (await _link(sessions, job["id"])).state == "processing"
    assert bucket["delete_calls"] == []

    await _run(sessions, control, t0 + timedelta(minutes=10))
    link = await _link(sessions, job["id"])
    assert link.state == "active"
    assert link.selected is True
    assert bucket["delete_calls"] == []  # 定论后仍保留到最短保留期

    # 生成侧按内容版本与调用范围查到 A 的可用关联；B 没有
    async with sessions() as session:
        account = await AssetLibraryRepository(session).get_account(account_id)
        assert account is not None
        scope = account_scope(account)
        found_a = await selected_link(session, project_name="demo", content_version_id=version_a["id"], scope=scope)
        found_b = await selected_link(
            session, project_name="demo", content_version_id=panel_b["content_version"]["id"], scope=scope
        )
    assert found_a is not None
    assert found_a.id == job["id"]
    assert found_b is None

    # 切回 A：面板显示 A 仍可用的关联
    resp = api.post("/api/v1/projects/demo/versions/characters/Alice/restore/1")
    assert resp.status_code == 200, resp.text
    [shown] = _panel(api)["links"]
    assert (shown["id"], shown["state"], shown["usable"]) == (job["id"], "active", True)
    assert shown["provider_asset_id"] == "asset-20261103-a"

    # 最短保留期过后实际清理中转副本，正式图片与版本快照不受影响
    await _run(sessions, control, t0 + timedelta(hours=2))
    assert bucket["delete_calls"] == [relay_key]
    assert bucket["objects"] == {}
    [relay] = await _relays(sessions)
    assert relay.state == "deleted"
    assert _panel(api)["content_version"]["id"] == version_a["id"]
    # 签名地址与控制面密钥不进日志
    assert "X-Tos-Signature" not in caplog.text
    assert SK not in caplog.text


async def test_unlink_releases_only_the_relation_and_late_result_does_not_restore_it(api, sessions, bucket):
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((1, 2, 3), size=(400, 400)))
    job = _start_upload(api, account_id)

    # 同一范围已有选定任务时不能重复上传
    resp = api.post(LINKS, json={"resource_type": "characters", "resource_id": "Alice", "account_id": account_id})
    assert resp.status_code == 409

    control = ControlPlane(asset_status=[{"Status": "Active"}])
    t0 = utc_now()
    await _run(sessions, control, t0)
    assert api.delete(f"{LINKS}/{job['id']}").status_code == 204
    assert _panel(api)["links"] == []

    # 迟到的可用结果只更新事实，不恢复关系
    await _run(sessions, control, t0 + timedelta(minutes=1))
    link = await _link(sessions, job["id"])
    assert (link.state, link.selected) == ("active", False)
    assert _panel(api)["links"] == []
    # 解除关联不删除供应商素材，也不删 ArcReel 图片
    assert control.actions("DeleteAsset") == []
    assert _panel(api)["content_version"] is not None

    # 解除后可以重新上传，得到新的入库任务
    again = _start_upload(api, account_id)
    assert again["id"] != job["id"]


async def test_unlinked_before_submit_never_reaches_the_provider(api, sessions, bucket):
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((5, 5, 5), size=(400, 400)))
    job = _start_upload(api, account_id)
    api.delete(f"{LINKS}/{job['id']}")

    control = ControlPlane()
    await _run(sessions, control, utc_now())
    link = await _link(sessions, job["id"])
    assert (link.state, link.error_code) == ("failed", "unlinked_before_submit")
    assert control.calls == []
    assert bucket["objects"] == {}


async def test_uncertain_submission_is_recovered_by_name_without_recreating(api, sessions, bucket):
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((9, 9, 9), size=(400, 400)))
    job = _start_upload(api, account_id)

    control = ControlPlane(create_error=httpx.ReadTimeout("timed out"))
    t0 = utc_now()
    await _run(sessions, control, t0)
    link = await _link(sessions, job["id"])
    assert (link.state, link.provider_asset_id, link.error_code) == ("uncertain", None, "request_network")
    assert link.selected is True
    panel = _panel(api)["links"][0]
    assert (panel["state"], panel["usable"]) == ("uncertain", False)

    # 下一轮按提交时的名称查回素材 ID，不再发 CreateAsset
    control.listed_assets = [
        {"Id": "asset-recovered", "Name": link.submit_name, "Status": "Processing"},
        {"Id": "asset-other", "Name": f"{link.submit_name}-copy", "Status": "Active"},
    ]
    control.asset_status = [{"Status": "Active"}]
    await _run(sessions, control, t0 + timedelta(minutes=1))
    await _run(sessions, control, t0 + timedelta(minutes=2))
    link = await _link(sessions, job["id"])
    assert (link.state, link.provider_asset_id) == ("active", "asset-recovered")
    assert len(control.actions("CreateAsset")) == 1
    assert control.actions("ListAssets")[0]["Filter"]["GroupIds"] == ["group-arcreel"]


async def test_interrupted_submission_is_treated_as_uncertain_after_restart(api, sessions, bucket):
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((7, 7, 7), size=(400, 400)))
    job = _start_upload(api, account_id)
    async with sessions() as session:
        link = await session.get(ProviderAssetLink, job["id"])
        assert link is not None
        link.state = "submitting"
        link.provider_group_id = "group-arcreel"
        link.next_check_at = None
        await session.commit()

    assert await ingest.recover_interrupted(sessions) == 1
    assert (await _link(sessions, job["id"])).state == "uncertain"


async def test_provider_rejection_keeps_reason_and_relay_is_cleaned_with_retry(api, sessions, bucket):
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((50, 60, 70), size=(400, 400)))
    _start_upload(api, account_id)
    control = ControlPlane(
        asset_status=[
            {
                "Status": "Failed",
                "Error": {"Code": "InputImageSensitiveContentDetected", "Message": "image rejected"},
            }
        ]
    )
    t0 = utc_now()
    await _run(sessions, control, t0)
    await _run(sessions, control, t0 + timedelta(minutes=1))
    [shown] = _panel(api)["links"]
    assert (shown["state"], shown["usable"]) == ("failed", False)
    assert shown["error_code"] == "InputImageSensitiveContentDetected"
    assert shown["error_message"] == "image rejected"

    # 删除失败保留定位与原因，退避后重试成功
    bucket["fail_delete"] = True
    await _run(sessions, control, t0 + timedelta(hours=2))
    [relay] = await _relays(sessions)
    assert (relay.state, relay.last_error, relay.delete_attempts) == ("retained", "provider:InternalError", 1)
    await _run(sessions, control, t0 + timedelta(hours=2, minutes=1))
    assert len(bucket["delete_calls"]) == 1  # 退避期内不重试
    bucket["fail_delete"] = False
    await _run(sessions, control, t0 + timedelta(hours=3))
    [relay] = await _relays(sessions)
    assert relay.state == "deleted"
    assert bucket["objects"] == {}


async def test_definite_create_failure_and_relay_upload_failure(api, sessions, bucket):
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((1, 1, 1), size=(400, 400)))
    job = _start_upload(api, account_id)

    bucket["fail_put"] = True
    control = ControlPlane()
    await _run(sessions, control, utc_now())
    link = await _link(sessions, job["id"])
    assert (link.state, link.error_code) == ("failed", "relay_upload_failed")
    assert control.actions("CreateAsset") == []

    api.delete(f"{LINKS}/{job['id']}")
    bucket["fail_put"] = False
    retry = _start_upload(api, account_id)
    control = ControlPlane(create_error={"Code": "InvalidParameter", "Message": "bad url"})
    await _run(sessions, control, utc_now())
    link = await _link(sessions, retry["id"])
    assert (link.state, link.error_code, link.provider_asset_id) == ("failed", "InvalidParameter", None)


async def test_relay_is_not_deleted_after_the_connection_moves_to_another_bucket(api, sessions, bucket):
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((3, 3, 3), size=(400, 400)))
    job = _start_upload(api, account_id)
    control = ControlPlane(asset_status=[{"Status": "Active"}])
    t0 = utc_now()
    await _run(sessions, control, t0)
    await _run(sessions, control, t0 + timedelta(minutes=1))
    assert (await _link(sessions, job["id"])).state == "active"

    async with sessions() as session:
        await ObjectStorageConnectionRepository(session).save(
            kind="tos",
            region="cn-beijing",
            endpoint="tos-cn-beijing.volces.com",
            bucket="another-bucket",
            access_key_id=None,
            secret_access_key=None,
        )
        await session.commit()
    await _run(sessions, control, t0 + timedelta(hours=2))
    [relay] = await _relays(sessions)
    assert (relay.state, relay.last_error) == ("retained", "connection_changed")
    assert bucket["delete_calls"] == []


async def test_upload_requires_an_image_and_object_storage(api, sessions, bucket):
    account_id = await _configure(sessions, object_storage=False)
    body = {"resource_type": "characters", "resource_id": "Alice", "account_id": account_id}
    assert _panel(api)["content_version"] is None
    resp = api.post(LINKS, json=body, headers={"Accept-Language": "zh"})
    assert resp.status_code == 422
    assert "还没有可上传的版本" in resp.json()["detail"]

    _upload_sheet(api, solid_png_bytes((4, 4, 4), size=(400, 400)))
    resp = api.post(LINKS, json=body, headers={"Accept-Language": "zh"})
    assert resp.status_code == 422
    assert "对象存储" in resp.json()["detail"]
    assert _panel(api)["links"] == []
