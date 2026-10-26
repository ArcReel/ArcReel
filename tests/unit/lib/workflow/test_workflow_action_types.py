"""`next_action.type` 闭集的跨语言契约。

枚举是唯一真相源；前端联合类型与 profile 受控动作表都从它派生，这里守住派生结果没有漂移。
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
    """补提示词是下一步动作：动作表要能路由它，generate-script skill 要写明它的调用契约。"""
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert f"`{WorkflowActionType.AUTHOR_PROMPTS.value}`" in md, (
        f"{WorkflowActionType.AUTHOR_PROMPTS.value} 未在 {relative_path} 中找到（漂移）"
    )


def _author_prompts_section(md: str) -> str:
    """取标题里点名 ``author_prompts`` 动作的那一小节，到下一个同级或更高级标题为止。"""
    action = f"`{WorkflowActionType.AUTHOR_PROMPTS.value}`"
    match = re.search(rf"^### [^\n]*{re.escape(action)}[^\n]*\n(.*?)(?=^#{{1,3}} |\Z)", md, re.DOTALL | re.MULTILINE)
    assert match is not None, f"generate-script skill 没有以 {action} 为标题的小节"
    return match.group(1)


def test_generate_script_skill_pins_the_author_prompts_contract() -> None:
    """补提示词小节点名的参数与问题码都取自请求模型与工具问题：改名或删字段而 skill 没跟上会红。

    默认补缺、``entry_ids`` 只划范围、``rewrite`` 显式重写、覆盖前经 ``overwrite_revision`` 确认，
    这几条规则落在参数上；已取消的 ``scope`` 由请求模型拒绝，skill 里也不得再出现。
    """
    md = (REPO / "agent_runtime_profile/.claude/skills/generate-script/SKILL.md").read_text(encoding="utf-8")
    section = _author_prompts_section(md)
    fields = GenerateEpisodeScriptRequest.model_fields
    overwrite_code = _prompt_overwrite_problem(PromptOverwriteRequiredError("", {"revision": "r"})).code

    for field in ("entry_ids", "rewrite", "overwrite_revision"):
        assert field in fields, field
        assert f"`{field}" in section, field
    assert f"`{overwrite_code}`" in section
    with pytest.raises(ValidationError):
        GenerateEpisodeScriptRequest.model_validate({"episode_id": 1, "scope": "all"})
    assert "scope" not in md
