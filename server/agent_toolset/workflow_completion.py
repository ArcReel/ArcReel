"""Completion declaration: atomically record completion facts required by the production plan so it can converge.

Completion cannot be inferred from file content (a rebuild may exactly match the old file); the producing party must explicitly submit it."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.tool_runtime import CompleteScriptPlanRebuildRequest, complete_script_plan_rebuild

COMPLETE_SCRIPT_PLAN_REBUILD = ToolDeclaration(
    name="complete_script_plan_rebuild",
    description=(
        "Call after successfully rebuilding a stale episode’s script_plan to atomically record completion; the production plan can converge even if the rebuilt content exactly matches the old script_plan. Use only when next_action.args includes expected_stale_script_plan_revision and pass that value unchanged. Rejects without writing if the episode is not stale (not_stale), the rebuild baseline is absent (missing_baseline), the baseline changed (baseline_conflict), or formal script_plan is absent (script_plan_missing). On conflicts, refresh the production plan rather than retrying old arguments. Success returns the episode and rebuilt script_plan revision."
    ),
    request_model=CompleteScriptPlanRebuildRequest,
    migration=BLOCKED,
    domain_key="script_plan_rebuild",
    handler=complete_script_plan_rebuild,
)

WORKFLOW_COMPLETION_TOOLS = (COMPLETE_SCRIPT_PLAN_REBUILD,)

__all__ = ["COMPLETE_SCRIPT_PLAN_REBUILD", "WORKFLOW_COMPLETION_TOOLS"]
