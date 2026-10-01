"""成片烧入的字幕：按剪映的字幕样式基线换算版式，生成交给 libass 的 ASS 文档。

字幕用随包的思源黑体 CN Bold：白字、粗体、黑色描边与阴影，字号与纵向位置按样式基线换算到成片画布。
中文按「可用宽度 ÷ 字号」显式插入换行，英文与越南语交给 libass 在空格处换行；多条字幕同时出现时，
交给 libass 的碰撞处理把后一条推开，所以事件不指定绝对位置。

换行与版式是纯函数；:func:`ass_document` 只负责把它们写成 ASS 文本。
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
"""描边宽度占字身的比例，对应剪映描边宽度 30。"""
_SHADOW_PER_EM = 0.05
_SHADOW_ALPHA = 0x4D
"""阴影的 ASS 透明度（0 为不透明）：剪映阴影不透明度 0.7。"""

_NO_LINE_START = frozenset("，。、；：？！…‥）」』”’》〉】〕｝］〗,.;:?!)]}%·・ー〜～")
"""不能出现在行首的标点：遇到时挂在上一行行尾。"""
_NO_LINE_END = frozenset("（「『“‘《〈【〔｛［〖([{")
"""不能出现在行尾的标点：遇到时连同后面的字一起换到下一行。"""


@dataclass(frozen=True, slots=True)
class SubtitleLayout:
    """成片画布上的字幕版式（像素）。

    ``em`` 是字身；``line_capacity`` 是一行可用宽度能放下的全角字数，即「可用宽度 ÷ 字号」；
    ``margin_x`` 是左右边距，``margin_bottom`` 是底边到单行字幕行盒底部的距离。
    """

    width: int
    height: int
    em: int
    line_capacity: float
    margin_x: int
    margin_bottom: int


def subtitle_layout(profile: OutputProfile) -> SubtitleLayout:
    """按样式基线把剪映字号、行宽与纵向位置换算到成片画布。"""
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
    """字符占的字宽：全角与宽字符一个字身，其余按半个字身估算。"""
    return 1.0 if unicodedata.east_asian_width(character) in {"W", "F"} else 0.5


def _tokens(text: str) -> list[str]:
    """断行的最小单位：一个全角字符、一段空白，或一串连续的半角字符（外文单词与数字不从中间断开）。"""
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
    """比一整行还宽的半角串只能硬断。"""
    pieces: list[str] = []
    current = ""
    for character in token:
        if current and _width(current + character) > capacity:
            pieces.append(current)
            current = ""
        current += character
    return [*pieces, current] if current else pieces


def wrap_chinese(text: str, line_capacity: float) -> tuple[str, ...]:
    """中文字幕按每行能放下的全角字数逐行填满。

    - 外文单词与数字不从中间断开，比一整行还宽时才硬断；
    - 行首不放句读与闭合标点，它们挂在上一行行尾，允许略超行宽；
    - 行尾不留开引号与开括号，它们随后面的字换到下一行；
    - 换行处的空白丢弃。
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
        current = "" if token.isspace() else carried + token
    if current:
        lines.append(current.rstrip())
    return tuple(lines)


def subtitle_lines(text: str, layout: SubtitleLayout) -> tuple[str, ...]:
    """一条字幕显示成哪几行：含汉字的按中文规则显式换行，其余整段交给 libass 在空格处换行。"""
    text = text.strip()
    if count_reading_units(text, "zh") > 0:
        return wrap_chinese(text, layout.line_capacity)
    return (text,)


@dataclass(frozen=True, slots=True)
class BurnedSubtitle:
    """时间线上的一条字幕：起止为成片时间（微秒）。"""

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
    """ASS 文本里的花括号是覆盖标记、反斜杠是转义符，原样显示时换成全角形。"""
    return line.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def ass_document(subtitles: Sequence[BurnedSubtitle], profile: OutputProfile) -> str:
    """整集字幕的 ASS 文档；画布即成片画布，时间即成片时间。"""
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
