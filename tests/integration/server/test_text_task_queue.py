from __future__ import annotations

from pathlib import Path

import pytest

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.episode_planner import EpisodePlanningError, EpisodePlanSummary, PlanResult
from lib.generation_queue import GenerationQueue
from lib.generation_result import GenerationAction, problem_from_task_failure
from lib.project_manager import ProjectManager
from server.text_generation import TextGenerationRequest
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
    monkeypatch,
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
    queue = GenerationQueue(session_factory=file_db_factory)
    services = Services(
        projects=projects,
        workflow_planner=object(),  # type: ignore[arg-type]
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    scope = ProjectScope(project_name=project_name, projects_root=projects.projects_root)
    caller = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")
    monkeypatch.setattr("lib.generation_queue.assert_project_migration_ok", lambda _project: None)

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
