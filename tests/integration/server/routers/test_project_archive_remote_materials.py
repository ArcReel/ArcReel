"""归档与导入包含远端图片的项目：归档带真实内容，另一实例导入后按自己的全局设置保存，不依赖源实例的桶。

两个实例各有自己的项目目录、数据库与对象存储连接（同一个内存 TOS 替身里的两个桶）。真实编排：资产图经文件路由
上传（正式提交入口按全局设置选择保存位置），工作文件经对象存储路由的清理入口清理，归档经项目路由导出与导入，
预览与版本切换经公开文件路由与版本路由。只替换对象存储的网络边界：TOS SDK 在 ``create_sdk_client`` 换成内存桶。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.config.repository import SystemSettingRepository
from lib.db import get_async_session
from lib.db.models.object_storage import FormalObject, FormalObjectRef
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
        assert not any(SOURCE_BUCKET.encode() in data or SOURCE_SK.encode() in data for data in members.values())
        packed = json.loads(members["demo/versions/versions.json"])["characters"]["Alice"]["versions"]
        assert all("storage" not in record for record in packed)
    assert [op for op, _key in bucket.calls].count("get") == 2  # 两个工作文件各取回一次，第二次导出沿用

    # 源实例的桶从此不可用：导入只凭归档里的内容
    bucket.objects = {location: data for location, data in bucket.objects.items() if location[0] != SOURCE_BUCKET}
    bucket.calls.clear()

    with _serving(monkeypatch, tmp_path / "target", file_db_factory) as other:
        if target == "object_storage":
            await _use_storage(file_db_factory, "object_storage", _connection(TARGET_BUCKET, TARGET_SK))
        response = _import(other.api, archives["full"])
        assert response.status_code == 200, response.text
        assert response.json()["project_name"] == "demo"

        imported = _history(other.projects)
        assert [record["content_version_id"] for record in imported] == [
            record["content_version_id"] for record in source_history
        ]
        assert all(call[0] != "get" for call in bucket.calls)
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


async def test_export_fails_without_partial_archive_when_remote_content_is_unreadable(
    monkeypatch, tmp_path, source_sessions
):
    bucket = _memory_tos(monkeypatch)
    with _source_instance(monkeypatch, tmp_path, source_sessions) as source:
        await _use_storage(source_sessions, "object_storage", _connection(SOURCE_BUCKET, SOURCE_SK))
        _upload(source.api, IMAGE_B)
        assert _cleanup(source.api) == 2

        bucket.fail.add("get")
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
        failed = _import(other.api, archive)
        assert failed.status_code == 502, failed.text
        assert not other.projects.projects_dir.joinpath("demo").exists()
        assert bucket.objects == {}  # 第一张已保存的图片没有被任何版本引用，补偿删除
        async with file_db_factory() as session:
            assert list((await session.execute(select(FormalObjectRef))).scalars()) == []
            states = [row.state for row in (await session.execute(select(FormalObject))).scalars()]
        assert states
        assert set(states) == {"deleted"}
