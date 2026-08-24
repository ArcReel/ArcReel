from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lib.workflow_plan import WorkflowPlanRequest, build_workflow_plan
from lib.workflow_state import WorkflowStatus
from server import text_generation as shared_text_generation
from server import tool_runtime
from server.tool_runtime import (
    CallerContext,
    PatchEpisodeScriptRequest,
    ProjectScope,
    Services,
    TextGenerationRequest,
    TextGenerationResult,
    ToolRequest,
    confirm_script_review,
    generate_episode_script,
    generate_step1,
    get_video_capabilities,
    get_workflow_plan,
    patch_episode_script,
)


class _Projects:
    def __init__(self, project: dict):
        self.project = project

    def load_project(self, name: str) -> dict:
        assert name == "demo"
        return self.project

    def load_script(self, name: str, script: str) -> dict:
        assert name == "demo"
        assert script == "episode_1.json"
        return {"episode": 1, "segments": []}


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


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        imported
        for node in ast.walk(tree)
        for imported in (
            [node.module]
            if isinstance(node, ast.ImportFrom) and node.module
            else [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else []
        )
    }


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


async def test_patch_episode_script_returns_typed_revision_conflict() -> None:
    project = {"generation_mode": "storyboard"}
    outcome = await patch_episode_script(
        ToolRequest(
            PatchEpisodeScriptRequest.model_validate(
                {
                    "script": "episode_1.json",
                    "base_revision": "sha256-v1:" + "0" * 64,
                    "operations": [{"op": "remove", "id": "E1S01"}],
                }
            )
        ),
        ProjectScope("demo", Path("/projects")),
        CallerContext(user_id="u1", source="embedded"),
        Services(projects=_Projects(project), workflow_planner=_Planner(_status()), capabilities=_Capabilities()),
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.problems[0].code == "revision_conflict"


async def test_text_generation_tools_return_typed_domain_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def script_handler(request: TextGenerationRequest, **_kwargs) -> TextGenerationResult:
        return TextGenerationResult(f"script:{request.episode}")

    async def step1_handler(request: TextGenerationRequest, **_kwargs) -> TextGenerationResult:
        return TextGenerationResult(f"step1:{request.episode}")

    async def confirm_handler(episode: int, **_kwargs) -> TextGenerationResult:
        return TextGenerationResult(f"confirmed:{episode}")

    monkeypatch.setattr(tool_runtime, "generate_episode_script_handler", script_handler)
    monkeypatch.setattr(tool_runtime, "generate_narration_step1", step1_handler)
    monkeypatch.setattr(tool_runtime, "confirm_script_review_handler", confirm_handler)

    project = {"generation_mode": "storyboard"}
    scope = ProjectScope("demo", Path("/projects"))
    caller = CallerContext(user_id="u1", source="embedded")
    services = Services(
        projects=_Projects(project),
        workflow_planner=_Planner(_status()),
        capabilities=_Capabilities(),
    )
    request = ToolRequest(TextGenerationRequest(episode=2))

    script = await generate_episode_script(request, scope, caller, services)
    step1 = await generate_step1(request, scope, caller, services)
    confirmed = await confirm_script_review(ToolRequest(2), scope, caller, services)

    assert script.value == TextGenerationResult("script:2")
    assert step1.value == TextGenerationResult("step1:2")
    assert confirmed.value == TextGenerationResult("confirmed:2")


def test_text_generation_dependency_points_from_host_adapters_to_shared_handler() -> None:
    shared_path = Path(shared_text_generation.__file__)
    sdk_path = shared_path.parent / "agent_runtime" / "sdk_tools" / "text_generation.py"
    shared_imports = _imported_modules(shared_path)
    sdk_imports = _imported_modules(sdk_path)

    assert "claude_agent_sdk" not in shared_imports
    assert not any(module.startswith("server.agent_runtime.sdk_tools") for module in shared_imports)
    assert "server.tool_runtime" in sdk_imports
    assert "server.text_generation" in sdk_imports
    assert '"is_error"' not in shared_path.read_text(encoding="utf-8")
