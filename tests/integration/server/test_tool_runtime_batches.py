from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import select

from lib.db.models.task import GenerationBatch
from lib.db.models.user import User
from lib.generation_batch import GenerationBatchRequestedItem, GenerationBatchRequestSnapshot
from lib.generation_queue import GenerationBatchNotFound, GenerationQueue
from lib.generation_queue_client import TaskSpec, batch_enqueue_only
from lib.generation_result import GenerationResultBuilder, GenerationSelectionMode
from lib.project_manager import ProjectManager
from lib.workflow_plan import WorkflowPlanRequest, build_workflow_plan
from lib.workflow_state import WorkflowStatus
from server.tool_runtime import CallerContext, ProjectScope, Services, submit_media_generation


class _Planner:
    async def get_plan(self, project_name: str, request: WorkflowPlanRequest):
        assert project_name == "demo"
        return build_workflow_plan(_status(), narration_delivery=request.narration_delivery)


class _Capabilities:
    async def video_capabilities_for_project(self, project: dict, *, capability=None) -> dict:
        return {"provider_id": "fake", "model": "video-1", "supported_durations": [4, 6]}


def _status() -> WorkflowStatus:
    return WorkflowStatus.model_validate(
        {
            "project_revision": "sha256-v1:project",
            "project": {"content_mode": "ad", "generation_mode": "storyboard", "grid_storyboard": False},
            "target": {
                "episode": 1,
                "script": "scripts/episode_1.json",
                "script_filename": "episode_1.json",
                "source": "source/episode_1.txt",
            },
            "state": "FINAL_SCRIPT",
            "blockers": [],
            "gates": {"script_plan_review": {"state": "not_applicable"}},
            "artifacts": {
                "asset_inventory": {"state": "not_applicable"},
                "asset_sheets": {},
                "script_plan": {"state": "not_applicable"},
                "script": {"state": "missing"},
                "storyboards": {"current_ids": [], "stale_ids": [], "missing_ids": []},
                "videos": {"current_ids": [], "stale_ids": [], "missing_ids": []},
                "audio": {"state": "not_applicable", "current_ids": [], "stale_ids": [], "missing_ids": []},
            },
            "next_action": {"type": "generate_script", "reason": "script missing"},
        }
    )


async def _enqueue_without_wait(**kwargs):
    _enqueued, failures = await batch_enqueue_only(**kwargs)
    return [], failures


@pytest.mark.parametrize("source", ["mcp", "embedded"])
async def test_repeated_host_submission_reuses_the_paid_task(
    session_factory,
    tmp_path: Path,
    source: Literal["mcp", "embedded"],
) -> None:
    queue = GenerationQueue(session_factory=session_factory)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=ProjectManager(tmp_path), workflow_planner=_Planner(), capabilities=_Capabilities(), queue=queue
    )
    spec = TaskSpec(
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        script_file="episode_01.json",
        source=source,
        unit_id="E1S01",
    )
    kwargs = {
        "scope": ProjectScope(project_name="demo", projects_root=tmp_path),
        "caller": CallerContext(user_id="default", source=source),
        "services": services,
        "operation": "generate_storyboards",
        "preflight": GenerationResultBuilder("generate_storyboards", GenerationSelectionMode.EXPLICIT).build(),
        "pending_ids": ["E1S01"],
        "specs": [spec],
        "embedded_waiter": _enqueue_without_wait,
    }

    first = await submit_media_generation(**kwargs)
    second = await submit_media_generation(**kwargs)

    assert len((await queue.list_tasks(project_name="demo"))["items"]) == 1
    assert first.batch.batch_id != second.batch.batch_id
    assert first.batch.members[0].task_id == second.batch.members[0].task_id
    assert second.batch.members[0].deduped is True


