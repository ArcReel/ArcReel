"""In-process MCP server for ArcReel Agent sessions.

Tool handlers run in the server main process, outside the Agent sandbox, so they can access ArcReel databases and provider HTTP
without exceptions in ``filesystem.denyRead`` or network whitelists. Each session builds a server for its project; all tools come from
Agent toolset declarations, exposed through the embedded adapter.
"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import McpSdkServerConfig

from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_queue_client import batch_enqueue_and_wait
from lib.project.project_manager import ProjectManager
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.tool_runtime import CallerContext, ProjectScope, Services


def build_arcreel_mcp_server(
    *,
    project_name: str,
    data_root: Path,
    user_id: str = DEFAULT_USER_ID,
) -> McpSdkServerConfig:
    """Build an in-process MCP server exposing all ArcReel tools for the session project; generation tools wait for terminal batch state before returning."""
    return embedded_server(
        AGENT_TOOLSET,
        name="arcreel",
        version="1.0.0",
        scope=ProjectScope(project_name=project_name, data_root=data_root),
        caller=CallerContext(user_id=user_id, source="embedded", batch_waiter=batch_enqueue_and_wait),
        services=Services.defaults(ProjectManager(data_root)),
    )


__all__ = ["build_arcreel_mcp_server"]
