"""远端主存储：资产图成品经正式提交保存到对象存储，清理本地工作文件后预览与缩略图从远端取回；版本切换、
回收站与项目删除按引用保留正式对象。

真实编排：资产图经文件路由上传（正式提交入口按全局设置选择保存位置），预览与缩略图经公开文件路由读取，
工作文件经对象存储路由的清理入口清理，删除与恢复经文件视图、资产、项目回收站与项目路由。只替换对象存储的
网络边界：TOS SDK 在 ``create_sdk_client`` 换成内存桶。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tos.exceptions import TosServerError

from lib.config.repository import SystemSettingRepository
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import FormalObject, FormalObjectRef
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.material.formal_objects import (
    MATERIAL_STORAGE_TARGET_SETTING,
    reconcile_and_collect,
    recover_formal_commits,
    retain_formal_objects,
    sweep_formal_objects,
)
from lib.material.work_files import ensure_content_version_file, hold_work_file
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import characters, file_view, files, object_storage, project_trash, versions
from server.services.project.provider_asset_links import load_link_panel
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import bind_session_factory, use_object_storage_sdk, use_project_manager, use_upload_claim_registration
from tests.integration.server.derivative_sheet_support import solid_png_bytes
from tests.integration.server.routers.projects_router_support import build_projects_client

TOS_SK = "TEST-TOS-SECRET-0066"
BUCKET = "formal-bucket"
CONNECTION = {
    "region": "cn-beijing",
    "endpoint": "tos-cn-beijing.volces.com",
    "bucket": BUCKET,
    "access_key_id": "TEST-TOS-AK-0066",
    "secret_access_key": TOS_SK,
}


def _server_error(status: int, code: str) -> TosServerError:
    resp = SimpleNamespace(request_id="req-1", headers={}, status=status)
    return TosServerError(resp, "failed", code, "host", f"/{BUCKET}")


class MemoryBucket:
    """TOS SDK 客户端替身：对象存在内存里；``fail`` 里列出的操作抛服务端错误。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def _check(self, op: str, key: str) -> None:
        self.calls.append((op, key))
        if op in self.fail:
            raise _server_error(503, "ServiceUnavailable")

    def put_object(self, *, bucket: str, key: str, content: bytes, content_type: str | None = None):
        self._check("put", key)
        self.objects[(bucket, key)] = content
        return SimpleNamespace(etag="etag")

    def head_object(self, *, bucket: str, key: str):
        self._check("head", key)
        if (bucket, key) not in self.objects:
            raise _server_error(404, "NoSuchKey")
        return SimpleNamespace(content_length=len(self.objects[(bucket, key)]), etag="etag", content_type=None)

    def get_object(self, *, bucket: str, key: str):
        self._check("get", key)
        if (bucket, key) not in self.objects:
            raise _server_error(404, "NoSuchKey")
        data = self.objects[(bucket, key)]
        return SimpleNamespace(read=lambda: data)

    def delete_object(self, *, bucket: str, key: str):
        self._check("delete", key)
        self.objects.pop((bucket, key), None)

    def stored_keys(self, prefix: str = "") -> list[str]:
        return sorted(key for _bucket, key in self.objects if key.startswith(prefix))


@pytest.fixture
def bucket(monkeypatch) -> MemoryBucket:
    memory = MemoryBucket()
    use_object_storage_sdk(monkeypatch, lambda config: memory)
    return memory


@pytest.fixture
def sessions(db_engine: AsyncEngine, monkeypatch) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    bind_session_factory(monkeypatch, factory)
    return factory


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    for name in ("demo", "other"):
        pm.create_project(name)
        pm.create_project_metadata(name, name, "Anime", "narration")
        pm.add_character(name, "Alice", "银发少女")
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
        versions.router,
        object_storage.router,
        file_view.router,
        project_trash.router,
        characters.router,
    ):
        app.include_router(router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(files.public_router, prefix="/api/v1")
    register_error_handlers(app)
    with TestClient(app) as client:
        yield client


async def _target(sessions, target: str) -> None:
    async with sessions() as session:
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, target)
        await session.commit()


async def _remote(sessions) -> None:
    async with sessions() as session:
        await ObjectStorageConnectionRepository(session).save(kind="tos", **CONNECTION)
        await session.commit()
    await _target(sessions, "object_storage")