async def test_embedded_submission_keeps_non_default_user_on_batch_and_task(session_factory, tmp_path: Path) -> None:
    async with session_factory() as session:
        session.add(User(id="embedded-user", username="embedded-user"))
        await session.commit()
    queue = GenerationQueue(session_factory=session_factory)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=ProjectManager(tmp_path), workflow_planner=_Planner(), capabilities=_Capabilities(), queue=queue
    )
    spec = TaskSpec(
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        script_file="episode_01.json",
        source="embedded",
        unit_id="E1S01",
    )

    submission = await submit_media_generation(
        scope=ProjectScope(project_name="demo", projects_root=tmp_path),
        caller=CallerContext(user_id="embedded-user", source="embedded"),
        services=services,
        operation="generate_storyboards",
        preflight=GenerationResultBuilder("generate_storyboards", GenerationSelectionMode.EXPLICIT).build(),
        pending_ids=["E1S01"],
        specs=[spec],
        embedded_waiter=_enqueue_without_wait,
    )

    task_id = submission.batch.members[0].task_id
    assert task_id
    task = await queue.get_task(task_id)
    assert task is not None
    assert task["user_id"] == "embedded-user"
    with pytest.raises(GenerationBatchNotFound):
        await queue.get_generation_batch(project_name="demo", batch_id=submission.batch.batch_id)


@pytest.mark.parametrize("source", ["mcp", "embedded"])
@pytest.mark.parametrize("cancel_state", ["fresh", "task", "membership"])
async def test_media_submission_cancellation_only_cleans_a_fresh_batch(
    session_factory,
    tmp_path: Path,
    source: Literal["mcp", "embedded"],
    cancel_state: str,
) -> None:
    reached_cancel_seam = asyncio.Event()

    class CancellationQueue(GenerationQueue):
        async def enqueue_task(self, **kwargs):
            if cancel_state == "fresh":
                reached_cancel_seam.set()
                await asyncio.Event().wait()
            return await super().enqueue_task(**kwargs)

        async def get_generation_batch(self, **kwargs):
            if cancel_state != "fresh" and not reached_cancel_seam.is_set():
                reached_cancel_seam.set()
                await asyncio.Event().wait()
            return await super().get_generation_batch(**kwargs)

    queue = CancellationQueue(session_factory=session_factory)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    historical_batch_id = await queue.create_generation_batch(
        project_name="demo",
        operation="historical",
        requested=GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.EXPLICIT,
            requested=[GenerationBatchRequestedItem(unit_id="E1S01" if cancel_state == "membership" else "old")],
        ),
        blocked=[],
        source=source,
    )
    historical_task = await GenerationQueue.enqueue_task(
        queue,
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01" if cancel_state == "membership" else "old",
        script_file="episode_01.json",
        batch_id=historical_batch_id,
        batch_unit_id="E1S01" if cancel_state == "membership" else "old",
    )
    services = Services(
        projects=ProjectManager(tmp_path), workflow_planner=_Planner(), capabilities=_Capabilities(), queue=queue
    )
    spec = TaskSpec(
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        script_file="episode_01.json",
        source="mcp",
        unit_id="E1S01",
    )

    submission = asyncio.create_task(
        submit_media_generation(
            scope=ProjectScope(project_name="demo", projects_root=tmp_path),
            caller=CallerContext(user_id="default", source=source),
            services=services,
            operation="generate_storyboards",
            preflight=GenerationResultBuilder("generate_storyboards", GenerationSelectionMode.EXPLICIT).build(),
            pending_ids=["E1S01"],
            specs=[spec],
            embedded_waiter=_enqueue_without_wait if source == "embedded" else None,
        )
    )
    await reached_cancel_seam.wait()
    submission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submission

    async with session_factory() as session:
        batch_ids = set((await session.scalars(select(GenerationBatch.batch_id))).all())
    if cancel_state != "fresh":
        submitted_batch_id = (batch_ids - {historical_batch_id}).pop()
        submitted = await GenerationQueue.get_generation_batch(queue, project_name="demo", batch_id=submitted_batch_id)
        assert [member.unit_id for member in submitted.members] == ["E1S01"]
        assert submitted.members[0].deduped is (cancel_state == "membership")
        if cancel_state == "membership":
            assert submitted.members[0].task_id == historical_task["task_id"]
        else:
            submitted_task_id = submitted.members[0].task_id
            assert submitted_task_id is not None
            submitted_task = await queue.get_task(submitted_task_id)
            assert submitted_task is not None
            assert submitted_task["batch_id"] == submitted_batch_id
    else:
        assert batch_ids == {historical_batch_id}
    historical = await GenerationQueue.get_generation_batch(queue, project_name="demo", batch_id=historical_batch_id)
    assert [(member.unit_id, member.task_id) for member in historical.members] == [
        ("E1S01" if cancel_state == "membership" else "old", historical_task["task_id"])
    ]
