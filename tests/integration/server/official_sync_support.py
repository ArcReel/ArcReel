"""经应用启动时的入口跑一轮官方目录同步：上游发布的数据由 respx 供给，``done`` 成立后停掉同步循环。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Callable
from typing import Any

import httpx
import respx
from starlette.datastructures import State

from arcreel_catalog_core import CatalogData, dump_entry
from lib.catalog import official_sync
from lib.catalog import query as catalog
from server import app as server_app
from tests.http_capture import capture_http

#: 比任何测试目录数据都新的数据集版本，同步一定会装入。
NEWER = "2099-01-01T00:00:00Z"


async def sync_as_app_starts_it(
    monkeypatch,
    db_factory,
    serve: Callable[[respx.MockRouter], None],
    done: Callable[[], bool],
    *,
    worker: Any = None,
) -> None:
    """经应用启动时的入口跑一轮官方同步，``done`` 成立后停掉同步循环。

    同步每用完一个数据库会话就检查一次 ``done``；装入了新数据时还要等应用回调完成容量重载，
    避免在目录已经可见、DB 容量仍在读取时取消同步任务。

    应用回调重载的是 ``server.app.app.state`` 上的 worker。lifespan 用例会在这个进程级对象上留下 worker 替身，
    这里按 ``worker`` 显式钉住，没有给就是没有 worker。
    """
    monkeypatch.setattr(server_app.app, "state", State({} if worker is None else {"generation_worker": worker}))
    closed = asyncio.Event()
    applied = asyncio.Event()
    initial_version = catalog.official_data().dataset_version

    @contextlib.asynccontextmanager
    async def session_factory():
        async with db_factory() as session:
            yield session
        closed.set()

    async def _until_done() -> None:
        while not done() or (catalog.official_data().dataset_version != initial_version and not applied.is_set()):
            closed.clear()
            await closed.wait()

    def start_sync(session_factory, http_client, on_applied):
        async def after_applied():
            await on_applied()
            applied.set()
            closed.set()

        return official_sync.start_official_sync(session_factory, http_client, on_applied=after_applied)

    # 根 conftest 把应用启动的同步换成了不发请求的空任务，这里换回真实入口
    monkeypatch.setattr(server_app, "start_official_sync", start_sync)
    async with httpx.AsyncClient() as client:
        with capture_http() as router:
            serve(router)
            task = server_app.start_catalog_sync(session_factory, lambda: client)
            assert task is not None
            waiting = asyncio.create_task(_until_done())
            # 防挂起的上限：不用 asyncio.timeout 包住 await，超时取消会掩盖挂起
            await asyncio.wait({waiting}, timeout=5)
            waiting.cancel()
            await official_sync.stop_official_sync(task)
    assert waiting.done()
    assert not waiting.cancelled(), "同步这一轮没有跑完"


def _published_files(data: CatalogData) -> tuple[bytes, bytes]:
    """目录数据仓库发布的一对文件 ``(清单文件, 数据文件)``，由 ``data`` 整份写出。"""
    raw = {
        "dataset_version": data.dataset_version,
        "entries": [dump_entry(entry) for entry in data.entries],
        "builtin_providers": {
            provider_id: {
                "models": [
                    {
                        key: value
                        for key, value in {
                            "api_model_name": model.api_model_name,
                            "entry": model.entry,
                            "media_type": model.media_type,
                            "multiplier": model.multiplier,
                            "min_app_version": model.min_app_version,
                            "endpoint": model.endpoint,
                        }.items()
                        if value is not None
                    }
                    for model in listing.models
                ],
                "defaults": dict(listing.defaults),
            }
            for provider_id, listing in data.builtin_providers.items()
        },
    }
    content = json.dumps(raw).encode()
    manifest = {"dataset_version": data.dataset_version, "sha256": hashlib.sha256(content).hexdigest()}
    return json.dumps(manifest).encode(), content


def serve_dataset(data: CatalogData) -> Callable[[respx.MockRouter], None]:
    """上游发布 ``data``。"""
    manifest, content = _published_files(data)

    def serve(router: respx.MockRouter) -> None:
        router.get(official_sync.MANIFEST_URL).respond(content=manifest)
        router.get(official_sync.CATALOG_URL).respond(content=content)

    return serve
