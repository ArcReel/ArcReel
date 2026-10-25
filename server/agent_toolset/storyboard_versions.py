"""Project-scoped selection of an existing storyboard image version."""

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.storyboard_versions import (
    ReuseStoryboardImageRequest,
    SelectStoryboardVersionRequest,
    reuse_storyboard_image,
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

REUSE_STORYBOARD_IMAGE = ToolDeclaration(
    name="reuse_storyboard_image",
    description=(
        "Use an existing image from this project as a shot's current starting image, creating a new saved "
        "version through the editor's upload transaction. Supply script, shot_id and source_path. No generation "
        "or charge. The source image, older versions, existing videos and frozen ending guide are preserved. "
        "Use this before edit_images when a new shot must derive from an approved composition instead of "
        "regenerating its geometry independently. Inspect the resulting start/end pair before generating video. "
        "This reuses an authored image; it does not certify its fit for the target shot."
    ),
    request_model=ReuseStoryboardImageRequest,
    migration=BLOCKED,
    domain_key="reuse_storyboard_image",
    handler=reuse_storyboard_image,
    summary=select_storyboard_version_summary,
)

STORYBOARD_VERSION_TOOLS = (SELECT_STORYBOARD_VERSION, REUSE_STORYBOARD_IMAGE)
