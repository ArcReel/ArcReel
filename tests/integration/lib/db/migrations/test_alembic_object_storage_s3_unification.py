"""Alembic 迁移：统一对象存储为标准 S3 兼容协议并原子升级存量活跃火山 TOS 连接。"""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import sqlalchemy as sa
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.material.formal_objects import sweep_formal_objects
from lib.material.storage import ObjectLocator
from lib.material.work_files import fetch_object_content
from lib.object_storage.client import ObjectStorageClient
from lib.object_storage.connection import config_from_connection
from lib.object_storage.relay import sweep_relay_objects
from server.error_handlers import register_error_handlers
from server.routers import object_storage
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import bind_session_factory, s3_client_error, use_object_storage_sdk

_NOW = "2026-11-20 00:00:00"
_REVISION_PATTERN = "*_object_storage_s3_unification.py"


def _engine(db_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{db_path}")


def _insert_legacy_connection(
    conn: sa.Connection,
    *,
    kind: str = "tos",
    region: str = "cn-beijing",
    endpoint: str,
    bucket: str,
    slot: int | None,
) -> int:
    result = conn.execute(
        sa.text(
            "INSERT INTO object_storage_connection "
            "(kind, region, endpoint, bucket, access_key_id, secret_access_key, slot, created_at, updated_at) "
            "VALUES (:kind, :region, :endpoint, :bucket, 'TEST-AK', 'TEST-SK', :slot, :now, :now)"
        ),
        {"kind": kind, "region": region, "endpoint": endpoint, "bucket": bucket, "slot": slot, "now": _NOW},
    )
    return int(result.lastrowid)


def _insert_formal_object(
    conn: sa.Connection,
    *,
    connection_id: int,
    endpoint: str,
    bucket: str,
    key: str,
    digest: str,
    state: str,
) -> int:
    result = conn.execute(
        sa.text(
            "INSERT INTO object_storage_formal_object "
            "(connection_id, endpoint, bucket, object_key, content_digest, size, state, delete_attempts, created_at, updated_at) "
            "VALUES (:cid, :endpoint, :bucket, :key, :digest, 12, :state, 0, :now, :now)"
        ),
        {
            "cid": connection_id,
            "endpoint": endpoint,
            "bucket": bucket,
            "key": key,
            "digest": digest,
            "state": state,
            "now": _NOW,
        },
    )
    return int(result.lastrowid)


def _insert_relay_object(
    conn: sa.Connection,
    *,
    connection_id: int,
    endpoint: str,
    bucket: str,
    key: str,
    state: str,
) -> int:
    result = conn.execute(
        sa.text(
            "INSERT INTO object_storage_relay_object "
            "(connection_id, endpoint, bucket, object_key, state, delete_attempts, created_at, updated_at) "
            "VALUES (:cid, :endpoint, :bucket, :key, :state, 0, :now, :now)"
        ),
        {
            "cid": connection_id,
            "endpoint": endpoint,
            "bucket": bucket,
            "key": key,
            "state": state,
            "now": _NOW,
        },
    )
    return int(result.lastrowid)


@pytest.mark.parametrize(
    ("raw_endpoint", "expected_endpoint"),
    [
        ("tos-cn-beijing.volces.com", "https://tos-s3-cn-beijing.volces.com"),
        ("https://tos-cn-shanghai.volces.com/", "https://tos-s3-cn-shanghai.volces.com"),
        ("tos-cn-beijing.ivolces.com", "https://tos-s3-cn-beijing.ivolces.com"),
        ("http://tos-cn-guangzhou.ivolces.com", "http://tos-s3-cn-guangzhou.ivolces.com"),
        ("tos-ap-southeast-1.bytepluses.com", "https://tos-s3-ap-southeast-1.bytepluses.com"),
        ("https://tos-ap-southeast-1.ibytepluses.com", "https://tos-s3-ap-southeast-1.ibytepluses.com"),
        ("tos-s3-cn-beijing.volces.com", "https://tos-s3-cn-beijing.volces.com"),
    ],
)
def test_upgrades_active_official_tos_domains_and_updates_matching_objects_only(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
    raw_endpoint: str,
    expected_endpoint: str,
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions(_REVISION_PATTERN)
    command.upgrade(cfg, parent)

    engine = _engine(db_path)
    try:
        with engine.begin() as conn:
            active_id = _insert_legacy_connection(conn, endpoint=raw_endpoint, bucket="studio-bucket", slot=1)
            retired_id = _insert_legacy_connection(conn, endpoint=raw_endpoint, bucket="studio-bucket", slot=None)

            for state in ("uploading", "stored", "orphaned", "deleted"):
                _insert_formal_object(
                    conn,
                    connection_id=active_id,
                    endpoint=raw_endpoint,
                    bucket="studio-bucket",
                    key=f"arcreel-materials/sha256/aa/{state}.png",
                    digest="a" * 64,
                    state=state,
                )
            _insert_formal_object(
                conn,
                connection_id=active_id,
                endpoint="tos-cn-hongkong.volces.com",
                bucket="studio-bucket",
                key="arcreel-materials/sha256/bb/other-endpoint.png",
                digest="b" * 64,
                state="orphaned",
            )
            _insert_formal_object(
                conn,
                connection_id=active_id,
                endpoint=raw_endpoint,
                bucket="other-bucket",
                key="arcreel-materials/sha256/cc/other-bucket.png",
                digest="c" * 64,
                state="orphaned",
            )
            _insert_formal_object(
                conn,
                connection_id=retired_id,
                endpoint=raw_endpoint,
                bucket="studio-bucket",
                key="arcreel-materials/sha256/dd/retired.png",
                digest="d" * 64,
                state="stored",
            )

            for state in ("retained", "deleted"):
                _insert_relay_object(
                    conn,
                    connection_id=active_id,
                    endpoint=raw_endpoint,
                    bucket="studio-bucket",
                    key=f"arcreel-relay/20261120/{state}.png",
                    state=state,
                )
            _insert_relay_object(
                conn,
                connection_id=retired_id,
                endpoint=raw_endpoint,
                bucket="studio-bucket",
                key="arcreel-relay/20261120/retired.png",
                state="retained",
            )

        command.upgrade(cfg, revision)

        with engine.connect() as conn:
            conns = conn.execute(
                sa.text(
                    "SELECT id, kind, endpoint, bucket, addressing_style, needs_endpoint_review, slot "
                    "FROM object_storage_connection ORDER BY id"
                )
            ).all()
            assert [(r[0], r[1], r[2], r[3], r[4], bool(r[5]), r[6]) for r in conns] == [
                (active_id, "s3", expected_endpoint, "studio-bucket", "virtual", False, 1),
                (retired_id, "tos", raw_endpoint, "studio-bucket", "auto", False, None),
            ]

            formal_rows = conn.execute(
                sa.text(
                    "SELECT connection_id, endpoint, bucket, object_key, content_digest, state "
                    "FROM object_storage_formal_object ORDER BY id"
                )
            ).all()
            by_key = {row[3]: tuple(row) for row in formal_rows}
            for state in ("uploading", "stored", "orphaned", "deleted"):
                assert by_key[f"arcreel-materials/sha256/aa/{state}.png"] == (
                    active_id,
                    expected_endpoint,
                    "studio-bucket",
                    f"arcreel-materials/sha256/aa/{state}.png",
                    "a" * 64,
                    state,
                )
            assert by_key["arcreel-materials/sha256/bb/other-endpoint.png"][1] == "tos-cn-hongkong.volces.com"
            assert by_key["arcreel-materials/sha256/cc/other-bucket.png"][1] == raw_endpoint
            assert by_key["arcreel-materials/sha256/dd/retired.png"][:2] == (retired_id, raw_endpoint)

            relay_rows = conn.execute(
                sa.text(
                    "SELECT connection_id, endpoint, bucket, object_key, state "
                    "FROM object_storage_relay_object ORDER BY id"
                )
            ).all()
            relay_by_key = {row[3]: tuple(row) for row in relay_rows}
            for state in ("retained", "deleted"):
                assert relay_by_key[f"arcreel-relay/20261120/{state}.png"] == (
                    active_id,
                    expected_endpoint,
                    "studio-bucket",
                    f"arcreel-relay/20261120/{state}.png",
                    state,
                )
            assert relay_by_key["arcreel-relay/20261120/retired.png"][:2] == (retired_id, raw_endpoint)
    finally:
        engine.dispose()

    command.downgrade(cfg, parent)
    engine = _engine(db_path)
    try:
        columns = {column["name"] for column in sa.inspect(engine).get_columns("object_storage_connection")}
    finally:
        engine.dispose()
    assert "addressing_style" not in columns
    assert "needs_endpoint_review" not in columns


def test_custom_tos_domain_is_preserved_and_flagged_for_review(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions(_REVISION_PATTERN)
    command.upgrade(cfg, parent)

    custom_endpoint = "custom-tos.corp.internal"
    engine = _engine(db_path)
    try:
        with engine.begin() as conn:
            conn_id = _insert_legacy_connection(conn, endpoint=custom_endpoint, bucket="corp-bucket", slot=1)
            _insert_formal_object(
                conn,
                connection_id=conn_id,
                endpoint=custom_endpoint,
                bucket="corp-bucket",
                key="arcreel-materials/sha256/ee/custom.png",
                digest="e" * 64,
                state="stored",
            )
            _insert_relay_object(
                conn,
                connection_id=conn_id,
                endpoint=custom_endpoint,
                bucket="corp-bucket",
                key="arcreel-relay/20261120/custom.png",
                state="retained",
            )

        command.upgrade(cfg, revision)

        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT id, kind, endpoint, addressing_style, needs_endpoint_review "
                    "FROM object_storage_connection WHERE id = :id"
                ),
                {"id": conn_id},
            ).one()
            assert (row[0], row[1], row[2], row[3], bool(row[4])) == (
                conn_id,
                "s3",
                custom_endpoint,
                "auto",
                True,
            )
            formal_ep = conn.execute(sa.text("SELECT endpoint FROM object_storage_formal_object")).scalar_one()
            relay_ep = conn.execute(sa.text("SELECT endpoint FROM object_storage_relay_object")).scalar_one()
            assert formal_ep == custom_endpoint
            assert relay_ep == custom_endpoint
    finally:
        engine.dispose()


def test_upgraded_legacy_tos_connection_works_end_to_end_via_high_level_apis(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
):
    """从旧版 Alembic 数据升级后，经高层 API 跑通读、签、删、同目标轮换与换桶分新 ID。"""
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions(_REVISION_PATTERN)
    command.upgrade(cfg, parent)

    payload = b"\x89PNG\r\n\x1a\nupgraded-image-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    stored_key = f"arcreel-materials/sha256/{digest[:2]}/{digest}.png"
    orphan_key = "arcreel-materials/sha256/00/orphan.png"
    relay_key = "arcreel-relay/20261120/relay.png"

    sync_engine = _engine(db_path)
    try:
        with sync_engine.begin() as conn:
            legacy_id = _insert_legacy_connection(
                conn, endpoint="tos-cn-beijing.volces.com", bucket="legacy-bucket", slot=1
            )
            _insert_formal_object(
                conn,
                connection_id=legacy_id,
                endpoint="tos-cn-beijing.volces.com",
                bucket="legacy-bucket",
                key=stored_key,
                digest=digest,
                state="stored",
            )
            _insert_formal_object(
                conn,
                connection_id=legacy_id,
                endpoint="tos-cn-beijing.volces.com",
                bucket="legacy-bucket",
                key=orphan_key,
                digest="0" * 64,
                state="orphaned",
            )
            _insert_relay_object(
                conn,
                connection_id=legacy_id,
                endpoint="tos-cn-beijing.volces.com",
                bucket="legacy-bucket",
                key=relay_key,
                state="retained",
            )
    finally:
        sync_engine.dispose()

    command.upgrade(cfg, revision)
    command.upgrade(cfg, "head")

    bucket_objects: dict[tuple[str, str], bytes] = {
        ("legacy-bucket", stored_key): payload,
        ("legacy-bucket", orphan_key): b"orphan",
        ("legacy-bucket", relay_key): b"relay",
    }
    seen_configs: list[Any] = []

    class _MemoryS3:
        def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
            return {}

        def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_kwargs: Any) -> dict[str, Any]:
            bucket_objects[(Bucket, Key)] = Body
            return {"ETag": '"etag"'}

        def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
            if (Bucket, Key) not in bucket_objects:
                raise s3_client_error(404, "NoSuchKey")
            return {"ContentLength": len(bucket_objects[(Bucket, Key)]), "ETag": '"etag"'}

        def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
            if (Bucket, Key) not in bucket_objects:
                raise s3_client_error(404, "NoSuchKey")
            return {"Body": io.BytesIO(bucket_objects[(Bucket, Key)])}

        def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
            bucket_objects.pop((Bucket, Key), None)
            return {}

        def generate_presigned_url(self, _method: str, *, Params: dict[str, Any], ExpiresIn: int) -> str:
            return f"https://{Params['Bucket']}.tos-s3-cn-beijing.volces.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}"

    def _sdk_factory(config: Any) -> _MemoryS3:
        seen_configs.append(config)
        return _MemoryS3()

    use_object_storage_sdk(monkeypatch, _sdk_factory)

    async def _verify_high_level() -> None:
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        sessions = async_sessionmaker(async_engine, expire_on_commit=False)
        bind_session_factory(monkeypatch, sessions)

        app = FastAPI()

        async def _session():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_async_session] = _session
        override_auth(app)
        app.include_router(object_storage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)

        try:
            with TestClient(app) as api, respx.mock(assert_all_called=False) as http_mock:
                http_mock.get(url__regex=r"^https://legacy-bucket\.tos-s3-cn-beijing\.volces\.com/.*").mock(
                    side_effect=lambda req: httpx.Response(
                        200,
                        content=bucket_objects.get(("legacy-bucket", req.url.path.lstrip("/")), b""),
                    )
                )
                envelope = api.get("/api/v1/object-storage/connection").json()
                assert envelope["connection"]["kind"] == "s3"
                assert envelope["connection"]["endpoint"] == "https://tos-s3-cn-beijing.volces.com"
                assert envelope["connection"]["addressing_style"] == "virtual"
                assert envelope["connection"]["needs_endpoint_review"] is False
                probe = api.post("/api/v1/object-storage/connection/test").json()
                assert probe["success"] is True
                assert [step["status"] for step in probe["steps"]] == ["passed"] * 5
                assert probe["residual_object_key"] is None

                fetched = await fetch_object_content(
                    ObjectLocator(legacy_id, "legacy-bucket", stored_key), session_factory=sessions
                )
                assert fetched == payload

                async with sessions() as session:
                    conn = await ObjectStorageConnectionRepository(session).get()
                    assert conn is not None
                    signed = await ObjectStorageClient(config_from_connection(conn)).presign_get(
                        stored_key, expires_seconds=3600
                    )
                assert signed.startswith(f"https://legacy-bucket.tos-s3-cn-beijing.volces.com/{stored_key}?")

                assert await sweep_formal_objects(sessions, now=utc_now() + timedelta(days=1)) == 1
                assert await sweep_relay_objects(sessions, now=utc_now() + timedelta(days=1)) == 1
                assert ("legacy-bucket", orphan_key) not in bucket_objects
                assert ("legacy-bucket", relay_key) not in bucket_objects

                rotate = api.put(
                    "/api/v1/object-storage/connection",
                    json={
                        "kind": "s3",
                        "region": "cn-beijing",
                        "endpoint": "https://tos-s3-cn-beijing.volces.com",
                        "bucket": "legacy-bucket",
                        "addressing_style": "virtual",
                        "access_key_id": "ROTATED-AK",
                    },
                )
                assert rotate.status_code == 200
                async with sessions() as session:
                    rotated = await ObjectStorageConnectionRepository(session).get()
                    assert rotated is not None
                    assert rotated.id == legacy_id
                    assert rotated.access_key_id == "ROTATED-AK"
                    assert rotated.secret_access_key == "TEST-SK"

                blank_relocate = api.put(
                    "/api/v1/object-storage/connection",
                    json={
                        "kind": "s3",
                        "region": "auto",
                        "endpoint": "https://123456.r2.cloudflarestorage.com",
                        "bucket": "new-r2-bucket",
                        "addressing_style": "auto",
                    },
                )
                assert blank_relocate.status_code == 422

                relocated = api.put(
                    "/api/v1/object-storage/connection",
                    json={
                        "kind": "s3",
                        "region": "auto",
                        "endpoint": "https://123456.r2.cloudflarestorage.com",
                        "bucket": "new-r2-bucket",
                        "addressing_style": "auto",
                        "access_key_id": "NEW-R2-AK",
                        "secret_access_key": "NEW-R2-SK",
                    },
                )
                assert relocated.status_code == 200
                async with sessions() as session:
                    new_conn = await ObjectStorageConnectionRepository(session).get()
                    assert new_conn is not None
                    assert new_conn.id > legacy_id
                    assert new_conn.endpoint == "https://123456.r2.cloudflarestorage.com"
                    assert new_conn.bucket == "new-r2-bucket"
            assert any(cfg.addressing_style == "virtual" for cfg in seen_configs)
        finally:
            await async_engine.dispose()

    asyncio.run(_verify_high_level())