def _upload(api: TestClient, content: bytes, project_name: str = "demo"):
    return api.post(
        f"/api/v1/projects/{project_name}/upload/character?name=Alice",
        files={"file": ("alice.png", content, "image/png")},
    )


def _history(project: ProjectManager, project_name: str = "demo") -> dict[str, Any]:
    path = project.get_project_path(project_name) / "versions" / "versions.json"
    return json.loads(path.read_text(encoding="utf-8"))["characters"]["Alice"]


def _durable_state(project: ProjectManager) -> tuple:
    project_dir = project.get_project_path("demo")
    return (
        (project_dir / "characters" / "Alice.png").read_bytes(),
        (project_dir / "project.json").read_bytes(),
        (project_dir / ".arcreel_artifacts.json").read_bytes(),
        (project_dir / "versions" / "versions.json").read_bytes(),
    )


async def _objects(sessions) -> list[FormalObject]:
    async with sessions() as session:
        return list((await session.execute(select(FormalObject).order_by(FormalObject.id))).scalars())


async def _refs(sessions) -> list[FormalObjectRef]:
    async with sessions() as session:
        return list((await session.execute(select(FormalObjectRef).order_by(FormalObjectRef.id))).scalars())


def _cleanup(api: TestClient) -> dict[str, int]:
    resp = api.post("/api/v1/object-storage/work-cache/cleanup", json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_remote_upload_survives_cache_cleanup_and_coexists_with_local_versions(api, sessions, project, bucket):
    """本地版本 A → 启用远端后上传 B → 清理工作文件 → 预览、缩略图、历史快照与版本切换从远端取回，A 仍是本地版本。"""
    image_a = solid_png_bytes((200, 10, 10), size=(400, 400))
    image_b = solid_png_bytes((10, 200, 10), size=(400, 400))
    assert _upload(api, image_a).status_code == 200
    await _remote(sessions)
    assert _upload(api, image_b).status_code == 200

    local_record, remote_record = _history(project)["versions"]
    assert "storage" not in local_record
    digest_b = hashlib.sha256(image_b).hexdigest()
    key_b = f"arcreel-materials/sha256/{digest_b[:2]}/{digest_b}.png"
    async with sessions() as session:
        connection = await ObjectStorageConnectionRepository(session).get()
    assert connection is not None
    assert remote_record["storage"] == {
        "kind": "object_storage",
        "connection_id": connection.id,
        "bucket": BUCKET,
        "key": key_b,
    }
    assert remote_record["content_digest"] == digest_b
    assert bucket.stored_keys() == [key_b]
    assert bucket.objects[(BUCKET, key_b)] == image_b
    [stored] = await _objects(sessions)
    assert stored.state == "stored"
    [ref] = await _refs(sessions)
    assert (ref.state, ref.project_name, ref.content_version_id) == (
        "active",
        "demo",
        remote_record["content_version_id"],
    )

    project_dir = project.get_project_path("demo")
    formal = project_dir / "characters" / "Alice.png"
    snapshot = project_dir / remote_record["file"]
    with hold_work_file(formal):
        held = _cleanup(api)
    assert (held["removed"], held["kept_in_use"]) == (1, 1)
    assert formal.is_file()
    assert not snapshot.exists()

    assert _cleanup(api)["removed"] == 1
    assert not formal.exists()
    assert (project_dir / local_record["file"]).is_file()  # 本地版本的持久快照不受清理影响
    assert bucket.stored_keys() == [key_b]  # 清理不删除正式对象

    preview = api.get("/api/v1/files/demo/characters/Alice.png")
    assert preview.status_code == 200
    assert preview.content == image_b
    thumbnail = api.get("/api/v1/files/demo/characters/Alice.png", params={"w": "160"})
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/webp"
    history = api.get(f"/api/v1/files/demo/{remote_record['file']}")
    assert history.status_code == 200
    assert history.content == image_b
    assert bucket.calls.count(("get", key_b)) == 2  # 正式文件与历史快照各取回一次，缩略图沿用取回的文件

    # 切回本地版本 A 不需要对象存储；之后新上传按全局设置继续保存到远端
    restored = api.post("/api/v1/projects/demo/versions/characters/Alice/restore/1")
    assert restored.status_code == 200, restored.text
    assert formal.read_bytes() == image_a

    # 清理后切回远端版本 B：还原前从对象存储取回快照
    assert _cleanup(api)["removed"] == 1
    restored = api.post("/api/v1/projects/demo/versions/characters/Alice/restore/2")
    assert restored.status_code == 200, restored.text
    assert formal.read_bytes() == image_b


async def test_concurrent_reads_download_once(api, sessions, project, bucket):
    await _remote(sessions)
    image = solid_png_bytes((20, 20, 200), size=(64, 64))
    assert _upload(api, image).status_code == 200
    assert _cleanup(api)["removed"] == 2
    record = _history(project)["versions"][0]
    project_dir = project.get_project_path("demo")

    from lib.artifacts.version_manager import VersionManager

    content_version = VersionManager(project_dir).find_content_version("characters", record["content_version_id"])
    assert content_version is not None
    paths = await asyncio.gather(*(ensure_content_version_file(project_dir, content_version) for _ in range(4)))
    assert {path.read_bytes() for path in paths} == {image}
    assert [op for op, _key in bucket.calls].count("get") == 1


async def test_fetch_failure_and_tampered_object_give_explicit_errors(api, sessions, project, bucket):
    await _remote(sessions)
    image = solid_png_bytes((90, 90, 90), size=(64, 64))
    assert _upload(api, image).status_code == 200
    _cleanup(api)
    formal = project.get_project_path("demo") / "characters" / "Alice.png"

    bucket.fail.add("get")
    failed = api.get("/api/v1/files/demo/characters/Alice.png")
    assert failed.status_code == 502
    assert not formal.exists()

    bucket.fail.clear()
    [(bucket_name, key)] = list(bucket.objects)
    bucket.objects[(bucket_name, key)] = solid_png_bytes((1, 2, 3), size=(64, 64))
    tampered = api.get("/api/v1/files/demo/characters/Alice.png")
    assert tampered.status_code == 502
    assert not formal.exists()
    assert not list(formal.parent.glob(".*fetch"))  # 校验失败不留下临时文件


@pytest.mark.parametrize("target", ["local", "object_storage"])
async def test_storage_contract_failed_commit_keeps_previous_version(
    api, sessions, project, bucket, monkeypatch, target
):
    """本地与远端共用的契约：内容可按摘要取回；正式提交失败不发布新版本，旧版本内容不变。"""
    if target == "object_storage":
        await _remote(sessions)
    image_a = solid_png_bytes((200, 100, 0), size=(64, 64))
    assert _upload(api, image_a).status_code == 200
    before = _durable_state(project)

    def _fail(*_args, **_kwargs):
        raise RuntimeError("injected manifest failure")

    use_upload_claim_registration(monkeypatch, _fail)
    assert _upload(api, solid_png_bytes((0, 100, 200), size=(64, 64))).status_code == 500
    assert _durable_state(project) == before

    _cleanup(api)
    project_dir = project.get_project_path("demo")
    from lib.artifacts.version_manager import VersionManager

    [record] = _history(project)["versions"]
    content_version = VersionManager(project_dir).find_content_version("characters", record["content_version_id"])
    assert content_version is not None
    assert (await ensure_content_version_file(project_dir, content_version)).read_bytes() == image_a
    assert (content_version.stored is not None) == (target == "object_storage")


async def test_upload_failure_publishes_nothing(api, sessions, project, bucket):
    await _remote(sessions)
    assert _upload(api, solid_png_bytes((5, 5, 5), size=(64, 64))).status_code == 200
    before = _durable_state(project)

    bucket.fail.add("put")
    failed = _upload(api, solid_png_bytes((250, 5, 5), size=(64, 64)))
    assert failed.status_code == 502
    assert _durable_state(project) == before
    first, second = await _objects(sessions)
    assert first.state == "stored"
    assert second.state == "deleted"  # 没写进去的对象按新建对象补偿，删除时对象不在也算完成
    assert [ref.state for ref in await _refs(sessions)] == ["active"]


async def test_commit_failure_deletes_only_new_unreferenced_objects_and_retries(
    api, sessions, project, bucket, monkeypatch
):
    """正式提交失败：本次新建的对象删除失败时保留原因并重试；复用的共享对象不删。"""
    await _remote(sessions)
    shared = solid_png_bytes((40, 80, 120), size=(64, 64))
    assert _upload(api, shared, project_name="other").status_code == 200
    assert _upload(api, solid_png_bytes((1, 1, 1), size=(64, 64))).status_code == 200

    def _fail(*_args, **_kwargs):
        raise RuntimeError("injected manifest failure")

    use_upload_claim_registration(monkeypatch, _fail)
    # 与另一个项目同字节：复用共享对象，失败补偿不删除它
    assert _upload(api, shared).status_code == 500
    shared_key = next(
        row.object_key for row in await _objects(sessions) if row.content_digest == hashlib.sha256(shared).hexdigest()
    )
    assert shared_key in bucket.stored_keys()

    # 新内容：上传成功后提交失败，删除也失败时保留定位与原因
    bucket.fail.add("delete")
    fresh = solid_png_bytes((210, 30, 90), size=(64, 64))
    assert _upload(api, fresh).status_code == 500
    fresh_row = next(row for row in await _objects(sessions) if row.content_digest == hashlib.sha256(fresh).hexdigest())
    assert fresh_row.state == "orphaned"
    assert fresh_row.last_error == "provider:ServiceUnavailable"
    assert fresh_row.object_key in bucket.stored_keys()

    bucket.fail.clear()
    assert await sweep_formal_objects(sessions, now=utc_now() + timedelta(days=1)) == 1
    assert fresh_row.object_key not in bucket.stored_keys()
    assert shared_key in bucket.stored_keys()
    states = {row.object_key: row.state for row in await _objects(sessions)}
    assert states[fresh_row.object_key] == "deleted"
    assert states[shared_key] == "stored"
    assert all(ref.state == "active" for ref in await _refs(sessions))


async def test_interrupted_commit_is_settled_on_startup(sessions, project, bucket):
    """进程在上传后、写版本记录前退出：下次启动按版本历史结算，删除没有引用的新对象。"""
    await _remote(sessions)
    key = "arcreel-materials/sha256/ab/abcdef.png"
    async with sessions() as session:
        connection = await ObjectStorageConnectionRepository(session).get()
        assert connection is not None
        row = FormalObject(
            connection_id=connection.id,
            endpoint=connection.endpoint,
            bucket=BUCKET,
            object_key=key,
            content_digest="ab" * 32,
            size=3,
            state="stored",
            created_attempt="crashed-attempt",
            delete_attempts=0,
        )
        session.add(row)
        await session.flush()
        session.add(
            FormalObjectRef(object_id=row.id, project_name="demo", attempt_id="crashed-attempt", state="pending")
        )
        await session.commit()
    bucket.objects[(BUCKET, key)] = b"abc"

    assert await recover_formal_commits(sessions, project_path_of=project.get_project_path) == 1
    assert bucket.stored_keys() == []
    [settled] = await _objects(sessions)
    assert settled.state == "deleted"
    assert await _refs(sessions) == []


async def test_connection_cannot_be_relocated_while_formal_objects_exist(api, sessions, project, bucket):
    await _remote(sessions)
    assert _upload(api, solid_png_bytes((7, 7, 7), size=(64, 64))).status_code == 200

    moved = api.put("/api/v1/object-storage/connection", json={**CONNECTION, "bucket": "another-bucket"})
    assert moved.status_code == 409
    assert "1" in moved.json()["detail"]
    assert api.delete("/api/v1/object-storage/connection").status_code == 409
    rotated = api.put(
        "/api/v1/object-storage/connection",
        json={**CONNECTION, "access_key_id": "TEST-TOS-AK-0067", "secret_access_key": "TEST-TOS-SECRET-0067"},
    )
    assert rotated.status_code == 200, rotated.text

    _cleanup(api)
    preview = api.get("/api/v1/files/demo/characters/Alice.png")
    assert preview.status_code == 200  # 换密钥后旧对象仍按原桶读取


async def test_old_locator_is_not_reinterpreted_against_another_bucket(api, sessions, project, bucket):
    """连接被绕过保护换成另一个桶时，旧定位明确不可读，不会去新桶里按同名 key 取图。"""
    await _remote(sessions)
    assert _upload(api, solid_png_bytes((9, 9, 9), size=(64, 64))).status_code == 200
    _cleanup(api)
    async with sessions() as session:
        connection = await ObjectStorageConnectionRepository(session).get()
        assert connection is not None
        connection.bucket = "another-bucket"
        await session.commit()

    resp = api.get("/api/v1/files/demo/characters/Alice.png")
    assert resp.status_code == 409
    assert not any(op == "get" for op, _key in bucket.calls)


def _link(project_name: str, record: dict[str, Any], provider_asset_id: str) -> ProviderAssetLink:
    return ProviderAssetLink(
        project_name=project_name,
        content_version_id=record["content_version_id"],
        content_digest=record["content_digest"],
        resource_type="characters",
        resource_id="Alice",
        version=record["version"],
        locator_path=record["file"],
        origin="upload",
        account_id=1,
        account_identity="TEST-identity",
        site="volcengine",
        region="cn-beijing",
        provider_project_name="film",
        state="active",
        provider_asset_id=provider_asset_id,
        submit_name=f"Alice-{provider_asset_id}",
        selected=True,
        check_attempts=0,
    )


async def _panel_assets(sessions, project: ProjectManager) -> list[str | None]:
    async with sessions() as session:
        panel = await load_link_panel(
            session,
            project_path=project.get_project_path("demo"),
            project_name="demo",
            resource_type="characters",
            resource_id="Alice",
        )
    return [link.provider_asset_id for link in panel.links]


def _identities(project: ProjectManager) -> list[tuple[int, str, object]]:
    return [
        (item["version"], item["content_version_id"], item.get("storage")) for item in _history(project)["versions"]
    ]


async def _object_of(sessions, record: dict[str, Any]) -> FormalObject:
    return next(row for row in await _objects(sessions) if row.object_key == record["storage"]["key"])


async def test_switching_local_and_remote_versions_keeps_content_versions_and_their_links(
    api, sessions, project, bucket
):
    """本地 A 与远端 B 互相切换：不新增内容版本，各自的关联随所选版本出现；取回失败时保留原选择。"""
    image_a = solid_png_bytes((220, 40, 40), size=(64, 64))
    image_b = solid_png_bytes((40, 40, 220), size=(64, 64))
    assert _upload(api, image_a).status_code == 200
    await _remote(sessions)
    assert _upload(api, image_b).status_code == 200
    record_a, record_b = _history(project)["versions"]
    async with sessions() as session:
        session.add_all([_link("demo", record_a, "asset-a"), _link("demo", record_b, "asset-b")])
        await session.commit()
    identities = _identities(project)
    assert await _panel_assets(sessions, project) == ["asset-b"]

    _cleanup(api)
    formal = project.get_project_path("demo") / "characters" / "Alice.png"
    bucket.fail.add("get")
    assert api.post("/api/v1/projects/demo/versions/characters/Alice/restore/1").status_code == 200
    assert formal.read_bytes() == image_a
    assert await _panel_assets(sessions, project) == ["asset-a"]

    # 远端 B 取不回：先取回校验再提交选择，失败时仍选着 A
    failed = api.post("/api/v1/projects/demo/versions/characters/Alice/restore/2")
    assert failed.status_code == 502
    assert _history(project)["current_version"] == 1
    assert formal.read_bytes() == image_a

    bucket.fail.clear()
    assert api.post("/api/v1/projects/demo/versions/characters/Alice/restore/2").status_code == 200
    assert formal.read_bytes() == image_b
    assert await _panel_assets(sessions, project) == ["asset-b"]
    assert _identities(project) == identities  # 切换不产生新的内容版本，也不改写持久定位


async def test_trashed_remote_version_keeps_its_object_until_the_record_is_purged(api, sessions, project, bucket):
    """删除远端历史版本：回收站持有引用，恢复回同一内容版本与对象；彻底删除后回收对象，删除失败可重试。"""
    await _remote(sessions)
    image_b = solid_png_bytes((10, 120, 10), size=(64, 64))
    assert _upload(api, image_b).status_code == 200
    assert _upload(api, solid_png_bytes((120, 10, 10), size=(64, 64))).status_code == 200
    record_b, record_c = _history(project)["versions"]
    project_dir = project.get_project_path("demo")

    deleted = api.delete("/api/v1/projects/demo/file-view", params={"path": record_b["file"]})
    assert deleted.status_code == 200, deleted.text
    assert await reconcile_and_collect("demo", project_dir, sessions) == 0
    assert {(ref.content_version_id, ref.state) for ref in await _refs(sessions)} == {
        (record_b["content_version_id"], "trashed"),
        (record_c["content_version_id"], "active"),
    }
    assert record_b["storage"]["key"] in bucket.stored_keys()

    puts = bucket.calls.count(("put", record_b["storage"]["key"]))
    restored = api.post(f"/api/v1/projects/demo/trash/{deleted.json()['record']['id']}/restore")
    assert restored.status_code == 200, restored.text
    back = _history(project)["versions"][-1]
    assert (back["content_version_id"], back["storage"]) == (record_b["content_version_id"], record_b["storage"])
    assert bucket.calls.count(("put", record_b["storage"]["key"])) == puts  # 加回的是原对象，不重新上传

    _cleanup(api)
    history = api.get(f"/api/v1/files/demo/{back['file']}")
    assert history.status_code == 200
    assert history.content == image_b

    again = api.delete("/api/v1/projects/demo/file-view", params={"path": back["file"]})
    assert again.status_code == 200, again.text
    bucket.fail.add("delete")
    assert api.delete(f"/api/v1/projects/demo/trash/{again.json()['record']['id']}").status_code == 200
    freed = await _object_of(sessions, record_b)
    assert (freed.state, freed.last_error) == ("orphaned", "provider:ServiceUnavailable")
    assert freed.object_key in bucket.stored_keys()

    bucket.fail.clear()
    assert await sweep_formal_objects(sessions, now=utc_now() + timedelta(days=1)) == 1
    assert bucket.stored_keys() == [record_c["storage"]["key"]]
    assert (await _object_of(sessions, record_c)).state == "stored"


async def test_deleted_asset_restores_from_remote_after_cache_cleanup(api, sessions, project, bucket):
    """工作文件清理后删除资产：回收站记录保留对象引用，恢复后预览从对象存储取回。"""
    await _remote(sessions)
    image = solid_png_bytes((60, 160, 200), size=(64, 64))
    assert _upload(api, image).status_code == 200
    assert _cleanup(api)["removed"] == 2

    assert api.delete("/api/v1/projects/demo/characters/Alice").status_code == 200
    [record] = api.get("/api/v1/projects/demo/trash").json()["records"]
    assert await reconcile_and_collect("demo", project.get_project_path("demo"), sessions) == 0
    assert [ref.state for ref in await _refs(sessions)] == ["trashed"]

    restored = api.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")
    assert restored.status_code == 200, restored.text
    preview = api.get("/api/v1/files/demo/characters/Alice.png")
    assert preview.status_code == 200
    assert preview.content == image


async def test_deleted_project_keeps_objects_until_purged_and_shared_objects_survive(
    api, sessions, project, bucket, monkeypatch
):
    """项目删除后对象仍保留、可整体恢复；彻底删除只回收没有其他引用的对象，供应商素材关联不受影响。"""
    await _remote(sessions)
    shared = solid_png_bytes((90, 30, 160), size=(64, 64))
    assert _upload(api, shared, project_name="other").status_code == 200
    assert _upload(api, shared).status_code == 200
    assert _upload(api, solid_png_bytes((160, 90, 30), size=(64, 64))).status_code == 200
    record_shared, record_own = _history(project)["versions"]
    shared_row = await _object_of(sessions, record_shared)
    async with sessions() as session:
        session.add(_link("demo", record_own, "asset-own"))
        # 覆盖导入先在项目名下提交新图片、再收尾旧项目：进行中提交的占用不随旧项目改挂
        session.add(
            FormalObjectRef(object_id=shared_row.id, project_name="demo", attempt_id="importing", state="pending")
        )
        await session.commit()

    with build_projects_client(monkeypatch, project, session_factory=sessions) as projects_api:
        assert projects_api.delete("/api/v1/projects/demo").status_code == 200
        [entry] = projects_api.get("/api/v1/deleted-projects").json()["projects"]
        layout = project.layout
        assert await retain_formal_objects(layout.projects_dir, layout.deleted_projects_dir, sessions) == 0
        assert {(ref.project_name, ref.state) for ref in await _refs(sessions)} == {
            ("other", "active"),
            (entry["id"], "active"),
            ("demo", "pending"),
        }
        assert record_own["storage"]["key"] in bucket.stored_keys()

        assert projects_api.delete(f"/api/v1/deleted-projects/{quote(entry['id'], safe='')}").status_code == 200
    assert bucket.stored_keys() == [record_shared["storage"]["key"]]
    assert {ref.project_name for ref in await _refs(sessions)} == {"other", "demo"}
    async with sessions() as session:
        [link] = (await session.execute(select(ProviderAssetLink))).scalars()
    assert (link.provider_asset_id, link.project_name) == ("asset-own", entry["id"])
    assert not any(op == "delete" and key == record_shared["storage"]["key"] for op, key in bucket.calls)
