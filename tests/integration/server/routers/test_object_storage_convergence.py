"""对象存储切换与删除的「收敛回本地」与旧桶失联强制断开高层集成测试（Spec #4204 / Issue #4211）。

保留真实 FastAPI 路由、SQLite 数据库、正式提交、项目/回收站/物理墓碑/全局资产文件操作与后台维护循环，
仅在 ``create_sdk_client`` 一处替换 S3 网络边界。
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import io
import json
import os
from collections.abc import Generator, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.artifacts.version_manager import VersionManager
from lib.asset_library import ingest
from lib.asset_library.ingest import run_once as run_ingest_once
from lib.config.repository import SystemSettingRepository
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.models.asset import Asset, AssetDerivative
from lib.db.models.asset_library import AssetLibraryAccount, ProviderAssetLink
from lib.db.models.object_storage import FormalObject, FormalObjectRef, RelayObject
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import UnprocessableError
from lib.infra.async_thread import EventLoopBridge
from lib.infra.data_root_layout import DataRootLayout
from lib.material import remote_holders
from lib.material.convergence import (
    RelocationTargetConfig,
    force_disconnect_connection,
    inspect_relocation_plan,
    run_relocation,
)
from lib.material.formal_objects import (
    MATERIAL_STORAGE_TARGET_SETTING,
    ObjectStorageRelocatingError,
    add_reference,
    collect_unreferenced_objects,
    retain_formal_objects,
    sweep_formal_objects,
)
from lib.material.global_library import cleanup_global_work_files
from lib.material.remote_holders import PromoteResult, RemoteContentItem
from lib.material.storage import ObjectLocator
from lib.material.work_files import cleanup_work_cache, mark_connection_relocating, unmark_connection_relocating
from lib.object_storage.relay import sweep_relay_objects
from lib.project import deleted_projects
from lib.project import project_trash as project_trash_store
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import (
    assets,
    characters,
    derivative_uploads,
    file_view,
    files,
    object_storage,
    project_trash,
    shot_uploads,
    versions,
)
from server.services.project import project_retirement
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import (
    bind_session_factory,
    intercept_file_replace,
    s3_client_error,
    use_assets_router_sessions,
    use_object_storage_sdk,
    use_project_manager,
)
from tests.integration.server.derivative_sheet_support import solid_png_bytes

OLD_BUCKET = "old-formal-bucket"
NEW_BUCKET = "new-formal-bucket"
OLD_CONN = {
    "kind": "s3",
    "region": "cn-beijing",
    "endpoint": "https://tos-s3-cn-beijing.volces.com",
    "bucket": OLD_BUCKET,
    "addressing_style": "virtual",
    "access_key_id": "TEST-OLD-AK-0001",
    "secret_access_key": "TEST-OLD-SK-0001",
}
NEW_CONN = {
    "kind": "s3",
    "region": "us-east-1",
    "endpoint": "https://s3.us-east-1.amazonaws.com",
    "bucket": NEW_BUCKET,
    "addressing_style": "auto",
    "access_key_id": "TEST-NEW-AK-0002",
    "secret_access_key": "TEST-NEW-SK-0002",
}


class MemoryS3:
    """多桶内存 S3 替身，支持按桶/操作/key 注入故障并记录调用轨迹。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.fail_ops: set[str] = set()
        self.denied_get_keys: set[str] = set()
        self.fail_get_keys: set[str] = set()
        self.unreachable_buckets: set[str] = set()
        self.unreachable_get_keys: set[str] = set()

    def _record(self, op: str, bucket: str, key: str) -> None:
        self.calls.append((op, bucket, key))
        if bucket in self.unreachable_buckets or (op == "get" and key in self.unreachable_get_keys):
            raise EndpointConnectionError(endpoint_url=f"https://{bucket}.unreachable.invalid")
        if op in self.fail_ops or (op == "get" and key in self.fail_get_keys):
            raise s3_client_error(503, "ServiceUnavailable", f"injected {op} failure")

    def head_bucket(self, *, Bucket: str):
        self._record("head_bucket", Bucket, "")
        return {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str | None = None):
        self._record("put", Bucket, Key)
        self.objects[(Bucket, Key)] = Body
        return {"ETag": '"etag"'}

    def head_object(self, *, Bucket: str, Key: str):
        self._record("head", Bucket, Key)
        if (Bucket, Key) not in self.objects:
            raise s3_client_error(404, "NoSuchKey")
        return {"ContentLength": len(self.objects[(Bucket, Key)]), "ETag": '"etag"', "ContentType": None}

    def get_object(self, *, Bucket: str, Key: str):
        self._record("get", Bucket, Key)
        if Key in self.denied_get_keys:
            raise s3_client_error(403, "AccessDenied")
        if (Bucket, Key) not in self.objects:
            raise s3_client_error(404, "NoSuchKey")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket: str, Key: str):
        self._record("delete", Bucket, Key)
        self.objects.pop((Bucket, Key), None)
        return {}

    def generate_presigned_url(self, client_method: str, *, Params: dict[str, Any], ExpiresIn: int) -> str:
        self._record("presign", Params["Bucket"], Params["Key"])
        return f"https://{Params['Bucket']}.example/{Params['Key']}?expires={ExpiresIn}"


@pytest.fixture
def s3(monkeypatch) -> MemoryS3:
    memory = MemoryS3()
    use_object_storage_sdk(monkeypatch, lambda _config: memory)
    return memory


@pytest.fixture
def convergence_sessions(db_engine: AsyncEngine, monkeypatch) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    bind_session_factory(monkeypatch, factory)
    return factory


@pytest.fixture
def convergence_pm(tmp_path: Path, monkeypatch) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    for name in ("demo", "other"):
        manager.create_project(name)
        manager.create_project_metadata(name, name, "Anime", "narration")
        manager.add_character(name, "Alice", "银发少女")
    use_project_manager(monkeypatch, manager)
    return manager


