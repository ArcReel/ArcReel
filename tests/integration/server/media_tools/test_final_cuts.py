"""渲染成片工具：经工具声明入口提交到 render 车道，内嵌调用方拿到可下载的成片，外部调用方拿到批次句柄。"""

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
from server.agent_toolset.final_cuts import RENDER_FINAL_CUT
from server.media_tools.final_cuts import FinalCutToolResult
from server.services.tasks.render_tasks import execute_render_task
from server.tool_runtime import CallerContext
from tests.factories import install_current_video, make_test_clip
from tests.fakes import refuse_resume_execution
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool

EMBEDDED = CallerContext(user_id=DEFAULT_USER_ID, source="embedded")
REMOTE = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")


def _install(timeline_project: ProjectManager, tmp_path: Path, unit_id: str) -> None:
    source = tmp_path / "media" / f"{unit_id}.mp4"
    make_test_clip(source, size="160x90", fps=30, seconds=1.0, tone=True)
    install_current_video(timeline_project.get_project_path("demo"), "reference_videos", unit_id, source)


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
async def test_embedded_agent_waits_for_a_downloadable_final_cut(
    tmp_path: Path, timeline_project: ProjectManager, render_queue: GenerationQueue
) -> None:
    _install(timeline_project, tmp_path, "E1U1")
    _install(timeline_project, tmp_path, "E1U2")
    timeline_id = await _timeline(timeline_project)

    outcome = await run_declared_tool(
        RENDER_FINAL_CUT,
        ToolHarness("demo", tmp_path, timeline_project, caller=EMBEDDED, queue=render_queue),
        {"timeline": timeline_id, "burn_subtitles": False},
    )

    assert outcome.problem is None
    assert isinstance(outcome.value, FinalCutToolResult)
    final_cut = outcome.value.final_cut
    assert (timeline_project.get_project_path("demo") / final_cut.artifact_path).is_file()
    assert outcome.value.download_url == f"/api/v1/files/demo/{final_cut.artifact_path}?v=1"
    assert final_cut.acceptance.video_duration == pytest.approx(2.0, abs=0.05)
    batch = await render_queue.get_generation_batch(
        project_name="demo",
        batch_id=outcome.value.batch_id,
        user_id=DEFAULT_USER_ID,
        resolver=active_artifact_currency_resolver(
            timeline_project.get_project_path("demo"), timeline_project.load_project("demo")
        ),
    )
    assert batch.generation_result is not None
    [item] = batch.generation_result.items
    assert (item.unit_id, item.artifact_path, item.artifact_status) == (
        f"{timeline_id}.without_narration.no_subtitles",
        final_cut.artifact_path,
        "current",
    )


async def test_external_agent_gets_a_batch_handle_to_poll(
    tmp_path: Path, timeline_project: ProjectManager, render_queue: GenerationQueue
) -> None:
    _install(timeline_project, tmp_path, "E1U1")
    _install(timeline_project, tmp_path, "E1U2")
    timeline_id = await _timeline(timeline_project)
    assert await render_queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)

    outcome = await run_declared_tool(
        RENDER_FINAL_CUT,
        ToolHarness("demo", tmp_path, timeline_project, caller=REMOTE, queue=render_queue),
        {"timeline": timeline_id},
    )

    assert isinstance(outcome.value, GenerationBatchReadModel)
    assert outcome.value.operation == "render_final_cut"
    [member] = outcome.value.members
    # 省略的选项按项目补齐：后期配音项目不带旁白，字幕默认烧入。
    assert member.unit_id == f"{timeline_id}.without_narration.burned_subtitles"


async def test_blocking_issues_are_refused_before_anything_is_queued(
    tmp_path: Path, timeline_project: ProjectManager, render_queue: GenerationQueue
) -> None:
    _install(timeline_project, tmp_path, "E1U1")
    timeline_id = await _timeline(timeline_project)

    outcome = await run_declared_tool(
        RENDER_FINAL_CUT,
        ToolHarness("demo", tmp_path, timeline_project, caller=REMOTE, queue=render_queue),
        {"timeline": timeline_id},
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "final_cut_blocked"
    assert [issue["unit_id"] for issue in (outcome.problem.params or {})["issues"]] == ["E1U2"]
    assert await render_queue.list_tasks(project_name="demo") == {"items": [], "total": 0, "page": 1, "page_size": 50}
