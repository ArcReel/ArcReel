"""供应商素材库与内容版本关联：上传入库、状态查询、中转副本清理，以及从素材库选择已有图片导入。

真实编排：资产图经文件路由上传成版本，入库经关联路由或 Agent 工具创建，后台处理直接调用 ``ingest.run_once``。
只替换外部边界：控制面请求与素材下载经 respx 拦截（控制面按 Action 分派），TOS SDK 在 ``create_sdk_client``
换成内存桶，素材地址的主机解析换成固定的公网地址。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
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
from lib.asset_library import importing, ingest, links
from lib.asset_library.bindings import resolve_bound_scope
from lib.asset_library.links import account_scope, scope_of, selected_link
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import SystemSettingRepository
from lib.config.resolver import ConfigResolver
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import FormalObjectRef, RelayObject
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.async_thread import run_sync_transaction
from lib.material.formal_objects import MATERIAL_STORAGE_TARGET_SETTING, formal_object_key
from lib.material.work_files import cleanup_work_cache
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager
from server.agent_toolset.declaration import ToolDeclaration
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.remote import remote_tool
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import (
    asset_library,
    characters,
    derivative_uploads,
    files,
    project_trash,
    provider_asset_links,
    versions,
)
from server.services.currency import upload_finalize
from server.services.project.provider_asset_import import import_provider_asset
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import CallerContext, ProjectScope, Services
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import bind_session_factory, use_object_storage_sdk, use_project_manager, use_upload_claim_registration
from tests.http_capture import capture_http, request_json
from tests.integration.server.derivative_sheet_support import solid_png_bytes

VOLC_HOST = "https://ark.cn-beijing.volcengineapi.com/"
SK = "TEST-CONTROLPLANE-SECRET-0003"
TOS_SK = "TEST-TOS-SECRET-0003"
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

    def head_object(self, *, bucket: str, key: str):
        if (bucket, key) not in self.state["objects"]:
            raise _server_error(404, "NoSuchKey")
        return SimpleNamespace(content_length=len(self.state["objects"][(bucket, key)]), etag="etag")

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
    use_object_storage_sdk(monkeypatch, lambda config: MemoryTos(state))
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
    for router in (
        files.router,
        derivative_uploads.router,
        versions.router,
        provider_asset_links.router,
        asset_library.router,
        characters.router,
        project_trash.router,
    ):
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
                secret_access_key=TOS_SK,
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


async def test_formal_object_is_presigned_directly_and_never_swept_as_relay(api, sessions, bucket, monkeypatch):
    """远端保存的正式图片入库时直接为固定内容版本签发地址：不写中转副本，中转清理也不会删除它。"""
    bind_session_factory(monkeypatch, sessions)
    account_id = await _configure(sessions)
    async with sessions() as session:
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, "object_storage")
        await session.commit()
    _upload_sheet(api, solid_png_bytes((30, 60, 90), size=(400, 400)))
    [(_bucket, formal_key)] = list(bucket["objects"])
    assert formal_key.startswith("arcreel-materials/")

    job = _start_upload(api, account_id)
    control = ControlPlane(asset_status=[{"Status": "Active", "Name": "x", "AssetType": "Image"}])
    t0 = utc_now()
    await _run(sessions, control, t0)
    assert [key for key, _expires in bucket["presigned"]] == [formal_key]
    assert control.actions("CreateAsset")[0]["URL"].startswith(f"https://relay-bucket.tos.example/{formal_key}")
    assert await _relays(sessions) == []

    await _run(sessions, control, t0 + timedelta(minutes=10))
    await _run(sessions, control, t0 + timedelta(hours=30))
    assert (await _link(sessions, job["id"])).state == "active"
    assert bucket["delete_calls"] == []
    assert list(bucket["objects"]) == [("relay-bucket", formal_key)]
    # 保留期过后入库任务撤下自己的引用，项目仍在使用，对象保留
    assert await _holders(sessions) == {("demo", "active")}


async def _holders(sessions) -> set[tuple[str, str]]:
    async with sessions() as session:
        return {(ref.project_name, ref.state) for ref in (await session.execute(select(FormalObjectRef))).scalars()}


async def test_processing_ingest_keeps_its_signed_formal_object_after_the_source_is_purged(
    api, sessions, bucket, monkeypatch
):
    """入库任务处理中，来源资产被删除并彻底清除：签名的正式对象由入库任务的引用保留到地址过期之后，再统一回收。"""
    bind_session_factory(monkeypatch, sessions)
    account_id = await _configure(sessions)
    async with sessions() as session:
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, "object_storage")
        await session.commit()
    _upload_sheet(api, solid_png_bytes((60, 160, 200), size=(400, 400)))
    [(_bucket, formal_key)] = list(bucket["objects"])
    job = _start_upload(api, account_id)
    control = ControlPlane()
    t0 = utc_now()
    await _run(sessions, control, t0)
    assert (await _link(sessions, job["id"])).state == "processing"
    assert await _holders(sessions) == {("demo", "active"), (ingest.ingest_holder(job["id"]), "active")}

    assert api.delete("/api/v1/projects/demo/characters/Alice").status_code == 200
    [record] = api.get("/api/v1/projects/demo/trash").json()["records"]
    assert api.delete(f"/api/v1/projects/demo/trash/{record['id']}").status_code == 200
    await _run(sessions, control, t0 + timedelta(hours=24))
    assert (await _link(sessions, job["id"])).state == "processing"
    assert await _holders(sessions) == {(ingest.ingest_holder(job["id"]), "active")}
    assert bucket["delete_calls"] == []
    assert list(bucket["objects"]) == [("relay-bucket", formal_key)]

    # 地址过期再过宽限期，供应商已无法取图：撤下入库任务的引用，对象失去全部引用后回收
    await _run(sessions, control, t0 + ingest.RELAY_URL_TTL + timedelta(hours=1, minutes=1))
    assert await _holders(sessions) == set()
    assert bucket["delete_calls"] == [formal_key]
    assert list(bucket["objects"]) == []


async def test_relay_read_of_a_remote_version_is_not_evicted_between_fetch_and_read(
    api, sessions, bucket, project, monkeypatch
):
    """远端图片回落中转上传时，取回工作文件到读出字节之间登记使用，同时发生的缓存清理跳过它，入库照常提交。"""
    bind_session_factory(monkeypatch, sessions)
    account_id = await _configure(sessions)
    async with sessions() as session:
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, "object_storage")
        await session.commit()
    _upload_sheet(api, solid_png_bytes((90, 30, 60), size=(400, 400)))

    async def _unavailable(*_args: Any, **_kwargs: Any) -> bool:
        return False  # 正式对象此刻不能签名，入库回落中转副本

    fetch = ingest.ensure_content_version_file
    cleanups: list[Any] = []

    async def _fetch_then_cleanup(*args: Any, **kwargs: Any) -> Path:
        path = await fetch(*args, **kwargs)
        cleanups.append(await cleanup_work_cache(project.projects_dir, min_idle=timedelta(0), session_factory=sessions))
        return path

    monkeypatch.setattr(ingest, "add_reference", _unavailable)
    monkeypatch.setattr(ingest, "ensure_content_version_file", _fetch_then_cleanup)
    job = _start_upload(api, account_id)
    await _run(sessions, ControlPlane(), utc_now())

    [cleanup] = cleanups
    assert cleanup.kept_in_use == 1
    link = await _link(sessions, job["id"])
    assert (link.state, link.error_code) == ("processing", None)
    [relay] = await _relays(sessions)
    assert relay.object_key.startswith("arcreel-relay/")


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


async def test_pending_upload_reads_the_content_version_after_its_resource_is_renamed(api, sessions, bucket, project):
    """入库在途时资源改名：快照随版本历史移到新路径，任务按内容版本身份读到原内容，不判为内容不可用。"""
    account_id = await _configure(sessions)
    image = solid_png_bytes((40, 80, 120), size=(400, 400))
    _upload_sheet(api, image)
    job = _start_upload(api, account_id)
    frozen = (await _link(sessions, job["id"])).locator_path

    vm = version_manager.VersionManager(project.get_project_path("demo"))
    assert vm.rename_resource("characters", "Alice", "Alicia") == 1
    assert not (project.get_project_path("demo") / frozen).exists()

    control = ControlPlane(asset_status=[{"Status": "Active"}])
    await _run(sessions, control, utc_now())
    link = await _link(sessions, job["id"])
    assert (link.state, link.error_code) == ("processing", None)
    assert list(bucket["objects"].values()) == [image]


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
    # 删除重试次数累积很多后，退避仍停在上限，不因溢出中断整轮处理
    async with sessions() as session:
        stored = await session.get(RelayObject, relay.id)
        assert stored is not None
        stored.delete_attempts = 100
        stored.next_attempt_at = t0 + timedelta(hours=2)
        await session.commit()
    await _run(sessions, control, t0 + timedelta(hours=2, minutes=2))
    [relay] = await _relays(sessions)
    assert relay.delete_attempts == 101
    assert relay.next_attempt_at is not None
    assert ingest._aware(relay.next_attempt_at) == t0 + timedelta(hours=8, minutes=2)
    bucket["fail_delete"] = False
    await _run(sessions, control, t0 + timedelta(hours=9))
    [relay] = await _relays(sessions)
    assert relay.state == "deleted"
    assert bucket["objects"] == {}


async def test_long_running_status_backoff_stays_capped_instead_of_overflowing(api, sessions, bucket):
    """状态查询次数累积很多后，退避仍停在上限，不因溢出中断整轮处理。"""
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((9, 9, 9), size=(400, 400)))
    job = _start_upload(api, account_id)
    control = ControlPlane(asset_status=[{"Status": "Processing"}])
    t0 = utc_now()
    await _run(sessions, control, t0)
    async with sessions() as session:
        link = await session.get(ProviderAssetLink, job["id"])
        assert link is not None
        link.check_attempts = 60
        link.next_check_at = t0
        await session.commit()

    at = t0 + timedelta(minutes=30)
    await _run(sessions, ControlPlane(asset_status=[{"Status": "Processing"}]), at)
    link = await _link(sessions, job["id"])
    assert link.state == "processing"
    assert link.next_check_at is not None
    assert ingest._aware(link.next_check_at) == at + ingest.CHECK_MAX_INTERVAL


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


async def test_overlong_provider_error_code_still_settles_the_submission(api, sessions, bucket):
    """供应商返回超过列宽的错误码：截断后落账，任务不停在 submitting。"""
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((2, 2, 2), size=(400, 400)))
    job = _start_upload(api, account_id)
    control = ControlPlane(create_error={"Code": "X" * 200, "Message": "bad"})
    await _run(sessions, control, utc_now())
    link = await _link(sessions, job["id"])
    assert (link.state, link.error_code) == ("failed", "X" * 128)


async def test_concurrent_upload_of_the_same_version_is_a_conflict(api, sessions, bucket, monkeypatch):
    """先查后写之间另一请求已选定同一范围：唯一索引拦下的写入报 409，不是服务器错误。"""
    account_id = await _configure(sessions)
    _upload_sheet(api, solid_png_bytes((3, 3, 3), size=(400, 400)))
    first = _start_upload(api, account_id)

    async def _missed(*_args, **_kwargs):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(links, "selected_link", _missed)
        resp = api.post(LINKS, json={"resource_type": "characters", "resource_id": "Alice", "account_id": account_id})
    assert resp.status_code == 409, resp.text
    assert [link["id"] for link in _panel(api)["links"]] == [first["id"]]


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


async def test_import_refuses_a_hostname_that_resolves_to_a_private_address(api, sessions, monkeypatch):
    """素材地址的主机名解析到私网地址：不发下载请求，拒绝导入，原选中版本不变，不留下关联。"""

    async def _resolve(host: str, port: int) -> list[str]:
        return ["10.0.0.1"]

    monkeypatch.setattr(importing, "resolve_host", _resolve)
    account_id = await _configure(sessions, object_storage=False)
    _upload_sheet(api, solid_png_bytes((1, 2, 3), size=(400, 400)))
    before = _versions(api)

    control = ControlPlane(asset_status=[_active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        route = router.get(ASSET_URL).mock(return_value=httpx.Response(200))
        resp = _import(api, account_id)

    assert resp.status_code == 502, resp.text
    assert not route.called
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


async def test_import_into_an_asset_deleted_during_download_writes_nothing(api, sessions, project, public_dns):
    """取图期间资产被删除：导入报不存在，不在原路径留下图片，资产从回收站恢复时仍落回原名。"""
    account_id = await _configure(sessions, object_storage=False)
    sheet = project.get_project_path("demo") / "characters" / "Alice.png"
    assert not sheet.exists()

    async def _download(_request: httpx.Request) -> httpx.Response:
        await run_sync_transaction(project.delete_asset, "demo", "characters", "Alice")
        return httpx.Response(200, content=solid_png_bytes((9, 9, 9), size=(400, 400)))

    control = ControlPlane(asset_status=[_active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        router.get(ASSET_URL).mock(side_effect=_download)
        resp = _import(api, account_id)

    assert resp.status_code == 404, resp.text
    assert not sheet.exists()
    assert await _all_links(sessions) == []


async def test_import_follows_the_storage_choice_and_keeps_the_original_bytes(
    api, sessions, project, bucket, public_dns, monkeypatch
):
    """存储选择为对象存储时，导入的原图作为正式对象保存，关联属于这个远端版本；保存失败不发布版本、不留关联。"""
    bind_session_factory(monkeypatch, sessions)
    account_id = await _configure(sessions)
    async with sessions() as session:
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, "object_storage")
        await session.commit()
    _upload_sheet(api, solid_png_bytes((200, 10, 10), size=(400, 400)))
    buffer = BytesIO()
    Image.new("RGB", (900, 900), (10, 10, 200)).save(buffer, format="PNG", compress_level=0)
    original = buffer.getvalue()
    assert len(original) > 2 * 1024 * 1024

    control = ControlPlane(asset_status=[_active_image(), _active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        router.get(ASSET_URL).mock(return_value=httpx.Response(200, content=original))
        resp = _import(api, account_id)
        assert resp.status_code == 201, resp.text
        before = _versions(api)
        record = before["versions"][-1]
        stored = record["storage"]
        assert (stored["bucket"], stored["key"]) == (
            "relay-bucket",
            formal_object_key(record["content_digest"], ".png"),
        )
        assert bucket["objects"][("relay-bucket", stored["key"])] == original
        sheet = project.get_project_path("demo") / "characters" / "Alice.png"
        assert sheet.read_bytes() == original
        [link] = await _all_links(sessions)
        assert link.content_version_id == record["content_version_id"] == resp.json()["content_version"]["id"]
        async with sessions() as session:
            refs = (await session.execute(select(FormalObjectRef.content_version_id, FormalObjectRef.state))).all()
        assert (record["content_version_id"], "active") in refs

        bucket["fail_put"] = True
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=solid_png_bytes((9, 9, 9), size=(400, 400)))
        )
        failed = _import(api, account_id, asset_id="asset-20261103-c")
    assert failed.status_code == 502, failed.text
    assert _versions(api) == before
    assert sheet.read_bytes() == original
    assert [kept.id for kept in await _all_links(sessions)] == [link.id]


# ── 角色衍生图 ───────────────────────────────────────────────────────
# 衍生图走自己的手动上传成品路径（规范路径固定为 .png），关联编排与资产图共用；
# 这里只覆盖衍生特有的部分：落盘与版本、PNG 封装下的原像素保真、关联可用与解除，以及提交钩子的回滚。

DERIVATIVE = {"resource_type": "character_derivatives", "resource_id": "Alice/战斗装"}
DERIVATIVE_SHEET = Path("characters") / "derivatives" / "Alice" / "战斗装.png"


@pytest.fixture
def derivative(project) -> None:
    def _register(entry: dict[str, Any]) -> None:
        entry.setdefault(DERIVATIVES_FIELD, {})["战斗装"] = {"description": "换上黑色重甲", "character_sheet": ""}

    project.update_asset_entry("character", "demo", "Alice", _register)


def _upload_derivative(api: TestClient, content: bytes) -> None:
    resp = api.post(
        "/api/v1/projects/demo/characters/Alice/derivatives/战斗装/upload",
        files={"file": ("armor.png", content, "image/png")},
    )
    assert resp.status_code == 200, resp.text


def _derivative_panel(api: TestClient) -> dict[str, Any]:
    resp = api.get(LINKS, params=DERIVATIVE)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _derivative_versions(api: TestClient) -> dict[str, Any]:
    resp = api.get("/api/v1/projects/demo/versions/character-derivative/Alice/战斗装")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _import_derivative(api: TestClient, account_id: int) -> httpx.Response:
    return api.post(IMPORT, json={**DERIVATIVE, "account_id": account_id, "asset_id": "asset-20261103-b"})


def _pixels(content: bytes) -> bytes:
    with Image.open(BytesIO(content)) as image:
        return image.convert("RGB").tobytes()


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    image = Image.new("RGB", (400, 400), (10, 10, 200))
    for x in range(0, 400, 7):
        image.putpixel((x, x), (250, 250, 250))
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


@pytest.mark.parametrize("original_format", ["png", "jpeg"])
async def test_derivative_import_saves_original_pixels_as_uploaded_version_with_active_link(
    api, sessions, project, derivative, public_dns, original_format
):
    """衍生图导入闭环：素材内容成为衍生的手动上传新版本，关联属于这个版本且可用；解除后图片保留。"""
    account_id = await _configure(sessions, object_storage=False)
    _upload_derivative(api, solid_png_bytes((200, 10, 10), size=(400, 400)))
    version_a = _derivative_panel(api)["content_version"]
    original = solid_png_bytes((10, 10, 200), size=(400, 400)) if original_format == "png" else _jpeg_bytes()

    control = ControlPlane(asset_status=[_active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        router.get(ASSET_URL).mock(return_value=httpx.Response(200, content=original))
        resp = _import_derivative(api, account_id)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # 衍生规范路径是 .png：PNG 原样保存，JPEG 只换封装，像素与素材一致
    saved = (project.get_project_path("demo") / DERIVATIVE_SHEET).read_bytes()
    if original_format == "png":
        assert saved == original
    else:
        assert saved.startswith(b"\x89PNG")
        assert _pixels(saved) == _pixels(original)
    history = _derivative_versions(api)
    assert history["current_version"] == 2
    assert history["versions"][-1]["source"] == "manual_upload"
    assert history["versions"][-1]["content_version_id"] == body["content_version"]["id"] != version_a["id"]

    link = body["link"]
    assert (link["state"], link["usable"], link["provider_asset_id"]) == ("active", True, "asset-20261103-b")
    assert link["content_version_id"] == body["content_version"]["id"]
    assert [shown["id"] for shown in _derivative_panel(api)["links"]] == [link["id"]]
    assert ASSET_SIGNATURE not in resp.text

    # 切回旧版本不继承关联；回到导入的版本后解除，只解除关系，图片仍在
    assert api.post("/api/v1/projects/demo/versions/character-derivative/Alice/战斗装/restore/1").status_code == 200
    assert _derivative_panel(api)["links"] == []
    assert api.post("/api/v1/projects/demo/versions/character-derivative/Alice/战斗装/restore/2").status_code == 200
    assert api.delete(f"{LINKS}/{link['id']}").status_code == 204
    assert _derivative_panel(api)["links"] == []
    assert (await _link(sessions, link["id"])).selected is False
    assert (project.get_project_path("demo") / DERIVATIVE_SHEET).read_bytes() == saved


@pytest.mark.parametrize("is_derivative", [False, True], ids=["asset-sheet", "derivative"])
async def test_import_cancellation_after_link_preserves_committed_image_and_link(
    api, sessions, project, derivative, public_dns, monkeypatch, is_derivative
):
    """正式提交后的取消不能把已经生效的关联当作孤儿删除。"""
    account_id = await _configure(sessions, object_storage=False)
    original = solid_png_bytes((10, 10, 200), size=(400, 400))
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    module = upload_finalize if is_derivative else version_manager
    name = "emit_generation_success_batch" if is_derivative else "record_project_activity"
    finish = getattr(module, name)

    def pause_after_commit(*args, **kwargs):
        result = finish(*args, **kwargs)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=10), "test did not release the commit thread"
        return result

    monkeypatch.setattr(module, name, pause_after_commit)
    target = DERIVATIVE if is_derivative else {"resource_type": "characters", "resource_id": "Alice"}
    control = ControlPlane(asset_status=[_active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        router.get(ASSET_URL).mock(return_value=httpx.Response(200, content=original))
        async with sessions() as session:
            task = asyncio.create_task(
                import_provider_asset(
                    session,
                    project_name="demo",
                    **target,
                    account_id=account_id,
                    asset_id="asset-20261103-b",
                    manager=project,
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=10)
                task.cancel()
            finally:
                release.set()
            [result] = await asyncio.gather(task, return_exceptions=True)

    sheet = DERIVATIVE_SHEET if is_derivative else Path("characters/Alice.png")
    assert (project.get_project_path("demo") / sheet).read_bytes() == original
    panel = _derivative_panel(api) if is_derivative else _panel(api)
    [link] = await _all_links(sessions)
    assert link.content_version_id == panel["content_version"]["id"]
    assert [shown["id"] for shown in panel["links"]] == [link.id]
    assert not isinstance(result, BaseException)


async def test_derivative_import_failing_after_link_keeps_selected_version_and_no_link(
    api, sessions, project, derivative, public_dns, monkeypatch
):
    """衍生的正式提交在关联落库后失败：衍生图与版本历史保持原样，也不留下关联。"""
    account_id = await _configure(sessions, object_storage=False)
    image_a = solid_png_bytes((5, 5, 5), size=(400, 400))
    _upload_derivative(api, image_a)
    before = _derivative_versions(api)
    _fail_after_link(monkeypatch)

    control = ControlPlane(asset_status=[_active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=solid_png_bytes((9, 9, 9), size=(400, 400)))
        )
        with pytest.raises(RuntimeError):
            _import_derivative(api, account_id)

    assert (project.get_project_path("demo") / DERIVATIVE_SHEET).read_bytes() == image_a
    assert _derivative_versions(api) == before
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
        assert TOS_SK not in text
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


async def test_agent_selects_a_library_image_for_a_character_derivative(
    api, sessions, derivative, public_dns, agent_services
):
    """Agent 也能为角色衍生图选择已有素材，与 Web 共用同一导入路径与关联。"""
    account_id = await _configure(sessions, object_storage=False)
    control = ControlPlane(asset_status=[_active_image()])
    with capture_http() as router:
        router.post(VOLC_HOST).mock(side_effect=control)
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=solid_png_bytes((10, 10, 200), size=(400, 400)))
        )
        selected, selected_json = await _embedded(
            agent_services,
            "select_provider_library_asset",
            {
                "resource_type": "character_derivative",
                "resource_id": "Alice/战斗装",
                "account_id": account_id,
                "asset_id": "asset-20261103-b",
            },
        )

    assert selected.isError is False
    chosen = selected_json["provider_asset_selection"]
    assert chosen["content_version"]["version"] == 1
    assert (chosen["link"]["state"], chosen["link"]["usable"]) == ("active", True)
    assert _derivative_versions(api)["versions"][-1]["source"] == "manual_upload"
    assert [link["id"] for link in _derivative_panel(api)["links"]] == [chosen["link"]["id"]]
    assert "TEST-SIGNATURE" not in _tool_text(selected)


#: 前端按码给出本地化原因的码表，前端测试核对同一份文件。
_LOCAL_FAILURES_CONTRACT = (
    Path(__file__).resolve().parents[4]
    / "frontend"
    / "src"
    / "test"
    / "contracts"
    / "provider-asset-local-failures.json"
)


def test_local_failure_codes_match_the_frontend_contract():
    assert sorted(ingest.LOCAL_FAILURE_CODES) == json.loads(_LOCAL_FAILURES_CONTRACT.read_text(encoding="utf-8"))
