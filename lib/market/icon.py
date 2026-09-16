"""条目 icon 的格式把关（规则 ③ 的文件内容部分）：PNG / WebP / SVG、正方形、≤ 64 KB。"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

from .issues import ROOT_PATH, MarketIssue, MarketIssueCode

ICON_MAX_BYTES = 64 * 1024

#: 扩展名 → Pillow 识别出的格式；SVG 不经 Pillow，记 None。
ICON_FORMATS: dict[str, str | None] = {".png": "PNG", ".webp": "WEBP", ".svg": None}

_SVG_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")

#: SVG 里出现 DTD 即拒：实体展开是 XML 解析的放大面，正常图标用不到。
_DTD_MARKERS = (b"<!DOCTYPE", b"<!ENTITY")


def inspect_icon(file: str, data: bytes) -> list[MarketIssue]:
    """判定一份 icon 的内容。``file`` 是它在市场源内的相对路径，扩展名决定期望格式。"""
    suffix = PurePosixPath(file).suffix.lower()
    if suffix not in ICON_FORMATS:
        return [_issue(file, MarketIssueCode.ICON_FORMAT_INVALID)]
    if len(data) > ICON_MAX_BYTES:
        return [_issue(file, MarketIssueCode.ICON_TOO_LARGE, size=len(data), limit=ICON_MAX_BYTES)]
    size = _svg_size(data) if suffix == ".svg" else _raster_size(data, ICON_FORMATS[suffix])
    if size is None:
        return [_issue(file, MarketIssueCode.ICON_FORMAT_INVALID)]
    width, height = size
    if width != height:
        return [
            _issue(file, MarketIssueCode.ICON_NOT_SQUARE, width=_format_length(width), height=_format_length(height))
        ]
    return []


def _raster_size(data: bytes, expected_format: str | None) -> tuple[float, float] | None:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != expected_format:
                return None
            width, height = image.size
            image.verify()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        return None
    return float(width), float(height)


def _svg_size(data: bytes) -> tuple[float, float] | None:
    """SVG 的宽高：优先 ``width`` / ``height`` 属性（无单位或 px），否则取 ``viewBox``。"""
    if any(marker in data for marker in _DTD_MARKERS):
        return None
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None
    if root.tag not in ("svg", "{http://www.w3.org/2000/svg}svg"):
        return None
    width = _svg_length(root.get("width"))
    height = _svg_length(root.get("height"))
    if width is not None and height is not None:
        return width, height
    parts = (root.get("viewBox") or "").replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        view_width, view_height = float(parts[2]), float(parts[3])
    except ValueError:
        return None
    if view_width <= 0 or view_height <= 0:
        return None
    return view_width, view_height


def _svg_length(value: str | None) -> float | None:
    match = _SVG_LENGTH.match(value or "")
    return float(match.group(1)) if match else None


def _format_length(value: float) -> str:
    return f"{value:g}"


def _issue(file: str, code: MarketIssueCode, **params: object) -> MarketIssue:
    return MarketIssue(file, ROOT_PATH, code, params)
