"""导出剪映草稿工具：经工具声明入口提交到 render 车道，内嵌调用方拿到已登记的草稿，外部调用方拿到批次句柄。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_currency import active_artifact_currency_resolver
from lib.db.base import DEFAULT_USER_ID
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.generation.generation_batch import GenerationBatchReadModel
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import CapacityTable, GenerationWorker
from lib.project.project_manager import ProjectManager
from server.agent_toolset.jianying_drafts import EXPORT_JIANYING_DRAFT
from server.media_tools.jianying_drafts import JianyingDraftToolResult
from server.services.tasks.render_tasks import execute_render_task
from server.tool_runtime import CallerContext
from tests.factories import install_current_video, install_uploaded_video, make_test_clip
from tests.fakes import refuse_resume_execution
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool

EMBEDDED = CallerContext(user_id=DEFAULT_USER_ID, source="embedded")
REMOTE = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")


def _install(timeline_project: ProjectManager, unit_id: str) -> None:
    install_uploaded_video(timeline_project.get_project_path("demo"), "reference_videos", unit_id, seconds=1.0)


async def _timeline(timeline_project: ProjectManager) -> str:
    readout = await EditTimelineService(timeline_project).create_from_script(
        "demo", episode=1, name="完整版", author=RevisionAuthor(kind="arcreel_agent")
    )
    return readout.timeline.id


@pytest.fixture
async def render_queue(file_db_factory, timeline_project: ProjectManager) -> GenerationQueue:
    return GenerationQueue(session_factory=file_db_factory, project_manager=timeline_project)


@pytest.fixture
async def render_worker(
    render_queue: GenerationQueue, timeline_project: ProjectManager
) -> AsyncIterator[GenerationWorker]:
    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        return await execute_render_task(task, projects=timeline_project)

    worker = GenerationWorker(
        queue=render_queue,
        capacity=CapacityTable.from_env(),
        executor=execute,
        lanes=("render",),
        resume_executor=refuse_resume_execution,
    )
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    assert await render_queue.acquire_or_renew_worker_lease(
        name=worker.lease_name, owner_id=worker.owner_id, ttl_seconds=worker.lease_ttl
    )
    await worker.start()
    try:
        yield worker
    finally:
        await worker.stop()


@pytest.mark.usefixtures("render_worker")
async def test_embedded_agent_waits_for_a_registered_draft(
    tmp_path: Path, timeline_project: ProjectManager, render_queue: GenerationQueue
) -> None:
    _install(timeline_project, "E1U1")
    _install(timeline_project, "E1U2")
    timeline_id = await _timeline(timeline_project)
    project_dir = timeline_project.get_project_path("demo")

    outcome = await run_declared_tool(
        EXPORT_JIANYING_DRAFT,
        ToolHarness("demo", tmp_path, timeline_project, caller=EMBEDDED, queue=render_queue),
        {"timeline": timeline_id},
    )

    assert outcome.problem is None
    assert isinstance(outcome.value, JianyingDraftToolResult)
    draft = outcome.value.jianying_draft
    assert draft.artifact_path == f"renders/episode_1/{timeline_id}/jianying_draft.without_narration.zip"
    assert (draft.revision, draft.narration, draft.version, draft.duration) == (1, "without_narration", 1, 2.0)
    assert (project_dir / draft.artifact_path).is_file()
    assert not list((project_dir / "renders").rglob(".*"))
    batch = await render_queue.get_generation_batch(
        project_name="demo",
        batch_id=outcome.value.batch_id,
        user_id=DEFAULT_USER_ID,
        resolver=active_artifact_currency_resolver(project_dir, timeline_project.load_project("demo")),
    )
    assert batch.operation == "export_jianying_draft"
    assert batch.generation_result is not None
    [item] = batch.generation_result.items
    assert (item.unit_id, item.artifact_path, item.artifact_status) == (
        f"{timeline_id}.jianying_draft.without_narration",
        draft.artifact_path,
        "current",
    )


async def test_external_agent_gets_a_batch_handle_to_poll(
    tmp_path: Path, timeline_project: ProjectManager, render_queue: GenerationQueue
) -> None:
    _install(timeline_project, "E1U1")
    _install(timeline_project, "E1U2")
    timeline_id = await _timeline(timeline_project)
    assert await render_queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)

    outcome = await run_declared_tool(
        EXPORT_JIANYING_DRAFT,
        ToolHarness("demo", tmp_path, timeline_project, caller=REMOTE, queue=render_queue),
        {"timeline": timeline_id},
    )

    assert isinstance(outcome.value, GenerationBatchReadModel)
    assert outcome.value.operation == "export_jianying_draft"
    [member] = outcome.value.members
    assert member.unit_id == f"{timeline_id}.jianying_draft.without_narration"
    [task] = (await render_queue.list_tasks(project_name="demo"))["items"]
    assert (task["task_type"], task["media_type"]) == ("render_jianying_draft", "render")


@pytest.mark.parametrize(
    ("installed", "narration", "code"),
    [
        (("E1U1",), "without_narration", "jianying_draft_blocked"),
        (("E1U1", "E1U2"), "with_narration", "jianying_draft_narration_unavailable"),
    ],
)
async def test_refusals_happen_before_anything_is_queued(
    tmp_path: Path,
    timeline_project: ProjectManager,
    render_queue: GenerationQueue,
    installed: tuple[str, ...],
    narration: str,
    code: str,
) -> None:
    for unit_id in installed:
        _install(timeline_project, unit_id)
    timeline_id = await _timeline(timeline_project)

    outcome = await run_declared_tool(
        EXPORT_JIANYING_DRAFT,
        ToolHarness("demo", tmp_path, timeline_project, caller=REMOTE, queue=render_queue),
        {"timeline": timeline_id, "narration": narration},
    )

    assert outcome.problem is not None
    assert outcome.problem.code == code
    assert await render_queue.list_tasks(project_name="demo") == {"items": [], "total": 0, "page": 1, "page_size": 50}


@pytest.mark.usefixtures("render_worker")
async def test_a_render_time_failure_comes_back_as_a_stable_problem_without_leftovers(
    tmp_path: Path, timeline_project: ProjectManager, render_queue: GenerationQueue
) -> None:
    _install(timeline_project, "E1U1")
    project_dir = timeline_project.get_project_path("demo")
    source = tmp_path / "media" / "E1U2.mp4"
    make_test_clip(source, size="160x90", fps=30, seconds=1.0, tone=True)
    # 没有类型化来源、也不是手动上传的版本投影不出呈现模型，只有任务开始物化素材层时才发现
    install_current_video(project_dir, "reference_videos", "E1U2", source)
    timeline_id = await _timeline(timeline_project)

    outcome = await run_declared_tool(
        EXPORT_JIANYING_DRAFT,
        ToolHarness("demo", tmp_path, timeline_project, caller=EMBEDDED, queue=render_queue),
        {"timeline": timeline_id},
    )

    assert outcome.problem is not None
    assert (outcome.problem.code, outcome.problem.params) == (
        "jianying_draft_presentation_unavailable",
        {"unit_id": "E1U2"},
    )
    assert not [path for path in (project_dir / "renders").rglob("*") if path.is_file()]
