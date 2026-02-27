"""Shared pytest fixtures for the ArcReel test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import lib.generation_queue as generation_queue_module
from lib.generation_queue import GenerationQueue
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


# ---------------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------------

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
def meta_store(tmp_path: Path) -> SessionMetaStore:
    """Create a SessionMetaStore backed by a temporary SQLite database."""
    return SessionMetaStore(tmp_path / "sessions.db")


@pytest.fixture()
def session_manager(tmp_path: Path, meta_store: SessionMetaStore) -> SessionManager:
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
def generation_queue(tmp_path: Path):
    """Create a GenerationQueue and register it as the module singleton.

    Automatically resets the singleton on teardown.
    """
    db_path = tmp_path / "task_queue.db"
    queue = GenerationQueue(db_path=db_path)
    generation_queue_module._QUEUE_INSTANCE = queue
    yield queue
    generation_queue_module._QUEUE_INSTANCE = None
