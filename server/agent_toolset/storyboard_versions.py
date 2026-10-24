"""Project-scoped selection of an existing storyboard image version."""

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.storyboard_versions import (
    SelectStoryboardVersionRequest,
    select_storyboard_version,
    select_storyboard_version_summary,
)

SELECT_STORYBOARD_VERSION = ToolDeclaration(
    name="select_storyboard_version",
    description=(
        "Restore a shot's saved storyboard image version as its current starting image, using the same "
        "transaction as the editor. Updates image bytes, current version, script metadata and provenance. "
        "No generation or charge. Does not change existing videos or frozen end_frame_image snapshots. "
        "To create an ending guide: remember the accepted starting version, edit_images for the ending pose, "
        "inspect it, set_video_end_frame to snapshot it, then use this tool to restore the accepted starting "
        "version. Inspect both guides before generate_videos. Version numbers are visible in the project's "
        "versions/versions.json. Missing versions return version_not_found and available_versions; unknown shots "
        "return segment_not_found. Only storyboard mode is supported."
    ),
    request_model=SelectStoryboardVersionRequest,
    migration=BLOCKED,
    domain_key="select_storyboard_version",
    handler=select_storyboard_version,
    summary=select_storyboard_version_summary,
)

STORYBOARD_VERSION_TOOLS = (SELECT_STORYBOARD_VERSION,)
