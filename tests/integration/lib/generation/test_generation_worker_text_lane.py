from __future__ import annotations

import asyncio
from typing import Any

from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import CapacityTable, GenerationWorker
from tests.fakes import refuse_resume_execution


async def test_backlog_keeps_queued_tasks_cancellable_and_drains_all_admitted_tasks(file_db_factory) -> None:
    class ObservedQueue(GenerationQueue):
        async def acquire_or_renew_worker_lease(self, **kwargs):
            renewed = await super().acquire_or_renew_worker_lease(**kwargs)
            claimed_per_cycle.append(0)
            return renewed

        async def claim_next_task(self, media_type, **kwargs):
            if media_type == "text":
                text_claim_cycles.append(len(claimed_per_cycle))
            task = await super().claim_next_task(media_type=media_type, **kwargs)
            if task and media_type == "text":
                claimed_per_cycle[-1] += 1
            if media_type == "render" and len(claimed_per_cycle) >= 5:
                fifth_render_turn.set()
            return task

    claimed_per_cycle: list[int] = []
    text_claim_cycles: list[int] = []
    fifth_render_turn = asyncio.Event()
    queue = ObservedQueue(session_factory=file_db_factory)

    async def enqueue(index):
        return await queue.enqueue_task(
            project_name=f"project-{index % 2}",
            resource_id=f"episode-{index}",
            task_type="text_episode_script",
            media_type="text",
            provider_id="text",
        )

    texts = [await enqueue(i) for i in range(18)]
    assert (await enqueue(0))["task_id"] == texts[0]["task_id"]
    media_done = {lane: asyncio.Event() for lane in ("image", "render")}
    for lane, task_type in (("image", "storyboard"), ("render", "render_final_cut")):
        await queue.enqueue_task(
            project_name="project-0", resource_id=lane, task_type=task_type, media_type=lane, provider_id=lane
        )
    at_capacity = asyncio.Event()
    released_first_fifteen = asyncio.Event()
    exited = set()
    release = [asyncio.Event() for _ in range(16)]
    started: list[str] = []

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None) -> dict[str, Any]:
        lane = task["media_type"]
        if lane == "text":
            started.append(task["task_id"])
            if len(started) == 16:
                at_capacity.set()
            await release[len(started) - 1].wait()
            exited.add(task["task_id"])
            if len(exited) == 15:
                released_first_fifteen.set()
        else:
            media_done[lane].set()
        return {}

    worker = GenerationWorker(
        queue=queue, capacity=CapacityTable.from_env(), executor=execute, resume_executor=refuse_resume_execution
    )
    await worker.start()
    try:
        async with asyncio.timeout(8):
            await fifth_render_turn.wait()
            await at_capacity.wait()
            for done in media_done.values():
                await done.wait()
        worker.stop_admission()
        assert claimed_per_cycle[:5] == [4, 4, 4, 4, 0]
        assert max(text_claim_cycles) == 4
        assert len(started) == 16
        queued = [t["task_id"] for t in texts if t["task_id"] not in started]
        for task_id in queued:
            row = await queue.get_task(task_id)
            assert row
            assert row["status"] == "queued"
            assert row["started_at"] is None
            await queue.cancel_task(task_id)
        stopping = asyncio.create_task(worker.stop())
        for event in release[:-1]:
            event.set()
        async with asyncio.timeout(8):
            await released_first_fifteen.wait()
        last = await queue.get_task(started[-1])
        assert last
        assert last["status"] == "running"
        assert not stopping.done()
        release[-1].set()
        await stopping
        for task_id in started:
            row = await queue.get_task(task_id)
            assert row
            assert row["status"] == "succeeded"
    finally:
        for event in release:
            event.set()
        await worker.stop()


async def test_records_execution_model_after_queueing_and_logs_only_safe_fields(file_db_factory, monkeypatch, caplog):
    import json
    import logging

    from lib.backends.text_backends.base import TextTaskType
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository
    from lib.generation.generation_result import GenerationAction, GenerationProblem, QueuedGenerationError
    from lib.video_runtime.text import resolve_and_assemble_text
    from tests.fakes import bind_session_factory

    bind_session_factory(monkeypatch, file_db_factory)
    secret = "SYNTHETIC_PRIVATE_TEXT_4116"
    async with file_db_factory.begin() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key=secret,
            models=[
                {"model_id": "house-model", "display_name": "House", "endpoint": "openai-chat", "is_enabled": True}
            ],
        )
        actual = make_provider_id(provider.id)
        await ConfigService(session).set_setting("default_text_backend", "openai/old")
    queue = GenerationQueue(session_factory=file_db_factory)
    rows = [
        await queue.enqueue_task(
            project_name="project",
            task_type="text_episode_script",
            resource_id=str(idx),
            media_type="text",
            provider_id=old,
            payload={"instructions": secret},
        )
        for idx, old in enumerate((None, "text", "openai"))
    ]
    async with file_db_factory.begin() as session:
        await ConfigService(session).set_setting("default_text_backend", f"{actual}/house-model")

    executed = set()
    all_started = asyncio.Event()

    async def execute(task, *, claimed_provider_id):
        executed.add(task["task_id"])
        if len(executed) == len(rows):
            all_started.set()
        await resolve_and_assemble_text(TextTaskType.SCRIPT)
        if task["resource_id"] == "1":
            raise QueuedGenerationError(
                GenerationProblem(code="text_output_truncated", detail=secret, action=GenerationAction.RETRY)
            )
        if task["resource_id"] == "2":
            raise ValueError(secret)
        return {}

    caplog.set_level(logging.INFO)
    worker = GenerationWorker(
        queue=queue, capacity=CapacityTable.from_env(), executor=execute, resume_executor=refuse_resume_execution
    )
    await worker.start()
    try:
        async with asyncio.timeout(8):
            await all_started.wait()
    finally:
        await worker.stop()
    for idx, row in enumerate(rows):
        task = await queue.get_task(row["task_id"])
        assert task
        assert task["provider_id"] == actual
        assert task["status"] == ("failed" if idx else "succeeded")
    assert secret not in caplog.text
    events = [
        json.loads(rec.getMessage()) for rec in caplog.records if rec.name == "lib.generation.text_task_lifecycle"
    ]
    for idx, row in enumerate(rows):
        started, resolved, terminal = [e for e in events if e["task_id"] == row["task_id"]]
        assert started["status"] == "running"
        assert started["queue_ms"] >= 0
        assert resolved["provider_id"] == terminal["provider_id"] == actual
        assert terminal["task_type"] == "text_episode_script"
        assert terminal["status"] == ("failed" if idx else "succeeded")
        assert terminal["failure_code"] == (None, "text_output_truncated", "unclassified")[idx]
        assert terminal["execution_ms"] >= 0
