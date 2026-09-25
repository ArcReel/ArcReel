"""Host-neutral media generation tool definitions and handlers."""

from server.media_tools.assets import generate_assets, list_pending_assets
from server.media_tools.grid import handle_generate_grid
from server.media_tools.image_edits import edit_images
from server.media_tools.narration_audio import generate_narration_audio
from server.media_tools.storyboards import generate_storyboards
from server.media_tools.videos import handle_generate_videos

__all__ = [
    "edit_images",
    "generate_assets",
    "generate_narration_audio",
    "generate_storyboards",
    "handle_generate_grid",
    "handle_generate_videos",
    "list_pending_assets",
]
