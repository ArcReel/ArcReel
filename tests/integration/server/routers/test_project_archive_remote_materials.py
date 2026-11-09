"""归档与导入包含远端图片的项目：归档带真实内容，另一实例导入后按自己的全局设置保存，不依赖源实例的桶。

两个实例各有自己的项目目录、数据库与对象存储连接（同一个内存 TOS 替身里的两个桶）。真实编排：资产图经文件路由
上传（正式提交入口按全局设置选择保存位置），工作文件经对象存储路由的清理入口清理，归档经项目路由导出与导入，
预览与版本切换经公开文件路由与版本路由。只替换对象存储的网络边界：TOS SDK 在 ``create_sdk_client`` 换成内存桶。

归档同时带上图片的可用供应商素材关联，导入实例按推理凭证实际绑定的素材库账号选用（真实的绑定与选用规则，
调用通道只用一个报出推理凭证的替身）；源实例的关联记录直接写进它的数据库，不经供应商控制面。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.bindings import account_scope, confirm_binding, get_bindable_credential
from lib.asset_library.generation_inputs import choose_reference_image_assets, reference_image_sources
from lib.backends.provider_assets import REFERENCE_IMAGE_ROLE, InferenceCredentialRef
from lib.config.repository import SystemSettingRepository
from lib.db import get_async_session
from lib.db.models.asset_library import AssetLibraryAccount, ProviderAssetLink
from lib.db.models.object_storage import FormalObject, FormalObjectRef
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.material.formal_objects import MATERIAL_STORAGE_TARGET_SETTING
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import files, object_storage, projects, versions
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import bind_session_factory, use_object_storage_sdk, use_project_manager, use_projects_session_factory
from tests.integration.server.derivative_sheet_support import solid_png_bytes
from tests.integration.server.routers.test_remote_material_storage import MemoryBucket

SOURCE_BUCKET = "source-bucket"
TARGET_BUCKET = "target-bucket"
SOURCE_SK = "TEST-TOS-SECRET-SOURCE-3569"
TARGET_SK = "TEST-TOS-SECRET-TARGET-3569"
ASSET_AK = "TEST-ASSET-LIBRARY-AK-3649"
ASSET_SK = "TEST-ASSET-LIBRARY-SK-3649"
ARK_KEY = "TEST-ark-inference-key-3649"
ARK_DEFAULT = "https://ark.cn-beijing.volces.com/api/v3"


def _connection(bucket: str, secret: str) -> dict[str, str]:
    return {
        "region": "cn-beijing",
        "endpoint": "tos-cn-beijing.volces.com",
        "bucket": bucket,
        "access_key_id": f"TEST-TOS-AK-{bucket}",
        "secret_access_key": secret,
    }


@dataclass
class Instance:
    projects: ProjectManager
    sessions: async_sessionmaker[AsyncSession]
    api: TestClient


@contextmanager
def _serving(monkeypatch, root: Path, sessions: async_sessionmaker[AsyncSession]) -> Generator[Instance]:
    """启动一个实例：项目目录、数据库与路由都只属于它，退出时撤下全局替换。"""
    pm = ProjectManager(root / "projects")
    with monkeypatch.context() as patch:
        use_project_manager(patch, pm)
        bind_session_factory(patch, sessions)
        use_projects_session_factory(patch, sessions)
        app = FastAPI()

        async def _session():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_async_session] = _session
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        for router in (files.router, versions.router, object_storage.router, projects.router):
            app.include_router(router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        app.include_router(files.public_router, prefix="/api/v1")
        app.include_router(projects.self_auth_router, prefix="/api/v1")
        register_error_handlers(app)
        with TestClient(app) as api:
            yield Instance(pm, sessions, api)


def _memory_tos(monkeypatch) -> MemoryBucket:
    """两个实例共用的内存 TOS：对象按 (桶, key) 存放，各实例只经自己的连接访问自己的桶。"""
    memory = MemoryBucket()
    use_object_storage_sdk(monkeypatch, lambda config: memory)
    return memory


@pytest.fixture
def source_sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _use_storage(sessions, target: str, connection: dict[str, str] | None = None) -> None:
    async with sessions() as session:
        if connection is not None:
            await ObjectStorageConnectionRepository(session).save(kind="tos", **connection)
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, target)
        await session.commit()


def _upload(api: TestClient, content: bytes):
    response = api.post(
        "/api/v1/projects/demo/upload/character?name=Alice",
        files={"file": ("alice.png", content, "image/png")},
    )
    assert response.status_code == 200, response.text


def _cleanup(api: TestClient) -> int:
    response = api.post("/api/v1/object-storage/work-cache/cleanup", json={})
    assert response.status_code == 200, response.text
    return response.json()["removed"]


def _export(api: TestClient, scope: str):
    token = api.post("/api/v1/projects/demo/export/token", params={"scope": scope})
    if token.status_code != 200:
        return token
    return api.get(
        "/api/v1/projects/demo/export", params={"scope": scope, "download_token": token.json()["download_token"]}
    )


def _import(api: TestClient, archive: bytes):
    return api.post(
        "/api/v1/projects/import",
        files={"file": ("demo.zip", archive, "application/zip")},
        data={"confirm_report": "true"},
    )


def _history(pm: ProjectManager) -> list[dict[str, Any]]:
    path = pm.get_project_path("demo") / "versions" / "versions.json"
    return json.loads(path.read_text(encoding="utf-8"))["characters"]["Alice"]["versions"]


def _key(content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    return f"arcreel-materials/sha256/{digest[:2]}/{digest}.png"


async def _create_account(sessions, access_key_id: str) -> AssetLibraryAccount:
    async with sessions() as session:
        account = await AssetLibraryRepository(session).create_account(
            site="volcengine",
            name=access_key_id,
            region="cn-beijing",
            project_name="default",
            access_key_id=access_key_id,
            secret_access_key=ASSET_SK,
        )
        await session.commit()
        return account


async def _link(sessions, account: AssetLibraryAccount, record: dict[str, Any], *, state: str, asset_id: str | None):
    """源实例里图片版本 ``record`` 在账号范围内选定的关联或入库任务。"""
    scope = account_scope(account)
    async with sessions() as session:
        session.add(
            ProviderAssetLink(
                project_name="demo",
                content_version_id=record["content_version_id"],
                content_digest=record["content_digest"],
                resource_type="characters",
                resource_id="Alice",
                version=record["version"],
                locator_path=record["file"],
                origin="upload",
                account_id=account.id,
                account_identity=scope.account_identity,
                site=scope.site,
                region=scope.region,
                provider_project_name=scope.project_name,
                state=state,
                provider_asset_id=asset_id,
                submit_name=f"Alice-{state}",
                selected=True,
                check_attempts=0,
            )
        )
        await session.commit()


@dataclass
class _Backend:
    """调用通道替身：只报出实际使用的推理凭证与可接受素材引用的槽位。"""

    inference_credential: InferenceCredentialRef
    provider_asset_roles: frozenset[str] = frozenset({REFERENCE_IMAGE_ROLE})


async def _bind_inference_credential(sessions, access_key_id: str, credential_id: int | None = None) -> _Backend:
    """目标实例的推理凭证确认绑定到 ``access_key_id`` 对应的素材库账号，返回使用这条凭证的调用通道。"""
    account = await _create_account(sessions, access_key_id)
    async with sessions() as session:
        if credential_id is None:
            credential_id = (
                await CredentialRepository(session).create(provider="ark", name="主 Key", api_key=ARK_KEY)
            ).id
        bindable = await get_bindable_credential(session, "builtin", credential_id)
        assert bindable is not None
        await confirm_binding(session, account, bindable)
        await session.commit()
    return _Backend(InferenceCredentialRef("builtin", credential_id, ARK_KEY, None, ARK_DEFAULT))


async def _reference_choice(instance: Instance, sessions, backend: _Backend):
    project_path = instance.projects.get_project_path("demo")
    sources = reference_image_sources(project_path, [(project_path / "characters" / "Alice.png", "Alice")])
    [choice] = await choose_reference_image_assets(
        sessions, backend=backend, project_name="demo", project_path=project_path, sources=sources
    )
    return choice


def _with_manifest(archive: bytes, edit) -> bytes:
    """改写归档信封后重新打包，其余成员原样保留。"""
    source = zipfile.ZipFile(io.BytesIO(archive))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w") as package:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "demo/arcreel-export.json":
                manifest = json.loads(data)
                edit(manifest)
                data = json.dumps(manifest).encode()
            package.writestr(info, data)
    return output.getvalue()


@contextmanager
def _source_instance(monkeypatch, tmp_path, sessions) -> Generator[Instance]:
    """源实例：项目 demo 里有角色 Alice，还没有资产图。"""
    with _serving(monkeypatch, tmp_path / "source", sessions) as source:
        source.projects.create_project("demo")
        source.projects.create_project_metadata("demo", "demo", "Anime", "narration")
        source.projects.add_character("demo", "Alice", "银发少女")
        yield source


IMAGE_A = solid_png_bytes((200, 10, 10), size=(96, 96))
IMAGE_B = solid_png_bytes((10, 200, 10), size=(96, 96))


@pytest.mark.parametrize("target", ["local", "object_storage"])
async def test_archive_carries_remote_content_and_imports_into_target_storage(
    monkeypatch, tmp_path, source_sessions, file_db_factory, target
):
    bucket = _memory_tos(monkeypatch)
    with _source_instance(monkeypatch, tmp_path, source_sessions) as source:
        _upload(source.api, IMAGE_A)
        await _use_storage(source_sessions, "object_storage", _connection(SOURCE_BUCKET, SOURCE_SK))
        _upload(source.api, IMAGE_B)
        source_history = _history(source.projects)
        assert [record.get("storage", {}).get("bucket") for record in source_history] == [None, SOURCE_BUCKET]
        # 两个版本在同一账号里都有可用关联；B 在另一个账号里还有一条尚未入库的任务
        account = await _create_account(source_sessions, ASSET_AK)
        other_account = await _create_account(source_sessions, "TEST-ASSET-LIBRARY-AK-OTHER")
        await _link(source_sessions, account, source_history[0], state="active", asset_id="asset-a")
        await _link(source_sessions, account, source_history[1], state="active", asset_id="asset-b")
        await _link(source_sessions, other_account, source_history[1], state="pending", asset_id=None)
        assert _cleanup(source.api) == 2  # 远端版本 B 的正式文件与版本快照
        source_dir = source.projects.get_project_path("demo")
        assert not (source_dir / "characters" / "Alice.png").exists()

        archives = {}
        for scope in ("current", "full"):
            response = _export(source.api, scope)
            assert response.status_code == 200, response.text
            archives[scope] = response.content

    # 归档里是真实内容，不带源实例的定位、桶或凭证
    for scope, archive in archives.items():
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            members = {name: package.read(name) for name in package.namelist() if not name.endswith("/")}
        assert members["demo/characters/Alice.png"] == IMAGE_B
        assert members[f"demo/{source_history[1]['file']}"] == IMAGE_B
        assert (f"demo/{source_history[0]['file']}" in members) is (scope == "full")
        assert not any(
            secret.encode() in data
            for data in members.values()
            for secret in (SOURCE_BUCKET, SOURCE_SK, ASSET_AK, ASSET_SK)
        )
        packed = json.loads(members["demo/versions/versions.json"])["characters"]["Alice"]["versions"]
        assert all("storage" not in record for record in packed)
        # 只带范围内内容版本的可用关联，冻结的调用范围原样随带，不带账号记录 ID 与入库中的任务
        carried = json.loads(members["demo/arcreel-export.json"])["provider_asset_links"]
        assert [link["provider_asset_id"] for link in carried] == (
            ["asset-a", "asset-b"] if scope == "full" else ["asset-b"]
        )
        assert {
            (link["account_identity"], link["site"], link["region"], link["provider_project_name"]) for link in carried
        } == {(account_scope(account).account_identity, "volcengine", "cn-beijing", "default")}
        assert all("account_id" not in link for link in carried)
    assert [op for op, _key in bucket.calls].count("get") == 2  # 两个工作文件各取回一次，第二次导出沿用

    # 源实例的桶从此不可用：导入只凭归档里的内容
    bucket.objects = {location: data for location, data in bucket.objects.items() if location[0] != SOURCE_BUCKET}
    bucket.calls.clear()

    # 包被改过：版本 A 的关联摘要与包内图片对不上，导入时丢弃这条关联，图片照常导入
    def _tamper_first_link(manifest):
        manifest["provider_asset_links"][0]["content_digest"] = "0" * 64

    tampered = _with_manifest(archives["full"], _tamper_first_link)

    with _serving(monkeypatch, tmp_path / "target", file_db_factory) as other:
        if target == "object_storage":
            await _use_storage(file_db_factory, "object_storage", _connection(TARGET_BUCKET, TARGET_SK))
        response = _import(other.api, tampered)
        assert response.status_code == 200, response.text
        assert response.json()["project_name"] == "demo"

        imported = _history(other.projects)
        assert [record["content_version_id"] for record in imported] == [
            record["content_version_id"] for record in source_history
        ]
        assert all(call[0] != "get" for call in bucket.calls)

        async with file_db_factory() as session:
            [restored_link] = list((await session.execute(select(ProviderAssetLink))).scalars())
        frozen = account_scope(account)
        assert (restored_link.project_name, restored_link.content_version_id, restored_link.content_digest) == (
            "demo",
            imported[1]["content_version_id"],
            imported[1]["content_digest"],
        )
        assert (restored_link.state, restored_link.selected, restored_link.provider_asset_id) == (
            "active",
            True,
            "asset-b",
        )
        assert (restored_link.account_identity, restored_link.site, restored_link.region) == (
            frozen.account_identity,
            frozen.site,
            frozen.region,
        )
        assert (restored_link.provider_project_name, restored_link.origin, restored_link.account_id) == (
            "default",
            "upload",
            0,
        )

        # 推理凭证绑定到同一云账号身份的账号时选用这条关联；改绑到另一个身份后不适用，照常发送图片内容
        backend = await _bind_inference_credential(file_db_factory, ASSET_AK)
        choice = await _reference_choice(other, file_db_factory, backend)
        assert choice is not None
        assert choice.asset_uri == "asset://asset-b"
        await _bind_inference_credential(
            file_db_factory, "TEST-ASSET-LIBRARY-AK-ELSE", backend.inference_credential.credential_id
        )
        assert await _reference_choice(other, file_db_factory, backend) is None
        if target == "local":
            assert all("storage" not in record for record in imported)
            assert bucket.objects == {}
        else:
            async with file_db_factory() as session:
                connection = await ObjectStorageConnectionRepository(session).get()
                objects = list((await session.execute(select(FormalObject))).scalars())
                refs = list((await session.execute(select(FormalObjectRef))).scalars())
            assert connection is not None
            assert [record["storage"] for record in imported] == [
                {"kind": "object_storage", "connection_id": connection.id, "bucket": TARGET_BUCKET, "key": _key(image)}
                for image in (IMAGE_A, IMAGE_B)
            ]
            assert bucket.objects == {(TARGET_BUCKET, _key(IMAGE_A)): IMAGE_A, (TARGET_BUCKET, _key(IMAGE_B)): IMAGE_B}
            assert {row.state for row in objects} == {"stored"}
            assert sorted((ref.state, ref.project_name, ref.content_version_id) for ref in refs) == sorted(
                ("active", "demo", record["content_version_id"]) for record in imported
            )
            # 目标实例的远端图片同样可以清理缓存后预览、切换版本
            assert _cleanup(other.api) == 3

        preview = other.api.get("/api/v1/files/demo/characters/Alice.png")
        assert preview.status_code == 200
        assert preview.content == IMAGE_B
        restored = other.api.post("/api/v1/projects/demo/versions/characters/Alice/restore/1")
        assert restored.status_code == 200, restored.text
        formal = other.projects.get_project_path("demo") / "characters" / "Alice.png"
        assert formal.read_bytes() == IMAGE_A


@contextmanager
def _get_during_connection_read(monkeypatch, bucket, sessions):
    """Hold SDK reads until another download is executing its connection query, and require its cancellation."""
    engine = sessions.kw["bind"].sync_engine
    query_started, release_query = Event(), Event()
    invalidations = []
    reads = 0

    def pause_query():
        query_started.set()
        assert release_query.wait(10), "connection query was not cancelled"
        return 1

    def register_barrier(dbapi_connection, _record, _proxy):
        dbapi_connection.create_function("archive_query_barrier", 0, pause_query)

    def delay_second_read(_conn, _cursor, statement, parameters, _context, _executemany):
        nonlocal reads
        if "FROM object_storage_connection" in statement:
            reads += 1
            if reads == 2:
                statement += " AND archive_query_barrier() = 1"
        return statement, parameters

    def release_on_invalidate(_connection, _record, error):
        invalidations.append(error)
        release_query.set()

    get_object = bucket.get_object

    def get_after_query_started(**kwargs):
        assert query_started.wait(10), "second connection query did not start"
        return get_object(**kwargs)

    event.listen(engine, "checkout", register_barrier)
    event.listen(engine, "before_cursor_execute", delay_second_read, retval=True)
    event.listen(engine, "invalidate", release_on_invalidate)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(bucket, "get_object", get_after_query_started)
            yield
        assert len(invalidations) == 1
        assert isinstance(invalidations[0], asyncio.CancelledError)
    finally:
        release_query.set()
        event.remove(engine, "checkout", register_barrier)
        event.remove(engine, "before_cursor_execute", delay_second_read)
        event.remove(engine, "invalidate", release_on_invalidate)


async def test_export_fails_without_partial_archive_when_remote_content_is_unreadable(
    monkeypatch, tmp_path, file_db_factory
):
    # Cancelled queries discard connections, so this scenario needs a persistent database.
    bucket = _memory_tos(monkeypatch)
    with _source_instance(monkeypatch, tmp_path, file_db_factory) as source:
        await _use_storage(file_db_factory, "object_storage", _connection(SOURCE_BUCKET, SOURCE_SK))
        _upload(source.api, IMAGE_B)
        assert _cleanup(source.api) == 2

        bucket.fail.add("get")
        with _get_during_connection_read(monkeypatch, bucket, file_db_factory):
            assert _export(source.api, "full").status_code == 502

        bucket.fail.clear()
        [location] = list(bucket.objects)
        bucket.objects[location] = IMAGE_A  # 对象被替换：内容与记录的摘要不符
        assert _export(source.api, "current").status_code == 502
        assert not (source.projects.get_project_path("demo") / "characters" / "Alice.png").exists()


async def test_failed_remote_import_installs_nothing_and_removes_new_objects(
    monkeypatch, tmp_path, source_sessions, file_db_factory
):
    bucket = _memory_tos(monkeypatch)
    with _source_instance(monkeypatch, tmp_path, source_sessions) as source:
        _upload(source.api, IMAGE_A)
        _upload(source.api, IMAGE_B)
        response = _export(source.api, "full")
        assert response.status_code == 200, response.text
        archive = response.content

    with _serving(monkeypatch, tmp_path / "target", file_db_factory) as other:
        await _use_storage(file_db_factory, "object_storage", _connection(TARGET_BUCKET, TARGET_SK))
        put = bucket.put_object
        uploads: list[str] = []

        def _second_put_fails(*, bucket: str, key: str, content: bytes, content_type: str | None = None):
            uploads.append(key)
            if len(uploads) == 2:
                memory.fail.add("put")
            return put(bucket=bucket, key=key, content=content, content_type=content_type)

        memory = bucket
        monkeypatch.setattr(bucket, "put_object", _second_put_fails)
        failed = other.api.post(
            "/api/v1/projects/import",
            files={"file": ("demo.zip", archive, "application/zip")},
            data={"confirm_report": "true"},
            headers={"Accept-Language": "zh"},
        )
        assert failed.status_code == 502, failed.text
        # 新导入没有「原来的版本」：说明这次导入没有写入任何项目
        assert failed.json()["detail"] == "图片没能保存到对象存储，这次导入没有写入任何项目。检查对象存储连接后重试"
        assert not other.projects.projects_dir.joinpath("demo").exists()
        assert bucket.objects == {}  # 第一张已保存的图片没有被任何版本引用，补偿删除
        async with file_db_factory() as session:
            assert list((await session.execute(select(FormalObjectRef))).scalars()) == []
            states = [row.state for row in (await session.execute(select(FormalObject))).scalars()]
        assert states
        assert set(states) == {"deleted"}


@pytest.mark.parametrize("target", ["local", "object_storage"])
async def test_current_archive_of_a_generated_image_follows_the_target_storage(
    monkeypatch, tmp_path, source_sessions, file_db_factory, target
):
    """仅当前版本的包裁掉了生成资产图的版本记录：导入到对象存储时补登内容版本并保存到目标桶；导入到本地时照旧不补登。"""
    bucket = _memory_tos(monkeypatch)
    with _source_instance(monkeypatch, tmp_path, source_sessions) as source:
        _upload(source.api, IMAGE_A)
        source_dir = source.projects.get_project_path("demo")
        (source_dir / "characters" / "Alice.png").write_bytes(IMAGE_B)
        VersionManager(source_dir).add_version(
            "characters", "Alice", "generated portrait", source_file=source_dir / "characters" / "Alice.png"
        )
        response = _export(source.api, "current")
        assert response.status_code == 200, response.text
        archive = response.content
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        assert "Alice" not in json.loads(package.read("demo/versions/versions.json")).get("characters", {})

    with _serving(monkeypatch, tmp_path / "target", file_db_factory) as other:
        if target == "object_storage":
            await _use_storage(file_db_factory, "object_storage", _connection(TARGET_BUCKET, TARGET_SK))
        imported = _import(other.api, archive)
        assert imported.status_code == 200, imported.text
        versions = json.loads(
            (other.projects.get_project_path("demo") / "versions" / "versions.json").read_text(encoding="utf-8")
        )
        if target == "local":
            assert "Alice" not in versions.get("characters", {})
            assert bucket.objects == {}
            return

        [record] = versions["characters"]["Alice"]["versions"]
        assert record["content_digest"] == hashlib.sha256(IMAGE_B).hexdigest()
        assert (record["storage"]["bucket"], record["storage"]["key"]) == (TARGET_BUCKET, _key(IMAGE_B))
        assert list(bucket.objects) == [(TARGET_BUCKET, _key(IMAGE_B))]
        async with file_db_factory() as session:
            refs = list((await session.execute(select(FormalObjectRef))).scalars())
        assert [(ref.project_name, ref.state, ref.content_version_id) for ref in refs] == [
            ("demo", "active", record["content_version_id"])
        ]
        assert _cleanup(other.api) == 2
        preview = other.api.get("/api/v1/files/demo/characters/Alice.png")
        assert preview.status_code == 200
        assert preview.content == IMAGE_B
