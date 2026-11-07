"""Burned final-cut subtitles: convert Jianying style baselines to layouts and generate ASS for libass.

Use bundled 思源黑体 CN Bold: white bold text with black outline/shadow, converting font size
and vertical position from baseline to final-cut canvas. Chinese explicitly wraps at available
width ÷ font size; libass wraps English/Vietnamese at spaces. Simultaneous subtitles rely on
libass collision handling to push later entries away, so events specify no absolute positions.

Wrapping/layout are pure functions; :func:`ass_document` only writes their ASS text.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from lib.final_cut.render_plan import OutputProfile
from lib.infra.text_metrics import count_reading_units
from lib.subtitle_style.baseline import JIANYING_FONT_PX_PER_SIZE, subtitle_style_baseline
from lib.subtitle_style.font import SUBTITLE_FONT_FAMILY, cell_height_per_em

_BASE_SHORT_SIDE = 1080
_OUTLINE_PER_EM = 0.06
"""Outline width as an em ratio, matching Jianying outline width 30."""
_SHADOW_PER_EM = 0.05
_SHADOW_ALPHA = 0x4D
"""ASS shadow transparency (0 opaque), matching Jianying shadow opacity 0.7."""

_NO_LINE_START = frozenset("，。、；：？！…‥）」』”’》〉】〕｝］〗,.;:?!)]}%·・ー〜～")
"""Punctuation prohibited at line starts; hang it on the previous line end."""
_NO_LINE_END = frozenset("（「『“‘《〈【〔｛［〖([{")
"""Punctuation prohibited at line ends; move it with the following character onto the next line."""


@dataclass(frozen=True, slots=True)
class SubtitleLayout:
    """Subtitle layout on final-cut canvas, in pixels.

    ``em`` is em size; ``line_capacity`` counts full-width characters fitting the available line
    width (available width ÷ font size). ``margin_x`` gives horizontal margins; ``margin_bottom``
    measures from canvas bottom to the bottom of a single subtitle line box.
    """

    width: int
    height: int
    em: int
    line_capacity: float
    margin_x: int
    margin_bottom: int


def subtitle_layout(profile: OutputProfile) -> SubtitleLayout:
    """Convert Jianying font size, line width, and vertical position to final-cut canvas according to style baselines."""
    baseline = subtitle_style_baseline(profile.width, profile.height)
    em = round(baseline.size * JIANYING_FONT_PX_PER_SIZE * min(profile.width, profile.height) / _BASE_SHORT_SIDE)
    usable = profile.width * baseline.max_line_width
    center_from_top = profile.height * (1 - baseline.transform_y) / 2
    cell = em * cell_height_per_em()
    return SubtitleLayout(
        width=profile.width,
        height=profile.height,
        em=em,
        line_capacity=usable / em,
        margin_x=round((profile.width - usable) / 2),
        margin_bottom=max(round(profile.height - center_from_top - cell / 2), 0),
    )


def _advance(character: str) -> float:
    """Character width: one em for full-width/wide characters; estimate others at half an em."""
    return 1.0 if unicodedata.east_asian_width(character) in {"W", "F"} else 0.5


def _tokens(text: str) -> list[str]:
    """Smallest wrapping units: one full-width character, whitespace span, or consecutive half-width string; never split foreign words/digits internally."""
    tokens: list[str] = []
    for character in text:
        previous = tokens[-1] if tokens else ""
        joins = (
            previous
            and _advance(character) < 1
            and _advance(previous[-1]) < 1
            and character.isspace() == previous[-1].isspace()
            and character not in _NO_LINE_START
        )
        if joins:
            tokens[-1] += character
        else:
            tokens.append(character)
    return tokens


def _width(text: str) -> float:
    return sum(_advance(character) for character in text)


def _split_oversized(token: str, capacity: float) -> list[str]:
    """Half-width strings wider than a whole line require hard breaks."""
    pieces: list[str] = []
    current = ""
    for character in token:
        if current and _width(current + character) > capacity:
            pieces.append(current)
            current = ""
        current += character
    return [*pieces, current] if current else pieces


def wrap_chinese(text: str, line_capacity: float) -> tuple[str, ...]:
    """Fill Chinese subtitle lines according to full-width character capacity.

    - Keep foreign words/digits intact unless wider than an entire line, when hard breaks apply;
    - Hang sentence/closing punctuation at the previous line end rather than starting a line, allowing slight overflow;
    - Move opening quotes/brackets with their following character to the next line rather than leaving them at line ends;
    - Discard whitespace at wraps.
    """
    capacity = max(math.floor(line_capacity), 1)
    tokens: list[str] = []
    for token in _tokens(text.strip()):
        tokens.extend(_split_oversized(token, capacity) if _width(token) > capacity else [token])
    lines: list[str] = []
    current = ""
    for token in tokens:
        if not current:
            if not token.isspace():
                current = token
            continue
        if _width(current + token) <= capacity or token[0] in _NO_LINE_START:
            current += token
            continue
        carried = ""
        while current and current[-1] in _NO_LINE_END and len(current) > 1:
            carried = current[-1] + carried
            current = current[:-1]
        lines.append(current.rstrip())
        current = carried if token.isspace() else carried + token
    if current:
        lines.append(current.rstrip())
    return tuple(lines)


def subtitle_lines(text: str, layout: SubtitleLayout) -> tuple[str, ...]:
    """Subtitle display lines: explicitly wrap Han-containing text by Chinese rules; leave other text to libass space wrapping.

    Flatten source line breaks into spaces: ASS events occupy one line; only this function explicitly supplies wrapping.
    """
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if count_reading_units(text, "zh") > 0:
        return wrap_chinese(text, layout.line_capacity)
    return (text,)


@dataclass(frozen=True, slots=True)
class BurnedSubtitle:
    """One timeline subtitle, with start/end in final-cut microseconds."""

    start_us: int
    end_us: int
    text: str


def _timestamp(microseconds: int) -> str:
    centiseconds = max(round(microseconds / 10_000), 0)
    seconds, centi = divmod(centiseconds, 100)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02d}:{sec:02d}.{centi:02d}"


def _escape(line: str) -> str:
    """ASS braces mark overrides and backslashes escape; replace them with full-width forms for literal display."""
    return line.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def ass_document(subtitles: Sequence[BurnedSubtitle], profile: OutputProfile) -> str:
    """Episode ASS subtitle document; canvas/time are final-cut canvas/time."""
    layout = subtitle_layout(profile)
    font_size = round(layout.em * cell_height_per_em())
    outline = round(layout.em * _OUTLINE_PER_EM, 1)
    shadow = round(layout.em * _SHADOW_PER_EM, 1)
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {layout.width}",
        f"PlayResY: {layout.height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Subtitle,{SUBTITLE_FONT_FAMILY},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H{_SHADOW_ALPHA:02X}000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,"
        f"{layout.margin_x},{layout.margin_x},{layout.margin_bottom},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = [
        f"Dialogue: 0,{_timestamp(item.start_us)},{_timestamp(item.end_us)},Subtitle,,0,0,0,,"
        + r"\N".join(_escape(line) for line in subtitle_lines(item.text, layout))
        for item in sorted(subtitles, key=lambda item: item.start_us)
        if item.end_us > item.start_us and item.text.strip()
    ]
    return "\n".join([*header, *events, ""])


__all__ = [
    "BurnedSubtitle",
    "SubtitleLayout",
    "ass_document",
    "subtitle_layout",
    "subtitle_lines",
    "wrap_chinese",
]
