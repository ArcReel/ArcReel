from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import pytest
from sqlalchemy import func, select

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.backends.text_backends.base import TextGenerationResult as BackendTextGenerationResult
from lib.backends.text_backends.base import TextOutputTruncatedError
from lib.backends.text_generator import TextGenerator
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.models.task import GenerationBatch
from lib.episode.episode_planner import (
    EpisodePlanner,
    EpisodePlanningError,
    EpisodePlanSummary,
    NoCutPointError,
    PlanningOutputTruncatedError,
    PlanResult,
)
from lib.generation.generation_batch import GenerationBatchRequestedItem, GenerationBatchRequestSnapshot
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import wait_for_task
from lib.generation.generation_result import (
    GenerationAction,
    GenerationSelectionMode,
    decode_generation_problem,
    problem_from_task_failure,
)
from lib.generation.generation_worker import CapacityTable, GenerationWorker
from lib.infra.api_errors import ConflictError
from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import ProjectMigrationError, record_migration_failure
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_SCRIPT_PLAN,
    draft_revision,
    quarantine_path,
    read_quarantine,
    write_quarantine,
)
from server import draft_repair as draft_repair_module
from server.text_generation import TextGenerationRequest
from server.tool_runtime import (
    CallerContext,
    GenerateEpisodeScriptRequest,
    GenerateScriptPlanRequest,
    PlanEpisodesRequest,
    ProjectScope,
    RepairDraftRequest,
    Services,
    ToolRequest,
    execute_queued_text_task,
    generate_episode_script,
    generate_script_plan,
    plan_episodes,
    repair_draft,
)
from tests.factories import register_project_sources
from tests.fakes import FakeTextGenerator, refuse_resume_execution


def _admit_text_operations(projects: ProjectManager, project_name: str) -> None:
    """放上文本长调用的准入输入：整本源文与第 1 集集原文；广告/短片填创作灵感。"""
    if projects.load_project(project_name).get("content_mode") == "ad":
        projects.update_project(project_name, lambda project: project.update(brief="夏季新品"))
        return
    register_project_sources(
        projects,
        project_name,
        whole_source={"novel.txt": "第一章\n张三走向村口。"},
        own_episodes=("张三走向村口。",),
    )


async def _start_text_worker(
    queue: GenerationQueue,
    executor: Callable[..., Awaitable[dict[str, Any]]],
) -> GenerationWorker:
    async def text_provider(_task: dict[str, Any]) -> str:
        return "text"

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"text": 1}),
        provider_projection=text_provider,
        executor=executor,
        lanes=("text",),
        resume_executor=refuse_resume_execution,
    )
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    assert await queue.acquire_or_renew_worker_lease(
        name=worker.lease_name,
        owner_id=worker.owner_id,
        ttl_seconds=worker.lease_ttl,
    )
    await worker.start()
    return worker


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
    _admit_text_operations(projects, project_name)
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    scope = ProjectScope(project_name=project_name, data_root=projects.data_root)
    caller = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")
    if handler == "script":
        outcome = await generate_episode_script(
            ToolRequest(GenerateEpisodeScriptRequest(episode_id=1)), scope, caller, services
        )
    elif handler == "script_plan":
        outcome = await generate_script_plan(
            ToolRequest(GenerateScriptPlanRequest(episode_id=1)), scope, caller, services
        )
    else:
        outcome = await plan_episodes(ToolRequest(PlanEpisodesRequest()), scope, caller, services)

    assert outcome.problem is None
    batch = outcome.value
    assert batch is not None
    assert batch.done is False
    assert len(batch.members) == 1
    assert batch.members[0].task_type == expected_task_type
    assert batch.poll_after_seconds is not None
    task_id = batch.members[0].task_id
    assert task_id is not None
    task = await queue.get_task(task_id)
    assert task is not None
    assert str(projects.data_root) not in json.dumps(task["payload"], ensure_ascii=False)


