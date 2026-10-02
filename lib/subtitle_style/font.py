"""随包的字幕字体：思源黑体 CN Bold（Source Han Sans 2.005 按地区子集的 OTF）。

字体文件原样分发，不自行子集化，放在 ``fonts/`` 下，许可证（SIL Open Font License 1.1）见 ``fonts/LICENSE.txt``。
成片烧入字幕时把 :data:`SUBTITLE_FONT_FILE` 经 ``fontsdir`` 交给 libass；剪映草稿用剪映内置的同款
``SourceHanSansCN_Bold``。:func:`missing_glyphs` 按字体的 cmap 预检字幕文本里字体覆盖不到的字符。
"""

from __future__ import annotations

import bisect
import functools
import struct
import unicodedata
from pathlib import Path

SUBTITLE_FONT_DIR = Path(__file__).resolve().parent / "fonts"
SUBTITLE_FONT_FILE = SUBTITLE_FONT_DIR / "SourceHanSansCN-Bold.otf"
SUBTITLE_FONT_FAMILY = "Source Han Sans CN"
"""字体的家族名；ASS 样式按它选字，粗细由样式的粗体开关选到 Bold。"""

_UNICODE_FULL_REPERTOIRE = ((3, 10), (0, 4), (0, 6))
"""cmap 里按 Unicode 全字库编码的子表（平台 ID, 编码 ID），按优先顺序；它们都用 format 12。"""


class SubtitleFontError(RuntimeError):
    """随包字体缺失或不是预期的 OpenType 形态。"""


def _table_offsets(data: bytes) -> dict[bytes, int]:
    (num_tables,) = struct.unpack_from(">H", data, 4)
    offsets: dict[bytes, int] = {}
    for index in range(num_tables):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        offsets[tag] = offset
    return offsets


def _table(data: bytes, tag: bytes) -> int:
    offset = _table_offsets(data).get(tag)
    if offset is None:
        raise SubtitleFontError(f"字幕字体没有 {tag.decode()} 表")
    return offset


def _cmap_groups(data: bytes) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """读出 cmap format 12 子表的码位区间：区间起点与终点两个有序元组。"""
    cmap_offset = _table(data, b"cmap")
    (num_subtables,) = struct.unpack_from(">H", data, cmap_offset + 2)
    subtables: dict[tuple[int, int], int] = {}
    for index in range(num_subtables):
        platform, encoding, offset = struct.unpack_from(">HHI", data, cmap_offset + 4 + 8 * index)
        subtables[(platform, encoding)] = cmap_offset + offset
    subtable = next((subtables[key] for key in _UNICODE_FULL_REPERTOIRE if key in subtables), None)
    if subtable is None or struct.unpack_from(">H", data, subtable)[0] != 12:
        raise SubtitleFontError("字幕字体没有 format 12 的 Unicode cmap 子表")
    (num_groups,) = struct.unpack_from(">I", data, subtable + 12)
    starts: list[int] = []
    ends: list[int] = []
    for index in range(num_groups):
        start, end, start_glyph = struct.unpack_from(">III", data, subtable + 16 + 12 * index)
        if start_glyph == 0:
            # 映射到 0 号字形（.notdef）的码位等于缺字，区间从下一个码位算起。
            start += 1
        if start <= end:
            starts.append(start)
            ends.append(end)
    return tuple(starts), tuple(ends)


@functools.cache
def _font_data() -> bytes:
    try:
        return SUBTITLE_FONT_FILE.read_bytes()
    except OSError as exc:
        raise SubtitleFontError(f"随包字幕字体不可读：{exc}") from exc


@functools.cache
def _coverage() -> tuple[tuple[int, ...], tuple[int, ...]]:
    return _cmap_groups(_font_data())


@functools.cache
def cell_height_per_em() -> float:
    """字体的行盒高度（OS/2 的 winAscent 与 winDescent 之和）与字身之比。

    libass 按行盒高度解释 ASS 的字号，成片字幕据此把字身像素换算成 ASS 字号。
    """
    data = _font_data()
    (units_per_em,) = struct.unpack_from(">H", data, _table(data, b"head") + 18)
    win_ascent, win_descent = struct.unpack_from(">HH", data, _table(data, b"OS/2") + 74)
    return (win_ascent + win_descent) / units_per_em


def _covered(codepoint: int) -> bool:
    starts, ends = _coverage()
    index = bisect.bisect_right(starts, codepoint) - 1
    return index >= 0 and codepoint <= ends[index]


def _needs_glyph(character: str) -> bool:
    """空白与控制、格式类字符不画出字形，不算缺字。"""
    return not character.isspace() and not unicodedata.category(character).startswith("C")


def missing_glyphs(text: str) -> str:
    """``text``（先做 NFC 规范化）里字幕字体覆盖不到的字符，去重后按首次出现的顺序拼成字符串。"""
    missing = dict.fromkeys(
        character
        for character in unicodedata.normalize("NFC", text)
        if _needs_glyph(character) and not _covered(ord(character))
    )
    return "".join(missing)


__all__ = [
    "SUBTITLE_FONT_DIR",
    "SUBTITLE_FONT_FAMILY",
    "SUBTITLE_FONT_FILE",
    "SubtitleFontError",
    "cell_height_per_em",
    "missing_glyphs",
]
