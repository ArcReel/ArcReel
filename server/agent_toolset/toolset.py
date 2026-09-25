"""已声明的 Agent 工具，以及由声明派生的工具名集合与迁移阻断集合。"""

from __future__ import annotations

from typing import Any

from server.agent_toolset.content_read import CONTENT_READ_TOOLS
from server.agent_toolset.declaration import Blocked, ToolDeclaration

AGENT_TOOLSET: tuple[ToolDeclaration[Any, Any], ...] = (*CONTENT_READ_TOOLS,)

DECLARED_TOOL_IDS: tuple[str, ...] = tuple(declaration.name for declaration in AGENT_TOOLSET)

DECLARED_MIGRATION_BLOCKED_TOOL_IDS: frozenset[str] = frozenset(
    declaration.name for declaration in AGENT_TOOLSET if isinstance(declaration.migration, Blocked)
)

if len(set(DECLARED_TOOL_IDS)) != len(DECLARED_TOOL_IDS):
    raise RuntimeError("Agent 工具集中存在重名声明")

__all__ = ["AGENT_TOOLSET", "DECLARED_MIGRATION_BLOCKED_TOOL_IDS", "DECLARED_TOOL_IDS"]
