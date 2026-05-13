"""Per-session context shared by ArcReel SDK MCP tool handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from lib.project_manager import ProjectManager


@dataclass(slots=True)
class ToolContext:
    """Bind a tool handler to one agent session's project + projects_root.

    The agent never names the project explicitly — every tool is closure-bound
    to ``project_name`` via ``build_arcreel_mcp_server(project_name=...)``.
    """

    project_name: str
    projects_root: Path
    _pm: ProjectManager | None = field(default=None, init=False, repr=False)

    @cached_property
    def pm(self) -> ProjectManager:
        """Cached ProjectManager bound to ``projects_root``.

        Avoids ``ProjectManager.from_cwd()`` — the server main process cwd is
        the repo root, not ``projects/<name>/``.
        """
        return ProjectManager(str(self.projects_root))

    @property
    def project_path(self) -> Path:
        return self.pm.get_project_path(self.project_name)
