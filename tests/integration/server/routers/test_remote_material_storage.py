"""远端主存储：资产图成品经正式提交保存到对象存储，清理本地工作文件后预览与缩略图从远端取回；版本切换、
回收站与项目删除按引用保留正式对象。

真实编排：资产图经文件路由上传，分镜图、角色衍生图与宫格图经各自的上传路由（正式提交入口按全局设置选择保存位置），预览与缩略图经公开文件路由读取，
工作文件经对象存储路由的清理入口清理，删除与恢复经文件视图、资产、项目回收站与项目路由，全局资产库经资产库路由保存与应用。
只替换对象存储的网络边界：TOS SDK 在 ``create_sdk_client`` 换成内存桶。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Generator
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from starlette.types import Message, Receive, Scope, Send
from tos.exceptions import TosClientError, TosServerError

from lib.artifacts.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ArtifactStatus
from lib.artifacts.artifact_planner import plan_artifact_target_state
from lib.artifacts.version_manager import VersionManager
from lib.backends.image_backends.dashscope import DashScopeImageBackend
from lib.config.repository import SystemSettingRepository
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import FormalObject, FormalObjectRef, RelayObject
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.content_digest import sha256_file
from lib.material.formal_objects import (
    MATERIAL_STORAGE_TARGET_SETTING,
    ObjectContentStorage,
    add_reference,
    formal_object_key,
    holding_object_count,
    reconcile_and_collect,
    recover_formal_commits,
    retain_formal_objects,
    sweep_formal_objects,
)
from lib.material.storage import ObjectLocator
from lib.material.work_files import ensure_content_version_file, hold_work_file
from lib.project import project_trash as project_trash_store
from lib.project.asset_derivatives import derivative_artifact_id, derivative_sheet_relative_path
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.models import GridGeneration, ReferenceImage
from lib.script.reference_video.execution_checkpoint import ProviderMediaInput, stage_provider_media_for_task
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import (
    assets,
    characters,
    derivative_uploads,
    end_frames,
    file_view,
    files,
    grids,
    object_storage,
    project_trash,
    shot_uploads,
    versions,
)
from server.services.project import project_retirement
from server.services.project.provider_asset_links import load_link_panel
from server.services.tasks import derivative_sheet_tasks
from server.services.tasks.image_edit_tasks import execute_image_edit_task
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import (
    bind_session_factory,
    use_assets_router_sessions,
    use_generation_context,
    use_object_storage_sdk,
    use_project_manager,
    use_upload_claim_registration,
)
from tests.http_capture import capture_http, only_request, request_json
from tests.integration.server.agent_tool_support import ToolHarness, problem_of, run_declared_tool
from tests.integration.server.derivative_sheet_support import (
    GENERATION_URL,
    RESULT_IMAGE_RGB,
    RESULT_URL,
    build_generator,
    close_to,
    decode_data_url_image,
    solid_png_bytes,
    wire_derivative_generation,
)
from tests.integration.server.routers.projects_router_support import build_projects_client
from tests.integration.server.services.tasks.generation_tasks_support import fake_resolve_ctx

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
        self.fail_delete_keys: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def _check(self, op: str, key: str) -> None:
        self.calls.append((op, key))
        if op in self.fail or (op == "delete" and key in self.fail_delete_keys):
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
def api(sessions, project, monkeypatch) -> Generator[TestClient]:
    use_assets_router_sessions(monkeypatch, sessions)
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
        assets.router,
        shot_uploads.router,
        derivative_uploads.router,
        grids.router,
        end_frames.router,
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


async def test_version_restore_holds_remote_snapshot_until_commit(api, sessions, project, bucket, monkeypatch):
    await _remote(sessions)
    content = solid_png_bytes((30, 200, 30))
    assert _upload(api, content).status_code == 200
    await _target(sessions, "local")
    assert _upload(api, solid_png_bytes((200, 30, 30))).status_code == 200
    restoring = threading.Event()
    allow_restore = threading.Event()
    original_restore = VersionManager.restore_version

    def paused_restore(self, *args, **kwargs):
        restoring.set()
        assert allow_restore.wait(10), "清理没有结束"
        return original_restore(self, *args, **kwargs)

    monkeypatch.setattr(VersionManager, "restore_version", paused_restore)
    restore_task = asyncio.create_task(
        asyncio.to_thread(api.post, "/api/v1/projects/demo/versions/characters/Alice/restore/1")
    )
    try:
        assert await asyncio.to_thread(restoring.wait, 10), "还原没有到达版本管理器"
        cleaned = await asyncio.to_thread(_cleanup, api)
    finally:
        allow_restore.set()
    restored = await restore_task
    assert restored.status_code == 200, restored.text
    assert (project.get_project_path("demo") / "characters/Alice.png").read_bytes() == content
    assert _history(project)["current_version"] == 1
    assert cleaned["kept_in_use"] == 1
    assert _cleanup(api)["removed"] == 2


async def test_concurrent_reads_download_once(api, sessions, project, bucket):
    await _remote(sessions)
    image = solid_png_bytes((20, 20, 200), size=(64, 64))
    assert _upload(api, image).status_code == 200
    assert _cleanup(api)["removed"] == 2
    record = _history(project)["versions"][0]
    project_dir = project.get_project_path("demo")

    content_version = VersionManager(project_dir).find_content_version("characters", record["content_version_id"])
    assert content_version is not None
    paths = await asyncio.gather(*(ensure_content_version_file(project_dir, content_version) for _ in range(4)))
    assert {path.read_bytes() for path in paths} == {image}
    assert [op for op, _key in bucket.calls].count("get") == 1


@pytest.mark.parametrize("source", ["project", "library"])
@pytest.mark.parametrize("cleanup_hash_started", [False, True])
async def test_media_response_holds_remote_cache_until_the_body_is_sent(
    api, sessions, bucket, project, monkeypatch, source, cleanup_hash_started
):
    """响应头发出、FileResponse 尚未打开文件时清缓存，项目和全局库图片仍可读，响应后可清理。"""
    await _remote(sessions)
    content = solid_png_bytes((20, 40, 60))
    if source == "project":
        assert _upload(api, content).status_code == 200
        url = "/api/v1/files/demo/characters/Alice.png"
        target = project.get_project_path("demo") / "characters/Alice.png"
        digest_module = "lib.material.work_files"
    else:
        created = api.post(
            "/api/v1/assets",
            data={"type": "character", "name": "Alice"},
            files={"image": ("alice.png", content, "image/png")},
        )
        assert created.status_code == 200, created.text
        image_path = created.json()["asset"]["image_path"]
        url = f"/api/v1/global-assets/character/{Path(image_path).name}"
        target = project.data_root / image_path
        digest_module = "lib.material.global_library"
    hash_started = threading.Event()
    allow_hash = threading.Event()

    def paused_hash(path: Path) -> str:
        digest = sha256_file(path)
        if path == target:
            hash_started.set()
            assert allow_hash.wait(10), "媒体请求没有到达响应头"
        return digest

    cleanup_task = None
    if cleanup_hash_started:
        monkeypatch.setattr(f"{digest_module}.sha256_file", paused_hash)
        cleanup_task = asyncio.create_task(asyncio.to_thread(_cleanup, api))
        assert await asyncio.to_thread(hash_started.wait, 10), "清理没有到达摘要校验"
    else:
        assert _cleanup(api)["removed"] > 0
    during: list[dict[str, int]] = []

    async def clean_before_body(scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_cleanup(message: Message) -> None:
            if message["type"] == "http.response.start":
                if cleanup_task is not None:
                    allow_hash.set()
                    during.append(await cleanup_task)
                else:
                    during.append(await asyncio.to_thread(_cleanup, api))
            await send(message)

        await api.app(scope, receive, send_with_cleanup)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=clean_before_body), base_url="http://test"
    ) as client:
        try:
            response = await client.get(url)
        finally:
            allow_hash.set()
    assert response.status_code == 200
    assert response.content == content
    assert len(during) == 1
    assert during[0]["kept_in_use"] > 0
    assert _cleanup(api)["removed"] > 0


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


async def test_upload_confirmed_while_another_project_retakes_the_same_object_stays_stored(
    api, sessions, project, bucket, monkeypatch
):
    """两个项目同时保存同一字节的新图片：后到的提交接手了上传与补偿，它上传失败时，先到那次已确认的对象仍记为已保存，
    可以登记新的引用，项目照常持有它。"""
    await _remote(sessions)
    image = solid_png_bytes((30, 140, 60), size=(64, 64))
    uploading, release = threading.Event(), threading.Event()
    put = bucket.put_object
    puts = 0

    def put_object(*, bucket: str, key: str, content: bytes, content_type: str | None = None):
        nonlocal puts
        puts += 1
        if puts == 1:
            uploading.set()
            release.wait(10)
        elif puts == 2:
            raise _server_error(503, "ServiceUnavailable")
        return put(bucket=bucket, key=key, content=content, content_type=content_type)

    monkeypatch.setattr(bucket, "put_object", put_object)
    first = asyncio.create_task(asyncio.to_thread(_upload, api, image))
    try:
        assert await asyncio.to_thread(uploading.wait, 10)
        assert (await asyncio.to_thread(_upload, api, image, "other")).status_code == 502
    finally:
        release.set()
    assert (await first).status_code == 200

    [row] = await _objects(sessions)
    assert row.state == "stored"
    assert {(ref.project_name, ref.state) for ref in await _refs(sessions)} == {("demo", "active")}
    locator = ObjectLocator(row.connection_id, row.bucket, row.object_key)
    assert await add_reference(locator, "ingest:1", "content-version", session_factory=sessions)


async def test_same_object_failed_by_both_concurrent_uploads_is_reclaimed_by_the_last_settlement(
    api, sessions, project, bucket, monkeypatch
):
    """两个项目同时保存同一字节的新图片且都上传失败：后到的提交先结算时先到那次的占用还在，最后结算的一次补偿删除，
    对象不停在上传中、不占用连接。"""
    await _remote(sessions)
    image = solid_png_bytes((70, 20, 210), size=(64, 64))
    uploading, release = threading.Event(), threading.Event()
    puts = 0

    def put_object(*, bucket: str, key: str, content: bytes, content_type: str | None = None):
        nonlocal puts
        puts += 1
        if puts == 1:
            uploading.set()
            release.wait(10)
        raise _server_error(503, "ServiceUnavailable")

    monkeypatch.setattr(bucket, "put_object", put_object)
    first = asyncio.create_task(asyncio.to_thread(_upload, api, image))
    try:
        assert await asyncio.to_thread(uploading.wait, 10)
        assert (await asyncio.to_thread(_upload, api, image, "other")).status_code == 502
        [pending] = await _objects(sessions)
        assert pending.state == "uploading"
    finally:
        release.set()
    assert (await first).status_code == 502

    [row] = await _objects(sessions)
    assert row.state == "deleted"
    assert await _refs(sessions) == []
    async with sessions() as session:
        assert await holding_object_count(session, row.connection_id) == 0


async def test_shared_object_confirmed_by_a_commit_that_then_fails_is_reclaimed_when_its_last_hold_goes(
    api, sessions, project, bucket, monkeypatch
):
    """两个项目同时保存同一字节的新图片：后到的提交接手后上传失败先结算，先到那次确认了上传却没能发布版本。
    最后一个占用撤下时对象没有任何引用，照常回收，不留下无人引用却占用连接的已保存对象。"""
    await _remote(sessions)
    image = solid_png_bytes((90, 200, 30), size=(64, 64))
    uploading, release = threading.Event(), threading.Event()
    put = bucket.put_object
    puts = 0

    def put_object(*, bucket: str, key: str, content: bytes, content_type: str | None = None):
        nonlocal puts
        puts += 1
        if puts == 1:
            uploading.set()
            release.wait(10)
        elif puts == 2:
            raise _server_error(503, "ServiceUnavailable")
        return put(bucket=bucket, key=key, content=content, content_type=content_type)

    def _fail(*_args, **_kwargs):
        raise RuntimeError("injected manifest failure")

    monkeypatch.setattr(bucket, "put_object", put_object)
    use_upload_claim_registration(monkeypatch, _fail)
    first = asyncio.create_task(asyncio.to_thread(_upload, api, image))
    try:
        assert await asyncio.to_thread(uploading.wait, 10)
        assert (await asyncio.to_thread(_upload, api, image, "other")).status_code == 502
    finally:
        release.set()
    assert (await first).status_code == 500

    [row] = await _objects(sessions)
    assert row.state == "deleted"
    assert await _refs(sessions) == []
    async with sessions() as session:
        assert await holding_object_count(session, row.connection_id) == 0


async def test_startup_settlement_skips_a_commit_still_running_in_this_process(
    api, sessions, project, bucket, monkeypatch
):
    """启动补做在后台进行时，刚开始的远端上传还在传：补做不把它当作中断的提交，上传照常发布并持有对象。"""
    await _remote(sessions)
    image = solid_png_bytes((200, 120, 40), size=(64, 64))
    uploading, release = threading.Event(), threading.Event()
    put = bucket.put_object

    def put_object(*, bucket: str, key: str, content: bytes, content_type: str | None = None):
        uploading.set()
        release.wait(10)
        return put(bucket=bucket, key=key, content=content, content_type=content_type)

    monkeypatch.setattr(bucket, "put_object", put_object)
    upload = asyncio.create_task(asyncio.to_thread(_upload, api, image))
    try:
        assert await asyncio.to_thread(uploading.wait, 10)
        assert await recover_formal_commits(sessions, project_path_of=project.get_project_path) == 0
        assert _held(await _refs(sessions)) == {("demo", "pending")}
    finally:
        release.set()
    assert (await upload).status_code == 200

    [row] = await _objects(sessions)
    assert row.state == "stored"
    assert _held(await _refs(sessions)) == {("demo", "active")}
    assert row.object_key in bucket.stored_keys()


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


@pytest.mark.parametrize("action", ["delete", "bucket", "endpoint"])
@pytest.mark.parametrize("cleanup", [True, False])
async def test_connection_departure_disposes_all_unprotected_records(api, sessions, bucket, action, cleanup):
    assert api.put("/api/v1/object-storage/connection", json=CONNECTION).is_success
    async with sessions() as session:
        conn = await ObjectStorageConnectionRepository(session).get()
        assert conn is not None
        for i, state in enumerate(["stored", "orphaned", "stored", "deleted"]):
            row = FormalObject(
                connection_id=conn.id,
                endpoint=conn.endpoint,
                bucket=conn.bucket,
                object_key=f"arcreel-materials/{i}.png",
                content_digest=str(i) * 64,
                state=state,
            )
            session.add(row)
            await session.flush()
            if i == 2:
                session.add(
                    FormalObjectRef(object_id=row.id, project_name="ingest:42", content_version_id="cv", state="active")
                )
            if state != "deleted":
                bucket.objects[(BUCKET, row.object_key)] = b"image"
        session.add(
            RelayObject(
                connection_id=conn.id, endpoint=conn.endpoint, bucket=conn.bucket, object_key="arcreel-relay/new.png"
            )
        )
        bucket.objects[(BUCKET, "arcreel-relay/new.png")] = b"relay"
        bucket.objects[(BUCKET, "user-owned.png")] = b"untouched"
        await session.commit()
    if action == "delete":
        response = api.delete(f"/api/v1/object-storage/connection?delete_remote_objects={str(cleanup).lower()}")
    else:
        change = {"bucket": "next-bucket"} if action == "bucket" else {"endpoint": "tos-next.example.invalid"}
        response = api.put(
            "/api/v1/object-storage/connection", json={**CONNECTION, **change, "delete_remote_objects": cleanup}
        )
    assert response.status_code == 200, response.text
    assert response.json()["cleanup_failures"] == {}
    assert await _objects(sessions) == []
    assert await _refs(sessions) == []
    async with sessions() as session:
        assert list((await session.execute(select(RelayObject))).scalars()) == []
    assert bucket.objects == (
        {(BUCKET, "user-owned.png"): b"untouched"}
        if cleanup
        else {
            (BUCKET, "user-owned.png"): b"untouched",
            (BUCKET, "arcreel-materials/0.png"): b"image",
            (BUCKET, "arcreel-materials/1.png"): b"image",
            (BUCKET, "arcreel-materials/2.png"): b"image",
            (BUCKET, "arcreel-relay/new.png"): b"relay",
        }
    )


async def test_region_only_keeps_retention_and_partial_delete_failure_is_reported_once(api, sessions, bucket):
    assert api.put("/api/v1/object-storage/connection", json=CONNECTION).is_success
    async with sessions() as session:
        conn = await ObjectStorageConnectionRepository(session).get()
        assert conn is not None
        for key in ["arcreel-relay/ok", "arcreel-relay/denied"]:
            session.add(RelayObject(connection_id=conn.id, endpoint=conn.endpoint, bucket=conn.bucket, object_key=key))
            bucket.objects[(BUCKET, key)] = b"relay"
        await session.commit()
    moved = api.put("/api/v1/object-storage/connection", json={**CONNECTION, "region": "cn-shanghai"})
    assert moved.is_success
    assert moved.json()["cleanup_failures"] == {}
    assert len(bucket.objects) == 2
    assert bucket.calls == []
    bucket.fail_delete_keys.add("arcreel-relay/denied")
    deleted = api.delete("/api/v1/object-storage/connection")
    assert deleted.json() == {"cleanup_failures": {"provider": 1}}
    assert bucket.objects == {(BUCKET, "arcreel-relay/denied"): b"relay"}
    async with sessions() as session:
        assert list((await session.execute(select(RelayObject))).scalars()) == []
    assert api.get("/api/v1/object-storage/connection").json()["connection"] is None


@pytest.mark.parametrize("action", ["delete", "bucket"])
async def test_connection_disposal_finishes_after_cancellation_during_remote_delete(
    api, sessions, bucket, monkeypatch, action
):
    assert api.put("/api/v1/object-storage/connection", json=CONNECTION).is_success
    async with sessions() as session:
        conn = await ObjectStorageConnectionRepository(session).get()
        assert conn is not None
        session.add(
            RelayObject(connection_id=conn.id, endpoint=conn.endpoint, bucket=conn.bucket, object_key="arcreel-relay/x")
        )
        await session.commit()
    bucket.objects[(BUCKET, "arcreel-relay/x")] = b"relay"
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    class PausedDelete:
        def delete_object(self, **kwargs):
            bucket.delete_object(**kwargs)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(timeout=10), "test did not release the delete"

    use_object_storage_sdk(monkeypatch, lambda config: PausedDelete())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
        request = (
            client.delete("/api/v1/object-storage/connection")
            if action == "delete"
            else client.put("/api/v1/object-storage/connection", json={**CONNECTION, "bucket": "next-bucket"})
        )
        deleting = asyncio.create_task(request)
        try:
            await asyncio.wait_for(entered.wait(), 10)
            deleting.cancel()
        finally:
            release.set()
        response = await asyncio.wait_for(deleting, 10)
    assert response.status_code == 200, response.text
    assert response.json()["cleanup_failures"] == {}
    assert bucket.objects == {}
    async with sessions() as session:
        assert list((await session.execute(select(RelayObject))).scalars()) == []
    connection = api.get("/api/v1/object-storage/connection").json()["connection"]
    assert connection is None if action == "delete" else connection["bucket"] == "next-bucket"


@pytest.mark.parametrize("kind", ["formal", "relay"])
async def test_disposal_does_not_reinterpret_legacy_locations_against_the_current_bucket(api, sessions, bucket, kind):
    assert api.put("/api/v1/object-storage/connection", json=CONNECTION).is_success
    key = "arcreel-materials/old" if kind == "formal" else "arcreel-relay/old"
    async with sessions() as session:
        conn = await ObjectStorageConnectionRepository(session).get()
        assert conn is not None
        values = {"connection_id": conn.id, "endpoint": conn.endpoint, "bucket": "previous-bucket", "object_key": key}
        session.add(
            FormalObject(**values, content_digest="f" * 64, state="orphaned")
            if kind == "formal"
            else RelayObject(**values)
        )
        await session.commit()
    bucket.objects[(BUCKET, key)] = b"belongs-to-current-bucket"
    response = api.delete("/api/v1/object-storage/connection")
    assert response.status_code == 200, response.text
    assert response.json() == {"cleanup_failures": {"unavailable": 1}}
    assert bucket.objects == {(BUCKET, key): b"belongs-to-current-bucket"}
    assert bucket.calls == []
    assert await _objects(sessions) == []
    async with sessions() as session:
        assert list((await session.execute(select(RelayObject))).scalars()) == []


UNREACHABLE_ENDPOINT = "tos-cn-beijing.test-unreachable.invalid"


@pytest.fixture
def unreachable_endpoint(monkeypatch, bucket: MemoryBucket) -> MemoryBucket:
    """写错的 Endpoint 解析不到主机：发往它的每个请求都在客户端失败；其他 Endpoint 落到内存桶。"""

    class _Unreachable:
        def __getattr__(self, _name: str):
            def _fail(**_kwargs):
                raise TosClientError("connection failed", ConnectionError("Name or service not known"))

            return _fail

    use_object_storage_sdk(
        monkeypatch, lambda config: _Unreachable() if config.endpoint == UNREACHABLE_ENDPOINT else bucket
    )
    return bucket


def _fail_once_with_unreachable_endpoint(api: TestClient, content: bytes) -> None:
    """按写错的 Endpoint 配置连接并选择对象存储，上传失败只留下等待删除、没有任何引用的对象，再改回本地。"""
    assert api.put(
        "/api/v1/object-storage/connection", json={**CONNECTION, "endpoint": UNREACHABLE_ENDPOINT}
    ).is_success
    assert api.put("/api/v1/object-storage/new-image-target", json={"target": "object_storage"}).is_success
    assert _upload(api, content).status_code == 502
    assert api.put("/api/v1/object-storage/new-image-target", json={"target": "local"}).is_success


async def test_failed_saves_on_a_wrong_endpoint_do_not_lock_the_connection(
    api, sessions, project, unreachable_endpoint
):
    image = solid_png_bytes((90, 30, 200), size=(64, 64))
    _fail_once_with_unreachable_endpoint(api, image)
    fixed = api.put("/api/v1/object-storage/connection", json=CONNECTION)
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["cleanup_failures"] == {"network": 1}
    assert await _objects(sessions) == []
    assert await _refs(sessions) == []
    assert await sweep_formal_objects(sessions, now=utc_now() + timedelta(days=1)) == 0
    assert not any(op == "delete" for op, _key in unreachable_endpoint.calls)
    assert api.put("/api/v1/object-storage/new-image-target", json={"target": "object_storage"}).is_success
    assert _upload(api, image).status_code == 200
    [saved] = await _objects(sessions)
    assert (saved.state, saved.endpoint) == ("stored", CONNECTION["endpoint"])
    assert api.delete("/api/v1/object-storage/connection").status_code == 409


async def test_failed_saves_on_a_wrong_endpoint_do_not_block_deleting_the_connection(
    api, sessions, project, unreachable_endpoint
):
    _fail_once_with_unreachable_endpoint(api, solid_png_bytes((20, 160, 60), size=(64, 64)))
    deleted = api.delete("/api/v1/object-storage/connection")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["cleanup_failures"] == {"network": 1}
    assert await _objects(sessions) == []
    assert api.get("/api/v1/object-storage/connection").json() == {
        "connection": None,
        "new_image_target": "local",
        "pending_ingest_tasks": 0,
    }


@pytest.mark.parametrize("held_by", ["uploading", "referenced_orphan"])
async def test_objects_that_may_still_be_read_keep_blocking_connection_changes(api, sessions, project, bucket, held_by):
    """进行中的提交（上传未确认）与仍有引用的待删除对象可能被读取，照旧挡住更换 Endpoint 与删除连接。"""
    assert api.put("/api/v1/object-storage/connection", json=CONNECTION).status_code == 200
    async with sessions() as session:
        connection = await ObjectStorageConnectionRepository(session).get()
        assert connection is not None
        row = FormalObject(
            connection_id=connection.id,
            endpoint=connection.endpoint,
            bucket=BUCKET,
            object_key="arcreel-materials/sha256/cd/cdef.png",
            content_digest="cd" * 32,
            size=3,
            state="uploading" if held_by == "uploading" else "orphaned",
            created_attempt="attempt-in-flight",
            delete_attempts=0,
        )
        session.add(row)
        await session.flush()
        session.add(
            FormalObjectRef(object_id=row.id, project_name="demo", attempt_id="attempt-in-flight", state="pending")
        )
        await session.commit()

    moved = api.put("/api/v1/object-storage/connection", json={**CONNECTION, "endpoint": "tos-cn-shanghai.volces.com"})
    assert moved.status_code == 409
    assert api.delete("/api/v1/object-storage/connection").status_code == 409
    [kept] = await _objects(sessions)
    assert kept.last_error is None


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


def _file_view_entries(api: TestClient, path: str) -> dict[str, dict[str, Any]]:
    resp = api.get("/api/v1/projects/demo/file-view", params={"path": path})
    assert resp.status_code == 200, resp.text
    return {entry["path"]: entry for entry in resp.json()["entries"]}


async def test_evicted_remote_history_is_listed_and_deleted_from_the_file_view(api, sessions, project, bucket):
    """清理缓存后：文件视图按版本记录列出远端版本且不下载；删除历史版本先取回内容再移进回收站，对象按引用保留。"""
    image_a = solid_png_bytes((30, 30, 200), size=(64, 64))
    assert _upload(api, image_a).status_code == 200  # 本地版本 A
    await _remote(sessions)
    image_b, image_c = solid_png_bytes((30, 200, 30), size=(64, 64)), solid_png_bytes((200, 30, 30), size=(64, 64))
    assert _upload(api, image_b).status_code == 200
    assert _upload(api, image_c).status_code == 200
    record_a, record_b, record_c = _history(project)["versions"]
    assert _cleanup(api)["removed"] == 3  # B、C 的快照与 C 的正式文件
    project_dir = project.get_project_path("demo")
    snapshot_dir = str(Path(record_b["file"]).parent)
    assert not (project_dir / record_b["file"]).exists()
    calls = len(bucket.calls)

    entries = _file_view_entries(api, snapshot_dir)
    files = {path for path, entry in entries.items() if entry["type"] == "file"}
    assert files == {record_a["file"], record_b["file"], record_c["file"]}
    remote_b = entries[record_b["file"]]
    assert (remote_b["role"], remote_b.get("remote_only"), remote_b["size"]) == ("history", True, len(image_b))
    assert entries[record_c["file"]]["is_current"] is True
    assert "remote_only" not in entries[record_a["file"]]  # 本地版本照旧按盘上文件列出
    assert entries[record_a["file"]]["size"] == (project_dir / record_a["file"]).stat().st_size
    [sheet] = [entry for path, entry in _file_view_entries(api, "characters").items() if path.endswith("Alice.png")]
    assert (sheet["role"], sheet.get("remote_only"), sheet["size"]) == ("referenced", True, len(image_c))
    parent = _file_view_entries(api, str(Path(snapshot_dir).parent))[snapshot_dir]
    assert parent["size"] == entries[record_a["file"]]["size"] + len(image_b) + len(image_c)
    assert bucket.calls[calls:] == []  # 列出不触发下载
    assert not (project_dir / record_b["file"]).exists()

    deleted = api.delete("/api/v1/projects/demo/file-view", params={"path": record_b["file"]})
    assert deleted.status_code == 200, deleted.text
    assert bucket.calls[calls:] == [("get", record_b["storage"]["key"])]  # 经共用读取入口取回一次，不签发地址
    assert record_b["file"] not in _file_view_entries(api, snapshot_dir)
    assert await reconcile_and_collect("demo", project_dir, sessions) == 0
    assert {(ref.content_version_id, ref.state) for ref in await _refs(sessions)} == {
        (record_b["content_version_id"], "trashed"),
        (record_c["content_version_id"], "active"),
    }
    assert record_b["storage"]["key"] in bucket.stored_keys()

    restored = api.post(f"/api/v1/projects/demo/trash/{deleted.json()['record']['id']}/restore")
    assert restored.status_code == 200, restored.text
    back = _history(project)["versions"][-1]
    assert (back["content_version_id"], back["storage"]) == (record_b["content_version_id"], record_b["storage"])

    # 本地版本 A 的删除不经对象存储
    calls = len(bucket.calls)
    assert api.delete("/api/v1/projects/demo/file-view", params={"path": record_a["file"]}).status_code == 200
    assert bucket.calls[calls:] == []


async def test_evicted_remote_version_previews_from_the_file_view_and_fetch_failure_keeps_it(
    api, sessions, project, bucket
):
    """文件视图预览已清理缓存的远端版本时取回内容；删除时取回失败返回 502，版本留在原处。"""
    await _remote(sessions)
    image_b = solid_png_bytes((30, 200, 30), size=(64, 64))
    assert _upload(api, image_b).status_code == 200
    assert _upload(api, solid_png_bytes((200, 30, 30), size=(64, 64))).status_code == 200
    record_b, _record_c = _history(project)["versions"]
    assert _cleanup(api)["removed"] == 3

    bucket.fail.add("get")
    failed = api.delete("/api/v1/projects/demo/file-view", params={"path": record_b["file"]})
    assert failed.status_code == 502, failed.text
    assert record_b["file"] in _file_view_entries(api, str(Path(record_b["file"]).parent))
    assert _history(project)["versions"][0] == record_b
    assert api.get("/api/v1/projects/demo/trash").json()["records"] == []

    bucket.fail.clear()
    preview = api.get("/api/v1/projects/demo/file-view/content", params={"path": record_b["file"]})
    assert preview.status_code == 200, preview.text
    assert preview.content == image_b


async def test_file_view_holds_existing_remote_cache_until_open(api, sessions, bucket, monkeypatch):
    await _remote(sessions)
    content = solid_png_bytes((30, 200, 30))
    assert _upload(api, content).status_code == 200
    opening = threading.Event()
    allow_open = threading.Event()
    original_open = file_view.open_project_file

    def paused_open(project_dir, path):
        opening.set()
        assert allow_open.wait(10), "清理没有结束"
        return original_open(project_dir, path)

    monkeypatch.setattr(file_view, "open_project_file", paused_open)
    preview_task = asyncio.create_task(
        asyncio.to_thread(api.get, "/api/v1/projects/demo/file-view/content", params={"path": "characters/Alice.png"})
    )
    try:
        assert await asyncio.to_thread(opening.wait, 10), "预览没有到达文件打开处"
        cleaned = await asyncio.to_thread(_cleanup, api)
    finally:
        allow_open.set()
    preview = await preview_task
    assert preview.status_code == 200, preview.text
    assert preview.content == content
    assert cleaned["kept_in_use"] == 1
    assert _cleanup(api)["removed"] == 1


async def test_agent_deletes_an_evicted_remote_history_version_like_the_file_view(
    api, sessions, project, bucket, tmp_path
):
    """Agent 删除已清理缓存的远端历史版本：同样先取回再移进回收站，引用转为 trashed；取回失败转述为工具问题。"""
    await _remote(sessions)
    image_b = solid_png_bytes((30, 200, 30), size=(64, 64))
    for rgb in ((30, 200, 30), (200, 30, 30), (30, 30, 200)):
        assert _upload(api, solid_png_bytes(rgb, size=(64, 64))).status_code == 200
    record_b, record_c, _record_d = _history(project)["versions"]
    assert _cleanup(api)["removed"] == 4
    project_dir = project.get_project_path("demo")
    ctx = ToolHarness("demo", tmp_path / "projects", project)
    calls = len(bucket.calls)

    outcome = await run_declared_tool("delete_project_file", ctx, {"path": record_b["file"]})

    assert outcome.problem is None, outcome.problem
    assert bucket.calls[calls:] == [("get", record_b["storage"]["key"])]
    [record] = api.get("/api/v1/projects/demo/trash").json()["records"]
    assert (record["id"], record["origin"]) == (outcome.value["record"]["id"], "agent")
    assert (
        project_trash_store.trash_dir(project_dir) / record["id"] / project_trash_store.FILES_DIRNAME / record_b["file"]
    ).read_bytes() == image_b
    assert await reconcile_and_collect("demo", project_dir, sessions) == 0
    assert (record_b["content_version_id"], "trashed") in {
        (ref.content_version_id, ref.state) for ref in await _refs(sessions)
    }

    bucket.fail.add("get")
    failed = await run_declared_tool("delete_project_file", ctx, {"path": record_c["file"]})
    assert problem_of(failed).code == "material_remote_fetch_failed"
    assert record_c in _history(project)["versions"]


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


class _PausedSettlement:
    """让正式提交的第一次结算停在发布版本之后、结算之前；``crash`` 时这次结算不再发生（进程在此处退出）。"""

    def __init__(self, monkeypatch, *, crash: bool = False) -> None:
        self.ready = threading.Event()
        self.release = threading.Event()
        original = ObjectContentStorage.finish
        paused = self

        async def finish(storage: ObjectContentStorage) -> None:
            if not paused.ready.is_set():
                paused.ready.set()
                await asyncio.to_thread(paused.release.wait, 10)
                if crash:
                    return
            await original(storage)

        monkeypatch.setattr(ObjectContentStorage, "finish", finish)

    async def wait(self) -> None:
        assert await asyncio.to_thread(self.ready.wait, 10)


def _held(refs: list[FormalObjectRef]) -> set[tuple[str, str]]:
    return {(ref.project_name, ref.state) for ref in refs}


@pytest.mark.parametrize("settled_by", ["commit", "startup"])
async def test_asset_trashed_before_its_upload_settles_keeps_the_object(
    api, sessions, project, bucket, monkeypatch, settled_by
):
    """正式上传发布版本后、结算前，工作文件被清理、资产被删进回收站：结算（或启动补做）连同回收站一起记引用，
    对象不被补偿删除，恢复后预览从对象存储取回。"""
    await _remote(sessions)
    paused = _PausedSettlement(monkeypatch, crash=settled_by == "startup")
    image = solid_png_bytes((60, 160, 200), size=(64, 64))
    upload = asyncio.create_task(asyncio.to_thread(_upload, api, image))
    try:
        await paused.wait()
        assert _held(await _refs(sessions)) == {("demo", "pending")}
        assert (await asyncio.to_thread(_cleanup, api))["removed"] == 2
        deleted = await asyncio.to_thread(api.delete, "/api/v1/projects/demo/characters/Alice")
        assert deleted.status_code == 200, deleted.text
    finally:
        paused.release.set()
    assert (await upload).status_code == 200
    if settled_by == "startup":
        # 进程在结算前退出：下次启动的新进程没有进行中的提交
        assert await recover_formal_commits(sessions, project_path_of=project.get_project_path, running=()) == 1

    key = formal_object_key(hashlib.sha256(image).hexdigest(), ".png")
    assert bucket.stored_keys() == [key]
    assert _held(await _refs(sessions)) == {("demo", "trashed")}
    [record] = api.get("/api/v1/projects/demo/trash").json()["records"]
    restored = api.post(f"/api/v1/projects/demo/trash/{record['id']}/restore")
    assert restored.status_code == 200, restored.text
    preview = api.get("/api/v1/files/demo/characters/Alice.png")
    assert preview.status_code == 200, preview.text
    assert preview.content == image


def _export_archive(client: TestClient) -> bytes:
    token = client.post("/api/v1/projects/demo/export/token", params={"scope": "full"})
    assert token.status_code == 200, token.text
    exported = client.get(
        "/api/v1/projects/demo/export", params={"scope": "full", "download_token": token.json()["download_token"]}
    )
    assert exported.status_code == 200, exported.text
    return exported.content


async def test_overwrite_import_retires_the_old_projects_unsettled_version_with_the_tombstone(
    api, sessions, project, bucket, monkeypatch
):
    """旧项目刚发布、尚未结算的远端版本随墓碑保留；覆盖导入的新图片引用记在项目名下，彻底删除墓碑不影响它。"""
    await _remote(sessions)
    kept = solid_png_bytes((20, 120, 60), size=(64, 64))
    assert _upload(api, kept).status_code == 200
    with build_projects_client(monkeypatch, project, session_factory=sessions) as projects_api:
        archive = _export_archive(projects_api)
        paused = _PausedSettlement(monkeypatch)
        unsettled = solid_png_bytes((200, 120, 20), size=(64, 64))
        upload = asyncio.create_task(asyncio.to_thread(_upload, api, unsettled))
        try:
            await paused.wait()
            imported = await asyncio.to_thread(
                projects_api.post,
                "/api/v1/projects/import",
                files={"file": ("demo.zip", archive, "application/zip")},
                data={"confirm_report": "true", "conflict_policy": "overwrite"},
            )
            assert imported.status_code == 200, imported.text
        finally:
            paused.release.set()
        assert (await upload).status_code == 200
        [entry] = projects_api.get("/api/v1/deleted-projects").json()["projects"]
        tombstone = entry["id"]

        kept_key = formal_object_key(hashlib.sha256(kept).hexdigest(), ".png")
        unsettled_key = formal_object_key(hashlib.sha256(unsettled).hexdigest(), ".png")
        assert bucket.stored_keys() == sorted([kept_key, unsettled_key])
        by_key = {row.id: row.object_key for row in await _objects(sessions)}
        assert {(by_key[ref.object_id], ref.project_name, ref.state) for ref in await _refs(sessions)} == {
            (kept_key, tombstone, "active"),
            (unsettled_key, tombstone, "active"),
            (kept_key, "demo", "active"),
        }
        layout = project.layout
        assert await retain_formal_objects(layout.projects_dir, layout.deleted_projects_dir, sessions) == 0

        assert projects_api.delete(f"/api/v1/deleted-projects/{quote(tombstone, safe='')}").status_code == 200
    assert bucket.stored_keys() == [kept_key]
    assert _held(await _refs(sessions)) == {("demo", "active")}
    _cleanup(api)
    preview = api.get("/api/v1/files/demo/characters/Alice.png")
    assert preview.status_code == 200
    assert preview.content == kept


async def test_remote_upload_published_while_the_project_is_being_deleted_is_refused(
    api, sessions, project, bucket, monkeypatch
):
    """项目删除补齐引用之后、目录移走之前，同项目的远端上传在上传前失败：墓碑里不会出现没被引用持有的远端版本。"""
    assert _upload(api, solid_png_bytes((200, 10, 10), size=(64, 64))).status_code == 200  # 本地版本
    await _remote(sessions)
    filled, allow_retire = asyncio.Event(), asyncio.Event()
    settling, allow_finish = asyncio.Event(), asyncio.Event()
    retire, finish = project_retirement.retire_project_records, ObjectContentStorage.finish

    async def _retire_after_fill(session, project_name):
        filled.set()
        await allow_retire.wait()
        return await retire(session, project_name)

    async def _paused_finish(storage):
        settling.set()
        await allow_finish.wait()
        return await finish(storage)

    monkeypatch.setattr(project_retirement, "retire_project_records", _retire_after_fill)
    monkeypatch.setattr(ObjectContentStorage, "finish", _paused_finish)
    projects_app = build_projects_client(monkeypatch, project, session_factory=sessions).app
    upload_url = "/api/v1/projects/demo/upload/character?name=Alice"
    image = solid_png_bytes((10, 200, 10), size=(64, 64))
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=projects_app), base_url="http://test") as projects_client,
    ):
        deleting = asyncio.create_task(projects_client.delete("/api/v1/projects/demo"))
        await asyncio.wait_for(filled.wait(), 10)
        uploading = asyncio.create_task(client.post(upload_url, files={"file": ("alice.png", image, "image/png")}))
        await asyncio.wait_for(settling.wait(), 10)
        cleanup = await client.post("/api/v1/object-storage/work-cache/cleanup", json={})
        assert cleanup.status_code == 200, cleanup.text
        allow_retire.set()
        deleted = await asyncio.wait_for(deleting, 10)
        allow_finish.set()
        uploaded = await asyncio.wait_for(uploading, 10)

    assert deleted.status_code == 200, deleted.text
    assert uploaded.status_code == 409, uploaded.text
    [tombstone] = list(project.layout.deleted_projects_dir.iterdir())
    history = json.loads((tombstone / "versions" / "versions.json").read_text(encoding="utf-8"))
    assert [record.get("storage") for record in history["characters"]["Alice"]["versions"]] == [None]
    assert bucket.objects == {}
    assert await _objects(sessions) == []
    assert await _refs(sessions) == []


async def test_choosing_remote_storage_and_deleting_the_connection_do_not_both_succeed(
    api, sessions, bucket, monkeypatch
):
    """把新增图片改为保存到对象存储与删除连接同时发生：两者串行，不会留下目标为对象存储而没有连接的状态。"""
    await _remote(sessions)
    await _target(sessions, "local")
    checked, proceed = asyncio.Event(), asyncio.Event()
    save_target = object_storage.set_new_content_target

    async def _paused(session, target):
        checked.set()
        await proceed.wait()
        await save_target(session, target)

    monkeypatch.setattr(object_storage, "set_new_content_target", _paused)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
        selecting = asyncio.create_task(
            client.put("/api/v1/object-storage/new-image-target", json={"target": "object_storage"})
        )
        await asyncio.wait_for(checked.wait(), 10)
        trying_lock, acquired_lock = asyncio.Event(), asyncio.Event()
        shared_lock = object_storage.connection_change_lock()

        @asynccontextmanager
        async def observed_lock():
            trying_lock.set()
            async with shared_lock:
                acquired_lock.set()
                yield

        monkeypatch.setattr(object_storage, "connection_change_lock", observed_lock)
        deleting = asyncio.create_task(client.delete("/api/v1/object-storage/connection"))
        await asyncio.wait_for(trying_lock.wait(), 10)
        assert not acquired_lock.is_set()  # 删除已到真实共享锁，等待设置提交之后再核对
        proceed.set()
        selected = await asyncio.wait_for(selecting, 10)
        deleted = await asyncio.wait_for(deleting, 10)
        final = (await client.get("/api/v1/object-storage/connection")).json()

    assert selected.status_code == 200, selected.text
    assert deleted.status_code == 409, deleted.text
    assert final["connection"] is not None
    assert final["new_image_target"] == "object_storage"


async def test_connection_moved_before_the_first_object_is_registered_fails_the_commit(
    api, sessions, project, bucket, monkeypatch
):
    """提交取得连接后、登记首个对象前，另一位管理员把桶换掉：这次提交在上传前失败、不发布版本，之后的提交写新桶。"""
    await _remote(sessions)
    entered, release = threading.Event(), threading.Event()
    original = ObjectContentStorage.persist

    def persist(storage: ObjectContentStorage, *args: Any) -> Any:
        if not entered.is_set():
            entered.set()
            release.wait(10)
        return original(storage, *args)

    monkeypatch.setattr(ObjectContentStorage, "persist", persist)
    before = (project.get_project_path("demo") / "project.json").read_bytes()
    upload = asyncio.create_task(asyncio.to_thread(_upload, api, solid_png_bytes((5, 50, 150), size=(64, 64))))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        moved = await asyncio.to_thread(
            api.put, "/api/v1/object-storage/connection", json={**CONNECTION, "bucket": "replacement-bucket"}
        )
        assert moved.status_code == 200, moved.text
    finally:
        release.set()
    failed = await upload
    assert failed.status_code == 502, failed.text
    assert bucket.calls == []
    assert await _objects(sessions) == []
    assert (project.get_project_path("demo") / "project.json").read_bytes() == before
    assert not (project.get_project_path("demo") / "versions" / "versions.json").is_file() or "Alice" not in (
        json.loads((project.get_project_path("demo") / "versions" / "versions.json").read_text()).get("characters", {})
    )

    assert _upload(api, solid_png_bytes((5, 50, 150), size=(64, 64))).status_code == 200
    assert {location[0] for location in bucket.objects} == {"replacement-bucket"}
    assert [row.bucket for row in await _objects(sessions)] == ["replacement-bucket"]


# ---- 远端图片参与时效判定、生成与编辑 ---------------------------------------------------------------

ALICE_RGB = (200, 10, 10)


def _alice_sheet_state(project: ProjectManager) -> tuple[ArtifactStatus, dict[ArtifactKey, ArtifactManifestEntry]]:
    project_dir = project.get_project_path("demo")
    comparison = ArtifactCurrencyResolver(project_dir).compare(
        ArtifactKey.asset_sheet("character", "Alice"), artifact_path="characters/Alice.png"
    )
    return comparison.status, dict(plan_artifact_target_state(project_dir).entries)


async def _remote_alice_with_evicted_cache(api: TestClient, sessions, project: ProjectManager) -> dict[str, Any]:
    await _remote(sessions)
    assert _upload(api, solid_png_bytes(ALICE_RGB, size=(400, 400))).status_code == 200
    [record] = _history(project)["versions"]
    assert "storage" in record
    assert _cleanup(api)["removed"] == 2
    project_dir = project.get_project_path("demo")
    assert not (project_dir / "characters" / "Alice.png").exists()
    assert not (project_dir / record["file"]).exists()
    return record


def _remote_saved(project_dir: Path, history: dict[str, Any], bucket: MemoryBucket, artifact_path: str) -> bytes:
    """当前版本已保存到远端：版本记录带 storage，对象就在桶里，正式文件就是它的工作副本。"""
    record = next(item for item in history["versions"] if item["version"] == history["current_version"])
    stored = record["storage"]
    content = bucket.objects[(stored["bucket"], stored["key"])]
    assert hashlib.sha256(content).hexdigest() == record["content_digest"]
    assert (project_dir / artifact_path).read_bytes() == content
    return content


async def test_evicted_remote_sheet_keeps_currency_and_workflow_plan(api, sessions, project, bucket):
    """清理工作文件不改变资产图的时效与目标态规划，规划的稳定性复核照常成立，且判定不触发取回。"""
    await _remote(sessions)
    assert _upload(api, solid_png_bytes(ALICE_RGB, size=(400, 400))).status_code == 200
    before_status, before_entries = await asyncio.to_thread(_alice_sheet_state, project)
    plan = await asyncio.to_thread(plan_artifact_target_state, project.get_project_path("demo"))
    assert before_status is ArtifactStatus.CURRENT

    assert _cleanup(api)["removed"] == 2
    after_status, after_entries = await asyncio.to_thread(_alice_sheet_state, project)

    assert after_status is ArtifactStatus.CURRENT
    assert after_entries == before_entries
    await asyncio.to_thread(assert_artifact_target_state_plan_unchanged, project.get_project_path("demo"), plan)
    assert not any(op == "get" for op, _key in bucket.calls)


async def test_generation_from_evicted_remote_sheet_fetches_it_and_saves_the_new_image_remotely(
    api, sessions, project, bucket, monkeypatch
):
    """衍生图生成以缓存已清理的远端角色图为参考：发给供应商的是取回的原图，生成结果保存到远端。"""
    await _remote_alice_with_evicted_cache(api, sessions, project)

    def _register(entry: dict[str, Any]) -> None:
        entry.setdefault(DERIVATIVES_FIELD, {})["战斗装"] = {"description": "换上黑色重甲", "character_sheet": ""}

    await asyncio.to_thread(project.update_asset_entry, "character", "demo", "Alice", _register)
    project_dir = project.get_project_path("demo")
    generator = build_generator(
        project_dir, DashScopeImageBackend(api_key="TEST-DASHSCOPE-KEY", model="qwen-image-2.0")
    )
    wire_derivative_generation(monkeypatch, project, generator)

    result = solid_png_bytes(RESULT_IMAGE_RGB)
    with capture_http() as router:
        submit = router.post(GENERATION_URL).mock(
            return_value=httpx.Response(
                200, json={"output": {"choices": [{"message": {"content": [{"image": RESULT_URL}]}}]}}
            )
        )
        router.get(RESULT_URL).mock(return_value=httpx.Response(200, content=result))
        await derivative_sheet_tasks.execute_character_derivative_task("demo", "Alice/战斗装", {}, task_id="task-1")

    content = request_json(only_request(submit))["input"]["messages"][0]["content"]
    [sent] = [item["image"] for item in content if "image" in item]
    assert close_to(decode_data_url_image(sent).convert("RGB").getpixel((8, 8)), ALICE_RGB)

    sheet_path = derivative_sheet_relative_path("Alice", "战斗装")
    versions = json.loads((project_dir / "versions" / "versions.json").read_text(encoding="utf-8"))
    derivative_history = versions[CHARACTER_DERIVATIVE_RESOURCE_TYPE][derivative_artifact_id("Alice", "战斗装")]
    assert _remote_saved(project_dir, derivative_history, bucket, sheet_path) == result


async def test_image_edit_of_evicted_remote_sheet_edits_the_fetched_image_and_saves_remotely(
    api, sessions, project, bucket, monkeypatch
):
    """编辑缓存已清理的远端角色图：编辑源取回后交给供应商，编辑结果作为新版本保存到远端，再次清理不改变时效。"""
    await _remote_alice_with_evicted_cache(api, sessions, project)
    project_dir = project.get_project_path("demo")
    generator = build_generator(
        project_dir, DashScopeImageBackend(api_key="TEST-DASHSCOPE-KEY", model="qwen-image-2.0")
    )
    use_generation_context(monkeypatch, fake_resolve_ctx(generator))

    edited = solid_png_bytes(RESULT_IMAGE_RGB)
    with capture_http() as router:
        submit = router.post(GENERATION_URL).mock(
            return_value=httpx.Response(
                200, json={"output": {"choices": [{"message": {"content": [{"image": RESULT_URL}]}}]}}
            )
        )
        router.get(RESULT_URL).mock(return_value=httpx.Response(200, content=edited))
        outcome = await execute_image_edit_task(
            "demo", "Alice", {"resource_type": "character", "prompt": "把头发改成红色"}, task_id="task-1"
        )

    content = request_json(only_request(submit))["input"]["messages"][0]["content"]
    [sent] = [item["image"] for item in content if "image" in item]
    assert close_to(decode_data_url_image(sent).convert("RGB").getpixel((8, 8)), ALICE_RGB)
    assert outcome["version"] == 2
    assert _remote_saved(project_dir, _history(project), bucket, "characters/Alice.png") == edited

    edited_state = await asyncio.to_thread(_alice_sheet_state, project)
    assert _cleanup(api)["removed"] == 2  # 新版本的快照与正式文件
    assert await asyncio.to_thread(_alice_sheet_state, project) == edited_state


async def test_video_reference_staging_fetches_the_evicted_remote_sheet(api, sessions, project, bucket):
    """视频任务把参考图暂存为任务私有副本前取回缓存已清理的远端图片；供应商延迟取图只读这份副本。"""
    record = await _remote_alice_with_evicted_cache(api, sessions, project)
    project_dir = project.get_project_path("demo")
    reference = ProviderMediaInput(
        path=project_dir / "characters" / "Alice.png",
        role="reference_image",
        logical_type="character",
        logical_name="Alice",
        kind="sheet",
    )

    [staged] = await stage_provider_media_for_task(project_dir, "task-1", (reference,))

    assert staged.sha256 == record["content_digest"]
    assert hashlib.sha256((project_dir / staged.staged_locator).read_bytes()).hexdigest() == record["content_digest"]


# ── 手动上传分镜图、角色衍生图与宫格图 ───────────────────────────────


@dataclass(frozen=True)
class _ManualUpload:
    url: str
    resource_type: str
    resource_id: str
    artifact_path: str


def _storyboard_upload(project: ProjectManager) -> _ManualUpload:
    segment = {
        "segment_id": "E1S01",
        "novel_text": "t",
        "duration_seconds": 5,
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
        "image_prompt": "安静的房间",
        "video_prompt": "镜头缓慢推进",
        "generated_assets": {"storyboard_image": None, "status": "pending"},
    }
    script = {"episode": 1, "title": "E1", "content_mode": "narration", "segments": [segment]}
    project.save_script("demo", script, "episode_1.json", validate=False)
    return _ManualUpload(
        url="/api/v1/projects/demo/shots/E1S01/upload/storyboard?script_file=episode_1.json",
        resource_type="storyboards",
        resource_id="E1S01",
        artifact_path="storyboards/scene_E1S01.png",
    )


def _derivative_upload(project: ProjectManager) -> _ManualUpload:
    def _register(entry: dict[str, Any]) -> None:
        entry.setdefault(DERIVATIVES_FIELD, {})["战斗装"] = {"description": "换上黑色重甲", "character_sheet": ""}

    project.update_asset_entry("character", "demo", "Alice", _register)
    return _ManualUpload(
        url="/api/v1/projects/demo/characters/Alice/derivatives/战斗装/upload",
        resource_type=CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        resource_id=derivative_artifact_id("Alice", "战斗装"),
        artifact_path=derivative_sheet_relative_path("Alice", "战斗装"),
    )


def _grid_upload(project: ProjectManager) -> _ManualUpload:
    _storyboard_upload(project)

    def _enable_grid(data: dict[str, Any]) -> None:
        data.update(generation_mode="storyboard", grid_storyboard=True)

    project.update_project("demo", _enable_grid)
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="p",
        model="m",
        video_aspect_ratio="9:16",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    GridManager(project.get_project_path("demo")).save(grid)
    return _ManualUpload(
        url=f"/api/v1/projects/demo/grids/{grid.id}/upload",
        resource_type="grids",
        resource_id=grid.id,
        artifact_path=f"grids/{grid.id}.png",
    )


def _project_files(project_dir: Path) -> dict[str, bytes]:
    return {str(path.relative_to(project_dir)): path.read_bytes() for path in project_dir.rglob("*") if path.is_file()}


@pytest.mark.parametrize("prepare", [_storyboard_upload, _derivative_upload, _grid_upload], ids=lambda f: f.__name__)
async def test_manual_image_upload_follows_the_storage_choice(api, sessions, project, bucket, prepare):
    """手动上传的分镜图、衍生图与宫格图按存储选择保存：本地时照旧；远端时对象确认后才发布版本，
    上传失败时项目文件逐字节不变；清理工作文件后从远端取回。"""
    entry = await asyncio.to_thread(prepare, project)
    project_dir = project.get_project_path("demo")

    def _send(rgb: tuple[int, int, int]):
        return api.post(entry.url, files={"file": ("shot.png", solid_png_bytes(rgb, size=(64, 64)), "image/png")})

    def _records() -> list[dict[str, Any]]:
        return VersionManager(project_dir).get_versions(entry.resource_type, entry.resource_id)["versions"]

    assert _send((200, 10, 10)).status_code == 200
    assert "storage" not in _records()[-1]
    assert bucket.stored_keys() == []

    await _remote(sessions)
    assert _send((10, 200, 10)).status_code == 200
    record = _records()[-1]
    saved = (project_dir / entry.artifact_path).read_bytes()
    assert record["storage"]["key"] == formal_object_key(record["content_digest"], ".png")
    assert bucket.objects[(BUCKET, record["storage"]["key"])] == saved
    [ref] = await _refs(sessions)
    assert (ref.state, ref.project_name, ref.content_version_id) == ("active", "demo", record["content_version_id"])

    before = await asyncio.to_thread(_project_files, project_dir)
    bucket.fail.add("put")
    failed = _send((10, 10, 200))
    assert failed.status_code == 502, failed.text
    assert await asyncio.to_thread(_project_files, project_dir) == before
    assert [ref.state for ref in await _refs(sessions)] == ["active"]

    bucket.fail.clear()
    _cleanup(api)
    assert not (project_dir / entry.artifact_path).exists()
    preview = api.get(f"/api/v1/files/demo/{entry.artifact_path}")
    assert preview.status_code == 200
    assert preview.content == saved


async def test_split_of_an_evicted_remote_grid_fetches_it_and_its_references(api, sessions, project, bucket):
    """联合图与生成时的参考资产图都是缓存已清理的远端图片：切分照常进行，切出的分镜图带上参考依据，时效判定为当前。"""
    await _remote_alice_with_evicted_cache(api, sessions, project)
    scene_ids = [f"E1S0{index}" for index in range(1, 5)]
    segments = [
        {
            "segment_id": scene_id,
            "novel_text": "t",
            "duration_seconds": 5,
            "characters_in_segment": ["Alice"],
            "scenes": [],
            "props": [],
            "image_prompt": "安静的房间",
            "video_prompt": "镜头缓慢推进",
            "generated_assets": {"storyboard_image": None, "status": "pending"},
        }
        for scene_id in scene_ids
    ]
    script = {"episode": 1, "title": "E1", "content_mode": "narration", "segments": segments}
    await asyncio.to_thread(project.save_script, "demo", script, "episode_1.json", validate=False)
    await asyncio.to_thread(
        project.update_project, "demo", lambda data: data.update(generation_mode="storyboard", grid_storyboard=True)
    )
    project_dir = project.get_project_path("demo")
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=scene_ids,
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="p",
        model="m",
        video_aspect_ratio="9:16",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    grid.reference_images = [ReferenceImage(path="characters/Alice.png", name="Alice", ref_type="character")]
    GridManager(project_dir).save(grid)
    uploaded = api.post(
        f"/api/v1/projects/demo/grids/{grid.id}/upload",
        files={"file": ("grid.png", solid_png_bytes((90, 30, 160), size=(360, 640)), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert _cleanup(api)["removed"] == 2
    assert not (project_dir / grid.grid_image_path).exists()
    assert not (project_dir / "characters" / "Alice.png").exists()

    split = api.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
    assert split.status_code == 200, split.text
    assert split.json()["updated_scene_ids"] == scene_ids
    storyboard = ArtifactCurrencyResolver(project_dir).compare(
        ArtifactKey.episode_storyboard(1, "E1S01"), artifact_path="storyboards/scene_E1S01.png"
    )
    assert storyboard.status is ArtifactStatus.CURRENT


async def test_settings_enable_remote_storage_and_switch_back_only_affect_new_versions(api, sessions, project, bucket):
    """经设置接口启用与切回：只配置连接不启用远端；没有连接不能启用；启用期间不能删除连接；
    切回本地后新版本照旧本地，已有远端版本按原定位读取与恢复，实际清理入口清理后仍可用。"""
    zh = {"Accept-Language": "zh"}

    def _settings() -> dict[str, Any]:
        resp = api.get("/api/v1/object-storage/connection")
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _choose(target: str):
        return api.put("/api/v1/object-storage/new-image-target", json={"target": target}, headers=zh)

    assert _settings()["new_image_target"] == "local"
    refused = _choose("object_storage")
    assert refused.status_code == 409
    assert "先配置对象存储连接" in refused.json()["detail"]

    # 只配置连接（上传中转）：新增图片仍保存在本地
    assert api.put("/api/v1/object-storage/connection", json=CONNECTION).status_code == 200
    assert _settings()["new_image_target"] == "local"
    image_a, image_b, image_c = (solid_png_bytes(rgb, size=(64, 64)) for rgb in ((1, 2, 3), (4, 5, 6), (7, 8, 9)))
    assert _upload(api, image_a).status_code == 200
    assert bucket.stored_keys() == []

    assert _choose("object_storage").json() == {"target": "object_storage"}
    assert _settings()["new_image_target"] == "object_storage"
    guarded = api.delete("/api/v1/object-storage/connection", headers=zh)
    assert guarded.status_code == 409  # 还没有正式对象，挡住删除的是保存位置本身
    assert "新增图片保存位置" in guarded.json()["detail"]
    assert _upload(api, image_b).status_code == 200

    assert _choose("local").status_code == 200
    assert _upload(api, image_c).status_code == 200
    record_a, record_b, record_c = _history(project)["versions"]
    assert "storage" not in record_a
    assert "storage" not in record_c
    assert record_b["storage"]["key"] == formal_object_key(record_b["content_digest"], ".png")
    assert bucket.stored_keys() == [record_b["storage"]["key"]]

    # 工作文件清理不碰中转副本，也不删除正式对象
    async with sessions() as session:
        connection = await ObjectStorageConnectionRepository(session).get()
        assert connection is not None
        session.add(
            RelayObject(
                connection_id=connection.id, endpoint=connection.endpoint, bucket=BUCKET, object_key="arcreel-relay/x"
            )
        )
        await session.commit()
    async with sessions() as session:
        assert len(list((await session.execute(select(RelayObject))).scalars())) == 1
    assert _cleanup(api)["removed"] == 1  # B 的快照工作文件
    async with sessions() as session:
        assert len(list((await session.execute(select(RelayObject))).scalars())) == 1
    assert bucket.stored_keys() == [record_b["storage"]["key"]]

    # 切回本地之后恢复远端版本 B：沿用原定位，不新增内容版本，也不重新上传
    puts = bucket.calls.count(("put", record_b["storage"]["key"]))
    restored = api.post("/api/v1/projects/demo/versions/characters/Alice/restore/2")
    assert restored.status_code == 200, restored.text
    assert (project.get_project_path("demo") / "characters" / "Alice.png").read_bytes() == image_b
    assert [record.get("storage") for record in _history(project)["versions"]] == [None, record_b["storage"], None]
    assert bucket.calls.count(("put", record_b["storage"]["key"])) == puts

    # 已有正式对象时，删除连接按正式对象挡下；保存位置改回本地也不解除
    assert api.delete("/api/v1/object-storage/connection").status_code == 409


async def test_end_frame_picked_from_an_evicted_remote_image_fetches_it_and_stays_local(api, sessions, project, bucket):
    """尾帧不建版本，始终本地保存；从缓存已清理的远端图片选取时先从对象存储取回原图。"""
    await asyncio.to_thread(_storyboard_upload, project)
    await _remote(sessions)
    image = solid_png_bytes((30, 60, 90), size=(64, 64))
    assert _upload(api, image).status_code == 200
    key = _history(project)["versions"][-1]["storage"]["key"]
    _cleanup(api)
    project_dir = project.get_project_path("demo")
    assert not (project_dir / "characters" / "Alice.png").exists()

    resp = api.post(
        "/api/v1/projects/demo/shots/E1S01/end-frame/select",
        json={"script_file": "episode_1.json", "source_path": "characters/Alice.png"},
    )

    assert resp.status_code == 200, resp.text
    assert bucket.calls.count(("get", key)) == 1
    assert (project_dir / resp.json()["end_frame_image"]).is_file()
    assert bucket.stored_keys() == [key]  # 尾帧快照不写对象存储
