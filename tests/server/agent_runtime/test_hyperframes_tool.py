"""Claude SDK tool boundary for HyperFrames authoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.hyperframes import prepare_hyperframes_episode_tool
from server.services.hyperframes_workspace import HyperframesWorkspace

pytestmark = pytest.mark.unit


class _Service:
    def __init__(self, _pm: ProjectManager, workspace: HyperframesWorkspace) -> None:
        self.workspace = workspace
        self.calls: list[tuple[str, int, str]] = []

    async def prepare(self, project_name: str, episode: int, *, variant: str):
        self.calls.append((project_name, episode, variant))
        return self.workspace


async def test_tool_returns_one_explicit_project_local_write_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "demo" / "hyperframes" / "episode_01"
    workspace = HyperframesWorkspace(
        project_name="demo",
        episode=1,
        path=workspace_path,
        relative_path="hyperframes/episode_01",
        composition_path="hyperframes/episode_01/index.html",
        manifest_path="hyperframes/episode_01/manifest.json",
    )
    service = _Service(ProjectManager(tmp_path), workspace)
    monkeypatch.setattr(
        "server.agent_runtime.sdk_tools.hyperframes.HyperframesWorkspaceService",
        lambda _pm: service,
    )
    ctx = ToolContext("demo", tmp_path, pm=ProjectManager(tmp_path))

    result = await prepare_hyperframes_episode_tool(ctx).handler(
        {"episode": 1, "narration_delivery": "post_production"}
    )

    assert result.get("is_error") is not True
    assert result["workspace"]["write_boundary"] == str(workspace_path)
    assert result["workspace"]["entry_file"] == str(workspace_path / "index.html")
    assert service.calls == [("demo", 1, "post_production")]


async def test_tool_rejects_invalid_episode_before_touching_workspace(tmp_path: Path) -> None:
    ctx = ToolContext("demo", tmp_path, pm=ProjectManager(tmp_path))

    result = await prepare_hyperframes_episode_tool(ctx).handler({"episode": 0})

    assert result["is_error"] is True
    assert "正整数" in result["content"][0]["text"]
