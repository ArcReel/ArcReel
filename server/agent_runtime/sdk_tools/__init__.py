"""ArcReel SDK in-process MCP tools.

Tools registered here run **in the server main process** (not inside the
agent sandbox), so they can read the ArcReel database and call provider
HTTP without poking holes in ``filesystem.denyRead`` / network allowlist.

Each session gets its own MCP server built via :func:`build_arcreel_mcp_server`.
Project-scoped tools are closure-bound to ``project_name``; project entry tools
may list, create, or upload within the same ``data_root``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_queue_client import batch_enqueue_and_wait
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.toolset import AGENT_TOOLSET, DECLARED_MIGRATION_BLOCKED_TOOL_IDS, DECLARED_TOOL_IDS
from server.media_tools.context import ToolContext, tool_services
from server.tool_runtime import CallerContext

__all__ = ["ARCREEL_MCP_TOOL_IDS", "ToolContext", "build_arcreel_mcp_server"]

# The ArcReel MCP tool catalogue, derived from the Agent toolset declarations. Each id is the **short tool
# name** (without the ``mcp__arcreel__`` prefix the SDK adds at registration). Frontend display names live in
# ``frontend/src/i18n/{zh,en,vi}/dashboard.ts`` under the ``tool_name_<id>`` keys;
# ``tests/unit/test_frontend_mcp_tool_i18n.py`` cross-checks that every id here has a translation in
# all locales, so adding a tool without wiring up i18n fails CI.
ARCREEL_MCP_TOOL_IDS: tuple[str, ...] = DECLARED_TOOL_IDS

# Tools refused at the shared declaration entry while the project's schema migration verdict is a failure,
# per each declaration's migration policy.
MIGRATION_BLOCKED_TOOL_IDS: frozenset[str] = DECLARED_MIGRATION_BLOCKED_TOOL_IDS


def build_arcreel_mcp_server(*, project_name: str, data_root: Path, user_id: str = DEFAULT_USER_ID) -> Any:
    """Build the per-session in-process MCP server with all ArcReel tools."""
    ctx = ToolContext(
        project_name=project_name,
        data_root=data_root,
        caller=CallerContext(user_id=user_id, source="embedded", batch_waiter=batch_enqueue_and_wait),
    )
    return embedded_server(
        AGENT_TOOLSET,
        name="arcreel",
        version="1.0.0",
        scope=ctx.scope,
        caller=ctx.caller,
        services=tool_services(ctx),
    )
