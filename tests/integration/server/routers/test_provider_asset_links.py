"""供应商素材库与内容版本关联：上传入库、状态查询、中转副本清理，以及从素材库选择已有图片导入。

真实编排：资产图经文件路由上传成版本，入库经关联路由或 Agent 工具创建，后台处理直接调用 ``ingest.run_once``。
只替换外部边界：控制面请求与素材下载经 respx 拦截（控制面按 Action 分派），TOS SDK 在 ``create_sdk_client``
换成内存桶，素材地址的主机解析换成固定的公网地址。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import types
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tos.exceptions import TosServerError

from lib.artifacts import version_manager
from lib.asset_library import importing, ingest
from lib.asset_library.bindings import resolve_bound_scope
from lib.asset_library.links import account_scope, scope_of, selected_link
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import RelayObject
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.object_storage import client as storage_client
from lib.project.project_manager import ProjectManager
from server.agent_toolset.declaration import ToolDeclaration
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.remote import remote_tool
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import asset_library, files, provider_asset_links, versions
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import CallerContext, ProjectScope, Services
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import use_project_manager, use_upload_claim_registration
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
    for router in (files.router, versions.router, provider_asset_links.router, asset_library.router):
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


ACCOUNTS = "/api/v1/asset-library/accounts"
INFERENCE_KEY = "TEST-inference-key-0003"


def _switch_account_key(api: TestClient, account_id: int) -> None:
    """经设置页接口把账号记录原地换成另一个云账号的 Access Key。"""
    resp = api.patch(
        f"{ACCOUNTS}/{account_id}",
        json={"access_key_id": "TEST-CONTROL-PLANE-AK-OTHER", "secret_access_key": "TEST-SECRET-OTHER"},
    )
    assert resp.status_code == 200, resp.text


async def _bound_scope(sessions, credential_id: int):
    async with sessions() as session:
        return await resolve_bound_scope(
            session,
            credential_type="builtin",
            credential_id=credential_id,
            api_key=INFERENCE_KEY,
            base_url=None,
            default_base_url=PROVIDER_REGISTRY["ark"].default_base_url,
        )


async def test_reconfirmed_binding_after_key_change_does_not_select_the_old_accounts_asset(api, sessions, bucket):
    """账号记录换成另一个云账号的 Key 并重新确认绑定后，旧账号的素材 ID 不再被选中；旧关联保留原范围。"""
    account_id = await _configure(sessions)
    async with sessions() as session:
        credential = await CredentialRepository(session).create(provider="ark", name="TEST-ark", api_key=INFERENCE_KEY)
        await session.commit()
    binding = f"/api/v1/asset-library/bindings/builtin/{credential.id}"
    assert api.put(binding, json={"account_id": account_id}).status_code == 200
    _upload_sheet(api, solid_png_bytes((40, 50, 60), size=(400, 400)))
    content_version_id = _panel(api)["content_version"]["id"]
    job = _start_upload(api, account_id)
    await _run(sessions, ControlPlane(asset_status=[{"Status": "Active"}]), utc_now())
    await _run(sessions, ControlPlane(asset_status=[{"Status": "Active"}]), utc_now() + timedelta(minutes=1))
    old = await _link(sessions, job["id"])
    assert old.state == "active"
    scope_a = await _bound_scope(sessions, credential.id)
    assert scope_a is not None

    _switch_account_key(api, account_id)
    assert await _bound_scope(sessions, credential.id) is None
    assert api.put(binding, json={"account_id": account_id}).status_code == 200
    scope_b = await _bound_scope(sessions, credential.id)
    assert scope_b is not None
    assert scope_b.account_id == account_id
    async with sessions() as session:
        assert (
            await selected_link(session, project_name="demo", content_version_id=content_version_id, scope=scope_b)
            is None
        )
        # 旧关联不删除、不改写，仍属于原账号身份的范围
        kept = await selected_link(session, project_name="demo", content_version_id=content_version_id, scope=scope_a)
    assert kept is not None
    assert kept.id == job["id"]
    assert scope_of(kept) == scope_a

    # 面板把旧关联单列为另一范围，账号行可以为新身份重新上传
    panel = _panel(api)
    [shown] = panel["links"]
    [account] = panel["accounts"]
    assert shown["scope_key"] != account["scope_key"]
    again = _start_upload(api, account_id)
    assert again["id"] != job["id"]


async def test_tasks_submitted_under_the_old_key_are_not_advanced_with_the_new_key(api, sessions, bucket):
    """处理中与待提交的任务都不能用换过的 Key 推进：不建素材、不查询，也不把新账号的结果写进旧关联。"""
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((70, 80, 90), size=(400, 400)))
    processing = _start_upload(api, account_id)
    t0 = utc_now()
    await _run(sessions, ControlPlane(), t0)
    assert (await _link(sessions, processing["id"])).state == "processing"
    _upload_sheet(api, solid_png_bytes((90, 80, 70), size=(400, 400)))
    pending = _start_upload(api, account_id)
    relays_before = len(bucket["objects"])

    _switch_account_key(api, account_id)
    control = ControlPlane(asset_status=[{"Status": "Active"}])
    await _run(sessions, control, t0 + timedelta(minutes=10))

    assert control.calls == []
    assert len(bucket["objects"]) == relays_before
    failed = await _link(sessions, pending["id"])
    assert (failed.state, failed.error_code) == ("failed", "account_scope_changed")
    stalled = await _link(sessions, processing["id"])
    assert (stalled.state, stalled.check_error, stalled.next_check_at) == ("processing", "account_unavailable", None)


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


# ── 从素材库选择已有图片 ─────────────────────────────────────────────

ASSET_URL = "https://ark-asset.cn-beijing.volcengine.example/asset-20261103-b.png"
ASSET_SIGNATURE = "X-Tos-Signature=TEST-SIGNATURE-0003"
IMPORT = f"{LINKS}/import"


@pytest.fixture
def public_dns(monkeypatch) -> None:
    """素材地址的主机解析到公网地址；不发真实 DNS 查询。"""

    async def _resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(importing, "resolve_host", _resolve)


def _active_image(url: str = f"{ASSET_URL}?{ASSET_SIGNATURE}", **overrides: Any) -> dict[str, Any]:
    return {
        "Status": "Active",
        "AssetType": "Image",
        "Name": "林夕-正面",
        "GroupId": "group-team",
        "URL": url,
        **overrides,
    }


def _import(api: TestClient, account_id: int, asset_id: str = "asset-20261103-b") -> httpx.Response:
    return api.post(
        IMPORT,
        json={"resource_type": "characters", "resource_id": "Alice", "account_id": account_id, "asset_id": asset_id},
        headers={"Accept-Language": "zh"},
    )


def _versions(api: TestClient) -> dict[str, Any]:
    resp = api.get("/api/v1/projects/demo/versions/characters/Alice")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _all_links(sessions) -> list[ProviderAssetLink]:
    async with sessions() as session:
        return list((await session.execute(select(ProviderAssetLink))).scalars())


@pytest.mark.parametrize("large_original", [False, True])
async def test_import_saves_provider_image_as_uploaded_version_with_active_link(
    api, sessions, project, public_dns, caplog, large_original
):
    """导入闭环：取回素材原图保存为成品新版本，关联属于这个版本且可用；切回旧版本不继承关联。"""
    caplog.set_level(logging.INFO)
    account_id = await _configure(sessions, object_storage=False)  # 导入不经对象存储中转
    image_a = solid_png_bytes((200, 10, 10), size=(400, 400))
    image_b = solid_png_bytes((10, 10, 200), size=(400, 400))
    if large_original:
        buffer = BytesIO()
        Image.new("RGB", (900, 900), (10, 10, 200)).save(buffer, format="PNG", compress_level=0)
        image_b = buffer.getvalue()
        assert len(image_b) > 2 * 1024 * 1024
    _upload_sheet(api, image_a)
    version_a = _panel(api)["content_version"]

    control = ControlPlane(asset_status=[_active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        download = router.get(ASSET_URL).mock(return_value=httpx.Response(200, content=image_b))
        resp = _import(api, account_id)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # 只按 GetAsset 返回的地址取图，查询带账号的供应商项目
    assert [b["ProjectName"] for b in control.actions("GetAsset")] == ["film"]
    assert download.call_count == 1
    assert ASSET_SIGNATURE in str(download.calls.last.request.url)

    # 正式内容是素材原图，按手动上传成品记入版本历史
    sheet = project.get_project_path("demo") / "characters" / "Alice.png"
    assert sheet.read_bytes() == image_b
    history = _versions(api)
    assert history["current_version"] == 2
    record = history["versions"][-1]
    assert record["source"] == "manual_upload"
    assert record["content_version_id"] == body["content_version"]["id"] != version_a["id"]

    # 关联属于新内容版本，保留账号范围，可用于生成
    link = body["link"]
    assert (link["state"], link["usable"], link["provider_asset_id"]) == ("active", True, "asset-20261103-b")
    assert (link["site"], link["region"], link["provider_project_name"]) == ("volcengine", "cn-beijing", "film")
    assert link["content_version_id"] == body["content_version"]["id"]
    stored = await _link(sessions, link["id"])
    assert (stored.origin, stored.next_check_at) == ("import", None)
    async with sessions() as session:
        account = await AssetLibraryRepository(session).get_account(account_id)
        assert account is not None
        found = await selected_link(
            session, project_name="demo", content_version_id=body["content_version"]["id"], scope=account_scope(account)
        )
    assert found is not None
    assert found.id == link["id"]

    # 切回 A：A 没有关联；再切回导入的版本，关联仍在
    assert api.post("/api/v1/projects/demo/versions/characters/Alice/restore/1").status_code == 200
    assert _panel(api)["links"] == []
    assert api.post("/api/v1/projects/demo/versions/characters/Alice/restore/2").status_code == 200
    assert [shown["id"] for shown in _panel(api)["links"]] == [link["id"]]

    # 签名地址与控制面密钥不进响应与日志
    assert ASSET_SIGNATURE not in resp.text
    assert "TEST-SIGNATURE" not in caplog.text
    assert SK not in caplog.text


def _fail_claim(monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise RuntimeError("claim failed")

    use_upload_claim_registration(monkeypatch, _raise)


def _fail_after_link(monkeypatch) -> None:
    """关联已落库之后，正式写入的收尾步骤失败。"""

    def _raise(*_args, **_kwargs):
        raise RuntimeError("activity failed")

    monkeypatch.setattr(version_manager, "record_project_activity", _raise)


@pytest.mark.parametrize(
    ("status", "download", "status_code", "detail", "downloaded"),
    [
        pytest.param(_active_image(Status="Processing"), None, 422, "还不可用", False, id="not-active"),
        pytest.param(_active_image(AssetType="Video"), None, 422, "只能选择图片", False, id="not-image"),
        pytest.param(_active_image(URL=None), None, 502, "无法取回", False, id="no-url"),
        pytest.param(
            _active_image(url="https://127.0.0.1/asset.png?X-Tos-Signature=TEST-SIGNATURE-0003"),
            None,
            502,
            "无法取回",
            False,
            id="internal-destination",
        ),
        pytest.param(_active_image(), httpx.Response(403), 502, "无法取回", True, id="download-denied"),
        pytest.param(
            _active_image(), httpx.Response(200, content=b"<html>expired</html>"), 422, "PNG", True, id="not-an-image"
        ),
    ],
)
async def test_import_that_cannot_get_real_content_is_refused(
    api, sessions, public_dns, status, download, status_code, detail, downloaded
):
    """取不回实际内容或素材不可用：拒绝导入，原选中版本不变，不留下关联。"""
    account_id = await _configure(sessions, object_storage=False)
    _upload_sheet(api, solid_png_bytes((1, 2, 3), size=(400, 400)))
    before = _versions(api)

    control = ControlPlane(asset_status=[status])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        route = router.get(ASSET_URL).mock(return_value=download or httpx.Response(500))
        resp = _import(api, account_id)
    assert resp.status_code == status_code, resp.text
    assert detail in resp.json()["detail"]
    assert route.called is downloaded
    assert "TEST-SIGNATURE" not in resp.text
    assert _versions(api) == before
    assert await _all_links(sessions) == []


@pytest.mark.parametrize("fail", [_fail_claim, _fail_after_link], ids=["claim-fails", "fails-after-link-recorded"])
async def test_import_commit_failure_keeps_selected_version_and_no_link(
    api, sessions, project, public_dns, monkeypatch, fail
):
    """正式提交失败：资产图、版本历史保持原样，不显示导入成功，也不留下关联。"""
    account_id = await _configure(sessions, object_storage=False)
    image_a = solid_png_bytes((5, 5, 5), size=(400, 400))
    _upload_sheet(api, image_a)
    before = _versions(api)
    fail(monkeypatch)

    control = ControlPlane(asset_status=[_active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=solid_png_bytes((9, 9, 9), size=(400, 400)))
        )
        with pytest.raises(RuntimeError):
            _import(api, account_id)

    assert (project.get_project_path("demo") / "characters" / "Alice.png").read_bytes() == image_a
    assert _versions(api) == before
    assert await _all_links(sessions) == []


# ── Agent 工具 ───────────────────────────────────────────────────────
# 工具经两宿主的真实 adapter 调用，与 Web 共用同一组关联记录；领域规则已由上面的 Web 用例覆盖，
# 这里只验证明确指令、立即返回、两宿主结果一致，以及结果里不带密钥与签名。


@pytest.fixture
def agent_services(project, sessions) -> Services:
    return Services(
        projects=project,
        workflow_planner=WorkflowPlanner(project),
        capabilities=ConfigResolver(sessions),
        sessions=sessions,
    )


def _agent_tool(name: str) -> ToolDeclaration[Any, Any]:
    declaration = next(d for d in AGENT_TOOLSET if d.name == name)
    assert isinstance(declaration, ToolDeclaration)
    return declaration


async def _embedded(services: Services, name: str, arguments: dict[str, Any]) -> tuple[types.CallToolResult, Any]:
    server = embedded_server(
        [_agent_tool(name)],
        name="arcreel",
        version="1.0.0",
        scope=ProjectScope(project_name="demo", data_root=services.projects.data_root),
        caller=CallerContext(user_id="default", source="embedded"),
        services=services,
    )["instance"]
    request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
    result = (await server.request_handlers[types.CallToolRequest](request)).root
    assert isinstance(result, types.CallToolResult)
    texts = [block.text for block in result.content if isinstance(block, types.TextContent)]
    return result, json.loads(texts[-1])


async def _remote(services: Services, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    tool = remote_tool(
        _agent_tool(name),
        projects=services.projects,
        services=services,
        caller=lambda: CallerContext(user_id="default", source="mcp"),
    )
    result = await tool.run({"project": "demo", **arguments})
    assert isinstance(result, types.CallToolResult)
    return result


def _tool_text(result: types.CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, types.TextContent))


async def test_agent_uploads_only_on_explicit_request_and_shares_the_web_links(api, sessions, bucket, agent_services):
    """上传必须带用户明确要求；工具立即返回入库任务，两宿主与 Web 看到同一条关联，解除后两侧内容都在。"""
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((200, 10, 10), size=(400, 400)))
    target = {"resource_type": "character", "resource_id": "Alice", "account_id": account_id}

    # 没有用户明确要求（例如只是生成被拒）时，入口拒绝，不创建入库任务
    refused, refused_json = await _embedded(agent_services, "upload_to_provider_asset_library", target)
    assert refused.isError is True
    assert refused_json["problem"]["code"] == "invalid_request"
    assert await _all_links(sessions) == []

    started, started_json = await _embedded(
        agent_services, "upload_to_provider_asset_library", {**target, "user_requested": True}
    )
    assert started.isError is False
    job = started_json["provider_asset_link"]
    # 不等待供应商：后台尚未推进，返回的是待上传的任务
    assert (job["state"], job["usable"], job["polling"]) == ("pending", False, True)
    assert [link["id"] for link in _panel(api)["links"]] == [job["id"]]

    # 同一范围重复上传：沿用领域层的冲突判断与错误码
    again = await _remote(agent_services, "upload_to_provider_asset_library", {**target, "user_requested": True})
    assert again.isError is True
    assert again.structuredContent is not None
    assert again.structuredContent["problem"]["code"] == "provider_asset_link_exists"

    control = ControlPlane(asset_status=[{"Status": "Active", "Name": "x", "AssetType": "Image"}])
    t0 = utc_now()
    query = {"resource_type": "character", "resource_id": "Alice"}
    await _run(sessions, control, t0)
    _, processing = await _embedded(agent_services, "get_provider_asset_links", query)
    assert [link["state"] for link in processing["provider_asset_links"]["links"]] == ["processing"]
    await _run(sessions, control, t0 + timedelta(minutes=10))

    embedded, embedded_json = await _embedded(agent_services, "get_provider_asset_links", query)
    remote = await _remote(agent_services, "get_provider_asset_links", query)
    assert embedded.isError is remote.isError is False
    assert embedded_json == remote.structuredContent
    panel = embedded_json["provider_asset_links"]
    assert panel == _panel(api)
    [shown] = panel["links"]
    assert (shown["id"], shown["state"], shown["usable"]) == (job["id"], "active", True)
    assert [a["id"] for a in panel["accounts"]] == [account_id]

    released = await _remote(agent_services, "release_provider_asset_link", {"link_id": job["id"]})
    assert released.isError is False
    assert released.structuredContent is not None
    assert released.structuredContent["provider_asset_link"]["selected"] is False
    assert _panel(api)["links"] == []
    assert _panel(api)["content_version"] is not None
    assert control.actions("DeleteAsset") == []

    # 工具结果不带控制面密钥、对象存储密钥或签名地址
    for result in (started, again, embedded, remote, released):
        text = _tool_text(result)
        assert SK not in text
        assert "tos-secret" not in text
        assert "X-Tos-Signature" not in text


async def test_agent_selects_an_existing_library_image_as_a_new_version(api, sessions, public_dns, agent_services):
    """浏览素材库后选择已有图片：保存实际内容为新版本并建立可用关联；结果不含签名地址。"""
    account_id = await _configure(sessions, object_storage=False)
    _upload_sheet(api, solid_png_bytes((200, 10, 10), size=(400, 400)))
    image_b = solid_png_bytes((10, 10, 200), size=(400, 400))
    control = ControlPlane(
        asset_status=[_active_image()],
        listed_assets=[{"Id": "asset-20261103-b", "Name": "林夕-正面", "AssetType": "Image", "Status": "Active"}],
    )

    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        router.get(ASSET_URL).mock(return_value=httpx.Response(200, content=image_b))
        listed, listed_json = await _embedded(
            agent_services, "list_provider_library_assets", {"account_id": account_id}
        )
        selected, selected_json = await _embedded(
            agent_services,
            "select_provider_library_asset",
            {
                "resource_type": "character",
                "resource_id": "Alice",
                "account_id": account_id,
                "asset_id": "asset-20261103-b",
            },
        )

    assert listed.isError is False
    assert [item["id"] for item in listed_json["provider_library_assets"]["items"]] == ["asset-20261103-b"]
    assert selected.isError is False
    chosen = selected_json["provider_asset_selection"]
    assert chosen["content_version"]["version"] == 2
    assert (chosen["link"]["state"], chosen["link"]["usable"]) == ("active", True)
    assert _versions(api)["versions"][-1]["source"] == "manual_upload"
    assert [link["id"] for link in _panel(api)["links"]] == [chosen["link"]["id"]]
    for result in (listed, selected):
        assert "TEST-SIGNATURE" not in _tool_text(result)
        assert SK not in _tool_text(result)
