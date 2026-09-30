"""按集保存的附加指令：脚本规划与提示词编写各存一份，重新生成时预填；空白即清除。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.infra.api_errors import NotFoundError
from lib.project.project_manager import ProjectManager
from server.services.project.episode_instructions import EpisodeInstructionKind, save_episode_instructions


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    return pm


def _episode(pm: ProjectManager) -> dict:
    return pm.load_project("demo")["episodes"][0]


def test_each_action_keeps_its_own_instructions_per_episode(pm: ProjectManager) -> None:
    save_episode_instructions(pm, "demo", 1, EpisodeInstructionKind.SCRIPT_PLAN, "  节奏紧凑一些  ")
    save_episode_instructions(pm, "demo", 1, EpisodeInstructionKind.PROMPT_AUTHORING, "冷色调")

    episode = _episode(pm)
    assert episode["script_plan_instructions"] == "节奏紧凑一些"
    assert episode["prompt_authoring_instructions"] == "冷色调"


def test_blank_instructions_clear_only_that_action(pm: ProjectManager) -> None:
    save_episode_instructions(pm, "demo", 1, EpisodeInstructionKind.SCRIPT_PLAN, "节奏紧凑一些")
    save_episode_instructions(pm, "demo", 1, EpisodeInstructionKind.PROMPT_AUTHORING, "冷色调")

    save_episode_instructions(pm, "demo", 1, EpisodeInstructionKind.SCRIPT_PLAN, "   ")

    episode = _episode(pm)
    assert "script_plan_instructions" not in episode
    assert episode["prompt_authoring_instructions"] == "冷色调"


def test_unknown_episode_is_not_found(pm: ProjectManager) -> None:
    with pytest.raises(NotFoundError):
        save_episode_instructions(pm, "demo", 9, EpisodeInstructionKind.SCRIPT_PLAN, "节奏紧凑一些")
