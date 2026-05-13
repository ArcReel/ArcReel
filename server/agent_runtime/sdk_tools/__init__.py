"""ArcReel SDK in-process MCP tools.

Tools registered here run **in the server main process** (not inside the
agent sandbox), so they can read ``projects/.arcreel.db`` and call provider
HTTP without poking holes in ``filesystem.denyRead`` / network allowlist.

Each session gets its own MCP server built via :func:`build_arcreel_mcp_server`
— ``project_name`` is closure-bound, so the agent cannot redirect tools to a
different project via prompt injection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server

from server.agent_runtime.sdk_tools._context import ToolContext

__all__ = ["build_arcreel_mcp_server", "ToolContext"]


def build_arcreel_mcp_server(*, project_name: str, projects_root: Path) -> Any:
    """Build the per-session in-process MCP server.

    Tools are added incrementally across commits; commit 1 ships an empty
    server to exercise the wiring through ``_build_options`` without changing
    agent behavior.
    """
    ctx = ToolContext(project_name=project_name, projects_root=projects_root)
    # ctx is held by tool closures; silence "unused" lint until commit 2.
    _ = ctx
    return create_sdk_mcp_server(
        name="arcreel",
        version="1.0.0",
        tools=[],
    )
