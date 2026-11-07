"""Video-version selection declaration: switch a video unit’s current video version."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.video_versions import (
    SelectVideoVersionRequest,
    select_video_version,
    select_video_version_summary,
)

SELECT_VIDEO_VERSION = ToolDeclaration(
    name="select_video_version",
    description=(
        "Switch a video unit’s current video to the specified version number (the same service as Web version restoration). current is the project-wide source of truth for this unit; preview, editing, and export use it immediately. No charges, regeneration, or creator confirmation required. Two uses: after first review, choose a better candidate version; after regeneration acceptance review, revert when the new version is worse. unit_id is the shot ID for storyboard video and unit_id for reference video. Returns video_unit_not_found for missing units, version_not_found for absent versions (params.available_versions lists existing versions), or request_invalid for versions lacking complete provenance (such as manual uploads). Success returns current_version and file_path."
    ),
    request_model=SelectVideoVersionRequest,
    migration=BLOCKED,
    domain_key="select_video_version",
    handler=select_video_version,
    summary=select_video_version_summary,
)

VIDEO_VERSION_TOOLS = (SELECT_VIDEO_VERSION,)

__all__ = ["SELECT_VIDEO_VERSION", "VIDEO_VERSION_TOOLS"]
