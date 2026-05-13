"""Per-session context shared by ArcReel SDK MCP tool handlers."""

from __future__ import annotations

from pathlib import Path

from lib.project_manager import ProjectManager


class ToolContext:
    """Bind a tool handler to one agent session's project + projects_root.

    The agent never names the project explicitly — every tool is closure-bound
    to ``project_name`` via ``build_arcreel_mcp_server(project_name=...)``.
    """

    def __init__(self, project_name: str, projects_root: Path, pm: ProjectManager | None = None):
        self.project_name = project_name
        self.projects_root = projects_root
        # Avoid ``ProjectManager.from_cwd()`` — the server main process cwd is
        # the repo root, not ``projects/<name>/``. Tests may inject a fake pm.
        self.pm: ProjectManager = pm if pm is not None else ProjectManager(str(projects_root))

    @property
    def project_path(self) -> Path:
        return self.pm.get_project_path(self.project_name)
