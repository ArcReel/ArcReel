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

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
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

    assert RETRY_MIGRATION_ACTION == WorkflowActionType.RETRY_PROJECT_MIGRATION.value


def test_frontend_union_matches_the_backend_enum() -> None:
    """前端按这份闭集分派动作；漏一个就是界面把后端明确给出的一步讲成未知动作。"""

    assert _frontend_action_types() == [action.value for action in WorkflowActionType]
