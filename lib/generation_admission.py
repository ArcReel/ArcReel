"""Cross-process admission guard for generation and media selection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import portalocker

from lib.app_data_dir import app_data_dir
from lib.content_digest import canonical_json_digest

_POLL_SECONDS = 0.05


def _lock_path(*, project_name: str, resource_id: str) -> Path:
    digest = canonical_json_digest([project_name, resource_id])
    root = app_data_dir() / ".generation-admission-locks"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{digest}.lock"


@asynccontextmanager
async def generation_admission_lock(
    *,
    project_name: str,
    script_file: str,
    resource_id: str,
) -> AsyncGenerator[None]:
    """Serialize task admission with guarded media selection for one unit.

    Non-blocking lock attempts keep the event loop responsive and cancellation
    safe: no background thread can acquire the lock after the awaiting task has
    already exited.
    """

    # The script locator is deliberately absent from the key. Rebinding an episode must not let
    # a new task select the same resource while compensation for the former binding is in flight.
    path = _lock_path(project_name=project_name, resource_id=resource_id)
    handle = path.open("a+b")
    acquired = False
    try:
        while not acquired:
            try:
                portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                acquired = True
            except portalocker.AlreadyLocked:
                await asyncio.sleep(_POLL_SECONDS)
        yield
    finally:
        try:
            if acquired:
                portalocker.unlock(handle)
        finally:
            handle.close()


@contextmanager
def generation_admission_lock_sync(
    *,
    project_name: str,
    script_file: str,
    resource_id: str,
) -> Generator[None]:
    """Blocking counterpart for synchronous compensation after the async guard is released."""

    path = _lock_path(project_name=project_name, resource_id=resource_id)
    handle = path.open("a+b")
    acquired = False
    try:
        portalocker.lock(handle, portalocker.LOCK_EX)
        acquired = True
        yield
    finally:
        try:
            if acquired:
                portalocker.unlock(handle)
        finally:
            handle.close()


__all__ = ["generation_admission_lock", "generation_admission_lock_sync"]
