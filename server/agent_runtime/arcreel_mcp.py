"""ArcReel Agent 会话的 in-process MCP server。

工具 handler 跑在服务端主进程（不在 Agent sandbox 内），因此能读 ArcReel 数据库、调用供应商 HTTP，
而不必在 ``filesystem.denyRead`` / 网络白名单上开口子。每个会话以自己的项目建一个 server；工具全部来自
Agent 工具集声明，由内嵌 adapter 暴露。
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
    *, project_name: str, data_root: Path, user_id: str = DEFAULT_USER_ID
) -> McpSdkServerConfig:
    """以会话项目构建暴露全部 ArcReel 工具的 in-process MCP server；生成类工具等到批次终态再返回。"""
    return embedded_server(
        AGENT_TOOLSET,
        name="arcreel",
        version="1.0.0",
        scope=ProjectScope(project_name=project_name, data_root=data_root),
        caller=CallerContext(user_id=user_id, source="embedded", batch_waiter=batch_enqueue_and_wait),
        services=Services.defaults(ProjectManager(data_root)),
    )


__all__ = ["build_arcreel_mcp_server"]
