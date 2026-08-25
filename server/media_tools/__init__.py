"""Host-neutral media generation tool definitions and handlers."""

from server.media_tools.assets import handle_generate_assets, handle_list_pending_assets
from server.media_tools.grid import handle_generate_grid
from server.media_tools.image_edits import handle_edit_images
from server.media_tools.narration_audio import handle_generate_narration_audio
from server.media_tools.storyboards import handle_generate_storyboards
from server.media_tools.videos import handle_generate_videos

__all__ = [
    "handle_edit_images",
    "handle_generate_assets",
    "handle_generate_grid",
    "handle_generate_narration_audio",
    "handle_generate_storyboards",
    "handle_generate_videos",
    "handle_list_pending_assets",
]
