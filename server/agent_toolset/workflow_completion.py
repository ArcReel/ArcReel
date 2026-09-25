"""待补全工具的声明：把制作计划要求的完成事实原子记入项目，让计划继续收敛。

完成事实不能由文件内容推断（资产可能为空、重建可能与旧文件逐字相同），只能由产出它的一方显式提交。
"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.tool_runtime import (
    CompleteAssetInventoryRequest,
    CompleteScriptPlanRebuildRequest,
    complete_asset_inventory,
    complete_script_plan_rebuild,
)

_MIGRATION_REFUSAL = "项目数据升级失败时拒绝执行，返回 project_migration_failed problem。"

COMPLETE_ASSET_INVENTORY = ToolDeclaration(
    name="complete_asset_inventory",
    description=(
        "资产分析完成后调用一次，原子提交本次提取出的资产与资产清单完成事实；制作计划的下一动作为资产分析时，"
        "scope 与 expected_source_revision 原样取自其 args。工具在项目锁内重算源文 revision，与 expected_source_revision "
        "不一致时整笔拒绝（source_revision_conflict）；scope 内源文缺失、不可读、越界、不是源文或指向派生的分集文件时"
        "返回 source_blocked，params.blockers 列出逐文件原因。两种情况都不修改 project.json，应刷新制作计划后重新分析，"
        "不要用旧参数重试。资产只接受 Agent 可编辑字段，reference_image、*_sheet 等系统管理字段或结构非法时返回 "
        "invalid_request；project.json 中资产表或 workflow 结构损坏时返回 inventory_unavailable；两者同样整笔不写。"
        "成功时新增资产写入 project.json（已存在的同名资产保持不变），"
        "并记录完成事实，返回 scope、source_revision 与提交后角色 / 场景 / 道具各自的总数。三类清单全空也是合法结果，"
        "仍须调用。" + _MIGRATION_REFUSAL
    ),
    request_model=CompleteAssetInventoryRequest,
    migration=BLOCKED,
    domain_key="asset_inventory",
    handler=complete_asset_inventory,
)

COMPLETE_SCRIPT_PLAN_REBUILD = ToolDeclaration(
    name="complete_script_plan_rebuild",
    description=(
        "stale 分集的 script_plan 重建成功后调用，原子记录重建完成事实；即使重建内容与旧 script_plan 逐字相同，"
        "制作计划也能继续收敛。仅在制作计划 next_action.args 带 expected_stale_script_plan_revision 时使用，"
        "该值原样传入。该集不在待重建状态（not_stale）、缺少重建基线（missing_baseline）、基线已变化（baseline_conflict）"
        "或正式 script_plan 文件缺失（script_plan_missing）时拒绝且不写入；报冲突时刷新制作计划，不要用旧参数重试。"
        "成功返回该集与重建后 script_plan 的 revision。" + _MIGRATION_REFUSAL
    ),
    request_model=CompleteScriptPlanRebuildRequest,
    migration=BLOCKED,
    domain_key="script_plan_rebuild",
    handler=complete_script_plan_rebuild,
)

WORKFLOW_COMPLETION_TOOLS = (COMPLETE_ASSET_INVENTORY, COMPLETE_SCRIPT_PLAN_REBUILD)

__all__ = ["COMPLETE_ASSET_INVENTORY", "COMPLETE_SCRIPT_PLAN_REBUILD", "WORKFLOW_COMPLETION_TOOLS"]
