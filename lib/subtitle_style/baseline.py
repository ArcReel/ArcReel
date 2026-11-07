"""Subtitle-style baseline: Jianying draft styles, converted for burned-in final-cut subtitles.

White bold text, black outline/shadow; portrait font size 12, line width 82% of canvas, vertical
position −0.75; landscape font size 8, width 60%, position −0.8. Editing previews convert the same
values (frontend/src/components/canvas/edit/preview-tracks.ts ``subtitleLayout``); update together.
"""

from __future__ import annotations

from dataclasses import dataclass

JIANYING_FONT_PX_PER_SIZE = 5
"""Jianying font size 1 approximates a 5-pixel em on a canvas with short edge 1080."""


@dataclass(frozen=True, slots=True)
class SubtitleStyleBaseline:
    """``size`` is Jianying font size; ``max_line_width`` is the line/canvas width ratio; ``transform_y`` is Jianying vertical position,
    measured in half-canvas heights: −1 at the bottom, 0 centered."""

    size: float
    max_line_width: float
    transform_y: float


def subtitle_style_baseline(width: int, height: int) -> SubtitleStyleBaseline:
    """Choose the subtitle-style baseline by portrait/landscape canvas orientation."""
    if height > width:
        return SubtitleStyleBaseline(size=12.0, max_line_width=0.82, transform_y=-0.75)
    return SubtitleStyleBaseline(size=8.0, max_line_width=0.6, transform_y=-0.8)


__all__ = ["JIANYING_FONT_PX_PER_SIZE", "SubtitleStyleBaseline", "subtitle_style_baseline"]
