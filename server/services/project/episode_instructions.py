"""按集保存的附加指令：脚本规划与提示词编写各存一份在集条目上，重新生成时预填。

提交生成与「交给 Agent」都经这里保存；空白视同清除。
"""

from enum import StrEnum
from typing import Any

from lib.infra.api_errors import NotFoundError
from lib.project.project_manager import ProjectManager, find_episode


class EpisodeInstructionKind(StrEnum):
    """附加指令所属的 AI 动作；值即集条目上的字段名。"""

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
        pm.update_project(project_name, mutate)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
