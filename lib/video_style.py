"""Project-wide unified video direction shared by Web, Agent and generation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SoundFocus = Literal["balanced", "asmr", "dialogue", "ambience", "silent"]
MusicPolicy = Literal["auto", "none", "custom"]
VideoStyleSource = Literal["agent", "user"]


class UnifiedVideoStyleDraft(BaseModel):
    """Editable content of the single project-level video style."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    visual_treatment: str = Field(default="", max_length=2000)
    camera_language: str = Field(default="", max_length=2000)
    pacing: str = Field(default="", max_length=2000)
    sound_focus: SoundFocus = "balanced"
    music_policy: MusicPolicy = "auto"
    music_description: str = Field(default="", max_length=2000)
    sound_design: str = Field(default="", max_length=3000)
    additional_instructions: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def _music_fields_match_policy(self) -> UnifiedVideoStyleDraft:
        if self.music_policy == "custom" and not self.music_description:
            raise ValueError("music_description is required when music_policy is custom")
        if self.music_policy != "custom":
            self.music_description = ""
        return self


class UnifiedVideoStylePatch(BaseModel):
    """Partial edit shape used by the shared update operation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    visual_treatment: str | None = Field(default=None, max_length=2000)
    camera_language: str | None = Field(default=None, max_length=2000)
    pacing: str | None = Field(default=None, max_length=2000)
    sound_focus: SoundFocus | None = None
    music_policy: MusicPolicy | None = None
    music_description: str | None = Field(default=None, max_length=2000)
    sound_design: str | None = Field(default=None, max_length=3000)
    additional_instructions: str | None = Field(default=None, max_length=3000)


class UnifiedVideoStyle(UnifiedVideoStyleDraft):
    """Persisted project-level style with provenance metadata."""

    source: VideoStyleSource
    updated_at: datetime


def video_style_summary(style: UnifiedVideoStyle) -> str:
    """Compact stable summary for Agent responses and small UI surfaces."""

    parts = [style.sound_focus.upper()]
    if style.music_policy == "none":
        parts.append("no BGM")
    elif style.music_policy == "custom":
        parts.append(style.music_description)
    if style.camera_language:
        parts.append(style.camera_language)
    return " · ".join(parts)


__all__ = [
    "MusicPolicy",
    "SoundFocus",
    "UnifiedVideoStyle",
    "UnifiedVideoStyleDraft",
    "UnifiedVideoStylePatch",
    "VideoStyleSource",
    "video_style_summary",
]
