"""全局资产库与远端主存储：库里的图片按全局设置保存到对象存储，保存与应用时带上供应商素材关联；
删除来源后库与已应用的项目照常从对象存储取得实际内容。

真实编排：资产图经文件路由上传，经资产库路由保存到库与应用到项目，预览经公开文件路由读取，
工作文件经对象存储路由的清理入口清理，项目删除与彻底删除经项目路由。
只替换对象存储的网络边界：TOS SDK 在 ``create_sdk_client`` 换成内存桶；供应商素材库的关联直接按导入的形状落库，
不发出供应商 HTTP 请求。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tos.exceptions import TosServerError

from lib.artifacts.version_manager import VersionManager
from lib.asset_library import links
from lib.asset_library.bindings import account_scope
from lib.asset_library.control_plane import ProviderAssetDetail
from lib.asset_library.generation_inputs import ReferenceImageSource, choose_in_scope
from lib.config.repository import SystemSettingRepository
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.models.asset import Asset
from lib.db.models.asset_library import AssetLibraryAccount, ProviderAssetLink
from lib.db.models.object_storage import FormalObject, FormalObjectRef
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.material.formal_objects import (
    MATERIAL_STORAGE_TARGET_SETTING,
    recover_formal_commits,
    retain_formal_objects,
    sweep_formal_objects,
)
from lib.material.global_library import library_holder
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import assets, files, object_storage
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import (
    bind_session_factory,
    use_assets_router_sessions,
    use_object_storage_sdk,
    use_project_manager,
)
from tests.integration.server.derivative_sheet_support import solid_png_bytes
from tests.integration.server.routers.projects_router_support import build_projects_client

BUCKET = "library-bucket"
CONNECTION = {
    "region": "cn-beijing",
    "endpoint": "tos-cn-beijing.volces.com",
    "bucket": BUCKET,
    "access_key_id": "TEST-TOS-AK-3570",
    "secret_access_key": "TEST-TOS-SECRET-3570",
}


class MemoryBucket:
    """TOS SDK 客户端替身：对象存在内存里；``fail`` 里列出的操作抛服务端错误。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def _check(self, op: str, key: str) -> None:
        self.calls.append((op, key))
        if op in self.fail:
            resp = SimpleNamespace(request_id="req-1", headers={}, status=503)
            raise TosServerError(resp, "failed", "ServiceUnavailable", "host", f"/{BUCKET}")

    def _missing(self, key: str) -> TosServerError:
        resp = SimpleNamespace(request_id="req-1", headers={}, status=404)
        return TosServerError(resp, "failed", "NoSuchKey", "host", f"/{BUCKET}/{key}")

    def put_object(self, *, bucket: str, key: str, content: bytes, content_type: str | None = None):
        self._check("put", key)
        self.objects[(bucket, key)] = content
        return SimpleNamespace(etag="etag")

    def head_object(self, *, bucket: str, key: str):
        self._check("head", key)
        if (bucket, key) not in self.objects:
            raise self._missing(key)
        return SimpleNamespace(content_length=len(self.objects[(bucket, key)]), etag="etag", content_type=None)

    def get_object(self, *, bucket: str, key: str):
        self._check("get", key)
        if (bucket, key) not in self.objects:
            raise self._missing(key)
        data = self.objects[(bucket, key)]
        return SimpleNamespace(read=lambda: data)

    def delete_object(self, *, bucket: str, key: str):
        self._check("delete", key)
        self.objects.pop((bucket, key), None)

    def stored_keys(self) -> list[str]:
        return sorted(key for _bucket, key in self.objects)


@pytest.fixture
def library_bucket(monkeypatch) -> MemoryBucket:
    memory = MemoryBucket()
    use_object_storage_sdk(monkeypatch, lambda config: memory)
    return memory


@pytest.fixture
def library_sessions(db_engine: AsyncEngine, monkeypatch) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    bind_session_factory(monkeypatch, factory)
    return factory


