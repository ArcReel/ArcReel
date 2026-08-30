from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock

import portalocker
import pytest

from lib import generation_admission


async def test_admission_guard_serializes_one_resource_across_script_rebinds(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_admission, "app_data_dir", lambda: tmp_path)
    acquired = asyncio.Event()

    async def _acquire_rebound_binding() -> None:
        async with generation_admission.generation_admission_lock(
            project_name="demo",
            script_file="rebound_episode_01.json",
            resource_id="E1S01",
        ):
            acquired.set()

    async with generation_admission.generation_admission_lock(
        project_name="demo",
        script_file="episode_01.json",
        resource_id="E1S01",
    ):
        rebound = asyncio.create_task(_acquire_rebound_binding())
        await asyncio.sleep(0.1)
        assert not acquired.is_set()

    await rebound
    assert acquired.is_set()


async def test_sync_compensation_guard_shares_the_async_resource_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_admission, "app_data_dir", lambda: tmp_path)
    acquired = threading.Event()

    def _acquire_for_compensation() -> None:
        with generation_admission.generation_admission_lock_sync(
            project_name="demo",
            script_file="rebound_episode_01.json",
            resource_id="E1S01",
        ):
            acquired.set()

    async with generation_admission.generation_admission_lock(
        project_name="demo",
        script_file="episode_01.json",
        resource_id="E1S01",
    ):
        compensation = asyncio.create_task(asyncio.to_thread(_acquire_for_compensation))
        await asyncio.sleep(0.1)
        assert not acquired.is_set()

    await compensation
    assert acquired.is_set()


async def test_admission_guard_propagates_non_contention_lock_failures(tmp_path, monkeypatch):
    failure = portalocker.LockException("lock backend failed")
    sleep = AsyncMock(side_effect=AssertionError("non-contention failures must not be polled"))

    def _fail_lock(*_args):
        raise failure

    monkeypatch.setattr(generation_admission, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(generation_admission.portalocker, "lock", _fail_lock)
    monkeypatch.setattr(generation_admission.asyncio, "sleep", sleep)

    with pytest.raises(portalocker.LockException, match="lock backend failed"):
        async with generation_admission.generation_admission_lock(
            project_name="demo",
            script_file="episode_01.json",
            resource_id="E1S01",
        ):
            pass

    sleep.assert_not_awaited()


async def test_admission_guard_closes_its_file_when_unlock_fails(tmp_path, monkeypatch):
    captured_handle = None

    def _fail_unlock(handle):
        nonlocal captured_handle
        captured_handle = handle
        raise OSError("unlock failed")

    monkeypatch.setattr(generation_admission, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(generation_admission.portalocker, "unlock", _fail_unlock)

    with pytest.raises(OSError, match="unlock failed"):
        async with generation_admission.generation_admission_lock(
            project_name="demo",
            script_file="episode_01.json",
            resource_id="E1S01",
        ):
            pass

    assert captured_handle is not None
    assert captured_handle.closed
