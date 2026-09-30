"""本地渲染车道：不绑定供应商、全局串行，不阻塞其他车道。"""

from __future__ import annotations

import asyncio
from typing import Any

from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import wait_for_task
from lib.generation.generation_worker import CapacityTable, GenerationWorker
from tests.fakes import refuse_resume_execution


async def test_render_lane_runs_one_task_at_a_time_without_blocking_text(file_db_factory) -> None:
    queue = GenerationQueue(session_factory=file_db_factory)
    project_name = "render-lane-test-missing-project"
    first_render_started = asyncio.Event()
    second_render_started = asyncio.Event()
    release_first_render = asyncio.Event()
    render_calls = 0

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        nonlocal render_calls
        del claimed_provider_id
        if task["media_type"] == "render":
            render_calls += 1
            if render_calls == 1:
                first_render_started.set()
                await release_first_render.wait()
            else:
                second_render_started.set()
        return {}

    first = await queue.enqueue_task(
        project_name=project_name,
        task_type="render_final_cut",
        media_type="render",
        resource_id="tl-00000001.without_narration.no_subtitles",
    )
    second = await queue.enqueue_task(
        project_name=project_name,
        task_type="render_final_cut",
        media_type="render",
        resource_id="tl-00000002.without_narration.no_subtitles",
    )
    text = await queue.enqueue_task(
        project_name=project_name,
        task_type="text_episode_script",
        media_type="text",
        resource_id="episode-1",
        provider_id="text",
    )
    capacity = CapacityTable.from_env()
    capacity.replace({"render": {"render": 99}})
    worker = GenerationWorker(
        queue=queue,
        capacity=capacity,
        executor=execute,
        resume_executor=refuse_resume_execution,
    )
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    await worker.start()
    try:
        await first_render_started.wait()
        text_task = await wait_for_task(text["task_id"], 0.01, queue=queue)
        queued_second = await queue.get_task(second["task_id"])

        assert text_task["status"] == "succeeded"
        assert queued_second is not None
        assert queued_second["status"] == "queued"
        assert queued_second["provider_id"] == "render"
        assert not second_render_started.is_set()

        release_first_render.set()
        await second_render_started.wait()
        assert (await wait_for_task(first["task_id"], 0.01, queue=queue))["status"] == "succeeded"
        assert (await wait_for_task(second["task_id"], 0.01, queue=queue))["status"] == "succeeded"
    finally:
        release_first_render.set()
        await worker.stop()