async def test_web_prompt_authoring_returns_the_batch_without_waiting(tmp_path: Path, file_db_factory) -> None:
    """Web 入口与 Agent 同一个服务命令：提交即返批次句柄，任务按 Web 来源登记，载荷带范围与模式。"""
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo", content_mode="ad")
    projects.create_project_metadata("demo", "demo", "", "ad")
    projects.update_project("demo", lambda project: project.update(brief="夏季新品"))
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )

    outcome = await generate_episode_script(
        ToolRequest(GenerateEpisodeScriptRequest(episode_id=1, instructions="冷色调")),
        ProjectScope(project_name="demo", data_root=projects.data_root),
        CallerContext(user_id=DEFAULT_USER_ID, source="webui"),
        services,
    )

    assert outcome.problem is None
    batch = outcome.value
    assert batch is not None
    assert batch.done is False
    task_id = batch.members[0].task_id
    assert task_id is not None
    task = await queue.get_task(task_id)
    assert task is not None
    assert task["source"] == "webui"
    assert task["payload"]["instructions"] == "冷色调"
    assert task["payload"]["rewrite"] is False


def _narration_segment(**overrides: Any) -> dict[str, Any]:
    segment: dict[str, Any] = {
        "segment_id": "E1S01",
        "novel_text": "张三走向村口。",
        "duration_seconds": 4,
        "segment_break": False,
        "characters_in_segment": ["张三"],
        "scenes": [],
        "props": [],
    }
    segment.update(overrides)
    return segment


