"""EPUB extraction: traverse chapters in spine order, injecting # title markers.

EPUB is an OCF zip: ``META-INF/container.xml`` points to the OPF package, whose manifest
lists resources and spine specifies reading order. TOCs come from NCX (EPUB 2) or nav (EPUB 3).
Parse these with standard-library zipfile and xml.etree.
"""

import codecs
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .base import ExtractedText
from .errors import CorruptFileError

_DOCUMENT_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})
# Identify chapters with incorrect media types (such as application/xml) by extension as fallback.
_DOCUMENT_EXTENSIONS = (".xhtml", ".html", ".htm")
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
_PACKAGE_MEDIA_TYPE = "application/oebps-package+xml"
_OPS_NS = "http://www.idpf.org/2007/ops"
# Encodings natively supported by expat; decode others (GBK, Big5, Shift_JIS, etc.) with Python before parsing.
_EXPAT_NATIVE_CODECS = frozenset({"utf-8", "utf-16", "utf-16-le", "utf-16-be", "iso8859-1", "ascii"})
_XML_DECLARATION = re.compile(rb"""^\s*<\?xml[^>]*?\bencoding\s*=\s*["']([A-Za-z0-9._:-]+)["'][^>]*\?>""")


class _EpubFormatError(Exception):
    """EPUB structure violates the specification."""


@dataclass(frozen=True)
class _ManifestItem:
    path: str  # Normalized path within the ZIP
    media_type: str
    properties: frozenset[str]


def _local(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c) == name]


def _descendants(el: ET.Element, name: str) -> list[ET.Element]:
    return [d for d in el.iter() if _local(d) == name]


def _text(el: ET.Element) -> str:
    return " ".join("".join(el.itertext()).split())


def _parse_xml(data: bytes) -> ET.Element:
    declaration = _XML_DECLARATION.match(data)
    if declaration is not None:
        codec = codecs.lookup(declaration.group(1).decode("ascii")).name
        if codec not in _EXPAT_NATIVE_CODECS:
            return ET.fromstring(data[declaration.end() :].decode(codec))
    return ET.fromstring(data)


def _decode_chapter(data: bytes) -> str | bytes:
    """Decode chapters following XML rules: declared encoding first, otherwise UTF-8.

    For BOMs, undecodable declared encodings, or non-UTF-8 without declarations (such as only <meta charset>),
    return raw bytes for BeautifulSoup BOM/HTML-metadata detection.
    """
    if data.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data
    declaration = _XML_DECLARATION.match(data)
    try:
        if declaration is None:
            return data.decode("utf-8")
        codec = codecs.lookup(declaration.group(1).decode("ascii")).name
        return data[declaration.end() :].decode(codec)
    except (LookupError, ValueError):
        return data


def _resolve(base_file: str, href: str) -> str:
    """Resolve href relative to base_file into a zip path, dropping #fragments and percent-decoding."""
    href = unquote(href.split("#", 1)[0].strip())
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_file), href)).lstrip("/")


class _Archive:
    def __init__(self, zf: zipfile.ZipFile):
        self._zf = zf
        self._names = set(zf.namelist())

    def read(self, name: str) -> bytes:
        if name not in self._names:
            raise _EpubFormatError(f"缺少文件 {name}")
        return self._zf.read(name)

    def xml(self, name: str) -> ET.Element:
        # Treat uploaded EPUBs as untrusted: standard-library parsers load no external DTD/entities;
        # expat ≥ 2.4.1 amplification protection blocks billion-laughs entity expansion.
        return _parse_xml(self.read(name))


def _find_package_path(archive: _Archive) -> str:
    container = archive.xml("META-INF/container.xml")
    rootfiles = [r for r in _descendants(container, "rootfile") if r.get("full-path")]
    # Alternative formats such as PDF may coexist; select package documents by media type, falling back for undeclared malformed entries.
    packages = [r for r in rootfiles if (r.get("media-type") or "").strip().lower() == _PACKAGE_MEDIA_TYPE]
    candidates = packages or [r for r in rootfiles if not r.get("media-type")]
    if not candidates:
        raise _EpubFormatError("container.xml 未指定包文档")
    # EPUB 3.3 defines full-path as a URL relative to the container root.
    return _resolve("", candidates[0].get("full-path", ""))


def _parse_manifest(package: ET.Element, package_path: str) -> dict[str, _ManifestItem]:
    items: dict[str, _ManifestItem] = {}
    for manifest in _children(package, "manifest"):
        for item in _children(manifest, "item"):
            item_id, href = item.get("id"), item.get("href")
            if not item_id or not href:
                continue
            items[item_id] = _ManifestItem(
                path=_resolve(package_path, href),
                media_type=(item.get("media-type") or "").strip().lower(),
                properties=frozenset((item.get("properties") or "").split()),
            )
    return items


