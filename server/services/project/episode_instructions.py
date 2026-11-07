"""Per-episode additional instructions: separate script-planning and prompt-authoring values stored on episode entries and prefilled on regeneration.

Generation submissions and handoffs to Agent both save here; blank means clear.
"""

from enum import StrEnum
from typing import Any

from lib.infra.api_errors import NotFoundError
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager, find_episode


class EpisodeInstructionKind(StrEnum):
    """AI action owning additional instructions; values are episode-entry field names."""

    SCRIPT_PLAN = "script_plan_instructions"
    PROMPT_AUTHORING = "prompt_authoring_instructions"


def save_episode_instructions(
    pm: ProjectManager,
    project_name: str,
    episode: int,
    kind: EpisodeInstructionKind,
    instructions: str | None,
) -> None:
    text = (instructions or "").strip()

    def mutate(project: dict[str, Any]) -> None:
        entry = find_episode(project, episode)
        if entry is None:
            raise NotFoundError("episode_not_found", episode=episode)
        if text:
            entry[kind.value] = text
        else:
            entry.pop(kind.value, None)

    try:
        with project_change_source("webui"):
            pm.update_project(project_name, mutate)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
