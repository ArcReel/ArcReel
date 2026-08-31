from __future__ import annotations

import asyncio
from typing import Any

from lib.generation_queue import GenerationQueue
from lib.generation_queue_client import wait_for_task
from lib.generation_worker import CapacityTable, GenerationWorker


async def test_text_lane_is_serial_and_does_not_block_media(file_db_factory) -> None:
    queue = GenerationQueue(session_factory=file_db_factory)
    project_name = "text-lane-test-missing-project"
    first_text_started = asyncio.Event()
    second_text_started = asyncio.Event()
    release_first_text = asyncio.Event()
    text_calls = 0

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        nonlocal text_calls
        del claimed_provider_id
        if task["media_type"] == "text":
            text_calls += 1
            if text_calls == 1:
                first_text_started.set()
                await release_first_text.wait()
            else:
                second_text_started.set()
        return {}

    async def provider(task: dict[str, Any]) -> str:
        return str(task["provider_id"])

    first = await queue.enqueue_task(
        project_name=project_name,
        task_type="text_episode_script",
        media_type="text",
        resource_id="episode-1",
        provider_id="text",
    )
    second = await queue.enqueue_task(
        project_name=project_name,
        task_type="text_episode_script",
        media_type="text",
        resource_id="episode-2",
        provider_id="text",
    )
    image = await queue.enqueue_task(
        project_name=project_name,
        task_type="storyboard",
        media_type="image",
        resource_id="scene-1",
        provider_id="image",
    )
    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"image": 1, "text": 1}),
        provider_projection=provider,
        executor=execute,
        lanes=("image", "text"),
    )
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    await worker.start()
    try:
        await first_text_started.wait()
        image_task = await wait_for_task(image["task_id"], 0.01, queue=queue)
        queued_second = await queue.get_task(second["task_id"])

        assert image_task["status"] == "succeeded"
        assert queued_second is not None
        assert queued_second["status"] == "queued"
        assert not second_text_started.is_set()

        release_first_text.set()
        await second_text_started.wait()
        assert (await wait_for_task(first["task_id"], 0.01, queue=queue))["status"] == "succeeded"
        assert (await wait_for_task(second["task_id"], 0.01, queue=queue))["status"] == "succeeded"
    finally:
        release_first_text.set()
        await worker.stop()
