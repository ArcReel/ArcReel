"""Cross-language closed-set contract for `next_action.type`.

The enum is the only source; frontend union and profile controlled-action table derive from it.
These tests prevent derived-output drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.generation.generation_result import GenerationAction
from lib.project.project_migration_failure import RETRY_MIGRATION_ACTION
from lib.workflow.workflow_state import WorkflowActionType
from server.text_generation import PromptOverwriteRequiredError
from server.tool_runtime import GenerateEpisodeScriptRequest, _prompt_overwrite_problem

REPO = Path(__file__).resolve().parents[4]
FRONTEND_TYPES = REPO / "frontend" / "src" / "types" / "workflow.ts"

_ARRAY_RE = re.compile(r"export const WORKFLOW_ACTION_TYPES = \[(.*?)\] as const;", re.DOTALL)


def _frontend_action_types() -> list[str]:
    match = _ARRAY_RE.search(FRONTEND_TYPES.read_text(encoding="utf-8"))
    assert match is not None, "frontend/src/types/workflow.ts 未导出 WORKFLOW_ACTION_TYPES"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_generation_actions_are_all_dispatchable_next_actions() -> None:
    """Batch-admission rejection projects problems[0].action unchanged into next_action.type; the closed set must include it."""

    missing = {action.value for action in GenerationAction} - {action.value for action in WorkflowActionType}

    assert not missing, f"WorkflowActionType 缺少 GenerationAction 取值：{sorted(missing)}"


def test_migration_retry_action_is_in_the_closed_set() -> None:
    """Migration-failed projects report only this action; disconnecting it from the set breaks status queries entirely."""

    assert WorkflowActionType.RETRY_PROJECT_MIGRATION.value == RETRY_MIGRATION_ACTION


def test_frontend_union_matches_the_backend_enum() -> None:
    """Frontend dispatches using this set; missing an action renders an explicit backend step as unknown."""

    assert _frontend_action_types() == [action.value for action in WorkflowActionType]


PROFILE_ACTION_READERS = (
    "agent_runtime_profile/.claude/references/workflow-plan.md",
    "agent_runtime_profile/.claude/skills/generate-script/SKILL.md",
)


@pytest.mark.parametrize("relative_path", PROFILE_ACTION_READERS)
def test_author_prompts_action_is_mirrored_in_the_profile(relative_path: str) -> None:
    """Authoring prompts is a next action: the table must route it and generate-script must specify its invocation contract."""
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert f"`{WorkflowActionType.AUTHOR_PROMPTS.value}`" in md, (
        f"{WorkflowActionType.AUTHOR_PROMPTS.value} 未在 {relative_path} 中找到（漂移）"
    )


def _author_prompts_section(md: str) -> str:
    """Extract the heading naming ``author_prompts`` through the next same-or-higher-level heading."""
    action = f"`{WorkflowActionType.AUTHOR_PROMPTS.value}`"
    match = re.search(rf"^### [^\n]*{re.escape(action)}[^\n]*\n(.*?)(?=^#{{1,3}} |\Z)", md, re.DOTALL | re.MULTILINE)
    assert match is not None, f"generate-script skill 没有以 {action} 为标题的小节"
    return match.group(1)


def test_generate_script_skill_pins_the_author_prompts_contract() -> None:
    """Prompt-authoring arguments/problem codes derive from request models/tool problems; stale skills fail on rename/removal.

    Default fill-missing, ``entry_ids`` scope only, explicit ``rewrite``, and pre-overwrite
    ``overwrite_revision`` confirmation are parameter contracts. The request model rejects retired
    ``scope``, which must also disappear from the skill.
    """
    md = (REPO / "agent_runtime_profile/.claude/skills/generate-script/SKILL.md").read_text(encoding="utf-8")
    section = _author_prompts_section(md)
    fields = GenerateEpisodeScriptRequest.model_fields
    overwrite_code = _prompt_overwrite_problem(PromptOverwriteRequiredError("", {"revision": "r"})).code

    assert fields["rewrite"].default is False, "省略 rewrite 的请求须走默认补缺"
    for field in ("entry_ids", "rewrite", "overwrite_revision"):
        assert field in fields, field
        assert f"`{field}" in section, field
    assert f"`{overwrite_code}`" in section
    with pytest.raises(ValidationError):
        GenerateEpisodeScriptRequest.model_validate({"episode_id": 1, "scope": "all"})
    assert "scope" not in md
