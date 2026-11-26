import asyncio
import json
import logging
from contextlib import aclosing

import httpx
from fastapi import FastAPI, Request
from pydantic import BaseModel
from sqlalchemy import select

from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import TextGenerationRequest
from lib.backends.text_backends.openai import OpenAITextBackend
from lib.backends.text_generator import TextGenerator
from lib.billing.ledger import Ledger
from lib.db.models.api_call import ApiCall
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import GenerationWorker
from lib.infra.data_root_layout import DataRootLayout
from server.routers import tasks
from server.routers.project_events import stream_project_events
from server.services.project.project_events import ProjectEventService
from tests.fakes import bind_safe_session_factory, bind_session_factory, bounded_poll_clock, refuse_resume_execution
from tests.http_capture import capture_http, request_json


class _Script(BaseModel):
    title: str


def _response(content):
    frames = [
        {"choices": [{"index": 0, "delta": {"content": "<think>hidden</thi"}}]},
        {"choices": [{"index": 0, "delta": {"content": "nk>" + content}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 30}},
    ]
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        content="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n",
    )


async def test_worker_stream_snapshot_and_partial_settlement(file_db_factory, monkeypatch, caplog):
    bind_safe_session_factory(monkeypatch, file_db_factory)
    bind_session_factory(monkeypatch, file_db_factory)
    queue = GenerationQueue(session_factory=file_db_factory)
    service = ProjectEventService(data_root=DataRootLayout.current().root)
    await asyncio.to_thread(service.pm.create_project, "demo")
    await asyncio.to_thread(service.pm.create_project_metadata, "demo", "Demo", "Anime", "narration")
    marker = "SYNTHETIC_PRIVATE_4121"
    generator = TextGenerator(
        OpenAITextBackend(
            api_key=marker, model="qwen-plus", provider_name="dashscope", base_url="https://relay.example/v1"
        ),
        Ledger(session_factory=file_db_factory),
        "dashscope",
        purpose=CallPurpose.SCRIPT_GENERATION,
    )
    native_started = asyncio.Event()
    peer_finished = asyncio.Event()
    allow_failure = asyncio.Event()

    async def execute(task, *, claimed_provider_id):
        peer = task["resource_id"] == "peer"
        result = await generator.generate(
            TextGenerationRequest(prompt=task["payload"]["prompt"], response_schema=None if peer else _Script),
            project_name="demo",
        )
        if peer:
            peer_finished.set()
        return {"length": len(result.text)}

    worker = GenerationWorker(queue=queue, executor=execute, resume_executor=refuse_resume_execution)
    worker.text_progress.publish = service.publish_task_progress
    app = FastAPI()
    app.state.generation_worker = worker
    app.include_router(tasks.router)
    ids = {}
    for resource in ("structured", "peer"):
        row = await queue.enqueue_task(
            project_name="demo",
            resource_id=resource,
            task_type="text_episode_script",
            media_type="text",
            payload={"prompt": marker + resource},
        )
        ids[resource] = row["task_id"]

    fallback_attempts = 0

    async def upstream(request):
        nonlocal fallback_attempts
        body = request_json(request)
        if marker + "peer" in str(body):
            await native_started.wait()
            return _response("ok")
        if "response_format" in body:
            native_started.set()
            await peer_finished.wait()
            return _response(marker)
        fallback_attempts += 1
        if fallback_attempts == 1:
            return httpx.Response(429, json={"error": {"message": "busy"}})
        await allow_failure.wait()
        return httpx.Response(400, json={"error": {"message": "rejected"}})

    caplog.set_level(logging.INFO)
    progress = []

    async def receive():
        await asyncio.Event().wait()

    request = Request({"type": "http", "app": app}, receive=receive)
    await service.start()
    try:
        async with (
            asyncio.timeout(15),
            aclosing(stream_project_events("demo", request, service)) as stream,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app") as client,
        ):
            with capture_http(assert_all_called=True) as http, bounded_poll_clock(step=0.01):
                http.post("https://relay.example/v1/chat/completions").mock(side_effect=upstream)
                assert (await anext(stream)).event == "snapshot"
                await worker.start()
                try:
                    async for event in stream:
                        if event.event != "task_progress":
                            continue
                        payload = event.data
                        if payload["task_id"] != ids["structured"]:
                            continue
                        progress.append(payload)
                        if any(p["phase"] == "retrying" for p in progress) and payload["phase"] == "waiting":
                            break
                    response = await client.get("/projects/demo/tasks/active")
                    response.raise_for_status()
                    row = next(row for row in response.json()["items"] if row["task_id"] == ids["structured"])
                    assert row["progress"] == progress[-1]
                finally:
                    native_started.set()
                    peer_finished.set()
                    allow_failure.set()
                    await worker.stop()
    finally:
        await service.shutdown()
    assert [p["seq"] for p in progress] == sorted({p["seq"] for p in progress})
    assert [p["phase"] for p in progress] == ["waiting", "receiving", "receiving", "waiting", "retrying", "waiting"]
    assert progress[1]["thinking"]
    assert progress[1]["characters"] == 0
    assert not progress[2]["thinking"]
    assert progress[2]["characters"] == len(marker)
    assert progress[4]["attempt"] == 2
    assert progress[4]["max_attempts"] == 3
    for key, status in [("structured", "failed"), ("peer", "succeeded")]:
        task = await queue.get_task(ids[key])
        assert task
        assert task["status"] == status
        assert worker.text_progress.get(ids[key]) is None
    async with file_db_factory() as session:
        failure = (await session.scalars(select(ApiCall).where(ApiCall.status == "failed"))).one()
        assert (failure.input_tokens, failure.output_tokens) == (100, 30)
        assert failure.task_id == ids["structured"]
        assert failure.cost_amount > 0
    assert "text_generation" in caplog.text
    assert "text_task" in caplog.text
    assert marker not in caplog.text
