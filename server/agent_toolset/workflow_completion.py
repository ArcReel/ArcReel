"""待补全工具的声明：把制作计划要求的完成事实原子记入项目，让计划继续收敛。

完成事实不能由文件内容推断（重建可能与旧文件逐字相同），只能由产出它的一方显式提交。
"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.tool_runtime import CompleteScriptPlanRebuildRequest, complete_script_plan_rebuild

COMPLETE_SCRIPT_PLAN_REBUILD = ToolDeclaration(
    name="complete_script_plan_rebuild",
    description=(
        "stale 分集的 script_plan 重建成功后调用，原子记录重建完成事实；即使重建内容与旧 script_plan 逐字相同，"
        "制作计划也能继续收敛。仅在制作计划 next_action.args 带 expected_stale_script_plan_revision 时使用，"
        "该值原样传入。该集不在待重建状态（not_stale）、缺少重建基线（missing_baseline）、基线已变化（baseline_conflict）"
        "或正式 script_plan 文件缺失（script_plan_missing）时拒绝且不写入；报冲突时刷新制作计划，不要用旧参数重试。"
        "成功返回该集与重建后 script_plan 的 revision。"
    ),
    request_model=CompleteScriptPlanRebuildRequest,
    migration=BLOCKED,
    domain_key="script_plan_rebuild",
    handler=complete_script_plan_rebuild,
)

WORKFLOW_COMPLETION_TOOLS = (COMPLETE_SCRIPT_PLAN_REBUILD,)

__all__ = ["COMPLETE_SCRIPT_PLAN_REBUILD", "WORKFLOW_COMPLETION_TOOLS"]
