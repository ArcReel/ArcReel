from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import pytest
from sqlalchemy import func, select

from lib.api_errors import ConflictError
from lib.artifact_activation import activate_artifact_target_state
from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.async_thread import run_sync_transaction
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.models.task import GenerationBatch
from lib.draft_quarantine import (
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_SCRIPT_PLAN,
    quarantine_path,
)
from lib.episode_planner import EpisodePlanner, EpisodePlanningError, EpisodePlanSummary, PlanResult
from lib.generation_batch import GenerationBatchRequestedItem, GenerationBatchRequestSnapshot
from lib.generation_queue import CompensableGenerationResult, GenerationQueue
from lib.generation_queue_client import wait_for_task
from lib.generation_result import GenerationAction, GenerationSelectionMode, problem_from_task_failure
from lib.generation_worker import CapacityTable, GenerationWorker
from lib.project_manager import ProjectManager
from lib.project_migration_failure import ProjectMigrationError, record_migration_failure
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.text_backends.base import TextGenerationResult as BackendTextGenerationResult
from lib.workflow_state import WorkflowStateService
from server import tool_runtime
from server.text_generation import (
    CompensableTextGenerationResult,
    TextGenerationRequest,
)
from server.tool_runtime import (
    CallerContext,
    PlanEpisodesRequest,
    ProjectScope,
    Services,
    ToolRequest,
    execute_queued_text_task,
    generate_episode_script,
    generate_script_plan,
    plan_episodes,
)


async def _start_text_worker(
    queue: GenerationQueue,
    executor: Callable[..., Awaitable[dict[str, Any]]],
    *,
    dispatch_cancellation: bool = False,
) -> GenerationWorker:
    async def text_provider(_task: dict[str, Any]) -> str:
        return "text"

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"text": 1}),
        provider_projection=text_provider,
        executor=executor,
        lanes=("text",),
    )
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    assert await queue.acquire_or_renew_worker_lease(
        name=worker.lease_name,
        owner_id=worker.owner_id,
        ttl_seconds=worker.lease_ttl,
    )
    if dispatch_cancellation:
        queue.set_worker_cancel_callback(worker.request_cancel)
    await worker.start()
    return worker


