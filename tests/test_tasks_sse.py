"""Tests for task router endpoints and SSE events."""

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from server.routers import tasks as tasks_router


def _build_app():
    app = FastAPI()
    app.include_router(tasks_router.router, prefix="/api/v1")
    return app


class TestTaskRouterAndEvents:
    async def test_task_router_endpoints_and_incremental_events(self, generation_queue):
        queue = generation_queue
        task = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="webui",
        )
        await queue.claim_next_task(media_type="image")
        await queue.mark_task_failed(task["task_id"], "mock fail")

        app = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            task_resp = await client.get(f"/api/v1/tasks/{task['task_id']}")
            assert task_resp.status_code == 200
            assert task_resp.json()["task"]["status"] == "failed"

            list_resp = await client.get("/api/v1/tasks?project_name=demo")
            assert list_resp.status_code == 200
            assert list_resp.json()["total"] >= 1

            stats_resp = await client.get("/api/v1/tasks/stats?project_name=demo")
            assert stats_resp.status_code == 200
            stats = stats_resp.json()["stats"]
            assert stats["failed"] == 1

        events = await queue.get_events_since(last_event_id=0, project_name="demo")
        assert len(events) >= 3

        last_running_id = events[1]["id"]
        incremental = await queue.get_events_since(last_event_id=last_running_id, project_name="demo")
        assert all(event["id"] > last_running_id for event in incremental)
        assert any(event["event_type"] == "failed" for event in incremental)

    async def test_sse_task_event_has_frontend_shape(self, generation_queue):
        """SSE task 事件应匹配前端 TaskStreamTaskPayload 结构。"""
        from server.routers.tasks import _transform_task_event

        queue = generation_queue
        task = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S02",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="webui",
        )

        events = await queue.get_events_since(last_event_id=0, project_name="demo")
        assert len(events) >= 1

        stats = await queue.get_task_stats(project_name="demo")
        transformed = _transform_task_event(events[0], stats)

        assert transformed["action"] == "created"
        assert transformed["task"]["task_id"] == task["task_id"]
        assert transformed["task"]["status"] == "queued"
        assert "queued" in transformed["stats"]
        assert "running" in transformed["stats"]
        assert "total" in transformed["stats"]
        assert transformed["stats"]["queued"] >= 1
