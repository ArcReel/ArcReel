from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from lib.generation_queue import GenerationQueue
from lib.generation_worker import CapacityTable, GenerationWorker
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
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    queue.set_worker_cancel_callback(worker.request_cancel)
    await worker.start()
    try:
        yield ctx
    finally:
        await worker.stop()
        queue.set_worker_cancel_callback(None)
