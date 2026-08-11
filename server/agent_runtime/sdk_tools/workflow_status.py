"""SDK MCP adapter for the authoritative workflow-status service."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from claude_agent_sdk import tool

from lib.script_review import Step1RebuildCompletionError, complete_stale_step1_rebuild
from lib.workflow_state import WorkflowStateService
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error


def _error(code: str, detail: str) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"error": code, "detail": detail}, ensure_ascii=False, sort_keys=True),
            }
        ],
        "is_error": True,
    }


def get_workflow_status_tool(ctx: ToolContext):
    @tool(
        "get_workflow_status",
        "读取服务端权威工作流状态、blocker、gate、产物事实和唯一 next_action。无副作用；不要再根据文件名自行推断阶段。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1},
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        raw_episode = args.get("episode")
        if raw_episode is not None and (
            not isinstance(raw_episode, int) or isinstance(raw_episode, bool) or raw_episode < 1
        ):
            return _error("invalid_episode", "episode must be a positive integer")
        try:
            status = await asyncio.to_thread(WorkflowStateService(ctx.pm).get_status, ctx.project_name, raw_episode)
            return {"content": [{"type": "text", "text": status.model_dump_json()}]}
        except json.JSONDecodeError as exc:
            return tool_error("get_workflow_status", exc)
        except ValueError as exc:
            return _error("invalid_episode", str(exc))
        except Exception as exc:  # noqa: BLE001
            return tool_error("get_workflow_status", exc)

    return _handler


def complete_step1_rebuild_tool(ctx: ToolContext):
    @tool(
        "complete_step1_rebuild",
        "在 stale 分集预处理成功后原子记录完成事实；即使重建内容与旧 step1 相同，workflow-status 也能继续收敛。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1},
                "expected_stale_step1_revision": {"type": ["string", "null"]},
            },
            "required": ["episode", "expected_stale_step1_revision"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        episode = args.get("episode")
        if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1:
            return _error("invalid_episode", "episode must be a positive integer")
        if "expected_stale_step1_revision" not in args:
            return _error("invalid_request", "expected_stale_step1_revision is required")
        expected = args.get("expected_stale_step1_revision")
        if expected is not None and not isinstance(expected, str):
            return _error("invalid_request", "expected_stale_step1_revision must be a string or null")
        try:
            revision = await asyncio.to_thread(
                complete_stale_step1_rebuild,
                ctx.pm,
                ctx.project_name,
                episode,
                expected,
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"episode": episode, "step1_revision": revision}, ensure_ascii=False, sort_keys=True
                        ),
                    }
                ]
            }
        except Step1RebuildCompletionError as exc:
            return _error(exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001
            return tool_error("complete_step1_rebuild", exc)

    return _handler


__all__ = ["complete_step1_rebuild_tool", "get_workflow_status_tool"]