def _cancel_batch_after_text_commit(
    queue: GenerationQueue,
    *,
    project_name: str,
    batch_ids: list[str],
    ready: asyncio.Event,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        result = await execute_queued_text_task(task)
        await ready.wait()
        cancelled = await queue.cancel_generation_batch(project_name=project_name, batch_id=batch_ids[0])
        assert cancelled.cancelling == [task["task_id"]]
        return result

    return execute


@pytest.mark.parametrize(
    ("project_name", "content_mode", "generation_mode", "handler", "expected_task_type"),
    [
        ("script", "ad", "storyboard", "script", "text_episode_script"),
        ("drama", "drama", "storyboard", "script_plan", "text_drama_script_plan"),
        ("narration", "narration", "storyboard", "script_plan", "text_narration_script_plan"),
        ("reference", "narration", "reference_video", "script_plan", "text_reference_script_plan"),
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
    elif handler == "script_plan":
        outcome = await generate_script_plan(ToolRequest(TextGenerationRequest(episode=1)), scope, caller, services)
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

    outcome = await generate_script_plan(
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
    submitted = await generate_script_plan(request, scope, caller, services)
    assert submitted.value is not None
    existing_batch_id = submitted.value.batch_id
    record_migration_failure(
        projects.get_project_path("drama"),
        ProjectMigrationError("repair required"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )

    with pytest.raises(ConflictError, match="project_migration_failed"):
        await generate_script_plan(request, scope, caller, services)

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
        task_type="text_drama_script_plan",
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
        generate_script_plan(
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


async def test_queued_script_plan_preserves_runtime_cancellation_receipt(tmp_path: Path, monkeypatch) -> None:
    compensations: list[str] = []

    async def handler(*_args, **_kwargs):
        return CompensableTextGenerationResult("committed", lambda: compensations.append("restored"))

    monkeypatch.setattr(tool_runtime, "generate_reference_script_plan", handler)
    result = await execute_queued_text_task(
        {
            "task_id": "task-script_plan",
            "project_name": "demo",
            "task_type": "text_reference_script_plan",
            "payload": {"episode": 1, "projects_root": str(tmp_path)},
        }
    )

    assert isinstance(result, CompensableGenerationResult)
    assert result == {"message": "committed"}
    result.compensate_cancelled()
    assert compensations == ["restored"]


@pytest.mark.parametrize("later_write", [False, True])
async def test_episode_script_terminal_update_race_cancels_batch_and_compensates_files(
    tmp_path: Path,
    file_db_factory,
    monkeypatch,
    later_write: bool,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    project_path = projects.create_project("script", content_mode="ad")
    projects.create_project_metadata("script", "Script", "", "ad")
    projects.update_project(
        "script",
        lambda project: project.update(
            episodes=[{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}]
        ),
    )
    script_path = projects.save_script(
        "script",
        {"episode": 1, "title": "旧剧本", "shots": []},
        "episode_1.json",
        validate=False,
    )
    activate_artifact_target_state(project_path, bump_schema=False)
    before_script = script_path.read_bytes()
    before_project = (project_path / "project.json").read_bytes()
    before_manifest = ProjectArtifactManifestAdapter(project_path).snapshot_entries()

    class Generator:
        @classmethod
        async def create(cls, *_args, **_kwargs):
            return cls()

        async def generate(self, *, cancellation_file_receipts, cancellation_manifest_receipts, **_kwargs):
            return projects.save_script(
                "script",
                {"episode": 1, "title": "新剧本", "shots": []},
                "episode_1.json",
                validate=False,
                cancellation_file_receipts=cancellation_file_receipts,
                cancellation_manifest_receipts=cancellation_manifest_receipts,
            )

    monkeypatch.setattr("server.text_generation.ScriptGenerator", Generator)
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    batch_ids: list[str] = []
    ready = asyncio.Event()

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        result = await execute_queued_text_task(task)
        if later_write:
            projects.update_project("script", lambda project: project.update(title="后续写入"))
        await ready.wait()
        cancelled = await queue.cancel_generation_batch(project_name="script", batch_id=batch_ids[0])
        assert cancelled.cancelling == [task["task_id"]]
        return result

    worker = await _start_text_worker(queue, execute)
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    try:
        submitted = await generate_episode_script(
            ToolRequest(TextGenerationRequest(episode=1)),
            ProjectScope(project_name="script", projects_root=projects.projects_root),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
        assert submitted.value is not None
        batch_id = submitted.value.batch_id
        task_id = submitted.value.members[0].task_id
        assert task_id is not None
        batch_ids.append(batch_id)
        ready.set()
        task = await wait_for_task(task_id, 0.01, queue=queue)
        assert task["status"] == "cancelled"
    finally:
        ready.set()
        await worker.stop()

    batch = await queue.get_generation_batch(project_name="script", batch_id=batch_id)
    assert batch.done is True
    assert batch.members[0].status == "cancelled"
    if later_write:
        assert script_path.read_bytes() != before_script
        assert projects.load_project_readonly("script")["title"] == "后续写入"
        assert ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_script(1)) is not None
    else:
        assert script_path.read_bytes() == before_script
        assert (project_path / "project.json").read_bytes() == before_project
        assert ProjectArtifactManifestAdapter(project_path).snapshot_entries() == before_manifest


async def test_cancel_during_started_episode_script_commit_restores_formal_state(
    tmp_path: Path, file_db_factory, monkeypatch
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    project_path = projects.create_project("script", content_mode="ad")
    projects.create_project_metadata("script", "Script", "", "ad")
    projects.update_project(
        "script",
        lambda project: project.update(
            episodes=[{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}]
        ),
    )
    activate_artifact_target_state(project_path, bump_schema=False)
    before_project = (project_path / "project.json").read_bytes()
    before_manifest = ProjectArtifactManifestAdapter(project_path).snapshot_entries()
    started = threading.Event()
    release = threading.Event()

    class Generator:
        @classmethod
        async def create(cls, *_args, **_kwargs):
            return cls()

        async def generate(self, *, cancellation_file_receipts, cancellation_manifest_receipts, **_kwargs):
            def commit():
                path = projects.save_script(
                    "script",
                    {"episode": 1, "title": "新剧本", "shots": []},
                    "episode_1.json",
                    validate=False,
                    cancellation_file_receipts=cancellation_file_receipts,
                    cancellation_manifest_receipts=cancellation_manifest_receipts,
                )
                started.set()
                release.wait()
                return path

            return await run_sync_transaction(commit)

    monkeypatch.setattr("server.text_generation.ScriptGenerator", Generator)
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        return await execute_queued_text_task(task)

    worker = await _start_text_worker(queue, execute, dispatch_cancellation=True)
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    try:
        submitted = await generate_episode_script(
            ToolRequest(TextGenerationRequest(episode=1)),
            ProjectScope(project_name="script", projects_root=projects.projects_root),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
        assert submitted.value is not None
        batch_id = submitted.value.batch_id
        task_id = submitted.value.members[0].task_id
        assert task_id is not None
        assert await asyncio.to_thread(started.wait, 1)
        cancelled = await queue.cancel_generation_batch(project_name="script", batch_id=batch_id)
        assert cancelled.cancelling == [task_id]
    finally:
        release.set()
    try:
        task = await wait_for_task(task_id, 0.01, queue=queue)
        assert task["status"] == "cancelled"
    finally:
        await worker.stop()
        queue.set_worker_cancel_callback(None)
    batch = await queue.get_generation_batch(project_name="script", batch_id=batch_id)
    assert batch.done is True
    assert batch.members[0].status == "cancelled"
    assert not (project_path / "scripts" / "episode_1.json").exists()
    assert (project_path / "project.json").read_bytes() == before_project
    assert ProjectArtifactManifestAdapter(project_path).snapshot_entries() == before_manifest


async def test_episode_plan_terminal_update_race_cancels_batch_and_compensates_ledger(
    tmp_path: Path,
    file_db_factory,
    monkeypatch,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    project_path = projects.create_project("planning", content_mode="narration")
    projects.create_project_metadata("planning", "Planning", "", "narration")
    source = "第一章。少年得到古玉。第二章。玉中藏着剑诀。"
    (project_path / "source" / "novel.txt").write_text(source, encoding="utf-8")
    first_end = source.index("少年得到古玉。") + len("少年得到古玉。")
    projects.update_project(
        "planning",
        lambda project: project.update(
            episodes=[
                {
                    "episode": 1,
                    "title": "少年得玉",
                    "hook": "悬念",
                    "ledger_status": "planned",
                    "source_range": {"source_file": "source/novel.txt", "start": 0, "end": first_end},
                }
            ],
            planning_cursor={"source_file": "source/novel.txt", "offset": first_end},
        ),
    )
    before_project = (project_path / "project.json").read_bytes()
    before_manifest = ProjectArtifactManifestAdapter(project_path).snapshot_entries()

    class Generator:
        model = "fake-model"

        async def generate(self, _request, project_name=None):
            return BackendTextGenerationResult(
                text='{"episodes":[{"title":"玉中剑诀","hook":"悬念","end_anchor":"玉中藏着剑诀。"}]}',
                provider="fake",
                model="fake-model",
            )

    async def create_planner(_cls, path):
        return EpisodePlanner(path, generator=Generator())  # type: ignore[arg-type]

    monkeypatch.setattr(EpisodePlanner, "create", classmethod(create_planner))
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    batch_ids: list[str] = []
    ready = asyncio.Event()
    worker = await _start_text_worker(
        queue,
        _cancel_batch_after_text_commit(queue, project_name="planning", batch_ids=batch_ids, ready=ready),
    )
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    try:
        submitted = await plan_episodes(
            ToolRequest(PlanEpisodesRequest()),
            ProjectScope(project_name="planning", projects_root=projects.projects_root),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
        assert submitted.value is not None
        batch_id = submitted.value.batch_id
        task_id = submitted.value.members[0].task_id
        assert task_id is not None
        batch_ids.append(batch_id)
        ready.set()
        task = await wait_for_task(task_id, 0.01, queue=queue)
        assert task["status"] == "cancelled"
    finally:
        ready.set()
        await worker.stop()

    batch = await queue.get_generation_batch(project_name="planning", batch_id=batch_id)
    assert batch.done is True
    assert batch.members[0].status == "cancelled"
    assert (project_path / "project.json").read_bytes() == before_project
    assert not (project_path / "source" / "episode_1.txt").exists()
    assert not (project_path / "source" / "episode_2.txt").exists()
    assert ProjectArtifactManifestAdapter(project_path).snapshot_entries() == before_manifest


async def test_cancel_during_started_episode_plan_commit_restores_batch_and_ledger(
    tmp_path: Path,
    file_db_factory,
    monkeypatch,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    project_path = projects.create_project("planning", content_mode="narration")
    projects.create_project_metadata("planning", "Planning", "", "narration")
    (project_path / "source" / "novel.txt").write_text(
        "第一章。少年得到古玉，玉中藏着剑诀。",
        encoding="utf-8",
    )
    before_project = (project_path / "project.json").read_bytes()
    started = threading.Event()
    release = threading.Event()

    class Generator:
        model = "fake-model"

        async def generate(self, _request, project_name=None):
            return BackendTextGenerationResult(
                text='{"episodes":[{"title":"古玉藏诀","hook":"悬念","end_anchor":"玉中藏着剑诀。"}]}',
                provider="fake",
                model="fake-model",
            )

    class BlockingProjectManager(ProjectManager):
        def update_project(self, *args, **kwargs):
            result = super().update_project(*args, **kwargs)
            started.set()
            release.wait()
            return result

    async def create_planner(_cls, path):
        planner = EpisodePlanner(path, generator=Generator())  # type: ignore[arg-type]
        planner.pm = BlockingProjectManager(projects.projects_root)
        return planner

    monkeypatch.setattr(EpisodePlanner, "create", classmethod(create_planner))
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        return await execute_queued_text_task(task)

    worker = await _start_text_worker(queue, execute, dispatch_cancellation=True)
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    try:
        submitted = await plan_episodes(
            ToolRequest(PlanEpisodesRequest()),
            ProjectScope(project_name="planning", projects_root=projects.projects_root),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
        assert submitted.value is not None
        batch_id = submitted.value.batch_id
        task_id = submitted.value.members[0].task_id
        assert task_id is not None
        assert await asyncio.to_thread(started.wait, 1)
        cancelled = await queue.cancel_generation_batch(project_name="planning", batch_id=batch_id)
        assert cancelled.cancelling == [task_id]
    finally:
        release.set()
    try:
        task = await wait_for_task(task_id, 0.01, queue=queue)
        assert task["status"] == "cancelled"
    finally:
        await worker.stop()
        queue.set_worker_cancel_callback(None)

    batch = await queue.get_generation_batch(project_name="planning", batch_id=batch_id)
    assert batch.done is True
    assert batch.members[0].status == "cancelled"
    assert (project_path / "project.json").read_bytes() == before_project
    assert not (project_path / "source" / "episode_1.txt").exists()


@pytest.mark.parametrize(
    ("project_name", "generation_mode", "quarantine_kind", "generated_text"),
    [
        (
            "reference",
            "reference_video",
            QUARANTINE_KIND_SCRIPT_PLAN,
            '{"units":[{"duration_seconds":4,"source_text":"张三走向村口。","text":"@[未登记角色] 出场"}]}',
        ),
        (
            "narration",
            "storyboard",
            QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            '{"episode":1,"segments":[{"segment_id":"E1S01","novel_text":"张三走向村口。",'
            '"duration_seconds":4,"segment_break":false,"characters_in_segment":["未登记角色"],'
            '"scenes":[],"props":[]}]}',
        ),
    ],
)
async def test_cancel_during_invalid_script_plan_quarantine_leaves_no_workflow_blocker(
    tmp_path: Path,
    file_db_factory,
    monkeypatch,
    project_name: str,
    generation_mode: str,
    quarantine_kind: str,
    generated_text: str,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    project_path = projects.create_project(project_name, content_mode="narration")
    projects.create_project_metadata(project_name, project_name, "", "narration")
    projects.update_project(project_name, lambda project: project.update(generation_mode=generation_mode))
    (project_path / "source" / "episode_1.txt").write_text("张三走向村口。", encoding="utf-8")

    class Generator:
        async def generate(self, _request, project_name=None):
            return BackendTextGenerationResult(text=generated_text, provider="fake", model="fake-model")

    async def create_generator(_task_type, project_name=None):
        return Generator()

    monkeypatch.setattr("server.text_generation.TextGenerator.create", create_generator)
    draft_path = quarantine_path(project_path, 1, quarantine_kind)
    started = threading.Event()
    release = threading.Event()
    original_replace = os.replace

    def blocking_replace(src, dst):
        original_replace(src, dst)
        if Path(dst) == draft_path and not started.is_set():
            started.set()
            release.wait()

    monkeypatch.setattr("lib.json_io.os.replace", blocking_replace)
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        return await execute_queued_text_task(task)

    worker = await _start_text_worker(queue, execute, dispatch_cancellation=True)
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    try:
        submitted = await generate_script_plan(
            ToolRequest(TextGenerationRequest(episode=1)),
            ProjectScope(project_name=project_name, projects_root=projects.projects_root),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
        assert submitted.value is not None
        batch_id = submitted.value.batch_id
        task_id = submitted.value.members[0].task_id
        assert task_id is not None
        assert await asyncio.to_thread(started.wait, 1)
        cancelled = await queue.cancel_generation_batch(project_name=project_name, batch_id=batch_id)
        assert cancelled.cancelling == [task_id]
    finally:
        release.set()
    try:
        task = await wait_for_task(task_id, 0.01, queue=queue)
        assert task["status"] == "cancelled"
    finally:
        await worker.stop()
        queue.set_worker_cancel_callback(None)

    batch = await queue.get_generation_batch(project_name=project_name, batch_id=batch_id)
    assert batch.done is True
    assert batch.members[0].status == "cancelled"
    assert not draft_path.exists()
    status = WorkflowStateService(projects).get_status(project_name)
    assert all(blocker.code != "script_plan_quarantined" for blocker in status.blockers)
