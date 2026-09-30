"""AI 操作入口与制作状态共用一份准入：同一条件下，入口的拒绝理由码等于状态里该操作的理由码。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_activation import register_current_artifact
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_plan import WorkflowPlanRequest
from lib.workflow.workflow_state import WorkflowStateService
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import (
    CallerContext,
    GenerateEpisodeScriptRequest,
    GenerateScriptPlanRequest,
    PlanEpisodesRequest,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolRequest,
    generate_episode_script,
    generate_script_plan,
    plan_episodes,
)

_CALLER = CallerContext(user_id="u1", source="mcp")


def _project(tmp_path: Path, content_mode: str) -> ProjectManager:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo", content_mode=content_mode)
    projects.create_project_metadata("demo", "Demo", "", content_mode)
    return projects


def _services(projects: ProjectManager, db_factory) -> Services:
    return Services(
        projects=projects,
        workflow_planner=WorkflowPlanner(projects),
        capabilities=ConfigResolver(async_session_factory),
        queue=GenerationQueue(session_factory=db_factory, project_manager=projects),
    )


def _scope(projects: ProjectManager) -> ProjectScope:
    return ProjectScope("demo", projects.projects_dir)


def _refusal_reason(outcome: ToolOutcome[Any]) -> str:
    assert outcome.problem is not None
    assert outcome.problem.code == "operation_not_admitted"
    return outcome.problem.params["reason"]


def _manual_episode(projects: ProjectManager) -> None:
    projects.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {"episode": 1, "title": "番外", "script_file": "scripts/episode_1.json", "ledger_status": "planned"}
            ]
        ),
    )


async def test_plan_episodes_without_whole_source_is_refused_with_the_status_reason(tmp_path: Path, db_factory) -> None:
    projects = _project(tmp_path, "narration")

    outcome = await plan_episodes(
        ToolRequest(PlanEpisodesRequest()), _scope(projects), _CALLER, _services(projects, db_factory)
    )

    status = WorkflowStateService(projects).get_status("demo")
    assert _refusal_reason(outcome) == status.operations["plan_episodes"].reason == "whole_source_missing"


async def test_script_plan_without_episode_source_is_refused_with_the_status_reason(tmp_path: Path, db_factory) -> None:
    projects = _project(tmp_path, "narration")
    _manual_episode(projects)

    outcome = await generate_script_plan(
        ToolRequest(GenerateScriptPlanRequest(episode_id=1)), _scope(projects), _CALLER, _services(projects, db_factory)
    )

    status = WorkflowStateService(projects).get_status("demo", 1)
    assert status.blockers == []
    assert _refusal_reason(outcome) == status.operations["prepare_script_plan"].reason == "episode_source_missing"


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("registered", [False, True])
async def test_prompt_authoring_without_pending_entries_is_refused_with_the_status_reason(
    tmp_path: Path, db_factory, dry_run: bool, registered: bool
) -> None:
    projects = _project(tmp_path, "narration")
    _manual_episode(projects)
    project_path = projects.get_project_path("demo")
    (project_path / "scripts" / "episode_1.json").write_text(
        json.dumps({"episode": 1, "title": "番外", "content_mode": "narration", "segments": []}), encoding="utf-8"
    )
    if registered:
        register_current_artifact(project_path, ArtifactKey.episode_script(1))

    outcome = await generate_episode_script(
        ToolRequest(GenerateEpisodeScriptRequest(episode_id=1, dry_run=dry_run)),
        _scope(projects),
        _CALLER,
        _services(projects, db_factory),
    )

    status = WorkflowStateService(projects).get_status("demo", 1)
    expected_reason = "no_pending_authoring" if registered else "formal_script_missing"
    assert status.content is not None
    assert status.content.formal_script == ("present" if registered else "absent")
    assert status.next_action.type == ("add_script_items" if registered else "start_blank_script")
    assert _refusal_reason(outcome) == status.operations["author_prompts"].reason == expected_reason


async def test_ad_script_without_brief_or_products_is_refused_with_the_status_reason(
    tmp_path: Path, db_factory
) -> None:
    projects = _project(tmp_path, "ad")

    outcome = await generate_episode_script(
        ToolRequest(GenerateEpisodeScriptRequest(episode_id=1)),
        _scope(projects),
        _CALLER,
        _services(projects, db_factory),
    )

    status = WorkflowStateService(projects).get_status("demo")
    assert _refusal_reason(outcome) == status.operations["generate_script"].reason == "ad_brief_and_products_missing"


@pytest.mark.usefixtures("video_request_facts")
async def test_ad_generation_ignores_an_unregistered_script_file(tmp_path: Path, db_factory) -> None:
    projects = _project(tmp_path, "ad")
    projects.update_project("demo", lambda project: project.update(brief="展示一瓶饮料"))
    project_path = projects.get_project_path("demo")
    (project_path / "scripts" / "episode_1.json").write_text(
        json.dumps({"episode": 1, "title": "广告", "content_mode": "ad", "shots": []}), encoding="utf-8"
    )

    outcome = await generate_episode_script(
        ToolRequest(GenerateEpisodeScriptRequest(episode_id=1, dry_run=True)),
        _scope(projects),
        _CALLER,
        _services(projects, db_factory),
    )

    status = WorkflowStateService(projects).get_status("demo", 1)
    assert status.content is not None
    assert status.content.formal_script == "absent"
    assert status.operations["generate_script"].state == "admitted"
    assert outcome.problem is None
    assert "展示一瓶饮料" in str(outcome.value)
    assert "本次没有要编写的条目" not in str(outcome.value)


async def test_unreadable_project_data_is_returned_as_a_plan_blocker(tmp_path: Path) -> None:
    projects = _project(tmp_path, "narration")
    (projects.get_project_path("demo") / "project.json").write_text("{", encoding="utf-8")

    plan = await WorkflowPlanner(projects).get_plan("demo", WorkflowPlanRequest(episode_id=1))

    assert [blocker.code for blocker in plan.blockers] == ["project_data_unavailable"]
    assert plan.status.content is None
    assert plan.steps[0].state == "blocked"
    assert plan.next_action.type == "none"