def _titles_from_ncx(archive: _Archive, toc_path: str, by_path: dict[str, str]) -> None:
    for point in _descendants(archive.xml(toc_path), "navPoint"):
        contents = _children(point, "content")
        labels = _children(point, "navLabel")
        if not contents or not labels or not contents[0].get("src"):
            continue
        title = _text(labels[0])
        if title:
            by_path.setdefault(_resolve(toc_path, contents[0].get("src", "")), title)


def _collect_nav_links(nav: ET.Element, toc_path: str, by_path: dict[str, str]) -> None:
    for link in _descendants(nav, "a"):
        href, title = link.get("href"), _text(link)
        if href and title:
            by_path.setdefault(_resolve(toc_path, href), title)


def _nav_types(nav: ET.Element) -> list[str]:
    return (nav.get(f"{{{_OPS_NS}}}type") or "").split()


def _titles_from_nav_toc(archive: _Archive, toc_path: str, by_path: dict[str, str]) -> None:
    for nav in _descendants(archive.xml(toc_path), "nav"):
        if "toc" in _nav_types(nav):
            _collect_nav_links(nav, toc_path, by_path)


def _is_document(item: _ManifestItem) -> bool:
    if item.media_type in _DOCUMENT_MEDIA_TYPES:
        return True
    return item.media_type != _NCX_MEDIA_TYPE and item.path.lower().endswith(_DOCUMENT_EXTENSIONS)


def _read_book(archive: _Archive) -> list[tuple[str | None, bytes]]:
    """Return body chapters in spine order: (TOC title or None, XHTML bytes)."""
    package_path = _find_package_path(archive)
    package = archive.xml(package_path)
    manifest = _parse_manifest(package, package_path)
    spines = _children(package, "spine")
    if not spines:
        raise _EpubFormatError("包文档缺少 spine")
    spine = spines[0]

    ncx = manifest.get(spine.get("toc") or "") or next(
        (it for it in manifest.values() if it.media_type == _NCX_MEDIA_TYPE), None
    )
    # First two levels of three-level TOC fallback: toc nav (EPUB 3) first, NCX fills gaps. Other navigation such as
    # landmarks/page-list supplies no titles. Use the first chapter-level title per file.
    sources = [(_titles_from_nav_toc, it.path) for it in manifest.values() if "nav" in it.properties]
    if ncx is not None:
        sources.append((_titles_from_ncx, ncx.path))
    by_path: dict[str, str] = {}
    for parse_titles, toc_path in sources:
        # Unreadable/unparseable TOCs affect titles only; fall back to chapter N without failing the whole book.
        # Standard-library exceptions for malformed input are open-ended; catch them collectively at this boundary.
        try:
            parse_titles(archive, toc_path, by_path)
        except Exception:
            continue

    chapters: list[tuple[str | None, bytes]] = []
    for itemref in _children(spine, "itemref"):
        item = manifest.get(itemref.get("idref") or "")
        # Exclude navigation by properties="nav", independent of IDs (Calibre/Sigil commonly use navdoc/toc).
        if item is None or not _is_document(item) or "nav" in item.properties:
            continue
        chapters.append((by_path.get(item.path), archive.read(item.path)))
    return chapters


class EpubExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            with zipfile.ZipFile(path) as zf:
                chapters = _read_book(_Archive(zf))
        # Upload parsing boundary: zip/XML/codec exceptions on malformed input are open-ended
        # (corrupted compression, encrypted entries, unknown encodings, invalid filenames); convert collectively to CorruptFileError.
        except Exception as exc:
            raise CorruptFileError(filename=path.name, reason=f"EPUB 解析失败: {exc}") from exc

        if not chapters:
            raise CorruptFileError(filename=path.name, reason="EPUB 不含正文章节")

        parts: list[str] = []
        for idx, (title, content) in enumerate(chapters, start=1):
            soup = BeautifulSoup(_decode_chapter(content), "lxml")
            # Extract <body> only: <head> metadata such as <title> is not body text.
            body_text = (soup.body or soup).get_text("\n").strip()
            parts.append(f"\n\n# {title or f'第 {idx} 章'}\n\n{body_text}")

        return ExtractedText(
            text="".join(parts).lstrip(),
            used_encoding=None,
            chapter_count=len(chapters),
        )
