from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import func, select

from lib.api_errors import ConflictError
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.models.task import GenerationBatch
from lib.episode_planner import EpisodePlanningError, EpisodePlanSummary, PlanResult
from lib.generation_batch import GenerationBatchRequestedItem, GenerationBatchRequestSnapshot
from lib.generation_queue import CompensableGenerationResult, GenerationQueue
from lib.generation_result import GenerationAction, GenerationSelectionMode, problem_from_task_failure
from lib.project_manager import ProjectManager
from lib.project_migration_failure import ProjectMigrationError, record_migration_failure
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server import tool_runtime
from server.text_generation import CompensableTextGenerationResult, TextGenerationRequest
from server.tool_runtime import (
    CallerContext,
    PlanEpisodesRequest,
    ProjectScope,
    Services,
    ToolRequest,
    execute_queued_text_task,
    generate_episode_script,
    generate_step1,
    plan_episodes,
)


@pytest.mark.parametrize(
    ("project_name", "content_mode", "generation_mode", "handler", "expected_task_type"),
    [
        ("script", "ad", "storyboard", "script", "text_episode_script"),
        ("drama", "drama", "storyboard", "step1", "text_drama_step1"),
        ("narration", "narration", "storyboard", "step1", "text_narration_step1"),
        ("reference", "narration", "reference_video", "step1", "text_reference_step1"),
        ("planning", "narration", "storyboard", "plan", "text_episode_plan"),
    ],
)
async def test_all_text_long_calls_submit_single_member_batches(
    tmp_path: Path,
    file_db_factory,
    project_name: str,
    content_mode: str,
    generation_mode: str,
    handler: str,
    expected_task_type: str,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project(project_name, content_mode=content_mode)
    projects.create_project_metadata(project_name, project_name, "", content_mode)
    projects.update_project(project_name, lambda project: project.update(generation_mode=generation_mode))
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    scope = ProjectScope(project_name=project_name, projects_root=projects.projects_root)
    caller = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")
    if handler == "script":
        outcome = await generate_episode_script(ToolRequest(TextGenerationRequest(episode=1)), scope, caller, services)
    elif handler == "step1":
        outcome = await generate_step1(ToolRequest(TextGenerationRequest(episode=1)), scope, caller, services)
    else:
        outcome = await plan_episodes(ToolRequest(PlanEpisodesRequest()), scope, caller, services)

    assert outcome.problem is None
    batch = outcome.value
    assert batch is not None
    assert batch.done is False
    assert len(batch.members) == 1
    assert batch.members[0].task_type == expected_task_type
    assert batch.poll_after_seconds is not None


async def test_text_mcp_rejects_lost_worker_lease_without_persisting_queue_state(
    tmp_path: Path,
    file_db_factory,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("drama", content_mode="drama")
    projects.create_project_metadata("drama", "Drama", "", "drama")
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="lost-worker", ttl_seconds=60)
    await queue.release_worker_lease(name="default", owner_id="lost-worker")
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )

    outcome = await generate_step1(
        ToolRequest(TextGenerationRequest(episode=1)),
        ProjectScope(project_name="drama", projects_root=projects.projects_root),
        CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
        services,
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "generation_enqueue_failed"
    assert (await queue.list_tasks(project_name="drama"))["total"] == 0
    async with file_db_factory() as session:
        batch_count = await session.scalar(select(func.count()).select_from(GenerationBatch))
    assert batch_count == 0


async def test_text_mcp_migration_rejection_cleans_only_the_fresh_batch(
    tmp_path: Path,
    file_db_factory,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("drama", content_mode="drama")
    projects.create_project_metadata("drama", "Drama", "", "drama")
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    request = ToolRequest(TextGenerationRequest(episode=1))
    scope = ProjectScope(project_name="drama", projects_root=projects.projects_root)
    caller = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")
    submitted = await generate_step1(request, scope, caller, services)
    assert submitted.value is not None
    existing_batch_id = submitted.value.batch_id
    record_migration_failure(
        projects.get_project_path("drama"),
        ProjectMigrationError("repair required"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )

    with pytest.raises(ConflictError, match="project_migration_failed"):
        await generate_step1(request, scope, caller, services)

    assert (await queue.list_tasks(project_name="drama"))["total"] == 1
    existing_batch = await queue.get_generation_batch(project_name="drama", batch_id=existing_batch_id)
    assert len(existing_batch.members) == 1
    async with file_db_factory() as session:
        batch_count = await session.scalar(select(func.count()).select_from(GenerationBatch))
    assert batch_count == 1


@pytest.mark.parametrize("source", ["mcp", "embedded"])
@pytest.mark.parametrize("cancel_state", ["fresh", "task", "membership"])
async def test_text_submission_cancellation_only_cleans_a_fresh_batch(
    tmp_path: Path,
    concurrent_session_factory,
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

    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("drama", content_mode="drama")
    projects.create_project_metadata("drama", "Drama", "", "drama")
    queue = CancellationQueue(session_factory=concurrent_session_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    historical_batch_id = await queue.create_generation_batch(
        project_name="drama",
        operation="historical",
        requested=GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.EXPLICIT,
            requested=[GenerationBatchRequestedItem(unit_id="episode-1" if cancel_state == "membership" else "old")],
        ),
        blocked=[],
        source="mcp",
    )
    historical_task = await GenerationQueue.enqueue_task(
        queue,
        project_name="drama",
        task_type="text_drama_step1",
        media_type="text",
        resource_id="episode-1" if cancel_state == "membership" else "old",
        payload={
            "episode": 1,
            "source": None,
            "instructions": None,
            "dry_run": False,
            "projects_root": str(projects.projects_root),
        },
        batch_id=historical_batch_id,
        batch_unit_id="episode-1" if cancel_state == "membership" else "old",
    )
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )

    submission = asyncio.create_task(
        generate_step1(
            ToolRequest(TextGenerationRequest(episode=1)),
            ProjectScope(project_name="drama", projects_root=projects.projects_root),
            CallerContext(user_id=DEFAULT_USER_ID, source=source),
            services,
        )
    )
    await reached_cancel_seam.wait()
    submission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submission

    async with concurrent_session_factory() as session:
        batch_ids = set((await session.scalars(select(GenerationBatch.batch_id))).all())
    if cancel_state != "fresh":
        submitted_batch_id = (batch_ids - {historical_batch_id}).pop()
        submitted = await GenerationQueue.get_generation_batch(queue, project_name="drama", batch_id=submitted_batch_id)
        assert [member.unit_id for member in submitted.members] == ["episode-1"]
        assert submitted.members[0].deduped is (cancel_state == "membership")
        if cancel_state == "membership":
            assert submitted.members[0].task_id == historical_task["task_id"]
        else:
            submitted_task_id = submitted.members[0].task_id
            assert submitted_task_id is not None
            submitted_task = await queue.get_task(submitted_task_id)
            assert submitted_task is not None and submitted_task["batch_id"] == submitted_batch_id
    else:
        assert batch_ids == {historical_batch_id}
    historical = await GenerationQueue.get_generation_batch(queue, project_name="drama", batch_id=historical_batch_id)
    assert [(member.unit_id, member.task_id) for member in historical.members] == [
        ("episode-1" if cancel_state == "membership" else "old", historical_task["task_id"])
    ]


async def test_queued_plan_ignores_internal_payload_and_preserves_typed_failure(tmp_path: Path) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("planning", content_mode="narration")
    projects.create_project_metadata("planning", "Planning", "", "narration")
    task = {
        "task_id": "task-plan",
        "project_name": "planning",
        "task_type": "text_episode_plan",
        "payload": {"instructions": "按章节", "projects_root": str(projects.projects_root)},
    }

    class Planner:
        @classmethod
        async def create(cls, _project_path):
            return cls()

        async def plan(self, instructions=None):
            assert instructions == "按章节"
            return PlanResult(
                episodes=[
                    EpisodePlanSummary(
                        episode=1,
                        title="第一集",
                        hook="悬念",
                        reading_units=800,
                        ledger_status="planned",
                    )
                ],
                cursor=None,
            )

    result = await execute_queued_text_task(task, planner_cls=Planner)  # type: ignore[arg-type]
    assert result["episodes"][0]["title"] == "第一集"

    class FailingPlanner(Planner):
        async def plan(self, instructions=None):
            raise EpisodePlanningError("invalid source window")

    with pytest.raises(RuntimeError) as raised:
        await execute_queued_text_task(task, planner_cls=FailingPlanner)  # type: ignore[arg-type]
    problem = problem_from_task_failure(str(raised.value))
    assert problem.code == "episode_planning_failed"
    assert problem.action is GenerationAction.RETRY


async def test_queued_step1_preserves_runtime_cancellation_receipt(tmp_path: Path, monkeypatch) -> None:
    compensations: list[str] = []

    async def handler(*_args, **_kwargs):
        return CompensableTextGenerationResult("committed", lambda: compensations.append("restored"))

    monkeypatch.setattr(tool_runtime, "generate_reference_step1", handler)
    result = await execute_queued_text_task(
        {
            "task_id": "task-step1",
            "project_name": "demo",
            "task_type": "text_reference_step1",
            "payload": {"episode": 1, "projects_root": str(tmp_path)},
        }
    )

    assert isinstance(result, CompensableGenerationResult)
    assert result == {"message": "committed"}
    result.compensate_cancelled()
    assert compensations == ["restored"]
