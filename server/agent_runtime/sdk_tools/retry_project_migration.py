"""SDK MCP adapter for rerunning a project's schema migration chain."""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import tool

from lib.project_migration_failure import MigrationFailureRecord, load_migration_failure
from lib.project_migrations import migrate_project_with_verdict
from lib.workflow_plan import WorkflowPlanRequest
from server.agent_runtime.sdk_tools._context import ToolContext, migration_refusal_response, tool_error
from server.services import workflow_planner


def retry_project_migration_tool(ctx: ToolContext):
    @tool(
        "retry_project_migration",
        "重跑本项目的数据升级链（含产物补录）。升级失败时项目被阻断，阻断期仍可用的写入工具只有"
        "patch_project / patch_episode_meta / rename_asset；patch_episode_script / insert_segment / "
        "remove_segment / split_segment 一律被拒。按失败明细用前三个修好被点名的集 / 文件，再调用"
        "本工具。幂等：已是最新版本时直接返回成功。成功返回新的制作计划；失败返回结构化明细"
        "（episode / file / violation）。",
        {"type": "object", "properties": {}},
    )
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            project_dir = ctx.pm.get_project_path(ctx.project_name)
            failure = await asyncio.to_thread(migrate_project_with_verdict, project_dir)
            if failure is not None:
                return _failure_response(failure)
            plan = await workflow_planner.get_workflow_planner(ctx.pm).get_plan(ctx.project_name, WorkflowPlanRequest())
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "✅ 数据升级已完成，项目解除阻断。当前制作计划：\n" + plan.model_dump_json(),
                    }
                ],
                "workflow_plan": plan.model_dump(mode="json"),
            }
        except Exception as exc:  # noqa: BLE001
            # The chain itself never escapes ``migrate_project_with_verdict``; reaching
            # here means the project could not even be located or re-planned.
            residual = _residual_failure(ctx)
            if residual is not None:
                return _failure_response(residual)
            return tool_error("retry_project_migration", exc)

    return _handler


def _residual_failure(ctx: ToolContext) -> MigrationFailureRecord | None:
    try:
        return load_migration_failure(ctx.pm.get_project_path(ctx.project_name))
    except (FileNotFoundError, ValueError, OSError):
        return None


def _failure_response(failure: MigrationFailureRecord) -> dict[str, Any]:
    return migration_refusal_response(
        failure,
        text="❌ 数据升级仍未通过，项目继续阻断。修复下列位置后再重试：",
    )


__all__ = ["retry_project_migration_tool"]
