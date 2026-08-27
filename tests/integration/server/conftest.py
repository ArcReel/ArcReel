from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from lib.generation_queue import GenerationQueue
from lib.generation_queue_client import wait_for_task
from lib.generation_worker import CapacityTable, GenerationWorker
from lib.project_change_hints import register_project_change_batch_listener
from server.media_tools.context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import _fake_caps_resolver, _FakePM


def _build_fake_ctx(tmp_path: Path, session_factory, monkeypatch: pytest.MonkeyPatch) -> ToolContext:
    monkeypatch.setattr("lib.db.async_session_factory", session_factory)
    monkeypatch.setattr("server.services.video_batch_admission.async_session_factory", session_factory)
    monkeypatch.setattr("server.services.video_caps.async_session_factory", session_factory)
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    (project_dir / "storyboards").mkdir()
    (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
    (project_dir / "audio").mkdir()
    (project_dir / "audio" / "segment_E1S01.wav").write_bytes(b"")
    (project_dir / "audio" / "segment_E1S02.wav").write_bytes(b"")

    queue = GenerationQueue(session_factory=session_factory)
    return ToolContext(
        project_name="demo",
        projects_root=tmp_path,
        pm=_FakePM("demo", project_dir),  # type: ignore[arg-type]
        queue=queue,
        config_resolver=_fake_caps_resolver(),
    )


@pytest.fixture
def idle_fake_ctx(tmp_path: Path, concurrent_session_factory, monkeypatch: pytest.MonkeyPatch) -> ToolContext:
    return _build_fake_ctx(tmp_path, concurrent_session_factory, monkeypatch)


@pytest.fixture
async def fake_ctx(
    tmp_path: Path,
    concurrent_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[ToolContext]:
    ctx = _build_fake_ctx(tmp_path, concurrent_session_factory, monkeypatch)
    queue = ctx.queue

    async def text_provider(_task: dict[str, Any]) -> str:
        return "text"

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"text": 1}),
        provider_projection=text_provider,
        lanes=("text",),
    )
    worker.poll_interval = 60
    worker.heartbeat_interval = 60
    terminal_events: dict[str, asyncio.Event] = {}

    def record_terminal_events(_project_name, _source, changes) -> None:
        for change in changes:
            if change.get("entity_type") == "task":
                terminal_events.setdefault(str(change["entity_id"]), asyncio.Event()).set()

    async def wait_for_worker_task(task_id: str, *, queue: GenerationQueue) -> dict[str, Any]:
        terminal = terminal_events.setdefault(task_id, asyncio.Event())
        worker.wake()
        await asyncio.wait_for(terminal.wait(), timeout=5)
        return await wait_for_task(task_id, queue=queue)

    unregister = register_project_change_batch_listener(record_terminal_events)
    monkeypatch.setattr("server.tool_runtime.wait_for_task", wait_for_worker_task)
    assert await queue.acquire_or_renew_worker_lease(
        name=worker.lease_name,
        owner_id=worker.owner_id,
        ttl_seconds=worker.lease_ttl,
    )
    queue.set_worker_cancel_callback(worker.request_cancel)
    await worker.start()
    try:
        yield ctx
    finally:
        await worker.stop()
        queue.set_worker_cancel_callback(None)
        unregister()
