"""已声明的 Agent 工具，以及由声明派生的工具名集合与迁移阻断集合。"""

from __future__ import annotations

from server.agent_toolset.content_read import CONTENT_READ_TOOLS
from server.agent_toolset.declaration import AgentToolDeclaration, Blocked, ToolDeclaration
from server.agent_toolset.episode_planning import EPISODE_PLANNING_TOOLS
from server.agent_toolset.generation_batches import GENERATION_BATCH_TOOLS
from server.agent_toolset.grid_storyboards import GRID_STORYBOARD_TOOLS
from server.agent_toolset.media_generation import MEDIA_GENERATION_TOOLS
from server.agent_toolset.orientation import ORIENTATION_TOOLS
from server.agent_toolset.project_entry import PROJECT_ENTRY_TOOLS
from server.agent_toolset.repair_channel import REPAIR_CHANNEL_TOOLS
from server.agent_toolset.script_editing import SCRIPT_EDITING_TOOLS
from server.agent_toolset.workflow_completion import WORKFLOW_COMPLETION_TOOLS

AGENT_TOOLSET: tuple[AgentToolDeclaration, ...] = (
    *PROJECT_ENTRY_TOOLS,
    *ORIENTATION_TOOLS,
    *GENERATION_BATCH_TOOLS,
    *CONTENT_READ_TOOLS,
    *MEDIA_GENERATION_TOOLS,
    *GRID_STORYBOARD_TOOLS,
    *REPAIR_CHANNEL_TOOLS,
    *SCRIPT_EDITING_TOOLS,
    *EPISODE_PLANNING_TOOLS,
    *WORKFLOW_COMPLETION_TOOLS,
)

DECLARED_TOOL_IDS: tuple[str, ...] = tuple(declaration.name for declaration in AGENT_TOOLSET)

DECLARED_MIGRATION_BLOCKED_TOOL_IDS: frozenset[str] = frozenset(
    declaration.name
    for declaration in AGENT_TOOLSET
    if isinstance(declaration, ToolDeclaration) and isinstance(declaration.migration, Blocked)
)

if len(set(DECLARED_TOOL_IDS)) != len(DECLARED_TOOL_IDS):
    raise RuntimeError("Agent 工具集中存在重名声明")

__all__ = ["AGENT_TOOLSET", "DECLARED_MIGRATION_BLOCKED_TOOL_IDS", "DECLARED_TOOL_IDS"]
