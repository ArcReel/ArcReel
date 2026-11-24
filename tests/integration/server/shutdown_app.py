"""真实服务进程的本地执行替身，stdin 放行正在执行的渲染任务。"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import server.app as server
from lib.generation.generation_worker import WorkerStoppingError
from lib.project.project_manager import get_project_manager

app = server.app


async def slow_render(task, *, claimed_provider_id):
    if task["resource_id"] != "first":
        raise AssertionError("queued task unexpectedly started")
    await asyncio.to_thread(sys.stdin.buffer.read, 1)
    return {"rendered": True}


@asynccontextmanager
async def seeded_lifespan(app):
    async with server.lifespan(app):
        pm = get_project_manager()
        await asyncio.to_thread(pm.create_project, "demo")
        await asyncio.to_thread(pm.create_project_metadata, "demo")
        await server.assistant.assistant_service.meta_store.create("demo", "session")
        worker = app.state.generation_worker
        task_ids = []
        for resource_id in ("first", "second"):
            row = await worker.queue.enqueue_task(
                project_name="demo",
                task_type="render_video",
                media_type="render",
                resource_id=resource_id,
                provider_id="render",
            )
            task_ids.append(row["task_id"])
        worker.wake()
        yield
        with pytest.raises(WorkerStoppingError):
            await worker.retry_artifact_download(task_ids[1])
    await asyncio.to_thread((Path(os.environ["ARCREEL_DATA_DIR"]) / "stopped").touch)


def create_app():
    server.execute_generation_task = slow_render
    server.check_sandbox_available = lambda: False
    server.start_catalog_sync = lambda *_: None
    app.router.lifespan_context = seeded_lifespan
    return app
