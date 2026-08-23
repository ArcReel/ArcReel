"""Project-wide unified video direction shared by Web, Agent and generation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VideoStyleSource = Literal["agent", "user"]
VIDEO_STYLE_PROMPT_MAX_LENGTH = 16_000


class UnifiedVideoStyleDraft(BaseModel):
    """The single user-editable project-level video style prompt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str = Field(min_length=1, max_length=VIDEO_STYLE_PROMPT_MAX_LENGTH)


class UnifiedVideoStylePatch(BaseModel):
    """Partial edit shape used by the shared update operation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str | None = Field(default=None, min_length=1, max_length=VIDEO_STYLE_PROMPT_MAX_LENGTH)


class UnifiedVideoStyle(UnifiedVideoStyleDraft):
    """Persisted project-level style with provenance metadata."""

    source: VideoStyleSource
    updated_at: datetime


class _LegacyUnifiedVideoStyle(BaseModel):
    """v9 persisted shape, kept only for the v9→v10 lossless migration."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    visual_treatment: str = Field(default="", max_length=2000)
    camera_language: str = Field(default="", max_length=2000)
    pacing: str = Field(default="", max_length=2000)
    sound_focus: Literal["balanced", "asmr", "dialogue", "ambience", "silent"] = "balanced"
    music_policy: Literal["auto", "none", "custom"] = "auto"
    music_description: str = Field(default="", max_length=2000)
    sound_design: str = Field(default="", max_length=3000)
    additional_instructions: str = Field(default="", max_length=3000)
    source: VideoStyleSource
    updated_at: datetime

    @model_validator(mode="after")
    def _music_fields_match_policy(self) -> _LegacyUnifiedVideoStyle:
        if self.music_policy == "custom" and not self.music_description:
            raise ValueError("music_description is required when music_policy is custom")
        return self


_LEGACY_LABELS: dict[str, dict[str, str]] = {
    "zh": {
        "visual_treatment": "动态画面处理",
        "camera_language": "镜头语言",
        "pacing": "节奏",
        "sound_focus": "声音重点",
        "music_policy": "背景音乐",
        "sound_design": "声音设计",
        "additional_instructions": "其他项目级要求",
    },
    "en": {
        "visual_treatment": "Moving-image treatment",
        "camera_language": "Camera language",
        "pacing": "Pacing",
        "sound_focus": "Sound focus",
        "music_policy": "Background music",
        "sound_design": "Sound design",
        "additional_instructions": "Other project-wide requirements",
    },
    "vi": {
        "visual_treatment": "Xử lý hình ảnh động",
        "camera_language": "Ngôn ngữ máy quay",
        "pacing": "Nhịp độ",
        "sound_focus": "Trọng tâm âm thanh",
        "music_policy": "Nhạc nền",
        "sound_design": "Thiết kế âm thanh",
        "additional_instructions": "Yêu cầu khác cho toàn dự án",
    },
}

_LEGACY_SOUND_VALUES: dict[str, dict[str, str]] = {
    "zh": {"balanced": "平衡", "asmr": "ASMR", "dialogue": "对白", "ambience": "环境声", "silent": "静音"},
    "en": {"balanced": "balanced", "asmr": "ASMR", "dialogue": "dialogue", "ambience": "ambience", "silent": "silent"},
    "vi": {
        "balanced": "cân bằng",
        "asmr": "ASMR",
        "dialogue": "lời thoại",
        "ambience": "âm thanh môi trường",
        "silent": "im lặng",
    },
}

_LEGACY_MUSIC_VALUES: dict[str, dict[str, str]] = {
    "zh": {"auto": "自动决定", "none": "禁止背景音乐"},
    "en": {"auto": "decide automatically", "none": "no background music"},
    "vi": {"auto": "tự động quyết định", "none": "không có nhạc nền"},
}


def migrate_legacy_video_style(raw: Mapping[str, Any], source_language: object = None) -> UnifiedVideoStyle:
    """Convert every v9 dimension into one labelled paragraph without dropping metadata."""

    if "prompt" in raw:
        return UnifiedVideoStyle.model_validate(raw)

    legacy = _LegacyUnifiedVideoStyle.model_validate(raw)
    language = source_language if isinstance(source_language, str) and source_language in _LEGACY_LABELS else "en"
    labels = _LEGACY_LABELS[language]
    separator = "；" if language == "zh" else "; "
    ending = "。" if language == "zh" else "."
    parts: list[str] = []

    for field in ("visual_treatment", "camera_language", "pacing"):
        value = getattr(legacy, field)
        if value:
            parts.append(f"{labels[field]}：{value}" if language == "zh" else f"{labels[field]}: {value}")

    sound_value = _LEGACY_SOUND_VALUES[language][legacy.sound_focus]
    parts.append(
        f"{labels['sound_focus']}：{sound_value}" if language == "zh" else f"{labels['sound_focus']}: {sound_value}"
    )

    music_value = (
        legacy.music_description
        if legacy.music_policy == "custom"
        else _LEGACY_MUSIC_VALUES[language][legacy.music_policy]
    )
    parts.append(
        f"{labels['music_policy']}：{music_value}" if language == "zh" else f"{labels['music_policy']}: {music_value}"
    )

    for field in ("sound_design", "additional_instructions"):
        value = getattr(legacy, field)
        if value:
            parts.append(f"{labels[field]}：{value}" if language == "zh" else f"{labels[field]}: {value}")

    prompt = separator.join(parts).rstrip("。.") + ending
    return UnifiedVideoStyle(
        prompt=prompt,
        source=legacy.source,
        updated_at=legacy.updated_at,
    )


def video_style_summary(style: UnifiedVideoStyle, *, max_length: int = 160) -> str:
    """Compact stable summary for Agent responses and small UI surfaces."""

    summary = " ".join(style.prompt.split())
    if len(summary) <= max_length:
        return summary
    return summary[: max_length - 1].rstrip() + "…"


__all__ = [
    "VIDEO_STYLE_PROMPT_MAX_LENGTH",
    "UnifiedVideoStyle",
    "UnifiedVideoStyleDraft",
    "UnifiedVideoStylePatch",
    "VideoStyleSource",
    "migrate_legacy_video_style",
    "video_style_summary",
]
