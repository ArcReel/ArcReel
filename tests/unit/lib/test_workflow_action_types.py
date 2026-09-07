"""`next_action.type` 闭集的跨语言契约。

枚举是唯一真相源；前端联合类型与 profile 受控动作表都从它派生，这里守住派生结果没有漂移。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.generation_result import GenerationAction
from lib.project_migration_failure import RETRY_MIGRATION_ACTION
from lib.workflow_state import WorkflowActionType

REPO = Path(__file__).resolve().parents[3]
FRONTEND_TYPES = REPO / "frontend" / "src" / "types" / "workflow.ts"

_ARRAY_RE = re.compile(r"export const WORKFLOW_ACTION_TYPES = \[(.*?)\] as const;", re.DOTALL)


def _frontend_action_types() -> list[str]:
    match = _ARRAY_RE.search(FRONTEND_TYPES.read_text(encoding="utf-8"))
    assert match is not None, "frontend/src/types/workflow.ts 未导出 WORKFLOW_ACTION_TYPES"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_generation_actions_are_all_dispatchable_next_actions() -> None:
    """整批准入判定被拒时 problems[0].action 会原样成为 next_action.type，闭集必须容得下它。"""

    missing = {action.value for action in GenerationAction} - {action.value for action in WorkflowActionType}

    assert not missing, f"WorkflowActionType 缺少 GenerationAction 取值：{sorted(missing)}"


def test_migration_retry_action_is_in_the_closed_set() -> None:
    """升级失败的项目只报这一个动作；它与闭集脱钩就等于状态查询整体不可用。"""

    assert WorkflowActionType.RETRY_PROJECT_MIGRATION.value == RETRY_MIGRATION_ACTION


def test_frontend_union_matches_the_backend_enum() -> None:
    """前端按这份闭集分派动作；漏一个就是界面把后端明确给出的一步讲成未知动作。"""

    assert _frontend_action_types() == [action.value for action in WorkflowActionType]


PROFILE_ACTION_READERS = (
    "agent_runtime_profile/.claude/references/workflow-plan.md",
    "agent_runtime_profile/.claude/skills/generate-script/SKILL.md",
)


@pytest.mark.parametrize("relative_path", PROFILE_ACTION_READERS)
def test_author_prompts_action_is_mirrored_in_the_profile(relative_path: str) -> None:
    """补提示词是新的下一步动作：动作表要能路由它，generate-script skill 要写明按 ``entry_ids`` 补、不整集重出。"""
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert f"`{WorkflowActionType.AUTHOR_PROMPTS.value}`" in md, (
        f"{WorkflowActionType.AUTHOR_PROMPTS.value} 未在 {relative_path} 中找到（漂移）"
    )


def test_generate_script_skill_pins_the_author_prompts_contract() -> None:
    """补提示词只按 ``entry_ids`` 补：skill 须写明不得整集重写、不得覆盖用户手写的提示词。"""
    md = (REPO / "agent_runtime_profile/.claude/skills/generate-script/SKILL.md").read_text(encoding="utf-8")
    section = md.split("### 补充提示词", 1)[1].split("\n## ", 1)[0]

    assert "`entry_ids`" in section
    assert '`scope: "all"`' in section
    assert "手写" in section
    assert "mcp__arcreel__convert_script_plan" in section
