"""Tests for stage_provider_media_for_task."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_provider_media_staging_cleanup_survives_repeated_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from lib.reference_video.execution_checkpoint import ProviderMediaInput
    from server.services import reference_video_tasks as rvt

    project_path = tmp_path / "demo"
    image = project_path / "characters" / "Alice.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    staging_started = threading.Event()
    release_staging = threading.Event()
    staging_finished = threading.Event()
    real_stage_provider_media = rvt.stage_provider_media

    def _delayed_stage(*args, **kwargs):
        try:
            staged = real_stage_provider_media(*args, **kwargs)
            staging_started.set()
            release_staging.wait(timeout=5)
            return staged
        finally:
            staging_finished.set()

    monkeypatch.setattr(rvt, "stage_provider_media", _delayed_stage)
    task = asyncio.create_task(
        rvt._stage_provider_media_for_task(
            project_path,
            "task-double-cancel",
            (ProviderMediaInput(image, "reference_image", "character", "Alice", "sheet"),),
        )
    )
    assert await asyncio.to_thread(staging_started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release_staging.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert await asyncio.to_thread(staging_finished.wait, 5)
    assert not (project_path / ".arcreel" / "tasks" / "task-double-cancel" / "provider_media").exists()
