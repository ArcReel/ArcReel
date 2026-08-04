"""Smoke tests for task router endpoints against a real generation queue."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.auth import CurrentUserInfo, get_current_user, get_current_user_flexible
from server.routers import tasks as tasks_router
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.unit


def _build_app():
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.dependency_overrides[get_current_user_flexible] = lambda: CurrentUserInfo(
        id="default", sub="testuser", role="admin"
    )
    app.include_router(tasks_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


class TestTaskRouterEndpoints:
    async def test_task_router_endpoints_after_enqueue_claim_fail(self, generation_queue):
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
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
