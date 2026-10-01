"""「AI 规划分集」逐窗串联：真实队列与 worker 上经服务命令规划到结尾、停止与接续。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from lib.backends.text_backends.base import TextGenerationResult
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.models.task import Task
from lib.episode.episode_deletion import EpisodeDeletionConfirmationRequired, delete_episode
from lib.episode.episode_planner import EpisodePlanner
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import CapacityTable, GenerationWorker
from lib.infra.app_data_dir import reset_for_tests
from lib.project.project_manager import ProjectManager
from server.tool_runtime import (
    CallerContext,
    PlanEpisodesRequest,
    ProjectScope,
    Services,
    ToolRequest,
    execute_queued_text_task,
    plan_episodes,
    start_episode_planning,
    start_episode_replan,
    stop_episode_planning,
)
from tests.factories import register_project_sources
from tests.fakes import refuse_resume_execution

_CHAPTERS = (
    "第一章。少年得到古玉，玉中藏着剑诀。",
    "第二章。他在山下遇到被追杀的少女。",
    "第三章。两人从此卷入江湖漩涡之中。",
)
_ANCHORS = ("玉中藏着剑诀。", "被追杀的少女。", "卷入江湖漩涡之中。")
# 窗口只容得下一章多一点：前两窗都不是最后一窗，第三窗剩余不足 1.2 倍窗口而延伸到结尾
_WINDOW_CHARS = len(_CHAPTERS[0]) + 3


class _Generator:
    """窗口里出现的每个锚点各回一集；``hold`` 置位时第一次调用停在模型请求里，直到测试放行。"""

    model = "fake-model"

    def __init__(self, *, hold: bool = False, max_output_tokens: int = 64000) -> None:
        self.max_output_tokens = max_output_tokens
        self.prompts: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    async def generate(self, request: Any, project_name: str | None = None) -> TextGenerationResult:
        del project_name
        self.prompts.append(request.prompt)
        self.started.set()
        await self.release.wait()
        window = request.prompt.rsplit("---", 2)[-2]
        episodes = [
            {"title": f"第{index + 1}集", "hook": "悬念", "end_anchor": anchor}
            for index, anchor in enumerate(_ANCHORS)
            if anchor in window
        ]
        body = {"episodes": episodes}
        return TextGenerationResult(text=json.dumps(body, ensure_ascii=False), provider="fake", model="fake-model")


@pytest.fixture
async def planning(tmp_path: Path, file_db_factory, monkeypatch: pytest.MonkeyPatch):
    projects = ProjectManager(tmp_path / "projects")
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(projects.data_root))
    reset_for_tests()
    projects.create_project("planning", content_mode="narration")
    projects.create_project_metadata("planning", "Planning", "", "narration")
    register_project_sources(projects, "planning", whole_source={"novel.txt": "".join(_CHAPTERS)})
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=projects)
    services = Services(
        projects=projects,
        workflow_planner=object(),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        del claimed_provider_id
        return await execute_queued_text_task(task, services=services)

    async def text_provider(_task: dict[str, Any]) -> str:
        return "text"

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"text": 1}),
        provider_projection=text_provider,
        executor=execute,
        lanes=("text",),
        resume_executor=refuse_resume_execution,
    )
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    assert await queue.acquire_or_renew_worker_lease(
        name=worker.lease_name, owner_id=worker.owner_id, ttl_seconds=worker.lease_ttl
    )
    await worker.start()
    try:
        yield projects, services, file_db_factory
    finally:
        await worker.stop()


def _use_generator(
    monkeypatch: pytest.MonkeyPatch, generator: _Generator, *, window_chars: int = _WINDOW_CHARS
) -> None:
    async def create(_cls, path):
        return EpisodePlanner(path, generator=generator, window_chars=window_chars)

    monkeypatch.setattr(EpisodePlanner, "create", classmethod(create))


_WEB = CallerContext(user_id=DEFAULT_USER_ID, source="webui")


def _scope(projects: ProjectManager) -> ProjectScope:
    return ProjectScope(project_name="planning", data_root=projects.data_root)


async def _planning_tasks(session_factory) -> list[Task]:
    async with session_factory() as session:
        rows = await session.execute(
            select(Task).where(Task.task_type == "text_episode_plan").order_by(Task.queued_at, Task.task_id)
        )
        return list(rows.scalars())


async def _wait_until_idle(session_factory) -> list[Task]:
    for _ in range(500):
        tasks = await _planning_tasks(session_factory)
        if tasks and all(task.status in ("succeeded", "failed", "cancelled") for task in tasks):
            return tasks
        await asyncio.sleep(0.01)
    raise AssertionError("分集规划没有在限定时间内结束")


def _titles(projects: ProjectManager) -> list[str]:
    return [episode["title"] for episode in projects.load_project("planning")["episodes"]]


async def test_web_planning_runs_window_by_window_to_the_end(planning, monkeypatch: pytest.MonkeyPatch) -> None:
    projects, services, session_factory = planning
    generator = _Generator()
    _use_generator(monkeypatch, generator)

    outcome = await start_episode_planning(
        ToolRequest(PlanEpisodesRequest(instructions="按章节")), _scope(projects), _WEB, services
    )

    assert outcome.problem is None
    tasks = await _wait_until_idle(session_factory)
    assert [task.status for task in tasks] == ["succeeded", "succeeded", "succeeded"]
    assert _titles(projects) == ["第1集", "第2集", "第3集"]
    assert all("按章节" in prompt for prompt in generator.prompts)
    assert json.loads(tasks[-1].result_json or "{}")["source_exhausted"] is True


async def test_a_final_window_capped_by_the_batch_size_keeps_planning_to_the_end(
    planning, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects, services, session_factory = planning
    # 整本源文落在一个窗口里，模型一次给出三集；输出上限只够每批一集
    _use_generator(monkeypatch, _Generator(max_output_tokens=400), window_chars=len("".join(_CHAPTERS)))

    await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    tasks = await _wait_until_idle(session_factory)

    assert [task.status for task in tasks] == ["succeeded", "succeeded", "succeeded"]
    assert _titles(projects) == ["第1集", "第2集", "第3集"]


async def test_stop_keeps_finished_windows_and_the_next_run_resumes_from_the_cursor(
    planning, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects, services, session_factory = planning
    generator = _Generator(hold=True)
    _use_generator(monkeypatch, generator)
    await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    await asyncio.wait_for(generator.started.wait(), timeout=5)

    stopped = await stop_episode_planning(_scope(projects), _WEB, services)
    generator.release.set()

    assert len(stopped.cancelled) == 1
    assert len(stopped.running) == 1
    tasks = await _wait_until_idle(session_factory)
    assert [task.status for task in tasks] == ["succeeded", "cancelled"]
    assert _titles(projects) == ["第1集"]

    _use_generator(monkeypatch, _Generator())
    await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    await _wait_until_idle(session_factory)

    assert _titles(projects) == ["第1集", "第2集", "第3集"]


async def test_stop_during_a_capped_final_window_does_not_queue_another(
    planning, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects, services, session_factory = planning
    # 整本源文落在一个窗口里、每批只够一集：下一窗要等模型返回后才知道要不要排，停止时还没有可取消的窗口
    generator = _Generator(hold=True, max_output_tokens=400)
    _use_generator(monkeypatch, generator, window_chars=len("".join(_CHAPTERS)))
    await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    await asyncio.wait_for(generator.started.wait(), timeout=5)

    stopped = await stop_episode_planning(_scope(projects), _WEB, services)
    generator.release.set()

    assert stopped.cancelled == []
    assert len(stopped.running) == 1
    tasks = await _wait_until_idle(session_factory)
    await asyncio.sleep(0.1)
    assert [task.status for task in await _planning_tasks(session_factory)] == ["succeeded"]
    assert [task.task_id for task in tasks] == stopped.running
    assert _titles(projects) == ["第1集"]


async def test_a_running_web_planning_refuses_another_planning_request(
    planning, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects, services, _session_factory = planning
    generator = _Generator(hold=True)
    _use_generator(monkeypatch, generator)
    await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    await asyncio.wait_for(generator.started.wait(), timeout=5)

    try:
        web_again = await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
        agent = await plan_episodes(
            ToolRequest(PlanEpisodesRequest()),
            _scope(projects),
            CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
            services,
        )
    finally:
        await stop_episode_planning(_scope(projects), _WEB, services)
        generator.release.set()

    assert web_again.problem is not None
    assert web_again.problem.code == "generation_active_task_conflict"
    assert agent.problem is not None
    assert agent.problem.code == "generation_active_task_conflict"


async def test_the_gap_left_by_a_deleted_middle_episode_is_planned_back(
    planning, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects, services, session_factory = planning
    _use_generator(monkeypatch, _Generator())
    await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    await _wait_until_idle(session_factory)
    middle = projects.load_project("planning")["episodes"][1]
    preview = delete_episode(projects, "planning", middle["episode"])
    assert isinstance(preview, EpisodeDeletionConfirmationRequired)
    delete_episode(projects, "planning", middle["episode"], revision=preview.impact.revision)

    # 一键规划不回填空段：账本推导的规划起点仍在结尾
    again = await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    assert again.problem is None
    await _wait_until_idle(session_factory)
    assert _titles(projects) == ["第1集", "第3集"]

    gap_end = middle["source_range"]["end"]
    outcome = await start_episode_planning(
        ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services, gap=("source/novel.txt", gap_end)
    )

    assert outcome.problem is None
    tasks = await _wait_until_idle(session_factory)
    episodes = projects.load_project("planning")["episodes"]
    assert [episode["title"] for episode in episodes] == ["第1集", "第2集", "第3集"]
    assert episodes[1]["source_range"] == middle["source_range"]
    assert episodes[1]["episode"] > middle["episode"]
    assert "这段未切分的原文已全部规划完毕" in (tasks[-1].result_json or "")


async def test_replanning_fills_a_candidate_window_by_window_and_leaves_the_ledger_alone(
    planning, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects, services, session_factory = planning
    _use_generator(monkeypatch, _Generator())
    await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    await _wait_until_idle(session_factory)
    ledger = projects.load_project("planning")["episodes"]
    generator = _Generator()
    _use_generator(monkeypatch, generator)

    outcome = await start_episode_replan(
        ToolRequest(PlanEpisodesRequest(instructions="节奏放慢")),
        _scope(projects),
        _WEB,
        services,
        episode=ledger[1]["episode"],
    )

    assert outcome.problem is None
    tasks = await _wait_until_idle(session_factory)
    assert [task.status for task in tasks] == ["succeeded"] * 5
    project = projects.load_project("planning")
    assert project["episodes"] == ledger
    candidate = project["episode_replan"]
    assert candidate["complete"] is True
    assert [episode["source_range"] for episode in candidate["episodes"]] == [e["source_range"] for e in ledger[1:]]
    assert all("节奏放慢" in prompt for prompt in generator.prompts)
    assert "第一章" not in generator.prompts[0].rsplit("---", 2)[-2]


async def test_a_pending_candidate_refuses_planning_and_another_replan(
    planning, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects, services, session_factory = planning
    _use_generator(monkeypatch, _Generator())
    await start_episode_planning(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services)
    await _wait_until_idle(session_factory)
    first = projects.load_project("planning")["episodes"][0]["episode"]
    await start_episode_replan(ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services, episode=first)
    await _wait_until_idle(session_factory)

    agent = await plan_episodes(
        ToolRequest(PlanEpisodesRequest()),
        _scope(projects),
        CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
        services,
    )
    again = await start_episode_replan(
        ToolRequest(PlanEpisodesRequest()), _scope(projects), _WEB, services, episode=first
    )

    assert agent.problem is not None
    assert agent.problem.params["reason"] == "replan_candidate_pending"
    assert again.problem is not None
    assert again.problem.params["reason"] in ("replan_candidate_pending", "candidate_pending")
