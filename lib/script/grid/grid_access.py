"""Project-level admission for grid writes.

Regeneration, splitting, upload, and version restore share one decision. Any entry point omitting it would
let blocked projects mutate leftover grid records. Callers load projects; this module only decides whether
that project may currently modify grids.
"""

from __future__ import annotations

from typing import Any

from lib.infra.api_errors import BadRequestError
from lib.project.project_manager import grid_storyboard_enabled


def ensure_grid_writable(project: dict[str, Any]) -> None:
    """Raise BadRequestError when grids are not writable for this project.

    - Ad/short-film projects do not expose grid storyboards; existing records remain read-only.
    - Projects without grid enabled (including reference_video) likewise keep existing records read-only,
      prohibiting regeneration, splitting, upload, and restore.
    """
    if project.get("content_mode") == "ad":
        raise BadRequestError("ad_grid_not_supported")
    if not grid_storyboard_enabled(project):
        raise BadRequestError("grid_storyboard_not_enabled")
