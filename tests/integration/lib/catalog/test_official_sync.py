"""官方同步：从目录数据仓库的 raw 地址取回新数据集并整批应用（docs/adr/0103）。raw 抓取由 respx 替身，绑内存库。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import AsyncGenerator, Iterable
from dataclasses import replace
from pathlib import Path

import anyio
import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arcreel_catalog_core import CatalogData, CatalogEntry, EntryKey, TextFacts, dump_entry
from lib.catalog import builtin_models, official_sync
from lib.catalog import query as catalog
from lib.config.repository import SystemSettingRepository
from lib.db.base import Base
from lib.db.models.catalog_official_sync import CatalogOfficialSync
from lib.db.models.config import SystemSetting
from lib.market.sources import PROXY_PREFIX_SETTING
from server.routers.provider_models import reload_user_added_models
from tests.catalog_support import catalog_of, install_catalog
from tests.http_capture import capture_http, only_request

PROVIDER = "agnes"


def _text(name: str, display_name: str) -> CatalogEntry:
    return CatalogEntry(maker="acme", name=name, media_type="text", display_name=display_name, facts=TextFacts())


def _key(name: str) -> EntryKey:
    return EntryKey("acme", name, "text")


def _dataset(version: str, entries: Iterable[CatalogEntry], listed: Iterable[str]) -> CatalogData:
    entries = tuple(entries)
    return replace(catalog_of(entries, {PROVIDER: tuple(f"acme/{n}" for n in listed)}), dataset_version=version)


def _files(data: CatalogData) -> tuple[bytes, bytes]:
    """目录数据仓库发布的一对文件：清单文件与数据文件。"""
    raw = {
        "dataset_version": data.dataset_version,
        "entries": [dump_entry(entry) for entry in data.entries],
        "builtin_providers": {
            pid: {
                "models": [{"api_model_name": m.api_model_name, "entry": m.entry} for m in listing.models],
                "defaults": dict(listing.defaults),
                "some_future_field": True,  # 不认识的字段忽略
            }
            for pid, listing in data.builtin_providers.items()
        },
    }
    content = json.dumps(raw).encode()
    return _manifest(data.dataset_version, content), content


def _manifest(version: str, content: bytes) -> bytes:
    return json.dumps({"dataset_version": version, "sha256": hashlib.sha256(content).hexdigest()}).encode()


SNAPSHOT = _dataset(
    "2026-01-01T00:00:00Z",
    [_text("chat", "Chat v1"), _text("legacy", "Legacy"), _text("pinned", "Pinned v1")],
    ["chat", "legacy"],
)
UPSTREAM = _dataset(
    "2026-02-01T00:00:00Z",
    [_text("chat", "Chat v2"), _text("pinned", "Pinned v2"), _text("fresh", "Fresh")],
    ["chat", "fresh"],
)


@pytest.fixture
async def http_client() -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture(autouse=True)
def _catalog_state(monkeypatch: pytest.MonkeyPatch):
    install_catalog(monkeypatch, SNAPSHOT)
    yield
    official_sync.reset_for_tests()
    catalog.install_user_entries(())
    catalog.install_user_added_models(())


async def _sync(db_factory: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, upstream: CatalogData) -> None:
    manifest, content = _files(upstream)
    with capture_http() as http:
        http.get(official_sync.MANIFEST_URL).respond(content=manifest)
        http.get(official_sync.CATALOG_URL).respond(content=content)
        await official_sync.sync_once(db_factory, lambda: client)


async def test_newer_dataset_is_applied_as_a_batch(db_factory, http_client) -> None:
    pinned_copy = _text("pinned", "Pinned (mine)")
    catalog.install_user_entries([pinned_copy])

    await _sync(db_factory, http_client, UPSTREAM)

    assert catalog.official_data().dataset_version == UPSTREAM.dataset_version
    assert catalog.get_entry(_key("chat")).display_name == "Chat v2"
    assert catalog.get_entry(_key("fresh")).display_name == "Fresh"
    # 上游删掉的条目保留本地最后一个版本
    assert catalog.get_entry(_key("legacy")).display_name == "Legacy"
    # 停止跟随官方的条目照旧用用户副本，官方值随同步更新
    assert catalog.get_entry(_key("pinned")) == pinned_copy
    assert catalog.official_entry(_key("pinned")).display_name == "Pinned v2"
    # 内置供应商的供应商模型清单整份跟随数据
    assert [view.model_id for view in catalog.provider_models(PROVIDER)] == ["chat", "fresh"]

    status = official_sync.sync_status()
    assert status.last_success_at is not None
    assert status.last_error is None

    # 重启后从库里装回同一份数据，保留下来的条目也在
    official_sync.reset_for_tests()
    assert catalog.official_data() is SNAPSHOT
    async with db_factory() as session:
        await official_sync.load_synced_data(session)
    assert catalog.official_data().dataset_version == UPSTREAM.dataset_version
    assert catalog.get_entry(_key("legacy")).display_name == "Legacy"
    assert official_sync.sync_status().last_success_at == status.last_success_at


async def test_unparseable_data_is_rejected_and_local_catalog_unchanged(db_factory, http_client) -> None:
    garbage = b'{"dataset_version": "2026-02-01T00:00:00Z", "entries": "nope"}'
    with capture_http() as http:
        http.get(official_sync.MANIFEST_URL).respond(content=_manifest("2026-02-01T00:00:00Z", garbage))
        http.get(official_sync.CATALOG_URL).respond(content=garbage)
        await official_sync.sync_once(db_factory, lambda: http_client)

    assert catalog.official_data() is SNAPSHOT
    assert official_sync.sync_status().last_error
    async with db_factory() as session:
        row = await session.get(CatalogOfficialSync, 1)
    assert row is not None
    assert row.catalog is None
    assert row.last_error


async def test_hash_mismatch_and_network_failure_are_recorded_silently(db_factory, http_client) -> None:
    manifest, content = _files(UPSTREAM)
    with capture_http() as http:
        http.get(official_sync.MANIFEST_URL).respond(content=manifest)
        http.get(official_sync.CATALOG_URL).respond(content=content + b" ")
        await official_sync.sync_once(db_factory, lambda: http_client)
    assert "sha256_mismatch" in (official_sync.sync_status().last_error or "")

    with capture_http() as http:
        http.get(official_sync.MANIFEST_URL).respond(status_code=503)
        await official_sync.sync_once(db_factory, lambda: http_client)
    assert official_sync.sync_status().last_error == "HTTP 503"
    assert catalog.official_data() is SNAPSHOT

    # 下一轮成功后清掉失败原因
    await _sync(db_factory, http_client, UPSTREAM)
    assert official_sync.sync_status().last_error is None


async def test_newer_factory_snapshot_replaces_older_synced_data(
    db_factory, http_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sync(db_factory, http_client, UPSTREAM)

    upgraded = _dataset("2026-03-01T00:00:00Z", [_text("chat", "Chat v3")], ["chat"])
    install_catalog(monkeypatch, upgraded)

    assert catalog.official_data().dataset_version == upgraded.dataset_version
    assert catalog.get_entry(_key("chat")).display_name == "Chat v3"
    assert [view.model_id for view in catalog.provider_models(PROVIDER)] == ["chat"]
    # 已同步数据独有的条目仍保留
    assert catalog.get_entry(_key("fresh")).display_name == "Fresh"

    # 清单的数据集版本不比本地新：不取数据文件
    manifest, _ = _files(UPSTREAM)
    with capture_http() as http:
        http.get(official_sync.MANIFEST_URL).respond(content=manifest)
        data_route = http.get(official_sync.CATALOG_URL)
        await official_sync.sync_once(db_factory, lambda: http_client)
    assert not data_route.called
    assert official_sync.sync_status().last_error is None


async def test_official_listing_takes_over_user_added_model_keeping_enabled_state(db_factory, http_client) -> None:
    async with db_factory() as session:
        await builtin_models.add_builtin_model(session, PROVIDER, "fresh", media_type="text")
        await session.commit()
        await reload_user_added_models(session)
        await builtin_models.set_builtin_model_enabled(session, PROVIDER, "fresh", False)
        await session.commit()
        before = {i.view.model_id: i for i in await builtin_models.list_builtin_models(session, PROVIDER)}
    assert before["fresh"].view.official is False

    await _sync(db_factory, http_client, UPSTREAM)

    async with db_factory() as session:
        after = {i.view.model_id: i for i in await builtin_models.list_builtin_models(session, PROVIDER)}
    assert after["fresh"].view.official is True
    assert after["fresh"].view.model.display_name == "Fresh"
    assert after["fresh"].enabled is False


async def test_fetch_goes_through_the_market_github_proxy_prefix(db_factory, http_client) -> None:
    async with db_factory() as session:
        await SystemSettingRepository(session).set(PROXY_PREFIX_SETTING, "https://proxy.example.net/")
        await session.commit()
    manifest, _ = _files(SNAPSHOT)
    with capture_http() as http:
        route = http.get(f"https://proxy.example.net/{official_sync.MANIFEST_URL}").respond(content=manifest)
        await official_sync.sync_once(db_factory, lambda: http_client)
    request = only_request(route)
    assert not any(name.lower().startswith("x-arcreel") for name in request.headers)


async def test_unexpected_failure_still_shows_in_sync_status() -> None:
    def broken_session_factory() -> AsyncSession:
        raise RuntimeError("database is locked")

    task = official_sync.start_official_sync(broken_session_factory)
    try:
        for _ in range(10):
            await asyncio.sleep(0)
            if official_sync.sync_status().last_error:
                break
    finally:
        await official_sync.stop_official_sync(task)
    assert official_sync.sync_status().last_error == "database is locked"


class _AnyioConnectTransport(httpx.AsyncBaseTransport):
    """同 httpcore 经 ``anyio.connect_tcp`` 建连：连接建立后 anyio 取消自己的任务组，再回 500。"""

    def __init__(self) -> None:
        self.connected = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with anyio.create_task_group() as tg:

            async def connect() -> None:
                self.connected.set()
                tg.cancel_scope.cancel()

            tg.start_soon(connect)
            await anyio.sleep_forever()
        return httpx.Response(500)


async def test_stop_ends_the_sync_when_anyio_absorbs_the_cancellation(db_factory) -> None:
    transport = _AnyioConnectTransport()
    async with httpx.AsyncClient(transport=transport) as client:
        task = official_sync.start_official_sync(db_factory, lambda: client)

        async def stop_once_connected() -> None:
            await transport.connected.wait()
            # 关停的取消与 anyio 的任务组取消落在同一轮调度，被 anyio 一并吞掉
            await official_sync.stop_official_sync(task)

        stopping = asyncio.create_task(stop_once_connected())
        # 防挂起的上限：不用 asyncio.timeout 包住 await，它的超时取消会经 await 转给同步任务，掩盖挂起
        await asyncio.wait({stopping}, timeout=5)

        assert stopping.done()
        assert task.cancelled()


async def test_stop_returns_after_the_interrupted_database_write_is_cleaned_up(tmp_path: Path) -> None:
    # 与生产库同样的文件 SQLite 配默认连接池：被打断的会话在 asyncio.shield 里回滚、归还连接
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}")
    loop = asyncio.get_running_loop()
    insert_running = asyncio.Event()
    release_insert = threading.Event()

    def hold_insert() -> int:
        # 在 aiosqlite 的工作线程里执行：记录同步失败的 INSERT 停在这里，直到用例放行
        loop.call_soon_threadsafe(insert_running.set)
        release_insert.wait()
        return 0

    @event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_conn, _record) -> None:
        dbapi_conn.create_function("hold_insert", 0, hold_insert)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, [CatalogOfficialSync.__table__, SystemSetting.__table__])
        await conn.exec_driver_sql(
            "CREATE TRIGGER hold BEFORE INSERT ON catalog_official_sync BEGIN SELECT hold_insert(); END"
        )
    before = asyncio.all_tasks()
    try:
        failing = httpx.MockTransport(lambda _request: httpx.Response(500))
        async with httpx.AsyncClient(transport=failing) as client:
            task = official_sync.start_official_sync(async_sessionmaker(engine), lambda: client)
            await insert_running.wait()

            stopping = asyncio.create_task(official_sync.stop_official_sync(task))
            # 先让 INSERT 卡一段时间再放行：只决定能否暴露收尾被重投的取消打断，不影响正确实现的结果
            await asyncio.wait({stopping}, timeout=0.2)
            release_insert.set()
            await stopping

            assert task.cancelled()
            assert asyncio.all_tasks() - before == set()
            assert engine.pool.checkedout() == 0
    finally:
        release_insert.set()
        await engine.dispose()
