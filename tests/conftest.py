"""Shared pytest fixtures for the ArcReel test suite."""

from __future__ import annotations

from collections.abc import Callable


def make_translator(locale: str = "zh") -> Callable[..., str]:
    """Create a translator function bound to a fixed locale for testing."""
    from lib.i18n import _ as i18n_translate

    def translate(key: str, **kwargs) -> str:
        return i18n_translate(key, locale=locale, **kwargs)

    return translate


import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import lib.generation_queue as generation_queue_module
from lib.db.base import Base
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore

# ---------------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------------


def make_test_video(path: Path, *, duration_sec: float = 1.0, fps: int = 30) -> None:
    """使用 ffmpeg 生成极短测试视频（64x64 像素）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=black:size=64x64:duration={duration_sec}:rate={fps}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


@pytest.fixture()
def fd_count():
    """Return a callable that reports the current process file-descriptor count.

    Returns -1 on platforms where /dev/fd and /proc/self/fd are unavailable.
    """

    def _count() -> int:
        for fd_dir in ("/dev/fd", "/proc/self/fd"):
            try:
                return len(os.listdir(fd_dir))
            except OSError:
                continue
        return -1

    return _count


# ---------------------------------------------------------------------------
# SessionManager family (used by 3+ test files)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def meta_store():
    """Create an async SessionMetaStore backed by in-memory SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionMetaStore(session_factory=factory)
    yield store
    await engine.dispose()


@pytest.fixture()
async def session_manager(tmp_path: Path, meta_store: SessionMetaStore) -> SessionManager:
    """Create a SessionManager wired to *tmp_path* and *meta_store*."""
    return SessionManager(
        project_root=tmp_path,
        data_dir=tmp_path,
        meta_store=meta_store,
    )


# ---------------------------------------------------------------------------
# GenerationQueue family (used by 2+ test files)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def generation_queue():
    """Create an async GenerationQueue backed by in-memory SQLite.

    Automatically resets the module singleton on teardown.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    queue = generation_queue_module.GenerationQueue(session_factory=factory)
    generation_queue_module._QUEUE_INSTANCE = queue
    yield queue
    generation_queue_module._QUEUE_INSTANCE = None
    await engine.dispose()


# ---------------------------------------------------------------------------
# fork-private: 让测试创建的任意 FastAPI app 自动短路 admin guard
#
# 真实 app 上的 admin guard 是 server.fork_admin_guard 中间件，按路径白名单
# 守卫；测试自建 FastAPI() 通常不挂这层中间件，所以多数测试不用 bypass。
# 本 fixture 只为 server.routers.fork_users 这个 fork-private router 的 router 级
# ``Depends(require_admin)`` 服务（其他上游 router 文件零改动）。
# 通过 ``@pytest.mark.real_admin_guard`` 可退出本 fixture，验证真实 guard 行为。
# ---------------------------------------------------------------------------


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_admin_guard: 关闭 _bypass_admin_guard fixture，验证真实 admin guard 行为",
    )


@pytest.fixture(autouse=True)
def _bypass_admin_guard(request, monkeypatch):
    # 需要验证真实 admin guard 行为的测试可打上 marker 退出
    if request.node.get_closest_marker("real_admin_guard"):
        return

    from fastapi import FastAPI

    from server.auth import CurrentUserInfo
    from server.fork_permissions import require_admin

    def _stub_admin() -> CurrentUserInfo:
        return CurrentUserInfo(id="default", sub="testadmin", role="admin")

    orig_init = FastAPI.__init__

    def _patched(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.dependency_overrides.setdefault(require_admin, _stub_admin)

    monkeypatch.setattr(FastAPI, "__init__", _patched)
