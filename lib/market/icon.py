"""条目 icon 的格式把关（规则 ③ 的文件内容部分）：PNG / WebP / SVG、正方形、≤ 64 KB。"""

from __future__ import annotations

import math
import re
from io import BytesIO
from pathlib import PurePosixPath
from xml.parsers import expat

from PIL import Image, UnidentifiedImageError

from .issues import ROOT_PATH, MarketIssue, MarketIssueCode

ICON_MAX_BYTES = 64 * 1024

#: 扩展名 → Pillow 识别出的格式；SVG 不经 Pillow，记 None。
ICON_FORMATS: dict[str, str | None] = {".png": "PNG", ".webp": "WEBP", ".svg": None}

_SVG_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")

#: 根元素的合法名字：无命名空间，或 SVG 命名空间（expat 以空格连接命名空间与本地名）。
_SVG_ROOT_NAMES = frozenset({"svg", "http://www.w3.org/2000/svg svg"})


class _DtdDeclared(Exception):
    """SVG 声明了 DTD：实体展开是 XML 解析的放大面，正常图标用不到，出现即拒。"""


def inspect_icon(file: str, data: bytes) -> list[MarketIssue]:
    """判定一份 icon 的内容。``file`` 是它在市场源内的相对路径，扩展名决定期望格式。"""
    suffix = PurePosixPath(file).suffix.lower()
    if suffix not in ICON_FORMATS:
        return [_issue(file, MarketIssueCode.ICON_FORMAT_INVALID)]
    if len(data) > ICON_MAX_BYTES:
        return [_issue(file, MarketIssueCode.ICON_TOO_LARGE, size=len(data), limit=ICON_MAX_BYTES)]
    raster_format = ICON_FORMATS[suffix]
    size = _svg_size(data) if raster_format is None else _raster_size(data, raster_format)
    if size is None:
        return [_issue(file, MarketIssueCode.ICON_FORMAT_INVALID)]
    width, height = size
    if width != height:
        return [
            _issue(file, MarketIssueCode.ICON_NOT_SQUARE, width=_format_length(width), height=_format_length(height))
        ]
    return []


def _raster_size(data: bytes, expected_format: str) -> tuple[float, float] | None:
    try:
        # 只让扩展名对应的解码器读这份数据，其余格式一律判为不识别。
        with Image.open(BytesIO(data), formats=[expected_format]) as image:
            width, height = image.size
            image.verify()
    # 解码器以 SyntaxError / ValueError 报告结构损坏（如 chunk 校验和不符、头部字段过长），verify() 会原样抛出。
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return None
    return float(width), float(height)


def _svg_size(data: bytes) -> tuple[float, float] | None:
    """SVG 的宽高：优先 ``width`` / ``height`` 属性（无单位或 px），否则取 ``viewBox``；须为正的有限数。"""
    attributes = _svg_root_attributes(data)
    if attributes is None:
        return None
    width = _svg_length(attributes.get("width"))
    height = _svg_length(attributes.get("height"))
    if width is None or height is None:
        parts = (attributes.get("viewBox") or "").replace(",", " ").split()
        if len(parts) != 4:
            return None
        try:
            width, height = float(parts[2]), float(parts[3])
        except ValueError:
            return None
    if not all(math.isfinite(length) and length > 0 for length in (width, height)):
        return None
    return width, height


def _svg_root_attributes(data: bytes) -> dict[str, str] | None:
    """解析整份 SVG 并返回根 ``<svg>`` 的属性；不良构、根元素不是 svg 或声明了 DTD 时返回 None。

    DTD 在解析器回调里拒绝，与文件编码无关（UTF-16 文件里按字节找不到 ``<!DOCTYPE``）。
    """
    parser = expat.ParserCreate(namespace_separator=" ")
    elements: list[tuple[str, dict[str, str]]] = []

    def start_element(name: str, attributes: dict[str, str]) -> None:
        elements.append((name, attributes))

    def start_doctype(*_: object) -> None:
        raise _DtdDeclared

    parser.StartElementHandler = start_element
    parser.StartDoctypeDeclHandler = start_doctype
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, _DtdDeclared):
        return None
    root_name, attributes = elements[0]
    return attributes if root_name in _SVG_ROOT_NAMES else None


def _svg_length(value: str | None) -> float | None:
    match = _SVG_LENGTH.match(value or "")
    return float(match.group(1)) if match else None


def _format_length(value: float) -> str:
    return f"{value:g}"


def _issue(file: str, code: MarketIssueCode, **params: object) -> MarketIssue:
    return MarketIssue(file, ROOT_PATH, code, params)
