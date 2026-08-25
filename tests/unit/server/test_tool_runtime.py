from __future__ import annotations

from pathlib import Path

from lib.workflow_plan import WorkflowPlanRequest, build_workflow_plan
from lib.workflow_state import WorkflowStatus
from server.tool_runtime import (
    CallerContext,
    ProjectScope,
    Services,
    ToolRequest,
    get_video_capabilities,
    get_workflow_plan,
)


class _Projects:
    def __init__(self, project: dict):
        self.project = project

    def load_project(self, name: str) -> dict:
        assert name == "demo"
        return self.project


class _Planner:
    def __init__(self, status: WorkflowStatus):
        self.status = status

    async def get_plan(self, project_name: str, request: WorkflowPlanRequest):
        assert project_name == "demo"
        return build_workflow_plan(self.status, narration_delivery=request.narration_delivery)


class _Capabilities:
    async def video_capabilities_for_project(self, project: dict, *, capability=None) -> dict:
        assert project["generation_mode"] == "storyboard"
        assert capability is None
        return {"provider_id": "fake", "model": "video-1", "supported_durations": [4, 6]}


def _status() -> WorkflowStatus:
    return WorkflowStatus.model_validate(
        {
            "project_revision": "sha256-v1:project",
            "source_revision": None,
            "project": {"content_mode": "ad", "generation_mode": "storyboard", "grid_storyboard": False},
            "target": {
                "episode": 1,
                "script": "scripts/episode_1.json",
                "script_filename": "episode_1.json",
                "source": "source/episode_1.txt",
            },
            "state": "FINAL_SCRIPT",
            "blockers": [],
            "gates": {"step1_review": {"state": "not_applicable", "revision": None}},
            "artifacts": {
                "asset_inventory": {"state": "not_applicable"},
                "asset_sheets": {},
                "step1": {"state": "not_applicable"},
                "script": {"state": "missing"},
                "storyboards": {"current_ids": [], "stale_ids": [], "missing_ids": []},
                "videos": {"current_ids": [], "stale_ids": [], "missing_ids": []},
                "audio": {"state": "not_applicable", "current_ids": [], "stale_ids": [], "missing_ids": []},
            },
            "next_action": {"type": "generate_script", "reason": "script missing"},
        }
    )


async def test_workflow_plan_returns_typed_domain_outcome() -> None:
    project = {"generation_mode": "storyboard"}
    outcome = await get_workflow_plan(
        ToolRequest(WorkflowPlanRequest(episode=1)),
        ProjectScope("demo", Path("/projects")),
        CallerContext(user_id="u1", source="embedded"),
        Services(projects=_Projects(project), workflow_planner=_Planner(_status()), capabilities=_Capabilities()),
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.status.target is not None
    assert outcome.value.status.target.episode == 1


async def test_video_capabilities_returns_typed_domain_outcome() -> None:
    project = {"generation_mode": "storyboard", "content_mode": "drama"}
    outcome = await get_video_capabilities(
        ToolRequest(None),
        ProjectScope("demo", Path("/projects")),
        CallerContext(user_id="u1", source="embedded"),
        Services(projects=_Projects(project), workflow_planner=_Planner(_status()), capabilities=_Capabilities()),
    )

    assert outcome.problem is None
    assert outcome.value == {"provider_id": "fake", "model": "video-1", "supported_durations": [4, 6]}
