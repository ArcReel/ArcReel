import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db import get_async_session
from lib.db.models.task import Task
from lib.db.repositories.task_repo import TaskRepository
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.generation.generation_worker import GenerationWorker
from lib.generation.task_failure import encode_failure
from lib.project.project_change_hints import register_project_change_batch_listener
from lib.project.project_manager import get_project_manager
from server.error_handlers import register_error_handlers
from server.routers import tasks
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import bind_safe_session_factory
from tests.integration.lib.generation.worker_support import capacity_table, reference_checkpoint_json, stub_executors

TASKS = "/api/v1/tasks"
BASE_TIME = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)


@pytest.fixture
def task_app(db_factory, generation_queue) -> FastAPI:
    projects = get_project_manager()
    for project in ("demo", "other"):
        projects.create_project(project)
        projects.create_project_metadata(project, project, "Anime", "narration")

    async def db_session() -> AsyncIterator[AsyncSession]:
        async with db_factory() as session:
            yield session

    app = FastAPI()
    override_auth(app)
    app.dependency_overrides[get_async_session] = db_session
    register_error_handlers(app)
    app.include_router(tasks.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


@pytest.fixture
async def task_client(task_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=task_app), base_url="http://test") as client:
        yield client


async def seed_tasks(
    sessions: async_sessionmaker[AsyncSession],
    prefix: str,
    count: int,
    *,
    status: str = "queued",
    project: str = "demo",
    timestamp: datetime = BASE_TIME,
) -> set[str]:
    ids = {f"{prefix}-{index}" for index in range(count)}
    async with sessions() as session:
        await session.execute(
            insert(Task),
            [
                {
                    "task_id": task_id,
                    "project_name": project,
                    "task_type": "character",
                    "media_type": "image",
                    "resource_id": task_id,
                    "status": status,
                    "queued_at": timestamp,
                    "updated_at": timestamp,
                }
                for task_id in ids
            ],
        )
        await session.commit()
    return ids


async def test_active_snapshot_includes_tasks_older_than_the_recent_page(task_client, db_factory):
    queued = await seed_tasks(db_factory, "queued", 1)
    running = await seed_tasks(db_factory, "running", 1, status="running")
    other = await seed_tasks(db_factory, "other", 1, project="other")
    history = await seed_tasks(
        db_factory, "history", 201, status="succeeded", timestamp=BASE_TIME + timedelta(seconds=1)
    )

    recent = await task_client.get(TASKS, params={"project_name": "demo", "page_size": 200})
    assert recent.status_code == 200
    assert recent.json()["total"] == 203
    assert len(recent.json()["items"]) == 200
    assert {row["task_id"] for row in recent.json()["items"]} <= history

    for path, expected in (
        (f"{TASKS}/active", queued | running | other),
        (f"{TASKS}/active?project_name=demo", queued | running),
        ("/api/v1/projects/demo/tasks/active", queued | running),
    ):
        response = await task_client.get(path)
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["truncated"] is False
        assert {row["task_id"] for row in snapshot["items"]} == expected
        assert {row["updated_at"] for row in snapshot["items"]} == {"2026-01-02T03:04:05.123456+00:00"}


async def test_active_snapshot_truncates_only_above_the_safety_limit(task_client, db_factory):
    await seed_tasks(db_factory, "initial", 5000)

    at_limit = await task_client.get(f"{TASKS}/active")
    assert at_limit.status_code == 200
    assert len(at_limit.json()["items"]) == 5000
    assert at_limit.json()["truncated"] is False

    await seed_tasks(db_factory, "extra", 1, status="running")
    above_limit = await task_client.get(f"{TASKS}/active")
    assert above_limit.status_code == 200
    assert len(above_limit.json()["items"]) == 5000
    assert above_limit.json()["truncated"] is True


async def test_lookup_matches_detail_localization_and_applies_project_scope(task_client, db_factory):
    await seed_tasks(db_factory, "failed", 1, status="failed")
    await seed_tasks(db_factory, "other", 1, project="other")
    async with db_factory() as session:
        task = await session.get_one(Task, "failed-0")
        task.error_message = encode_failure("artifact_download_failed", detail="403")
        task.execution_checkpoint_json = "{}"
        await session.commit()

    headers = {"Accept-Language": "en"}
    details = await task_client.get(f"{TASKS}/failed-0", headers=headers)
    assert details.status_code == 200
    detail = details.json()["task"]
    assert detail["error_code"] == "artifact_download_failed"
    assert detail["error_message"] == "Video generation succeeded but artifact download failed; retry the download: 403"
    assert "execution_checkpoint_json" not in detail

    ids = ["other-0", "absent", "failed-0", "other-0", "failed-0", "absent"]
    global_result = await task_client.post(f"{TASKS}/lookup", headers=headers, json={"task_ids": ids})
    assert global_result.status_code == 200
    assert [row["task_id"] for row in global_result.json()["items"]] == ["other-0", "failed-0"]
    assert global_result.json()["missing_ids"] == ["absent"]

    scoped_result = await task_client.post(
        f"{TASKS}/lookup", headers=headers, json={"task_ids": ids, "project_name": "demo"}
    )
    assert scoped_result.status_code == 200
    assert scoped_result.json() == {"items": [detail], "missing_ids": ["other-0", "absent"]}