@pytest.fixture
def library_projects(tmp_path: Path, monkeypatch) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    for name in ("demo", "other"):
        pm.create_project(name)
        pm.create_project_metadata(name, name, "Anime", "narration")
        pm.add_character(name, "Alice", "银发少女")
    use_project_manager(monkeypatch, pm)
    return pm


@pytest.fixture
def library_api(library_sessions, library_projects, monkeypatch) -> Generator[TestClient]:
    use_assets_router_sessions(monkeypatch, library_sessions)
    app = FastAPI()

    async def _session():
        async with library_sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    for router in (files.router, object_storage.router, assets.router):
        app.include_router(router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(files.public_router, prefix="/api/v1")
    register_error_handlers(app)
    with TestClient(app) as client:
        yield client


async def _remote(library_sessions) -> None:
    async with library_sessions() as session:
        await ObjectStorageConnectionRepository(session).save(kind="tos", **CONNECTION)
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, "object_storage")
        await session.commit()


def _upload(library_api: TestClient, content: bytes):
    return library_api.post(
        "/api/v1/projects/demo/upload/character?name=Alice",
        files={"file": ("alice.png", content, "image/png")},
    )


def _history(library_projects: ProjectManager, project_name: str) -> dict[str, Any]:
    path = library_projects.get_project_path(project_name) / "versions" / "versions.json"
    return json.loads(path.read_text(encoding="utf-8"))["characters"]["Alice"]


async def _objects(library_sessions) -> list[FormalObject]:
    async with library_sessions() as session:
        return list((await session.execute(select(FormalObject).order_by(FormalObject.id))).scalars())


async def _refs(library_sessions) -> list[FormalObjectRef]:
    async with library_sessions() as session:
        return list((await session.execute(select(FormalObjectRef).order_by(FormalObjectRef.id))).scalars())


