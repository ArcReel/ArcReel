"""Claude SDK tool boundary for HyperFrames authoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.hyperframes import (
    generate_hyperframes_bgm_tool,
    prepare_hyperframes_episode_tool,
)
from server.services.hyperframes_music import HyperframesBackgroundMusic
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


async def test_music_tool_returns_the_fixed_volume_project_local_snippet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    music_path = tmp_path / "demo" / "hyperframes" / "episode_01" / "media" / "bgm.mp3"
    music = HyperframesBackgroundMusic(
        episode=1,
        path=music_path,
        relative_path="media/bgm.mp3",
        metadata_path="background-music.json",
        duration_seconds=12.0,
        actual_duration_seconds=12.0,
        volume=0.15,
        seed=7,
        html_snippet='<audio data-volume="0.150" src="media/bgm.mp3"></audio>',
    )
    calls = []

    class _MusicService:
        def __init__(self, _pm) -> None:
            pass

        async def generate(self, project_name, episode, *, direction, seed=None):
            calls.append((project_name, episode, direction, seed))
            return music

    monkeypatch.setattr(
        "server.agent_runtime.sdk_tools.hyperframes.HyperframesMusicService",
        _MusicService,
    )
    ctx = ToolContext("demo", tmp_path, pm=ProjectManager(tmp_path))

    result = await generate_hyperframes_bgm_tool(ctx).handler(
        {"episode": 1, "direction": "calm instrumental", "seed": 7}
    )

    assert result.get("is_error") is not True
    assert result["music"]["volume"] == 0.15
    assert 'data-volume="0.150"' in result["music"]["html_snippet"]
    assert calls == [("demo", 1, "calm instrumental", 7)]