async def test_active_snapshot_and_lookup_respect_repository_visibility_scope(task_client, db_factory, monkeypatch):
    await seed_tasks(db_factory, "visible", 1, status="running")
    await seed_tasks(db_factory, "hidden", 1, status="running")

    class VisibleOnlyTaskRepository(TaskRepository):
        def _scope_query(self, stmt, model):
            return stmt.where(Task.task_id == "visible-0")

    monkeypatch.setattr("lib.generation.generation_queue.TaskRepository", VisibleOnlyTaskRepository)

    snapshot = await task_client.get(f"{TASKS}/active")
    assert snapshot.status_code == 200
    assert [row["task_id"] for row in snapshot.json()["items"]] == ["visible-0"]

    lookup = await task_client.post(f"{TASKS}/lookup", json={"task_ids": ["visible-0", "hidden-0"]})
    assert lookup.status_code == 200
    assert [row["task_id"] for row in lookup.json()["items"]] == ["visible-0"]
    assert lookup.json()["missing_ids"] == ["hidden-0"]


async def test_lookup_limits_distinct_ids(task_client):
    ids = [str(index) for index in range(200)]
    at_limit = await task_client.post(f"{TASKS}/lookup", json={"task_ids": [*ids, ids[0]]})
    assert at_limit.status_code == 200
    assert at_limit.json() == {"items": [], "missing_ids": ids}

    above_limit = await task_client.post(f"{TASKS}/lookup", json={"task_ids": [*ids, "one-more"]})
    assert above_limit.status_code == 400


@pytest.mark.parametrize("legacy_timestamp", [None, "2026-01-02T11:04:05.123456+08:00"])
async def test_retry_download_advances_updated_at_when_clock_equals_failure(
    task_app, task_client, db_factory, monkeypatch, legacy_timestamp
):
    with time_machine.travel(BASE_TIME, tick=False):
        async with db_factory() as session:
            repo = TaskRepository(session)
            usage = UsageRepository(session)
            task = await repo.enqueue(
                project_name="demo",
                task_type="reference_video",
                media_type="video",
                resource_id="E1U1",
                script_file="scripts/episode_1.json",
                provider_id="ark",
            )
            task_id = task["task_id"]
            await repo.claim_next("video")
            await repo.persist_execution_checkpoint(task_id, reference_checkpoint_json(task_id), "ark")
            await repo.persist_provider_job_id(task_id, "existing-job")
            call_id = await usage.start_call(
                project_name="demo", call_type="video", model="model-v1", provider="ark", task_id=task_id
            )
            await usage.finish_call(call_id, status="failed", settlement=SettlementInput(cost_amount=0))
            await repo.mark_failed(task_id, encode_failure("artifact_download_failed", detail="403"))
            if legacy_timestamp is not None:
                await session.execute(
                    text("UPDATE tasks SET updated_at = :timestamp WHERE task_id = :task_id"),
                    {"timestamp": legacy_timestamp, "task_id": task_id},
                )
                await session.commit()

    release_download = asyncio.Event()
    terminal = asyncio.Event()

    async def download_existing_artifact(_task, *, job_id: str):
        await release_download.wait()
        return {"retrieved_job": job_id}

    def on_project_changes(project_name, _source, changes):
        if project_name == "demo" and any(
            change.get("entity_type") == "task" and change.get("entity_id") == task_id for change in changes
        ):
            terminal.set()

    queue = tasks.get_task_queue()
    bind_safe_session_factory(monkeypatch, db_factory)
    task_app.state.generation_worker = GenerationWorker(
        queue=queue,
        executor=stub_executors.execute,
        resume_executor=download_existing_artifact,
        capacity=capacity_table(),
    )
    unregister = register_project_change_batch_listener(on_project_changes)
    dispatched = False
    try:
        path = f"{TASKS}/{task_id}"
        failed = (await task_client.get(path)).json()["task"]
        assert failed["status"] == "failed"

        with time_machine.travel(BASE_TIME, tick=False):
            retried = await task_client.post(f"{path}/retry-download")
            assert retried.status_code == 200
            dispatched = True
            running = (await task_client.get(path)).json()["task"]
            assert running["status"] == "running"
            assert retried.json()["task"]["updated_at"] == running["updated_at"]
            assert failed["updated_at"] == "2026-01-02T03:04:05.123456+00:00"
            assert running["updated_at"] == "2026-01-02T03:04:05.123457+00:00"

            snapshot = await task_client.get(f"{TASKS}/active")
            assert snapshot.status_code == 200
            assert [(row["task_id"], row["model"]) for row in snapshot.json()["items"]] == [(task_id, "model-v1")]
    finally:
        release_download.set()
        try:
            if dispatched:
                await asyncio.wait_for(terminal.wait(), timeout=5)
        finally:
            unregister()

    completed = (await task_client.get(path)).json()["task"]
    assert completed["status"] == "succeeded"
    assert completed["result"] == {"retrieved_job": "existing-job"}
