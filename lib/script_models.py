"""
script_models.py - Script data models

Defines the data structures for scripts using Pydantic, used for:
1. Gemini API response_schema (Structured Outputs)
2. Output validation
"""

from typing import Literal

from pydantic import BaseModel, Field

# ============ Enum type definitions ============

ShotType = Literal[
    "Extreme Close-up",
    "Close-up",
    "Medium Close-up",
    "Medium Shot",
    "Medium Long Shot",
    "Long Shot",
    "Extreme Long Shot",
    "Over-the-shoulder",
    "Point-of-view",
]

CameraMotion = Literal[
    "Static",
    "Pan Left",
    "Pan Right",
    "Tilt Up",
    "Tilt Down",
    "Zoom In",
    "Zoom Out",
    "Tracking Shot",
]


class Dialogue(BaseModel):
    """Dialogue entry"""

    speaker: str = Field(description="Speaker name")
    line: str = Field(description="Dialogue content")


class Composition(BaseModel):
    """Composition information"""

    shot_type: ShotType = Field(description="Shot type")
    lighting: str = Field(description="Lighting description including source, direction, and atmosphere")
    ambiance: str = Field(description="Overall ambiance matching the emotional tone")


class ImagePrompt(BaseModel):
    """Storyboard image generation prompt"""

    scene: str = Field(description="Scene description: character positions, expressions, actions, and environment details")
    composition: Composition = Field(description="Composition information")


class VideoPrompt(BaseModel):
    """Video generation prompt"""

    action: str = Field(description="Action description: specific actions of characters within this segment")
    camera_motion: CameraMotion = Field(description="Camera motion")
    ambiance_audio: str = Field(description="Ambient audio: describe only sounds within the scene; no BGM")
    dialogue: list[Dialogue] = Field(default_factory=list, description="Dialogue list; fill in only when the source text has quoted dialogue")


class GeneratedAssets(BaseModel):
    """Generated asset status (initialised as empty)"""

    storyboard_image: str | None = Field(default=None, description="Storyboard image path")
    video_clip: str | None = Field(default=None, description="Video clip path")
    video_uri: str | None = Field(default=None, description="Video URI")
    status: Literal["pending", "storyboard_ready", "completed"] = Field(default="pending", description="Generation status")


# ============ Narration mode ============


class NarrationSegment(BaseModel):
    """Narration mode segment"""

    segment_id: str = Field(description="Segment ID, format E{episode}S{number} or E{episode}S{number}_{sub-number}")
    episode: int = Field(description="Episode this segment belongs to")
    duration_seconds: int = Field(ge=1, le=60, description="Segment duration (seconds)")
    segment_break: bool = Field(default=False, description="Whether this is a scene transition point")
    novel_text: str = Field(description="Original novel text (must be preserved verbatim for post-production dubbing)")
    characters_in_segment: list[str] = Field(description="List of character names appearing in this segment")
    clues_in_segment: list[str] = Field(default_factory=list, description="List of clue names appearing in this segment")
    image_prompt: ImagePrompt = Field(description="Storyboard image generation prompt")
    video_prompt: VideoPrompt = Field(description="Video generation prompt")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut", description="Transition type")
    note: str | None = Field(default=None, description="User notes (not used in generation)")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="Generated asset status")


class NovelInfo(BaseModel):
    """Novel source information"""

    title: str = Field(description="Novel title")
    chapter: str = Field(description="Chapter name")


class NarrationEpisodeScript(BaseModel):
    """Narration mode episode script"""

    episode: int = Field(description="Episode number")
    title: str = Field(description="Episode title")
    content_mode: Literal["narration"] = Field(default="narration", description="Content mode")
    duration_seconds: int = Field(default=0, description="Total duration (seconds)")
    summary: str = Field(description="Episode summary")
    novel: NovelInfo = Field(description="Novel source information")
    segments: list[NarrationSegment] = Field(description="Segment list")


# ============ Drama animation mode ============


class DramaScene(BaseModel):
    """Drama animation mode scene"""

    scene_id: str = Field(description="Scene ID, format E{episode}S{number} or E{episode}S{number}_{sub-number}")
    duration_seconds: int = Field(default=8, ge=1, le=60, description="Scene duration (seconds)")
    segment_break: bool = Field(default=False, description="Whether this is a scene transition point")
    scene_type: str = Field(default="drama", description="Scene type")
    characters_in_scene: list[str] = Field(description="List of character names appearing in this scene")
    clues_in_scene: list[str] = Field(default_factory=list, description="List of clue names appearing in this scene")
    image_prompt: ImagePrompt = Field(description="Storyboard image generation prompt")
    video_prompt: VideoPrompt = Field(description="Video generation prompt")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut", description="Transition type")
    note: str | None = Field(default=None, description="User notes (not used in generation)")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="Generated asset status")


class DramaEpisodeScript(BaseModel):
    """Drama animation mode episode script"""

    episode: int = Field(description="Episode number")
    title: str = Field(description="Episode title")
    content_mode: Literal["drama"] = Field(default="drama", description="Content mode")
    duration_seconds: int = Field(default=0, description="Total duration (seconds)")
    summary: str = Field(description="Episode summary")
    novel: NovelInfo = Field(description="Novel source information")
    scenes: list[DramaScene] = Field(description="Scene list")