@pytest.fixture
def convergence_api(convergence_sessions, convergence_pm, monkeypatch) -> Generator[TestClient]:
    use_assets_router_sessions(monkeypatch, convergence_sessions)
    app = FastAPI()

    async def _session():
        async with convergence_sessions() as session:
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
    ):
        app.include_router(router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(files.public_router, prefix="/api/v1")
    register_error_handlers(app)
    with TestClient(app) as client:
        yield client


async def _enable_remote(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        conn = await ObjectStorageConnectionRepository(session).save(
            kind="s3",
            region=OLD_CONN["region"],
            endpoint=OLD_CONN["endpoint"],
            bucket=OLD_CONN["bucket"],
            addressing_style="virtual",
            access_key_id=OLD_CONN["access_key_id"],
            secret_access_key=OLD_CONN["secret_access_key"],
        )
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, "object_storage")
        await session.commit()
        return conn.id


def _upload_character(api: TestClient, content: bytes, project_name: str = "demo", name: str = "Alice"):
    resp = api.post(
        f"/api/v1/projects/{project_name}/upload/character?name={name}",
        files={"file": ("sheet.png", content, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _cleanup_cache(api: TestClient) -> dict[str, int]:
    resp = api.post("/api/v1/object-storage/work-cache/cleanup", json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _character_versions(pm: ProjectManager, project_name: str = "demo", name: str = "Alice") -> list[dict[str, Any]]:
    project_dir = pm.get_project_path(project_name)
    manager = VersionManager(project_dir)
    if manager.sqlite.is_enabled():
        return manager.resource_records([("characters", name)])["characters"][name]["versions"]
    data = json.loads(manager.versions_file.read_text(encoding="utf-8"))
    return data["characters"][name]["versions"]


async def test_multi_holder_convergence_switch_localizes_active_trash_tombstone_and_global_derivatives(
    convergence_api: TestClient,
    convergence_sessions: async_sessionmaker[AsyncSession],
    convergence_pm: ProjectManager,
    s3: MemoryS3,
):
    """同一正式对象及独立对象跨活跃项目、三类回收站、同名重建前的物理墓碑与全局资产+衍生图，全部收敛回本地并原子切换新桶。"""
    api, sessions, pm = convergence_api, convergence_sessions, convergence_pm
    old_conn_id = await _enable_remote(sessions)

    shared_bytes = solid_png_bytes((20, 120, 220), size=(64, 64))
    trash_ver_bytes = solid_png_bytes((220, 40, 40), size=(64, 64))
    deleted_asset_bytes = solid_png_bytes((40, 200, 80), size=(64, 64))
    script_overwrite_bytes = solid_png_bytes((180, 140, 20), size=(64, 64))
    deriv_bytes = solid_png_bytes((120, 30, 180), size=(64, 64))

    _upload_character(api, trash_ver_bytes, "demo", "Alice")
    _upload_character(api, shared_bytes, "demo", "Alice")
    v1_file = _character_versions(pm, "demo", "Alice")[0]["file"]
    del_ver = api.delete("/api/v1/projects/demo/file-view", params={"path": v1_file})
    assert del_ver.status_code == 200, del_ver.text

    pm.add_character("demo", "Bob", "配角")
    _upload_character(api, deleted_asset_bytes, "demo", "Bob")
    _cleanup_cache(api)
    del_bob = api.delete("/api/v1/projects/demo/characters/Bob")
    assert del_bob.status_code == 200, del_bob.text

    demo_dir = pm.get_project_path("demo")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [
                {
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
            ],
        },
        "episode_1.json",
        validate=False,
    )
    shot_up = api.post(
        "/api/v1/projects/demo/shots/E1S01/upload/storyboard?script_file=episode_1.json",
        files={"file": ("shot.png", script_overwrite_bytes, "image/png")},
    )
    assert shot_up.status_code == 200, shot_up.text
    _cleanup_cache(api)
    sb_history = VersionManager(demo_dir).resource_records([("storyboards", "E1S01")])
    sb_rel = sb_history["storyboards"]["E1S01"]["versions"][0]["file"]
    overwrite_rec = project_trash_store.record_deletion(
        demo_dir,
        {"kind": project_trash_store.SCRIPT_OVERWRITE_KIND, "episode": 1, "path": "scripts/episode_1.json"},
        [],
        archived_bytes={
            "scripts/episode_1.json": b"{}",
            "versions/versions.json": json.dumps(sb_history, ensure_ascii=False).encode(),
        },
        remote_files=(sb_rel, "storyboards/scene_E1S01.png"),
        download_only=True,
    )
    VersionManager(demo_dir).detach_resources([("storyboards", "E1S01")])

    _upload_character(api, shared_bytes, "other", "Alice")
    _cleanup_cache(api)
    other_dir = pm.get_project_path("other")
    layout = DataRootLayout(pm.data_root)
    async with project_retirement.retiring_project(sessions, other_dir) as tombstone_id:
        tombstone_dir = deleted_projects.move_in(layout, other_dir, tombstone_id)
    pm.create_project("other")
    pm.create_project_metadata("other", "other", "Anime", "narration")
    pm.add_character("other", "Alice", "全新同名项目")
    new_other_local = solid_png_bytes((250, 250, 250), size=(64, 64))
    new_other_sheet = pm.get_project_path("other") / "characters" / "Alice.png"
    new_other_sheet.write_bytes(new_other_local)

    add_deriv = api.post(
        "/api/v1/projects/demo/characters/Alice/derivatives",
        json={"name": "winter", "description": "冬装造型"},
    )
    assert add_deriv.status_code == 200, add_deriv.text
    up_deriv = api.post(
        "/api/v1/projects/demo/characters/Alice/derivatives/winter/upload",
        files={"file": ("winter.png", deriv_bytes, "image/png")},
    )
    assert up_deriv.status_code == 200, up_deriv.text

    saved_global = api.post(
        "/api/v1/assets/from-project",
        json={
            "project_name": "demo",
            "resource_type": "character",
            "resource_id": "Alice",
            "override_name": "GlobalAlice",
        },
    )
    assert saved_global.status_code == 200, saved_global.text
    global_asset_id = saved_global.json()["asset"]["id"]
    global_asset_path = saved_global.json()["asset"]["image_path"]
    deriv_path = saved_global.json()["asset"]["derivatives"][0]["image_path"]

    _cleanup_cache(api)
    assert not (demo_dir / "characters" / "Alice.png").exists()
    assert not (pm.data_root / global_asset_path).exists()
    assert not (pm.data_root / deriv_path).exists()
    assert not (tombstone_dir / "characters" / "Alice.png").exists()

    plan_resp = api.get("/api/v1/object-storage/connection/relocation-plan")
    assert plan_resp.status_code == 200, plan_resp.text
    plan = plan_resp.json()
    assert plan["in_use_object_count"] == 5
    assert plan["download_object_count"] == 4
    assert plan["estimated_download_bytes"] > 0
    assert plan["in_flight_uploads"] == 0

    run_resp = api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={
            "expected_connection_id": old_conn_id,
            "mode": "switch",
            "delete_remote_objects": True,
            "target": NEW_CONN,
        },
    )
    assert run_resp.status_code == 200, run_resp.text
    result = run_resp.json()
    assert result["status"] == "completed"
    assert result["connection"]["bucket"] == NEW_BUCKET
    assert result["new_image_target"] == "object_storage"
    assert result["cleanup_failures"] == {}
    assert result["downloaded_objects"] == 4
    assert result["reused_local_objects"] == 1

    async with sessions() as session:
        new_conn = await ObjectStorageConnectionRepository(session).get()
        assert new_conn is not None
        assert new_conn.id > old_conn_id
        assert new_conn.relocating is False
        assert list((await session.execute(select(FormalObject))).scalars()) == []
        assert list((await session.execute(select(FormalObjectRef))).scalars()) == []
        g_asset = await session.get(Asset, global_asset_id)
        assert g_asset is not None
        assert g_asset.image_storage is None
        [g_deriv] = list((await session.execute(select(AssetDerivative))).scalars())
        assert g_deriv.image_storage is None
    assert not any(bucket == OLD_BUCKET for bucket, _key in s3.objects)

    _cleanup_cache(api)
    assert (demo_dir / "characters" / "Alice.png").read_bytes() == shared_bytes
    assert all("storage" not in v for v in _character_versions(pm, "demo", "Alice"))
    assert api.get("/api/v1/files/demo/characters/Alice.png").content == shared_bytes

    assert (tombstone_dir / "characters" / "Alice.png").read_bytes() == shared_bytes
    assert new_other_sheet.read_bytes() == new_other_local

    assert (pm.data_root / global_asset_path).read_bytes() == shared_bytes
    assert (pm.data_root / deriv_path).read_bytes() == deriv_bytes
    global_cleanup = await cleanup_global_work_files(pm.data_root, min_idle=timedelta(0), session_factory=sessions)
    assert global_cleanup.removed == 0
    assert (pm.data_root / global_asset_path).read_bytes() == shared_bytes
    assert (pm.data_root / deriv_path).read_bytes() == deriv_bytes

    trash_records = api.get("/api/v1/projects/demo/trash").json()["records"]
    bob_rec = next(r for r in trash_records if r["object"].get("kind") == "asset")
    ver_rec = next(r for r in trash_records if r["object"].get("kind") == "version")
    assert api.post(f"/api/v1/projects/demo/trash/{bob_rec['id']}/restore").status_code == 200
    assert (demo_dir / "characters" / "Bob.png").read_bytes() == deleted_asset_bytes
    assert api.post(f"/api/v1/projects/demo/trash/{ver_rec['id']}/restore").status_code == 200
    dl = api.get(
        f"/api/v1/projects/demo/trash/{overwrite_rec.id}/files",
        params={"path": "storyboards/scene_E1S01.png"},
    )
    assert dl.status_code == 200
    assert dl.content == script_overwrite_bytes


async def test_interrupted_convergence_blocks_writes_allows_key_rotation_and_supports_retry_and_cancel(
    convergence_api: TestClient,
    convergence_sessions: async_sessionmaker[AsyncSession],
    convergence_pm: ProjectManager,
    s3: MemoryS3,
):
    """中途下载失败不破坏旧连接与已完成转正文件；收敛期间阻断新写与新签名但允许同桶轮换密钥，支持重试完成或取消。"""
    api, sessions, pm = convergence_api, convergence_sessions, convergence_pm
    conn_id = await _enable_remote(sessions)
    img_a = solid_png_bytes((11, 22, 33), size=(64, 64))
    img_b = solid_png_bytes((44, 55, 66), size=(64, 64))
    _upload_character(api, img_a, "demo", "Alice")
    _upload_character(api, img_b, "other", "Alice")
    _cleanup_cache(api)

    digest_b = hashlib.sha256(img_b).hexdigest()
    key_b = f"arcreel-materials/sha256/{digest_b[:2]}/{digest_b}.png"
    s3.fail_get_keys.add(key_b)

    async with sessions() as session:
        account = AssetLibraryAccount(
            site="volcengine",
            name="acct",
            region="cn-beijing",
            project_name="default",
            access_key_id="AK-LIB",
            secret_access_key="SK-LIB",
        )
        session.add(account)
        await session.flush()
        other_rec = _character_versions(pm, "other", "Alice")[0]
        link = ProviderAssetLink(
            project_name="other",
            content_version_id=other_rec["content_version_id"],
            content_digest=other_rec["content_digest"],
            resource_type="characters",
            resource_id="Alice",
            version=other_rec["version"],
            locator_path=other_rec["file"],
            origin="upload",
            account_id=account.id,
            account_identity=ingest.account_identity(account.site, account.access_key_id),
            site="volcengine",
            region="cn-beijing",
            provider_project_name="default",
            state="pending",
            submit_name="Alice-pending",
            selected=True,
            check_attempts=0,
            next_check_at=utc_now() - timedelta(minutes=1),
        )
        session.add(link)
        await session.commit()

    async def start_relocation() -> None:
        async with sessions() as session:
            await ObjectStorageConnectionRepository(session).set_relocating(conn_id, delete_remote=False, mode="switch")
            await session.commit()

    bridge = EventLoopBridge.capture()

    def project_path_during_relocation(name: str) -> Path:
        bridge.run(start_relocation())
        return pm.get_project_path(name)

    now = utc_now()
    await run_ingest_once(sessions, now=now, project_path_of=project_path_during_relocation)
    async with sessions() as session:
        deferred_link = await session.get(ProviderAssetLink, link.id)
        assert deferred_link is not None
        assert deferred_link.state == "pending"
        assert deferred_link.next_check_at is not None
        assert deferred_link.next_check_at.replace(tzinfo=now.tzinfo) == now + ingest.CHECK_INITIAL_DELAY
        await ObjectStorageConnectionRepository(session).clear_relocating()
        await session.commit()

    first = api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={"expected_connection_id": conn_id, "mode": "switch", "delete_remote_objects": False, "target": NEW_CONN},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == "incomplete"
    assert first_body["unreachable"] is True
    assert first_body["completed_objects"] == 1
    assert first_body["failed_objects"] == 1

    env = api.get("/api/v1/object-storage/connection").json()
    assert env["connection"]["bucket"] == OLD_BUCKET
    assert env["connection"]["relocating"] is True
    assert env["connection"]["relocate_delete_remote"] is False
    run_url = "/api/v1/object-storage/connection/relocation/run"
    assert (
        api.post(
            run_url,
            json={
                "expected_connection_id": conn_id,
                "mode": "switch",
                "target": NEW_CONN,
                "delete_remote_objects": True,
            },
        ).status_code
        == 409
    )
    resumed = api.post(run_url, json={"expected_connection_id": conn_id, "mode": "switch", "target": NEW_CONN})
    assert resumed.status_code == 200
    assert resumed.json()["delete_remote_objects"] is False
    assert "storage" not in _character_versions(pm, "demo", "Alice")[0]
    assert "storage" in _character_versions(pm, "other", "Alice")[0]

    blocked_upload = api.post(
        "/api/v1/projects/demo/upload/character?name=Alice",
        files={"file": ("new.png", solid_png_bytes((9, 9, 9)), "image/png")},
    )
    assert blocked_upload.status_code == 409
    assert api.put("/api/v1/object-storage/new-image-target", json={"target": "object_storage"}).status_code == 409
    other_storage = _character_versions(pm, "other", "Alice")[0]["storage"]
    with pytest.raises(ObjectStorageRelocatingError):
        await add_reference(
            ObjectLocator(other_storage["connection_id"], other_storage["bucket"], other_storage["key"]),
            "demo",
            "cv-new",
            session_factory=sessions,
        )
    calls_before_ingest = len(s3.calls)
    next_check = now + ingest.CHECK_INITIAL_DELAY
    await run_ingest_once(sessions, now=next_check, project_path_of=pm.get_project_path)
    async with sessions() as session:
        deferred_link = await session.get(ProviderAssetLink, link.id)
        assert deferred_link is not None
        assert deferred_link.state == "pending"
        assert deferred_link.next_check_at is not None
        assert deferred_link.next_check_at.replace(tzinfo=now.tzinfo) == next_check + ingest.CHECK_INITIAL_DELAY
    assert [op for op, _b, _k in s3.calls[calls_before_ingest:] if op in ("put", "presign")] == []

    rotated = api.put(
        "/api/v1/object-storage/connection",
        json={**OLD_CONN, "access_key_id": "TEST-OLD-AK-ROTATED", "secret_access_key": "TEST-OLD-SK-ROTATED"},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["relocating"] is True
    assert rotated.json()["in_use_object_count"] == env["connection"]["in_use_object_count"] > 0
    before_probe = list(s3.calls)
    assert api.post("/api/v1/object-storage/connection/test").status_code == 409
    assert s3.calls == before_probe

    cancelled = api.post("/api/v1/object-storage/connection/relocation/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["connection"]["relocating"] is False
    assert "storage" not in _character_versions(pm, "demo", "Alice")[0]

    s3.fail_get_keys.clear()
    second = api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={"expected_connection_id": conn_id, "mode": "switch", "delete_remote_objects": False, "target": NEW_CONN},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "completed"
    assert second.json()["connection"]["bucket"] == NEW_BUCKET
    assert "storage" not in _character_versions(pm, "other", "Alice")[0]
    assert (pm.get_project_path("other") / "characters" / "Alice.png").read_bytes() == img_b


async def test_delete_remote_false_issues_zero_remote_deletes_under_concurrent_maintenance_and_cache_cleanup(
    convergence_api: TestClient,
    convergence_sessions: async_sessionmaker[AsyncSession],
    convergence_pm: ProjectManager,
    s3: MemoryS3,
):
    """选择保留旧桶文件（delete_remote=false）时，收敛全程与并发回收/扫盘/缓存清理对旧桶发起零次 DELETE。"""
    api, sessions, pm = convergence_api, convergence_sessions, convergence_pm
    conn_id = await _enable_remote(sessions)
    img_a = solid_png_bytes((10, 80, 140), size=(64, 64))
    img_b = solid_png_bytes((140, 80, 10), size=(64, 64))
    _upload_character(api, img_a, "demo", "Alice")
    _upload_character(api, img_b, "other", "Alice")
    global_resp = api.post(
        "/api/v1/assets",
        data={"type": "character", "name": "KeepGlobal"},
        files={"image": ("g.png", img_a, "image/png")},
    )
    assert global_resp.status_code == 200
    global_path = global_resp.json()["asset"]["image_path"]

    async with sessions() as session:
        session.add(
            RelayObject(
                connection_id=conn_id,
                endpoint=OLD_CONN["endpoint"],
                bucket=OLD_BUCKET,
                object_key="arcreel-relay/old-relay.png",
                state="retained",
                url_expires_at=utc_now() - timedelta(days=2),
            )
        )
        orphan = FormalObject(
            connection_id=conn_id,
            endpoint=OLD_CONN["endpoint"],
            bucket=OLD_BUCKET,
            object_key="arcreel-materials/orphan.png",
            content_digest="a" * 64,
            size=10,
            state="stored",
            next_attempt_at=utc_now() - timedelta(hours=1),
        )
        session.add(orphan)
        await session.commit()
        orphan_id = orphan.id
    s3.objects[(OLD_BUCKET, "arcreel-relay/old-relay.png")] = b"relay"
    s3.objects[(OLD_BUCKET, "arcreel-materials/orphan.png")] = b"orphan"

    _cleanup_cache(api)
    digest_b = hashlib.sha256(img_b).hexdigest()
    key_b = f"arcreel-materials/sha256/{digest_b[:2]}/{digest_b}.png"
    s3.fail_get_keys.add(key_b)

    scanning = asyncio.Event()
    resume = asyncio.Event()

    class PausedHolder:
        async def enumerate_remote_items(self, connection_id, bucket, *, strict_trash=True):
            scanning.set()
            await resume.wait()
            return []

        async def promote_item(self, item, data):
            raise AssertionError("empty holder")

        async def reconcile_holder(self, holder_key, items, session_factory):
            raise AssertionError("empty holder")

    run = asyncio.create_task(
        run_relocation(
            DataRootLayout(pm.data_root),
            sessions,
            mode="delete",
            delete_remote_objects=False,
            extra_holders=[PausedHolder()],
        )
    )
    try:
        async with asyncio.timeout(10):
            await scanning.wait()
            assert await collect_unreferenced_objects([orphan_id], session_factory=sessions) == 0
            assert await sweep_formal_objects(sessions, now=utc_now() + timedelta(days=2)) == 0
    finally:
        resume.set()
        res1 = await run
    assert res1.status == "incomplete"

    assert await retain_formal_objects(pm.projects_dir, pm.data_root / "deleted_projects", sessions) == 0
    assert await sweep_relay_objects(sessions, now=utc_now() + timedelta(days=2)) == 0
    await cleanup_work_cache(pm.projects_dir, min_idle=timedelta(0), session_factory=sessions)
    await cleanup_global_work_files(pm.data_root, min_idle=timedelta(0), session_factory=sessions)

    assert (pm.data_root / global_path).read_bytes() == img_a

    s3.fail_get_keys.clear()
    res2 = api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={"expected_connection_id": conn_id, "mode": "delete", "delete_remote_objects": False},
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["status"] == "completed"
    assert res2.json()["connection"] is None
    assert res2.json()["new_image_target"] == "local"

    delete_calls = [call for call in s3.calls if call[0] == "delete"]
    assert delete_calls == []
    assert (OLD_BUCKET, "arcreel-relay/old-relay.png") in s3.objects
    assert (OLD_BUCKET, "arcreel-materials/orphan.png") in s3.objects
    assert (OLD_BUCKET, key_b) in s3.objects


async def test_external_overwrite_conflict_is_preserved_and_disk_full_retries_cleanly(
    convergence_api: TestClient,
    convergence_sessions: async_sessionmaker[AsyncSession],
    convergence_pm: ProjectManager,
    s3: MemoryS3,
):
    """当前选中本地文件被外部改写时保留冲突文件不覆盖，同时版本快照落盘转正；磁盘满报错可重试。"""
    api, sessions, pm = convergence_api, convergence_sessions, convergence_pm
    conn_id = await _enable_remote(sessions)
    original_bytes = solid_png_bytes((70, 170, 70), size=(64, 64))
    _upload_character(api, original_bytes, "demo", "Alice")
    _cleanup_cache(api)

    demo_dir = pm.get_project_path("demo")
    formal_path = demo_dir / "characters" / "Alice.png"
    manual_edit = b"external-user-manual-edit"
    formal_path.write_bytes(manual_edit)

    enospc_bytes = solid_png_bytes((15, 25, 35), size=(32, 32))
    enospc_digest = hashlib.sha256(enospc_bytes).hexdigest()
    enospc_key = f"arcreel-materials/zzz-probe/{enospc_digest}.png"
    s3.objects[(OLD_BUCKET, enospc_key)] = enospc_bytes

    class _DiskFullOnceHolder:
        def __init__(self) -> None:
            self.failed_once = False
            self.stored: ObjectLocator | None = ObjectLocator(conn_id, OLD_BUCKET, enospc_key)

        async def enumerate_remote_items(
            self, connection_id: int, bucket: str, *, strict_trash: bool = True
        ) -> list[RemoteContentItem]:
            del strict_trash
            if self.stored is None or self.stored.connection_id != connection_id or self.stored.bucket != bucket:
                return []
            return [
                RemoteContentItem(
                    holder_kind="disk_full_probe",
                    holder_key="demo",
                    display_label="demo / disk-full-probe",
                    path="probe.png",
                    locator=self.stored,
                    digest=enospc_digest,
                    content_version_id=None,
                    local_paths=(),
                    metadata={},
                )
            ]

        async def promote_item(self, item: RemoteContentItem, data: bytes) -> PromoteResult:
            del item, data
            if not self.failed_once:
                self.failed_once = True
                raise OSError(errno.ENOSPC, "No space left on device")
            self.stored = None
            return PromoteResult(promoted=True)

        async def reconcile_holder(
            self, holder_key: str, items: Sequence[RemoteContentItem], session_factory: async_sessionmaker[AsyncSession]
        ) -> None:
            return None

    disk_holder = _DiskFullOnceHolder()
    layout = DataRootLayout(pm.data_root)
    disk_fail = await run_relocation(
        layout,
        sessions,
        mode="delete",
        delete_remote_objects=False,
        extra_holders=[disk_holder],
    )
    assert disk_fail.status == "incomplete"
    assert disk_fail.error_kind == "disk_full"
    assert disk_fail.unreachable is False
    assert disk_fail.conflicts_preserved == 1

    retry_ok = await run_relocation(
        layout,
        sessions,
        mode="switch",
        target=RelocationTargetConfig(
            region=NEW_CONN["region"],
            endpoint=NEW_CONN["endpoint"],
            bucket=NEW_BUCKET,
            access_key_id=NEW_CONN["access_key_id"],
            secret_access_key=NEW_CONN["secret_access_key"],
        ),
        extra_holders=[disk_holder],
    )
    assert retry_ok.status == "completed"
    assert retry_ok.delete_remote_objects is False
    assert disk_holder.stored is None
    assert formal_path.read_bytes() == manual_edit
    ver = _character_versions(pm, "demo", "Alice")[0]
    assert "storage" not in ver
    assert (demo_dir / ver["file"]).read_bytes() == original_bytes


async def test_relay_only_switch_keeps_local_target_and_corrupted_trash_blocks_relocation(
    convergence_api: TestClient,
    convergence_sessions: async_sessionmaker[AsyncSession],
    convergence_pm: ProjectManager,
    s3: MemoryS3,
):
    """纯中转切桶保持新增图片保存位置为本地；删除连接固定切回本地；损坏回收站记录明确阻断收敛。"""
    api, sessions, pm = convergence_api, convergence_sessions, convergence_pm
    assert api.put("/api/v1/object-storage/connection", json=OLD_CONN).status_code == 200
    assert api.get("/api/v1/object-storage/connection").json()["new_image_target"] == "local"
    plan_url = "/api/v1/object-storage/connection/relocation-plan"
    old_id = api.get(plan_url).json()["connection_id"]
    run_url = "/api/v1/object-storage/connection/relocation/run"
    assert api.post(run_url, json={"mode": "delete"}).status_code == 422
    rotation = api.post(
        run_url,
        json={
            "expected_connection_id": old_id,
            "mode": "switch",
            "target": {**OLD_CONN, "access_key_id": "TEST-ROTATED"},
        },
    )
    assert rotation.status_code == 422

    switched = api.put("/api/v1/object-storage/connection", json={**NEW_CONN, "delete_remote_objects": True})
    assert switched.status_code == 200, switched.text
    env = api.get("/api/v1/object-storage/connection").json()
    assert env["connection"]["bucket"] == NEW_BUCKET
    assert env["new_image_target"] == "local"

    for route, payload in [
        ("run", {"mode": "delete"}),
        ("force-disconnect", {"confirm": True, "acknowledged_unrecoverable_keys": []}),
    ]:
        stale = api.post(
            f"/api/v1/object-storage/connection/relocation/{route}", json={**payload, "expected_connection_id": old_id}
        )
        assert stale.status_code == 409, stale.text
    assert (
        api.post(
            "/api/v1/object-storage/connection/relocation/cancel", json={"expected_connection_id": old_id}
        ).status_code
        == 409
    )
    assert (
        api.delete(
            "/api/v1/object-storage/connection",
            params={"expected_connection_id": old_id, "reset_new_image_target": True},
        ).status_code
        == 409
    )
    assert api.get(plan_url).json()["connection_id"] != old_id
    assert api.get("/api/v1/object-storage/connection").json() == env

    assert api.put("/api/v1/object-storage/new-image-target", json={"target": "object_storage"}).status_code == 200
    deleted = api.delete("/api/v1/object-storage/connection?delete_remote_objects=true&reset_new_image_target=true")
    assert deleted.status_code == 200, deleted.text
    assert api.get("/api/v1/object-storage/connection").json() == {
        "connection": None,
        "new_image_target": "local",
        "pending_ingest_tasks": 0,
    }

    conn_id = await _enable_remote(sessions)
    corrupted_dir = project_trash_store.trash_dir(pm.get_project_path("demo")) / "20261128T120000Z-deadbeef"
    corrupted_dir.mkdir(parents=True, exist_ok=True)
    (corrupted_dir / "record.json").write_text("{broken json", encoding="utf-8")

    plan_resp = api.get("/api/v1/object-storage/connection/relocation-plan", headers={"Accept-Language": "zh"})
    assert plan_resp.status_code == 409
    assert "20261128T120000Z-deadbeef" in plan_resp.json()["detail"]

    forced = api.post(
        "/api/v1/object-storage/connection/relocation/force-disconnect",
        json={"confirm": True, "expected_connection_id": conn_id, "acknowledged_unrecoverable_keys": []},
    )
    assert forced.status_code == 409
    assert corrupted_dir.name in forced.json()["detail"]
    assert api.get("/api/v1/object-storage/connection").json()["connection"]["relocating"] is False

    run_resp = api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={"expected_connection_id": conn_id, "mode": "delete", "delete_remote_objects": False},
        headers={"Accept-Language": "zh"},
    )
    assert run_resp.status_code == 409
    status = api.get("/api/v1/object-storage/connection/relocation-status").json()
    assert status["phase"] == "failed"
    assert status["last_error_kind"]
    assert "20261128T120000Z-deadbeef" in run_resp.json()["detail"]


async def test_force_disconnect_salvages_valid_local_copies_across_holders_and_reports_unrecoverable(
    convergence_api: TestClient,
    convergence_sessions: async_sessionmaker[AsyncSession],
    convergence_pm: ProjectManager,
    s3: MemoryS3,
):
    """旧桶永久失联时，强制断开按 SHA-256 跨持有方抢救所有尚存有效本地副本为永久本地文件，列出并保留无法拉回项。"""
    api, sessions, pm = convergence_api, convergence_sessions, convergence_pm
    conn_id = await _enable_remote(sessions)
    salvageable_bytes = solid_png_bytes((30, 180, 180), size=(64, 64))
    unrecoverable_bytes = solid_png_bytes((190, 60, 60), size=(64, 64))

    _upload_character(api, salvageable_bytes, "demo", "Alice")
    _upload_character(api, salvageable_bytes, "other", "Alice")
    _cleanup_cache(api)
    layout = DataRootLayout(pm.data_root)
    other_dir = pm.get_project_path("other")
    async with project_retirement.retiring_project(sessions, other_dir) as tombstone_id:
        tombstone_dir = deleted_projects.move_in(layout, other_dir, tombstone_id)

    global_resp = api.post(
        "/api/v1/assets",
        data={"type": "character", "name": "SalvageSource"},
        files={"image": ("s.png", salvageable_bytes, "image/png")},
    )
    assert global_resp.status_code == 200
    global_id = global_resp.json()["asset"]["id"]
    global_path = global_resp.json()["asset"]["image_path"]
    assert (pm.data_root / global_path).is_file()

    pm.add_character("demo", "Bob", "缺失图")
    _upload_character(api, unrecoverable_bytes, "demo", "Bob")
    demo_dir = pm.get_project_path("demo")
    bob_ver = _character_versions(pm, "demo", "Bob")[0]
    (demo_dir / "characters" / "Bob.png").unlink()
    (demo_dir / bob_ver["file"]).unlink()

    s3.unreachable_buckets.add(OLD_BUCKET)

    force_url = "/api/v1/object-storage/connection/relocation/force-disconnect"
    declined = api.post(
        force_url, json={"confirm": True, "expected_connection_id": conn_id, "acknowledged_unrecoverable_keys": []}
    )
    assert declined.status_code == 409
    assert "storage" in _character_versions(pm, "demo", "Alice")[0]
    assert not (demo_dir / "characters/Alice.png").exists()
    assert api.get("/api/v1/object-storage/connection").json()["connection"]["relocating"] is False

    failed_run = api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={"expected_connection_id": conn_id, "mode": "delete", "delete_remote_objects": False},
    )
    assert failed_run.status_code == 200
    failed_body = failed_run.json()
    assert failed_body["status"] == "incomplete"
    assert failed_body["unreachable"] is True
    assert len(failed_body["unrecoverable_items"]) == 1
    assert failed_body["unrecoverable_items"][0]["digest"] == hashlib.sha256(unrecoverable_bytes).hexdigest()

    assert api.post(force_url, json={"confirm": True, "expected_connection_id": conn_id}).status_code == 422
    assert api.post(force_url, json={"confirm": True, "acknowledged_unrecoverable_keys": []}).status_code == 422
    changed = api.post(
        force_url, json={"confirm": True, "expected_connection_id": conn_id, "acknowledged_unrecoverable_keys": []}
    )
    assert changed.status_code == 409
    current = api.get("/api/v1/object-storage/connection").json()["connection"]
    assert (current["id"], current["relocating"], current["relocate_delete_remote"], current["relocate_mode"]) == (
        conn_id,
        True,
        False,
        "delete",
    )

    assert (
        api.post(
            "/api/v1/object-storage/connection/relocation/force-disconnect",
            json={"confirm": False, "expected_connection_id": conn_id, "acknowledged_unrecoverable_keys": []},
        ).status_code
        == 400
    )

    forced = api.post(
        "/api/v1/object-storage/connection/relocation/force-disconnect",
        json={
            "confirm": True,
            "expected_connection_id": conn_id,
            "acknowledged_unrecoverable_keys": [
                item["confirmation_key"] for item in failed_body["unrecoverable_items"]
            ],
        },
    )
    assert forced.status_code == 200, forced.text
    forced_body = forced.json()
    assert forced_body["new_image_target"] == "local"
    assert len(forced_body["unrecoverable_items"]) == 1
    assert forced_body["unrecoverable_items"][0]["digest"] == hashlib.sha256(unrecoverable_bytes).hexdigest()

    assert api.get("/api/v1/object-storage/connection").json() == {
        "connection": None,
        "new_image_target": "local",
        "pending_ingest_tasks": 0,
    }

    assert (demo_dir / "characters" / "Alice.png").read_bytes() == salvageable_bytes
    assert "storage" not in _character_versions(pm, "demo", "Alice")[0]
    assert (tombstone_dir / "characters" / "Alice.png").read_bytes() == salvageable_bytes
    assert (pm.data_root / global_path).read_bytes() == salvageable_bytes
    async with sessions() as session:
        g_asset = await session.get(Asset, global_id)
        assert g_asset is not None
        assert g_asset.image_storage is None

    assert "storage" in _character_versions(pm, "demo", "Bob")[0]
    assert api.get("/api/v1/files/demo/characters/Bob.png").status_code == 409


async def test_extra_holder_extension_slot_participates_in_plan_relocation_and_force_disconnect(
    convergence_sessions: async_sessionmaker[AsyncSession],
    convergence_pm: ProjectManager,
    s3: MemoryS3,
):
    """为 Spec 3 无版本项目媒体预留的 extra_holders 扩展位可无缝参与影响评估、收敛转正与强制断开抢救。"""
    sessions, pm = convergence_sessions, convergence_pm
    conn_id = await _enable_remote(sessions)
    raw = solid_png_bytes((5, 5, 200), size=(32, 32))
    digest = hashlib.sha256(raw).hexdigest()
    key = f"arcreel-materials/sha256/{digest[:2]}/{digest}.png"
    s3.objects[(OLD_BUCKET, key)] = raw

    class _CustomUnversionedHolder:
        def __init__(self) -> None:
            self.stored: ObjectLocator | None = ObjectLocator(conn_id, OLD_BUCKET, key)
            self.written_bytes: bytes | None = None

        async def enumerate_remote_items(
            self, connection_id: int, bucket: str, *, strict_trash: bool = True
        ) -> list[RemoteContentItem]:
            del strict_trash
            if self.stored is None or self.stored.connection_id != connection_id or self.stored.bucket != bucket:
                return []
            return [
                RemoteContentItem(
                    holder_kind="unversioned_end_frame",
                    holder_key="demo",
                    display_label="demo / end_frame/E1S01",
                    path="end_frames/E1S01.png",
                    locator=self.stored,
                    digest=digest,
                    content_version_id=None,
                    local_paths=(),
                    metadata={},
                )
            ]

        async def promote_item(self, item: RemoteContentItem, data: bytes) -> PromoteResult:
            assert item.digest == digest
            self.written_bytes = data
            self.stored = None
            return PromoteResult(promoted=True)

        async def reconcile_holder(
            self, holder_key: str, items: Sequence[RemoteContentItem], session_factory: async_sessionmaker[AsyncSession]
        ) -> None:
            return None

    holder = _CustomUnversionedHolder()
    layout = DataRootLayout(pm.data_root)

    plan = await inspect_relocation_plan(layout, sessions, extra_holders=[holder])
    assert plan.in_use_object_count == 1
    assert plan.download_object_count == 1

    res = await run_relocation(
        layout,
        sessions,
        mode="delete",
        delete_remote_objects=True,
        extra_holders=[holder],
    )
    assert res.status == "completed"
    assert holder.stored is None
    assert holder.written_bytes == raw

    conn_id_2 = await _enable_remote(sessions)
    holder.stored = ObjectLocator(conn_id_2, OLD_BUCKET, key)
    force_plan = await inspect_relocation_plan(layout, sessions, extra_holders=[holder])
    forced = await force_disconnect_connection(
        layout,
        sessions,
        acknowledged_unrecoverable_keys=[item.confirmation_key for item in force_plan.unrecoverable_items],
        extra_holders=[holder],
    )
    assert len(forced.unrecoverable_items) == 1
    assert forced.unrecoverable_items[0].holder_kind == "unversioned_end_frame"


async def test_cleanup_preserves_files_during_relocation_startup(
    convergence_api, convergence_sessions, convergence_pm, s3
):
    conn_id = await _enable_remote(convergence_sessions)
    data = solid_png_bytes((3, 7, 11))
    _upload_character(convergence_api, data, "demo", "Alice")
    target = convergence_pm.get_project_path("demo") / "characters/Alice.png"
    mark_connection_relocating(conn_id)
    try:
        assert _cleanup_cache(convergence_api)["removed"] == 0
        assert target.read_bytes() == data
    finally:
        unmark_connection_relocating(conn_id)
    assert _cleanup_cache(convergence_api)["removed"] > 0


async def test_relocation_continues_after_nonconsecutive_network_errors(convergence_api, convergence_sessions, s3):
    conn_id = await _enable_remote(convergence_sessions)
    for index in range(6):
        _upload_character(convergence_api, solid_png_bytes((index, 20, 30)), "demo", "Alice")
    _cleanup_cache(convergence_api)
    keys = sorted(key for bucket, key in s3.objects if bucket == OLD_BUCKET)
    s3.unreachable_get_keys.update(keys[::2])

    result = convergence_api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={"expected_connection_id": conn_id, "mode": "delete", "delete_remote_objects": False},
    ).json()
    assert result["status"] == "incomplete"
    assert result["failed_objects"] == result["downloaded_objects"] == 3


@pytest.mark.parametrize("force", [False, True])
async def test_relocation_uses_second_copy_if_first_disappears(
    convergence_api, convergence_sessions, convergence_pm, s3, monkeypatch, force
):
    conn_id = await _enable_remote(convergence_sessions)
    data = solid_png_bytes((1, 7, 3))
    _upload_character(convergence_api, data, "demo", "Alice")
    project_dir = convergence_pm.get_project_path("demo")
    snapshot = project_dir / _character_versions(convergence_pm, "demo", "Alice")[0]["file"]
    read_bytes = Path.read_bytes

    def evict_before_read(path):
        if path == snapshot:
            path.unlink()
        return read_bytes(path)

    base = "/api/v1/object-storage/connection/relocation"
    s3.unreachable_buckets.add(OLD_BUCKET)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", evict_before_read)
        if force:
            response = convergence_api.post(
                f"{base}/force-disconnect",
                json={"expected_connection_id": conn_id, "confirm": True, "acknowledged_unrecoverable_keys": []},
            )
            assert response.status_code == 200, response.text
            assert response.json()["salvaged_objects"] == 1
        else:
            response = convergence_api.post(
                f"{base}/run",
                json={"expected_connection_id": conn_id, "mode": "delete", "delete_remote_objects": False},
            )
            assert response.json()["status"] == "completed"
            assert response.json()["reused_local_objects"] == 1
    assert snapshot.read_bytes() == data
    assert all(op != "get" for op, _, _ in s3.calls)


@pytest.mark.parametrize("record_persisted", [False, True])
async def test_trash_manifest_retry_after_record_write(
    convergence_api, convergence_sessions, convergence_pm, s3, monkeypatch, record_persisted
):
    api, pm = convergence_api, convergence_pm
    conn_id = await _enable_remote(convergence_sessions)
    data = solid_png_bytes((10, 20, 30))
    _upload_character(api, data, "demo", "Alice")
    _cleanup_cache(api)
    assert api.delete("/api/v1/projects/demo/characters/Alice").status_code == 200
    holder = remote_holders.ActiveProjectsHolder(DataRootLayout(pm.data_root))
    [item] = await holder.enumerate_remote_items(conn_id, OLD_BUCKET)
    record_dir = project_trash_store.trash_dir(pm.get_project_path("demo")) / item.metadata["record_id"]
    record_path = record_dir / "record.json"
    original = json.loads(record_path.read_text())
    files_root = record_dir / project_trash_store.FILES_DIRNAME
    rel = item.metadata["formal_rel"]
    prematerialized = files_root / rel
    prematerialized.parent.mkdir(parents=True, exist_ok=True)
    prematerialized.write_bytes(b"edited")
    replace = os.replace

    observed_records = []

    def interrupt_manifest(source, target):
        if Path(target).name == "versions.json" and not observed_records:
            observed_records.append(json.loads(record_path.read_text()))
            raise OSError(errno.ENOSPC, "No space left on device")
        replace(source, target)

    with monkeypatch.context() as patch:
        intercept_file_replace(patch, interrupt_manifest)
        with pytest.raises(OSError, match="No space left on device"):
            await holder.promote_item(item, data)
    [recorded] = observed_records
    assert {rel, item.path} <= set(recorded["files"])
    assert recorded["size"] == original["size"] + len(data) + len(b"edited")
    record_path.write_text(json.dumps(recorded if record_persisted else original))
    [retry_item] = await holder.enumerate_remote_items(conn_id, OLD_BUCKET)
    retry = await holder.promote_item(retry_item, data)
    assert retry.promoted
    assert retry.conflict_preserved
    assert json.loads(record_path.read_text()) == recorded
    assert not await holder.enumerate_remote_items(conn_id, OLD_BUCKET)


@pytest.mark.parametrize(("listed", "recorded_size"), [(False, 0), (True, 0), (True, None)])
async def test_single_trash_version_counts_existing_file_once(
    convergence_api, convergence_sessions, convergence_pm, s3, listed, recorded_size
):
    conn_id = await _enable_remote(convergence_sessions)
    data = solid_png_bytes((11, 22, 33))
    _upload_character(convergence_api, data, "demo", "Alice")
    _upload_character(convergence_api, solid_png_bytes((10, 20, 30)), "demo", "Alice")
    file_rel = _character_versions(convergence_pm, "demo", "Alice")[0]["file"]
    _cleanup_cache(convergence_api)
    deleted = convergence_api.delete("/api/v1/projects/demo/file-view", params={"path": file_rel})
    assert deleted.status_code == 200
    record_dir = project_trash_store.trash_dir(convergence_pm.get_project_path("demo")) / deleted.json()["record"]["id"]
    target = record_dir / project_trash_store.FILES_DIRNAME / file_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    record_file = record_dir / "record.json"
    raw = json.loads(record_file.read_bytes())
    raw["files"] = [file_rel] if listed else []
    raw["size"] = len(data) if recorded_size is None else recorded_size
    record_file.write_text(json.dumps(raw))

    result = convergence_api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={"expected_connection_id": conn_id, "mode": "delete", "delete_remote_objects": False},
    )
    assert result.json()["status"] == "completed"
    record = json.loads(record_file.read_bytes())
    assert record["files"].count(file_rel) == 1
    assert record["size"] == len(data)


async def test_force_disconnect_rescans_projects_retired_during_salvage(
    convergence_api, convergence_sessions, convergence_pm, s3, monkeypatch
):
    conn_id = await _enable_remote(convergence_sessions)
    _upload_character(convergence_api, solid_png_bytes((3, 2, 1)), "demo", "Alice")
    project_dir = convergence_pm.get_project_path("demo")
    layout = DataRootLayout(convergence_pm.data_root)

    async def retire() -> None:
        async with project_retirement.retiring_project(convergence_sessions, project_dir) as tombstone_id:
            await asyncio.to_thread(deleted_projects.move_in, layout, project_dir, tombstone_id)

    read_bytes = Path.read_bytes

    def retire_after_read(path: Path) -> bytes:
        data = read_bytes(path)
        if path.is_relative_to(project_dir) and path.suffix == ".png":
            convergence_api.portal.call(retire)
        return data

    monkeypatch.setattr(Path, "read_bytes", retire_after_read)
    url = "/api/v1/object-storage/connection"
    result = convergence_api.post(
        f"{url}/relocation/force-disconnect",
        json={"expected_connection_id": conn_id, "confirm": True, "acknowledged_unrecoverable_keys": []},
    )
    assert result.status_code == 409, result.text
    connection = convergence_api.get(url).json()["connection"]
    assert connection["id"] == conn_id
    assert connection["relocating"] is False
    plan = convergence_api.get(f"{url}/relocation-plan").json()
    assert plan["in_use_reference_count"] == plan["reusable_local_object_count"] == 1


async def test_force_disconnect_blocks_db_reference_missed_during_project_restore(
    convergence_api, convergence_sessions, convergence_pm, s3, monkeypatch
):
    conn_id = await _enable_remote(convergence_sessions)
    data = solid_png_bytes((4, 3, 2))
    _upload_character(convergence_api, data, "demo", "Alice")
    _upload_character(convergence_api, data, "other", "Alice")
    _cleanup_cache(convergence_api)
    plan = convergence_api.get("/api/v1/object-storage/connection/relocation-plan").json()
    assert len({item["object_key"] for item in plan["unrecoverable_items"]}) == 1
    [acknowledged] = [
        item["confirmation_key"] for item in plan["unrecoverable_items"] if item["holder_label"].startswith("other /")
    ]
    project_dir = convergence_pm.get_project_path("demo")
    layout = DataRootLayout(convergence_pm.data_root)

    async def retire() -> str:
        async with project_retirement.retiring_project(convergence_sessions, project_dir) as tombstone:
            await asyncio.to_thread(deleted_projects.move_in, layout, project_dir, tombstone)
        return tombstone

    async def restore(tombstone: str) -> None:
        restored = await asyncio.to_thread(deleted_projects.move_out, layout, tombstone)
        await project_retirement.restore_retired_project(
            convergence_sessions, tombstone, restored, sdk_config_dir=layout.root
        )

    iterdir = Path.iterdir
    restores = []

    def list_during_restore(path):
        if path != layout.projects_dir:
            return iterdir(path)
        tombstone = convergence_api.portal.call(retire)
        children = list(iterdir(path))
        convergence_api.portal.call(restore, tombstone)
        restores.append(tombstone)
        return iter(children)

    url = "/api/v1/object-storage/connection"
    with monkeypatch.context() as patch:
        patch.setattr(Path, "iterdir", list_during_restore)
        result = convergence_api.post(
            f"{url}/relocation/force-disconnect",
            json={
                "expected_connection_id": conn_id,
                "confirm": True,
                "acknowledged_unrecoverable_keys": [acknowledged],
            },
        )
    assert len(restores) == 2
    assert result.status_code == 409, result.text
    connection = convergence_api.get(url).json()["connection"]
    assert connection["id"] == conn_id
    assert connection["in_use_object_count"] == 1
    assert connection["relocating"] is False


async def test_force_disconnect_blocks_in_flight_uploads(convergence_api, convergence_sessions):
    conn_id = await _enable_remote(convergence_sessions)
    async with convergence_sessions() as session:
        session.add(
            FormalObject(
                connection_id=conn_id,
                endpoint=OLD_CONN["endpoint"],
                bucket=OLD_BUCKET,
                object_key="pending.png",
                content_digest="a" * 64,
                size=1,
                state="uploading",
            )
        )
        await session.commit()
    url = "/api/v1/object-storage/connection"
    response = convergence_api.post(
        f"{url}/relocation/force-disconnect",
        json={"expected_connection_id": conn_id, "confirm": True, "acknowledged_unrecoverable_keys": []},
    )
    assert response.status_code == 409
    conn = convergence_api.get(url).json()["connection"]
    assert (conn["id"], conn["relocating"], conn["relocate_delete_remote"], conn["relocate_mode"]) == (
        conn_id,
        False,
        None,
        None,
    )
    assert convergence_api.get(f"{url}/relocation-plan").json()["reusable_local_object_count"] == 0
    result = convergence_api.post(
        f"{url}/relocation/run", json={"expected_connection_id": conn_id, "mode": "delete"}
    ).json()
    assert convergence_api.get(f"{url}/relocation-status").json()["failed_objects"] == result["failed_objects"] == 1


async def test_invalid_trash_snapshot_blocks_scan_and_purged_record_stays_absent(
    convergence_api, convergence_sessions, convergence_pm, s3, tmp_path
):
    conn_id = await _enable_remote(convergence_sessions)
    data = solid_png_bytes((10, 20, 30))
    _upload_character(convergence_api, data, "demo", "Alice")
    _cleanup_cache(convergence_api)
    assert convergence_api.delete("/api/v1/projects/demo/characters/Alice").status_code == 200
    holder = remote_holders.ActiveProjectsHolder(DataRootLayout(convergence_pm.data_root))
    [item] = await holder.enumerate_remote_items(conn_id, OLD_BUCKET)
    record_dir = project_trash_store.trash_dir(convergence_pm.get_project_path("demo")) / item.metadata["record_id"]
    manifest = record_dir / project_trash_store.SNAPSHOT_DIRNAME / "versions/versions.json"
    versions_data = json.loads(manifest.read_text())
    versions_data["characters"]["Alice"]["versions"][0]["content_digest"] = ""
    manifest.write_text(json.dumps(versions_data))
    with pytest.raises(remote_holders.CorruptedTrashRecordError):
        await holder.enumerate_remote_items(conn_id, OLD_BUCKET)
    record_dir.rename(tmp_path / "purged")
    assert not (await holder.promote_item(item, data)).promoted
    assert not record_dir.exists()


async def test_object_access_denied_continues_downloading_other_keys(convergence_api, convergence_sessions, s3):
    conn_id = await _enable_remote(convergence_sessions)
    for index in range(3):
        _upload_character(convergence_api, solid_png_bytes((index, 20, 30)))
    _cleanup_cache(convergence_api)
    keys = sorted(key for bucket, key in s3.objects if bucket == OLD_BUCKET)
    s3.denied_get_keys.add(keys[0])
    result = convergence_api.post(
        "/api/v1/object-storage/connection/relocation/run",
        json={"expected_connection_id": conn_id, "mode": "delete", "delete_remote_objects": False},
    ).json()
    assert result["status"] == "incomplete"
    assert result["failed_objects"] == 1
    assert result["promoted_references"] == result["downloaded_objects"] == 2


@pytest.mark.parametrize(("field", "length"), [("region", 65), ("bucket", 256)])
async def test_oversized_target_preserves_old_objects(
    convergence_api, convergence_sessions, convergence_pm, s3, field, length
):
    conn_id = await _enable_remote(convergence_sessions)
    key = "arcreel-relay/old.png"
    async with convergence_sessions() as session:
        session.add(
            RelayObject(connection_id=conn_id, endpoint=OLD_CONN["endpoint"], bucket=OLD_BUCKET, object_key=key)
        )
        await session.commit()
    s3.objects[(OLD_BUCKET, key)] = b"retained"
    url = "/api/v1/object-storage/connection"
    target = {**NEW_CONN, field: "a" * length}
    assert convergence_api.put(url, json=target).status_code == 422
    response = convergence_api.post(
        f"{url}/relocation/run", json={"mode": "switch", "expected_connection_id": conn_id, "target": target}
    )
    assert response.status_code == 422
    with pytest.raises(UnprocessableError):
        await run_relocation(
            DataRootLayout(convergence_pm.data_root),
            convergence_sessions,
            mode="switch",
            target=RelocationTargetConfig(**target),
        )
    assert convergence_api.get(url).json()["connection"]["id"] == conn_id
    assert s3.objects[(OLD_BUCKET, key)] == b"retained"
    assert all(op != "delete" for op, _, _ in s3.calls)


async def test_force_disconnect_reconciles_promoted_project_on_disk_full(
    convergence_api, convergence_sessions, convergence_pm, s3, monkeypatch
):
    conn_id = await _enable_remote(convergence_sessions)
    data = solid_png_bytes((31, 22, 13))
    for project in ("demo", "other"):
        _upload_character(convergence_api, data, project)
    layout = DataRootLayout(convergence_pm.data_root)
    holder = remote_holders.ActiveProjectsHolder(layout)
    first, second = await holder.enumerate_remote_items(conn_id, OLD_BUCKET)
    target = layout.projects_dir / second.holder_key / second.path
    target.unlink()
    original = Path.mkdir

    def disk_full(path, *args, **kwargs):
        if path == target.parent:
            raise OSError(errno.ENOSPC, "No space left on device")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", disk_full)
        with pytest.raises(OSError, match="No space left"):
            await force_disconnect_connection(layout, convergence_sessions, acknowledged_unrecoverable_keys=[])
    async with convergence_sessions() as session:
        refs = await session.scalars(
            select(FormalObjectRef.project_name).where(FormalObjectRef.state.in_(("active", "trashed")))
        )
        assert set(refs) == {second.holder_key}
    assert first.holder_key != second.holder_key
    result = await force_disconnect_connection(layout, convergence_sessions, acknowledged_unrecoverable_keys=[])
    assert result.salvaged_references == 1


async def test_episode_snapshot_promotes_only_owned_grid(convergence_api, convergence_sessions, convergence_pm, s3):
    conn_id = await _enable_remote(convergence_sessions)
    data = solid_png_bytes((10, 20, 30))
    _upload_character(convergence_api, data)
    _cleanup_cache(convergence_api)
    assert convergence_api.delete("/api/v1/projects/demo/characters/Alice").status_code == 200
    holder = remote_holders.ActiveProjectsHolder(DataRootLayout(convergence_pm.data_root))
    [source] = await holder.enumerate_remote_items(conn_id, OLD_BUCKET)
    record_dir = project_trash_store.trash_dir(convergence_pm.get_project_path("demo")) / source.metadata["record_id"]
    record_path = record_dir / "record.json"
    record = json.loads(record_path.read_bytes())
    record.update(object={"kind": "episode", "id": 1}, files=["grids/owned.json"], size=0)
    record_path.write_text(json.dumps(record))
    manifest = record_dir / project_trash_store.SNAPSHOT_DIRNAME / "versions/versions.json"
    history = json.loads(manifest.read_bytes())["characters"]["Alice"]
    grids = {
        name: {**history, "versions": [{**history["versions"][0], "file": f"versions/grids/{name}_v1.png"}]}
        for name in ("owned", "other")
    }
    manifest.write_text(json.dumps({"grids": grids}))
    items = {item.metadata["resource_id"]: item for item in await holder.enumerate_remote_items(conn_id, OLD_BUCKET)}
    assert set(items) == {"owned", "other"}
    assert items["owned"].metadata["owned"] is True
    assert items["owned"].metadata["formal_rel"] == "grids/owned.png"
    assert items["other"].metadata["owned"] is False
    assert items["other"].metadata["formal_rel"] is None
    assert all(p.name != "other.png" for p in items["other"].local_paths)
    for item in items.values():
        assert (await holder.promote_item(item, data)).promoted
    record = json.loads(record_path.read_bytes())
    assert set(record["files"]) == {"grids/owned.json", "grids/owned.png", items["owned"].path}
    assert record["size"] == 2 * len(data)
    files = record_dir / project_trash_store.FILES_DIRNAME
    assert not (files / "grids/other.png").exists()
    assert not (files / items["other"].path).exists()
    updated = json.loads(manifest.read_bytes())["grids"]
    assert "storage" not in updated["owned"]["versions"][0]
    assert "storage" not in updated["other"]["versions"][0]