def _narration_project_with_draft(segments: list[dict[str, Any]]) -> tuple[ProjectManager, str]:
    """当前数据根下的旁白/解说项目，第 1 集留着一份待修复的脚本规划草稿；返回项目管理器与草稿 revision。

    worker 执行未登记服务的任务时按当前配置解析数据根，项目须建在那里。
    """
    projects = ProjectManager(DataRootLayout.current().root)
    projects.create_project("demo")
    projects.create_project_metadata("demo", "Demo", "Anime", "narration")
    projects.add_character("demo", "张三", "村民")
    projects.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    project_path = projects.get_project_path("demo")
    (project_path / "source").mkdir(exist_ok=True)
    (project_path / "source" / "episode_1.txt").write_text("张三走向村口。", encoding="utf-8")
    write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": segments},
        violations=[],
        meta={"source": None},
    )
    draft = read_quarantine(project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
    assert draft is not None
    return projects, draft_revision(draft)


@pytest.mark.usefixtures("video_request_facts")
async def test_web_draft_repair_queues_a_text_task_that_adopts_the_repaired_draft(
    file_db_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI 修复以排队文本任务提交，占用本份草稿的槽；worker 修完照常重判，违约清零即采用。"""
    projects, revision = _narration_project_with_draft([_narration_segment(characters_in_segment=["王五"])])
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    scope = ProjectScope(project_name="demo", data_root=projects.data_root)
    caller = CallerContext(user_id=DEFAULT_USER_ID, source="webui")

    stale = await repair_draft(
        ToolRequest(RepairDraftRequest(episode_id=1, doc_type="narration_script_plan", base_revision="stale")),
        scope,
        caller,
        services,
    )
    assert stale.problem is not None
    assert stale.problem.code == "revision_conflict"

    outcome = await repair_draft(
        ToolRequest(
            RepairDraftRequest(
                episode_id=1, doc_type="narration_script_plan", base_revision=revision, instructions="只改出场角色"
            )
        ),
        scope,
        caller,
        services,
    )
    assert outcome.problem is None
    batch = outcome.value
    assert batch is not None
    assert batch.done is False
    (member,) = batch.members
    assert (member.task_type, member.unit_id) == ("text_draft_repair", "episode-1-narration_script_plan")
    assert member.task_id is not None
    task = await queue.get_task(member.task_id)
    assert task is not None
    assert task["payload"]["instructions"] == "只改出场角色"

    repaired = _narration_segment()
    model = FakeTextGenerator(json.dumps({"segments": [repaired]}, ensure_ascii=False))
    monkeypatch.setattr(TextGenerator, "create", model.create)
    result = await execute_queued_text_task(task)

    assert (result["adopted"], result["violation_count"]) == (True, 0)
    assert "只改出场角色" in model.requests[0].prompt
    project_path = projects.get_project_path("demo")
    assert read_quarantine(project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN) is None
    formal = json.loads((project_path / "drafts" / "episode_1" / "script_plan_segments.json").read_text("utf-8"))
    assert formal["segments"] == [repaired]


@pytest.mark.usefixtures("video_request_facts")
async def test_failed_queued_draft_repair_fails_the_task_with_a_localizable_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, revision = _narration_project_with_draft([_narration_segment(characters_in_segment=["王五"])])
    monkeypatch.setattr(TextGenerator, "create", FakeTextGenerator("not json").create)
    payload = {"episode_id": 1, "doc_type": "narration_script_plan", "base_revision": revision, "instructions": None}
    task = {"task_id": "task-repair", "project_name": "demo", "task_type": "text_draft_repair", "payload": payload}

    with pytest.raises(RuntimeError) as raised:
        await execute_queued_text_task(task)

    problem = decode_generation_problem(str(raised.value))
    assert problem is not None
    assert problem.code == "draft_repair_failed"
    draft = read_quarantine(projects.get_project_path("demo"), 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
    assert draft is not None
    assert draft_revision(draft) == revision

    stale_task = {**task, "payload": {**payload, "base_revision": "stale"}}
    with pytest.raises(RuntimeError) as raised:
        await execute_queued_text_task(stale_task)
    problem = decode_generation_problem(str(raised.value))
    assert problem is not None
    assert problem.code == "draft_revision_conflict"


@pytest.mark.usefixtures("video_request_facts")
async def test_draft_repair_failing_before_the_write_back_does_not_claim_the_draft_was_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, revision = _narration_project_with_draft([_narration_segment(characters_in_segment=["王五"])])

    def unreadable_source(*_args, **_kwargs):
        raise PermissionError("source/novel.txt")

    monkeypatch.setattr(draft_repair_module, "load_novel_source", unreadable_source)
    payload = {"episode_id": 1, "doc_type": "narration_script_plan", "base_revision": revision, "instructions": None}
    task = {"task_id": "task-repair", "project_name": "demo", "task_type": "text_draft_repair", "payload": payload}

    with pytest.raises(RuntimeError) as raised:
        await execute_queued_text_task(task)

    problem = decode_generation_problem(str(raised.value))
    assert problem is not None
    assert problem.code == "draft_repair_failed"
    draft = read_quarantine(projects.get_project_path("demo"), 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
    assert draft is not None
    assert draft_revision(draft) == revision


@pytest.mark.usefixtures("video_request_facts")
async def test_truncated_draft_repair_fails_with_the_way_out_and_keeps_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI 修复的回复被截断：任务以分集规划同一个问题码失败，带出路参数，草稿不变。"""
    projects, revision = _narration_project_with_draft([_narration_segment(characters_in_segment=["王五"])])
    model = FakeTextGenerator(
        TextOutputTruncatedError(
            provider="openai", model="my-llm", output_tokens=8192, provider_id="custom-3", custom_model=True
        )
    )
    monkeypatch.setattr(TextGenerator, "create", model.create)
    payload = {"episode_id": 1, "doc_type": "narration_script_plan", "base_revision": revision, "instructions": None}
    task = {"task_id": "task-repair", "project_name": "demo", "task_type": "text_draft_repair", "payload": payload}

    with pytest.raises(RuntimeError) as raised:
        await execute_queued_text_task(task)

    problem = problem_from_task_failure(str(raised.value))
    assert (problem.code, problem.action, problem.params) == (
        "text_output_truncated",
        GenerationAction.CONFIGURE_PROVIDER,
        {"provider_id": "custom-3", "model": "my-llm", "custom_model": True},
    )
    assert model.require_complete == [True]
    draft = read_quarantine(projects.get_project_path("demo"), 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
    assert draft is not None
    assert draft_revision(draft) == revision


async def test_text_mcp_rejects_lost_worker_lease_without_persisting_queue_state(
    tmp_path: Path,
    file_db_factory,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("drama", content_mode="drama")
    projects.create_project_metadata("drama", "Drama", "", "drama")
    _admit_text_operations(projects, "drama")
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="lost-worker", ttl_seconds=60)
    await queue.release_worker_lease(name="default", owner_id="lost-worker")
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )

    outcome = await generate_script_plan(
        ToolRequest(GenerateScriptPlanRequest(episode_id=1)),
        ProjectScope(project_name="drama", data_root=projects.data_root),
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
    _admit_text_operations(projects, "drama")
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    request = ToolRequest(GenerateScriptPlanRequest(episode_id=1))
    scope = ProjectScope(project_name="drama", data_root=projects.data_root)
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
    _admit_text_operations(projects, "drama")
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
        # payload 取请求对象自己的投影：本用例的 membership 变体要让新提交与这条在跑任务
        # 判成同一件事，两边的事实必须逐字段相同，写死字面量会随请求字段增删而失效。
        payload=TextGenerationRequest(episode=1).to_payload() | {"projects_root": str(projects.data_root)},
        batch_id=historical_batch_id,
        batch_unit_id="episode-1" if cancel_state == "membership" else "old",
    )
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )

    submission = asyncio.create_task(
        generate_script_plan(
            ToolRequest(GenerateScriptPlanRequest(episode_id=1)),
            ProjectScope(project_name="drama", data_root=projects.data_root),
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
            assert submitted_task is not None
            assert submitted_task["batch_id"] == submitted_batch_id
    else:
        assert batch_ids == {historical_batch_id}
    historical = await GenerationQueue.get_generation_batch(queue, project_name="drama", batch_id=historical_batch_id)
    assert [(member.unit_id, member.task_id) for member in historical.members] == [
        ("episode-1" if cancel_state == "membership" else "old", historical_task["task_id"])
    ]


@pytest.mark.parametrize("legacy_data_root", [False, True])
async def test_queued_plan_resolves_data_root_from_current_config_and_preserves_typed_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_data_root: bool
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("planning", content_mode="narration")
    projects.create_project_metadata("planning", "Planning", "", "narration")
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(projects.data_root))
    payload: dict[str, Any] = {"instructions": "按章节"}
    if legacy_data_root:
        # 旧版本排入的任务在载荷里带着入队时的数据根；数据根此后已挪走。
        payload["projects_root"] = str(tmp_path / "moved-away")
    task = {
        "task_id": "task-plan",
        "project_name": "planning",
        "task_type": "text_episode_plan",
        "payload": payload,
    }

    class Planner:
        @classmethod
        async def create(cls, _project_path):
            return cls()

        async def plan(self, instructions=None, gap=None):
            assert instructions == "按章节"
            return PlanResult(
                episodes=[
                    EpisodePlanSummary(
                        episode=1,
                        title="第一集",
                        hook="悬念",
                        reading_units=800,
                        ledger_status="planned",
                        first_sentence="第一句。",
                        last_sentence="最后一句。",
                    )
                ],
                cursor=None,
            )

    result = await execute_queued_text_task(task, planner_cls=Planner)
    assert result["episodes"][0]["title"] == "第一集"
    assert result["episodes"][0]["first_sentence"] == "第一句。"
    assert result["episodes"][0]["last_sentence"] == "最后一句。"

    class FailingPlanner(Planner):
        async def plan(self, instructions=None, gap=None):
            raise EpisodePlanningError("invalid source window")

    with pytest.raises(RuntimeError) as raised:
        await execute_queued_text_task(task, planner_cls=FailingPlanner)
    problem = problem_from_task_failure(str(raised.value))
    assert problem.code == "episode_planning_failed"
    assert problem.action is GenerationAction.RETRY
    assert problem.detail == "invalid source window"


@pytest.mark.parametrize(
    ("failure", "code", "action", "params"),
    [
        (
            PlanningOutputTruncatedError(
                TextOutputTruncatedError(
                    provider="openai", model="my-llm", output_tokens=8192, provider_id="custom-3", custom_model=True
                )
            ),
            "text_output_truncated",
            GenerationAction.CONFIGURE_PROVIDER,
            {"provider_id": "custom-3", "model": "my-llm", "custom_model": True},
        ),
        (
            NoCutPointError(source_file="source/novel.txt", offset=120),
            "episode_planning_no_cut_point",
            GenerationAction.FIX_INPUT,
            {"source_file": "source/novel.txt", "offset": 120},
        ),
    ],
)
async def test_queued_plan_failure_carries_the_way_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception, code: str, action, params: dict
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("planning", content_mode="narration")
    projects.create_project_metadata("planning", "Planning", "", "narration")
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(projects.data_root))

    class Planner:
        @classmethod
        async def create(cls, _project_path):
            return cls()

        async def plan(self, instructions=None, gap=None):
            raise failure

    task = {"task_id": "task-plan", "project_name": "planning", "task_type": "text_episode_plan", "payload": {}}
    with pytest.raises(RuntimeError) as raised:
        await execute_queued_text_task(task, planner_cls=Planner)

    problem = problem_from_task_failure(str(raised.value))
    assert (problem.code, problem.action, problem.params) == (code, action, params)
    if code == "text_output_truncated":
        assert problem.detail == str(failure)


async def test_cancel_during_started_episode_script_commit_leaves_member_running_to_success(
    tmp_path: Path, file_db_factory, monkeypatch
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    # worker 按当前配置解析数据根。
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(projects.data_root))
    project_path = projects.create_project("script", content_mode="ad")
    projects.create_project_metadata("script", "Script", "", "ad")
    _admit_text_operations(projects, "script")
    projects.update_project(
        "script",
        lambda project: project.update(
            episodes=[{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}]
        ),
    )
    activate_artifact_target_state(project_path, bump_schema=False)
    started = threading.Event()
    release = threading.Event()

    class Generator:
        content_mode = "ad"

        def __init__(self) -> None:
            self.project_json = projects.load_project("script")

        @classmethod
        async def create(cls, *_args, **_kwargs):
            return cls()

        async def generate(self, *_args, **_kwargs):
            def commit():
                path = projects.save_script(
                    "script",
                    {"episode": 1, "title": "新剧本", "shots": []},
                    "episode_1.json",
                    validate=False,
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

    worker = await _start_text_worker(queue, execute)
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    try:
        submitted = await generate_episode_script(
            ToolRequest(GenerateEpisodeScriptRequest(episode_id=1)),
            ProjectScope(project_name="script", data_root=projects.data_root),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
        assert submitted.value is not None
        batch_id = submitted.value.batch_id
        task_id = submitted.value.members[0].task_id
        assert task_id is not None
        assert await asyncio.to_thread(started.wait, 1)
        cancelled = await queue.cancel_generation_batch(project_name="script", batch_id=batch_id)
        assert cancelled.model_dump() == {"cancelled": [], "skipped_running": [task_id], "skipped_terminal": []}
    finally:
        release.set()
    try:
        task = await wait_for_task(task_id, 0.01, queue=queue)
        assert task["status"] == "succeeded"
    finally:
        await worker.stop()
    batch = await queue.get_generation_batch(project_name="script", batch_id=batch_id)
    assert batch.done is True
    assert batch.members[0].status == "succeeded"
    assert projects.load_script("script", "episode_1.json")["title"] == "新剧本"
    assert ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_script(1)) is not None


async def test_cancel_during_started_episode_plan_commit_leaves_member_running_to_success(
    tmp_path: Path,
    file_db_factory,
    monkeypatch,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    # worker 按当前配置解析数据根。
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(projects.data_root))
    project_path = projects.create_project("planning", content_mode="narration")
    projects.create_project_metadata("planning", "Planning", "", "narration")
    register_project_sources(projects, "planning", whole_source={"novel.txt": "第一章。少年得到古玉，玉中藏着剑诀。"})
    before_project = (project_path / "project.json").read_bytes()
    started = threading.Event()
    release = threading.Event()

    class Generator:
        model = "fake-model"
        max_output_tokens = 64000

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
        planner = EpisodePlanner(path, generator=Generator())
        planner.pm = BlockingProjectManager(projects.data_root)
        return planner

    monkeypatch.setattr(EpisodePlanner, "create", classmethod(create_planner))
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        return await execute_queued_text_task(task)

    worker = await _start_text_worker(queue, execute)
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    try:
        submitted = await plan_episodes(
            ToolRequest(PlanEpisodesRequest()),
            ProjectScope(project_name="planning", data_root=projects.data_root),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
        assert submitted.value is not None
        batch_id = submitted.value.batch_id
        task_id = submitted.value.members[0].task_id
        assert task_id is not None
        assert await asyncio.to_thread(started.wait, 1)
        cancelled = await queue.cancel_generation_batch(project_name="planning", batch_id=batch_id)
        assert cancelled.model_dump() == {"cancelled": [], "skipped_running": [task_id], "skipped_terminal": []}
    finally:
        release.set()
    try:
        task = await wait_for_task(task_id, 0.01, queue=queue)
        assert task["status"] == "succeeded"
    finally:
        await worker.stop()

    batch = await queue.get_generation_batch(project_name="planning", batch_id=batch_id)
    assert batch.done is True
    assert batch.members[0].status == "succeeded"
    assert (project_path / "project.json").read_bytes() != before_project
    assert [episode["title"] for episode in projects.load_project("planning")["episodes"]] == ["古玉藏诀"]
    assert (project_path / "source" / "episode_1.txt").exists()


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
async def test_cancel_during_invalid_script_plan_quarantine_leaves_member_running_to_its_refusal(
    tmp_path: Path,
    file_db_factory,
    monkeypatch,
    video_request_facts,
    project_name: str,
    generation_mode: str,
    quarantine_kind: str,
    generated_text: str,
) -> None:
    projects = ProjectManager(tmp_path / "projects")
    # worker 按当前配置解析数据根。
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(projects.data_root))
    project_path = projects.create_project(project_name, content_mode="narration")
    projects.create_project_metadata(project_name, project_name, "", "narration")
    projects.update_project(project_name, lambda project: project.update(generation_mode=generation_mode))
    register_project_sources(projects, project_name, own_episodes=("张三走向村口。",))

    class Generator:
        async def generate(self, _request, project_name=None):
            return BackendTextGenerationResult(text=generated_text, provider="fake", model="fake-model")

    async def create_generator(_task_type, project_name=None, **_kwargs):
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

    monkeypatch.setattr("lib.infra.json_io.os.replace", blocking_replace)
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        return await execute_queued_text_task(task)

    worker = await _start_text_worker(queue, execute)
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    try:
        submitted = await generate_script_plan(
            ToolRequest(GenerateScriptPlanRequest(episode_id=1)),
            ProjectScope(project_name=project_name, data_root=projects.data_root),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
        assert submitted.value is not None
        batch_id = submitted.value.batch_id
        task_id = submitted.value.members[0].task_id
        assert task_id is not None
        assert await asyncio.to_thread(started.wait, 1)
        cancelled = await queue.cancel_generation_batch(project_name=project_name, batch_id=batch_id)
        assert cancelled.model_dump() == {"cancelled": [], "skipped_running": [task_id], "skipped_terminal": []}
    finally:
        release.set()
    try:
        task = await wait_for_task(task_id, 0.01, queue=queue)
    finally:
        await worker.stop()

    assert task["status"] == "failed"
    assert problem_from_task_failure(task["error_message"]).code == "generation_refused"
    batch = await queue.get_generation_batch(project_name=project_name, batch_id=batch_id)
    assert batch.done is True
    assert batch.members[0].status == "failed"
    assert draft_path.exists()
