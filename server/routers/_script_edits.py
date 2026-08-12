"""HTTP compatibility adapters for the shared episode-script edit command."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from fastapi import HTTPException

from lib.project_manager import ProjectManager
from lib.script_batch_edit import (
    ScriptBatchEditCommand,
    ScriptBatchEditor,
    ScriptBatchEditResult,
    script_revision,
)

_SPEECH_PROBLEM_CODES = frozenset({"mixed_speech", "needs_replan", "parse_failed", "empty_speaker"})


class ScriptEditExecutor(Protocol):
    def execute(self, project_name: str, command: ScriptBatchEditCommand) -> ScriptBatchEditResult: ...


def script_batch_status(result: ScriptBatchEditResult) -> int:
    if result.success:
        return 200
    code = result.problems[0].code
    if code == "revision_conflict" or code in _SPEECH_PROBLEM_CODES:
        return 409
    if code == "commit_failed":
        return 500
    return 422


def execute_current_script_edit(
    manager: ProjectManager,
    project_name: str,
    script_file: str,
    operations: Sequence[Mapping[str, Any]],
    *,
    editor: ScriptEditExecutor | None = None,
) -> ScriptBatchEditResult:
    """Adapt an unversioned legacy request to one revisioned command.

    The revision is only a compatibility snapshot. The editor still compares it inside
    the project lock, so a concurrent writer is rejected instead of being overwritten.
    """

    current = manager.load_script(project_name, script_file)
    command = ScriptBatchEditCommand.model_validate(
        {
            "script": script_file,
            "expected_revision": script_revision(current),
            "operations": list(operations),
        }
    )
    return (editor or ScriptBatchEditor(manager)).execute(project_name, command)


def execute_current_episode_edit(
    manager: ProjectManager,
    project_name: str,
    episode: int,
    current_script: Mapping[str, Any],
    operations: Sequence[Mapping[str, Any]],
) -> ScriptBatchEditResult:
    """Adapt an episode-scoped legacy request while retaining binding TOCTOU checks."""

    command = ScriptBatchEditCommand.model_validate(
        {
            "episode": episode,
            "expected_revision": script_revision(current_script),
            "operations": list(operations),
        }
    )
    return ScriptBatchEditor(manager).execute(project_name, command)


def require_script_edit_result(
    result: ScriptBatchEditResult,
    *,
    operation_not_found: bool = False,
) -> None:
    if result.success:
        return
    first = result.problems[0]
    status_code = script_batch_status(result)
    if first.code == "operation_invalid" and operation_not_found:
        status_code = 404
    elif first.code == "references_invalid":
        status_code = 400
    raise HTTPException(status_code=status_code, detail=result.model_dump(mode="json"))


__all__ = [
    "execute_current_episode_edit",
    "execute_current_script_edit",
    "require_script_edit_result",
    "script_batch_status",
]