def _cleanup(library_api: TestClient) -> dict[str, int]:
    resp = library_api.post("/api/v1/object-storage/work-cache/cleanup", json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _preview_url(image_path: str) -> str:
    return f"/api/v1/{image_path.replace('global_assets', 'global-assets', 1)}"


async def _account(library_sessions, access_key_id: str) -> AssetLibraryAccount:
    async with library_sessions() as session:
        account = await AssetLibraryRepository(session).create_account(
            site="volcengine",
            name=access_key_id,
            region="cn-beijing",
            project_name="film",
            access_key_id=access_key_id,
            secret_access_key="TEST-CONTROL-PLANE-SECRET-0070",
        )
        await session.commit()
        return account


async def _link_current_version(
    library_sessions, library_projects: ProjectManager, account: AssetLibraryAccount
) -> ProviderAssetLink:
    """给 demo 里 Alice 当前选中的内容版本建立一条可用关联（与从素材库导入建立的关联同形）。"""
    manager = VersionManager(library_projects.get_project_path("demo"))
    content_version = manager.ensure_content_version(
        "characters", "Alice", manager.get_current_version("characters", "Alice")
    )
    asset = ProviderAssetDetail(
        id="asset-20261103-alice",
        name="alice",
        asset_type="Image",
        status="active",
        group_id="group-1",
        error_code=None,
        error_message=None,
    )
    async with library_sessions() as session:
        link = await links.record_imported_link(
            session, project_name="demo", content_version=content_version, account=account, asset=asset
        )
        await session.commit()
        return link


async def _links_of(library_sessions, project_name: str) -> list[ProviderAssetLink]:
    async with library_sessions() as session:
        stmt = select(ProviderAssetLink).where(ProviderAssetLink.project_name == project_name)
        return list((await session.execute(stmt.order_by(ProviderAssetLink.id))).scalars())


async def _library_asset(library_sessions) -> Asset:
    async with library_sessions() as session:
        return (await session.execute(select(Asset))).scalars().one()


async def _choose(library_sessions, library_projects: ProjectManager, project_name: str, scope, image: bytes):
    """目标项目生成时为 Alice 这张参考图选择：按实发字节对应内容版本，按调用范围选用关联。"""
    source = ReferenceImageSource(
        locator="characters/Alice.png", sha256=hashlib.sha256(image).hexdigest(), name="Alice"
    )
    [choice] = await choose_in_scope(
        library_sessions,
        scope=scope,
        project_name=project_name,
        project_path=library_projects.get_project_path(project_name),
        sources=[source],
    )
    return choice


def _save_to_library(library_api: TestClient):
    return library_api.post(
        "/api/v1/assets/from-project",
        json={"project_name": "demo", "resource_type": "character", "resource_id": "Alice"},
    )


def _apply(library_api: TestClient, asset_id: str):
    return library_api.post(
        "/api/v1/assets/apply-to-project",
        json={"asset_ids": [asset_id], "target_project": "other", "conflict_policy": "overwrite"},
    )


async def test_remote_image_through_global_library_survives_source_deletion(
    library_api, library_sessions, library_projects, library_bucket, monkeypatch
):
    """远端图片存进全局库、应用到另一个项目，删除来源后库与目标仍从对象存储取得实际内容；关联随内容复制、各自独立。"""
    await _remote(library_sessions)
    image = solid_png_bytes((30, 140, 220), size=(400, 400))
    digest = hashlib.sha256(image).hexdigest()
    key = f"arcreel-materials/sha256/{digest[:2]}/{digest}.png"
    assert _upload(library_api, image).status_code == 200
    account_a = await _account(library_sessions, "TEST-CONTROL-PLANE-AK-A")
    source_link = await _link_current_version(library_sessions, library_projects, account_a)
    # 来源的工作文件已被清理：保存时从远端取回
    assert _cleanup(library_api)["removed"] == 2

    saved = _save_to_library(library_api)
    assert saved.status_code == 200, saved.text
    entry = await _library_asset(library_sessions)
    holder = library_holder(entry.id)
    assert entry.image_digest == digest
    assert entry.image_storage is not None
    assert entry.image_storage["key"] == key
    assert library_bucket.stored_keys() == [key]  # 同字节共用一个正式对象，没有重复上传
    assert library_bucket.calls.count(("put", key)) == 1
    [library_link] = await _links_of(library_sessions, holder)
    assert library_link.content_version_id == entry.image_content_id
    assert (library_link.provider_asset_id, library_link.state, library_link.selected) == (
        source_link.provider_asset_id,
        "active",
        True,
    )
    assert links.scope_of(library_link) == links.scope_of(source_link)

    # 库里的图同样只是工作文件（连同保存时取回的来源正式文件）：清理后预览从远端取回
    assert _cleanup(library_api)["removed"] == 2
    library_file = library_projects.data_root / entry.image_path
    assert not library_file.exists()
    preview = library_api.get(_preview_url(entry.image_path))
    assert preview.status_code == 200
    assert preview.content == image

    assert _cleanup(library_api)["removed"] == 1
    applied = _apply(library_api, entry.id)
    assert applied.status_code == 200, applied.text
    assert applied.json()["succeeded"] == [{"id": entry.id, "name": "Alice"}]
    [target_record] = _history(library_projects, "other")["versions"]
    assert target_record["storage"]["key"] == key  # 遵循全局设置写远端，与库共用同一个对象
    assert target_record["content_digest"] == digest
    [target_link] = await _links_of(library_sessions, "other")
    assert target_link.content_version_id == target_record["content_version_id"]
    assert target_link.provider_asset_id == source_link.provider_asset_id

    # 解除任一副本只解除它自己
    async with library_sessions() as session:
        await links.release_link(session, await session.get(ProviderAssetLink, source_link.id))
        await session.commit()
    assert [link.selected for link in await _links_of(library_sessions, holder)] == [True]
    assert [link.selected for link in await _links_of(library_sessions, "other")] == [True]

    # 目标生成按实际调用范围选用：原账号范围用素材引用，另一个云账号的范围没有适用关联
    choice = await _choose(library_sessions, library_projects, "other", account_scope(account_a), image)
    assert choice is not None
    assert choice.asset_uri == f"asset://{source_link.provider_asset_id}"
    account_b = await _account(library_sessions, "TEST-CONTROL-PLANE-AK-B")
    assert await _choose(library_sessions, library_projects, "other", account_scope(account_b), image) is None

    # 删除来源项目并彻底清除，来源不再持有任何引用；启动结算与维护循环都不删除库与目标仍在用的对象
    with build_projects_client(monkeypatch, library_projects, session_factory=library_sessions) as projects_api:
        assert projects_api.delete("/api/v1/projects/demo").status_code == 200
        [deleted] = projects_api.get("/api/v1/deleted-projects").json()["projects"]
        assert projects_api.delete(f"/api/v1/deleted-projects/{quote(deleted['id'], safe='')}").status_code == 200
    await recover_formal_commits(library_sessions, project_path_of=library_projects.get_project_path)
    await retain_formal_objects(
        library_projects.layout.projects_dir,
        library_projects.layout.deleted_projects_dir,
        library_sessions,
        now=utc_now() + timedelta(days=30),
    )
    await sweep_formal_objects(library_sessions, now=utc_now() + timedelta(days=1))
    assert library_bucket.stored_keys() == [key]
    holders = {(ref.project_name, ref.state) for ref in await _refs(library_sessions)}
    assert holders == {(holder, "active"), ("other", "active")}

    _cleanup(library_api)
    target = library_api.get("/api/v1/files/other/characters/Alice.png")
    assert target.status_code == 200
    assert target.content == image
    assert library_api.get(_preview_url(entry.image_path)).content == image
    assert [link.selected for link in await _links_of(library_sessions, "other")] == [True]

    # 删除库条目只丢弃这条条目的关联与引用；目标仍引用对象，统一回收不删除它
    assert library_api.delete(f"/api/v1/assets/{entry.id}").status_code == 204
    assert await _links_of(library_sessions, holder) == []
    assert [link.selected for link in await _links_of(library_sessions, "other")] == [True]
    assert {(ref.project_name, ref.state) for ref in await _refs(library_sessions)} == {("other", "active")}
    assert library_bucket.stored_keys() == [key]
    assert not [op for op, _key in library_bucket.calls if op == "delete"]


async def test_startup_settlement_keeps_the_library_reference(
    library_api, library_sessions, library_projects, library_bucket
):
    """启动结算遇到全局资产库名下中断的占用时，不把它当作缺失的项目而撤下库已登记的引用。"""
    await _remote(library_sessions)
    assert _upload(library_api, solid_png_bytes((120, 60, 30), size=(64, 64))).status_code == 200
    assert _save_to_library(library_api).status_code == 200
    holder = library_holder((await _library_asset(library_sessions)).id)
    [row] = await _objects(library_sessions)
    async with library_sessions() as session:
        session.add(
            FormalObjectRef(object_id=row.id, project_name=holder, attempt_id="crashed-attempt", state="pending")
        )
        await session.commit()

    assert await recover_formal_commits(library_sessions, project_path_of=library_projects.get_project_path) == 1
    # 维护循环逐个项目重算引用、撤下过期墓碑名的引用；条目键既不是项目名也不是墓碑名，不受影响
    await retain_formal_objects(
        library_projects.layout.projects_dir,
        library_projects.layout.deleted_projects_dir,
        library_sessions,
        now=utc_now() + timedelta(days=30),
    )
    await sweep_formal_objects(library_sessions, now=utc_now() + timedelta(days=1))
    refs = {(ref.project_name, ref.state) for ref in await _refs(library_sessions)}
    assert refs == {(holder, "active"), ("demo", "active")}
    [kept] = await _objects(library_sessions)
    assert kept.state == "stored"


async def test_local_library_image_applied_under_remote_storage_fails_without_partial_target(
    library_api, library_sessions, library_projects, library_bucket
):
    """存量本地库图片应用到远端存储下的项目：上传失败时目标不变，成功后目标版本写远端；库条目照旧是本地文件。"""
    assert _upload(library_api, solid_png_bytes((10, 220, 90), size=(64, 64))).status_code == 200
    assert _save_to_library(library_api).status_code == 200
    entry = await _library_asset(library_sessions)
    assert entry.image_storage is None
    await _remote(library_sessions)
    other_dir = library_projects.get_project_path("other")
    before = (other_dir / "project.json").read_bytes()

    library_bucket.fail.add("put")
    failed = _apply(library_api, entry.id)
    assert failed.status_code == 502
    assert (other_dir / "project.json").read_bytes() == before
    assert not (other_dir / "versions" / "versions.json").exists()
    assert not (other_dir / "characters" / "Alice.png").exists()

    library_bucket.fail.clear()
    assert _apply(library_api, entry.id).status_code == 200
    [record] = _history(library_projects, "other")["versions"]
    assert record["storage"]["key"] in library_bucket.stored_keys()
    assert (library_projects.data_root / entry.image_path).is_file()


async def test_concurrent_replacements_of_a_library_image_each_retire_the_image_they_displace(
    library_api, library_sessions, library_bucket, monkeypatch
):
    """同一库条目的两次换图交错：后一次在锁内读到前一次提交的图片并撤下它，条目只持有当前图片的引用。"""
    await _remote(library_sessions)
    first, second, third = (
        solid_png_bytes(rgb, size=(64, 64)) for rgb in ((10, 10, 10), (90, 90, 90), (170, 170, 170))
    )
    uploading, release = threading.Event(), threading.Event()
    put = library_bucket.put_object

    def paused_put(*, bucket: str, key: str, content: bytes, content_type: str | None = None):
        if content == second:
            uploading.set()
            release.wait(10)
        return put(bucket=bucket, key=key, content=content, content_type=content_type)

    monkeypatch.setattr(library_bucket, "put_object", paused_put)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=library_api.app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/assets",
            data={"type": "character", "name": "Alice"},
            files={"image": ("first.png", first, "image/png")},
        )
        assert created.status_code == 200, created.text
        asset_id = created.json()["asset"]["id"]

        def _replace(content: bytes):
            return client.post(f"/api/v1/assets/{asset_id}/image", files={"image": ("next.png", content, "image/png")})

        replacing_second = asyncio.create_task(_replace(second))
        assert await asyncio.to_thread(uploading.wait, 10)
        trying_lock = asyncio.Event()
        shared_lock = assets.library_entry_lock

        @asynccontextmanager
        async def observed_lock(entry_id: str):
            trying_lock.set()
            async with shared_lock(entry_id):
                yield

        monkeypatch.setattr(assets, "library_entry_lock", observed_lock)
        replacing_third = asyncio.create_task(_replace(third))
        await asyncio.wait_for(trying_lock.wait(), 10)
        release.set()
        assert (await asyncio.wait_for(replacing_second, 10)).status_code == 200
        assert (await asyncio.wait_for(replacing_third, 10)).status_code == 200

    entry = await _library_asset(library_sessions)
    assert entry.image_digest == hashlib.sha256(third).hexdigest()
    held = {(ref.project_name, ref.content_version_id, ref.state) for ref in await _refs(library_sessions)}
    assert held == {(library_holder(asset_id), entry.image_content_id, "active")}


async def test_skipped_library_entry_does_not_fetch_its_evicted_remote_image(
    library_api, library_sessions, library_projects, library_bucket
):
    """应用到已有同名资产的项目、冲突策略为跳过：不会应用的条目不取回远端图片，对象存储读不了也照常返回跳过。"""
    await _remote(library_sessions)
    assert _upload(library_api, solid_png_bytes((150, 90, 20), size=(64, 64))).status_code == 200
    assert _save_to_library(library_api).status_code == 200
    entry = await _library_asset(library_sessions)
    _cleanup(library_api)
    assert not (library_projects.data_root / entry.image_path).exists()

    library_bucket.fail.add("get")
    applied = library_api.post(
        "/api/v1/assets/apply-to-project",
        json={"asset_ids": [entry.id], "target_project": "other", "conflict_policy": "skip"},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["skipped"] == [{"id": entry.id, "name": "Alice"}]
    assert not [call for call in library_bucket.calls if call[0] == "get"]
