"""Entry icon format gate (file-content portion of rule ③): PNG / WebP / SVG, square, ≤ 64 KB."""

from __future__ import annotations

import math
import re
from io import BytesIO
from pathlib import PurePosixPath
from xml.parsers import expat

from PIL import Image, UnidentifiedImageError

from .issues import ROOT_PATH, MarketIssue, MarketIssueCode

ICON_MAX_BYTES = 64 * 1024

#: Extension to (Pillow-recognized format, HTTP media type); SVG bypasses Pillow and records format None.
ICON_FORMATS: dict[str, tuple[str | None, str]] = {
    ".png": ("PNG", "image/png"),
    ".webp": ("WEBP", "image/webp"),
    ".svg": (None, "image/svg+xml"),
}

_SVG_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")

#: Valid root names: no namespace or the SVG namespace (expat joins namespace and local name with a space).
_SVG_ROOT_NAMES = frozenset({"svg", "http://www.w3.org/2000/svg svg"})


class _DtdDeclared(Exception):
    """SVG declares a DTD: entity expansion amplifies XML parsing; normal icons do not need it, so reject any occurrence."""


def inspect_icon(file: str, data: bytes) -> list[MarketIssue]:
    """Validate icon contents. ``file`` is its relative market-source path; the extension determines expected format."""
    suffix = PurePosixPath(file).suffix.lower()
    if suffix not in ICON_FORMATS:
        return [_issue(file, MarketIssueCode.ICON_FORMAT_INVALID)]
    if len(data) > ICON_MAX_BYTES:
        return [_issue(file, MarketIssueCode.ICON_TOO_LARGE, size=len(data), limit=ICON_MAX_BYTES)]
    raster_format, _media_type = ICON_FORMATS[suffix]
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
        # Let only the extension-specific decoder read this data; classify all other formats as unrecognized.
        with Image.open(BytesIO(data), formats=[expected_format]) as image:
            width, height = image.size
            image.verify()
    # Decoders report structural corruption through SyntaxError / ValueError (such as chunk-checksum mismatch or oversized headers); verify() propagates them unchanged.
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return None
    return float(width), float(height)


def _svg_size(data: bytes) -> tuple[float, float] | None:
    """SVG dimensions: prefer ``width`` / ``height`` attributes (unitless or px), otherwise ``viewBox``; require positive finite numbers."""
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
    """Parse the entire SVG and return root ``<svg>`` attributes; return None if malformed, root is not svg, or a DTD is declared.

    Reject DTDs in parser callbacks independently of encoding (byte searches cannot find ``<!DOCTYPE`` in UTF-16 files).
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
