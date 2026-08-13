"""Cross-process admission guard for generation and media selection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import portalocker

from lib.app_data_dir import app_data_dir

_POLL_SECONDS = 0.05


def _normalized_script_locator(script_file: str) -> str:
    normalized = posixpath.normpath(script_file.replace("\\", "/"))
    return normalized.removeprefix("scripts/")


def _lock_path(*, project_name: str, script_file: str, resource_id: str) -> Path:
    identity = json.dumps(
        [project_name, _normalized_script_locator(script_file), resource_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    root = app_data_dir() / ".generation-admission-locks"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{digest}.lock"


@asynccontextmanager
async def generation_admission_lock(
    *,
    project_name: str,
    script_file: str,
    resource_id: str,
) -> AsyncIterator[None]:
    """Serialize task admission with guarded media selection for one unit.

    Non-blocking lock attempts keep the event loop responsive and cancellation
    safe: no background thread can acquire the lock after the awaiting task has
    already exited.
    """

    path = _lock_path(project_name=project_name, script_file=script_file, resource_id=resource_id)
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


__all__ = ["generation_admission_lock"]
