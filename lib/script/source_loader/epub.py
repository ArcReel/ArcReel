"""EPUB 抽取：按 spine 顺序遍历章节，注入 # 标题 标记。

EPUB 是 zip 包（OCF）：``META-INF/container.xml`` 指向 OPF 包文档，OPF 的 manifest
列出资源、spine 给出阅读顺序，目录来自 NCX（EPUB 2）或 nav 文档（EPUB 3）。
这里用标准库 zipfile 与 xml.etree 解析这几份文件。
"""

import posixpath
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .base import ExtractedText
from .errors import CorruptFileError

_DOCUMENT_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})
# 媒体类型声明错误（如写成 application/xml）的章节按扩展名兜底识别。
_DOCUMENT_EXTENSIONS = (".xhtml", ".html", ".htm")
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
_OPS_NS = "http://www.idpf.org/2007/ops"
# 读取条目时上传坏文件的常规结局：压缩数据损坏或 CRC 不符（zlib.error、BadZipFile）、
# 条目截断（EOFError）、加密条目（RuntimeError）、不支持的压缩方式（NotImplementedError）、
# 本地文件头偏移错乱（ValueError、OSError）。
_ZIP_READ_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError, ValueError, OSError)


class _EpubFormatError(Exception):
    """EPUB 结构不符合规范；由 extract 统一转成 CorruptFileError。"""


@dataclass(frozen=True)
class _ManifestItem:
    path: str  # zip 内的规范化路径
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


def _resolve(base_file: str, href: str) -> str:
    """把相对 base_file 的 href 解析成 zip 内路径（去掉 #锚点、解码百分号编码）。"""
    href = unquote(href.split("#", 1)[0].strip())
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_file), href)).lstrip("/")


class _Archive:
    def __init__(self, zf: zipfile.ZipFile):
        self._zf = zf
        self._names = set(zf.namelist())

    def read(self, name: str) -> bytes:
        if name not in self._names:
            raise _EpubFormatError(f"缺少文件 {name}")
        try:
            return self._zf.read(name)
        except _ZIP_READ_ERRORS as exc:
            raise _EpubFormatError(f"{name} 读取失败: {exc}") from exc

    def xml(self, name: str) -> ET.Element:
        # EPUB 来自用户上传，按不可信输入处理：标准库解析器不加载外部 DTD 与外部实体，
        # 实体膨胀（billion laughs）由 expat ≥ 2.4.1 的放大保护拦截，均表现为 ParseError。
        # 声明了未知编码（encoding="bogus"）时抛 LookupError。
        try:
            return ET.fromstring(self.read(name))
        except (ET.ParseError, LookupError) as exc:
            raise _EpubFormatError(f"{name} 不是合法的 XML: {exc}") from exc


def _find_package_path(archive: _Archive) -> str:
    container = archive.xml("META-INF/container.xml")
    for rootfile in _descendants(container, "rootfile"):
        full_path = rootfile.get("full-path")
        if full_path:
            # full-path 是 zip 内字面路径（OCF），不是 URL：不去锚点、不解码百分号。
            return posixpath.normpath(full_path.strip()).lstrip("/")
    raise _EpubFormatError("container.xml 未指定包文档")


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


def _titles_from_nav(archive: _Archive, toc_path: str, by_path: dict[str, str]) -> None:
    navs = _descendants(archive.xml(toc_path), "nav")
    toc_navs = [n for n in navs if "toc" in (n.get(f"{{{_OPS_NS}}}type") or "").split()]
    for nav in toc_navs or navs[:1]:
        for link in _descendants(nav, "a"):
            href, title = link.get("href"), _text(link)
            if href and title:
                by_path.setdefault(_resolve(toc_path, href), title)


def _is_document(item: _ManifestItem) -> bool:
    if item.media_type in _DOCUMENT_MEDIA_TYPES:
        return True
    return item.media_type != _NCX_MEDIA_TYPE and item.path.lower().endswith(_DOCUMENT_EXTENSIONS)


def _read_book(archive: _Archive) -> list[tuple[str | None, bytes]]:
    """返回按 spine 顺序排列的正文章节：(目录标题或 None, XHTML 字节)。"""
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
    # 目录三级退化的前两级：nav（EPUB 3）优先，NCX 补缺；同一文件取第一条（章节级）标题。
    sources = [(_titles_from_nav, it.path) for it in manifest.values() if "nav" in it.properties]
    if ncx is not None:
        sources.append((_titles_from_ncx, ncx.path))
    by_path: dict[str, str] = {}
    # 目录损坏只影响标题，退化到"第 N 章"，不让整本书解析失败。
    for parse_titles, toc_path in sources:
        try:
            parse_titles(archive, toc_path, by_path)
        except _EpubFormatError:
            continue

    chapters: list[tuple[str | None, bytes]] = []
    for itemref in _children(spine, "itemref"):
        item = manifest.get(itemref.get("idref") or "")
        # 导航文档按 properties="nav" 排除，与 id 无关（Calibre/Sigil 常用 navdoc、toc 等 id）。
        if item is None or not _is_document(item) or "nav" in item.properties:
            continue
        chapters.append((by_path.get(item.path), archive.read(item.path)))
    return chapters


class EpubExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            with zipfile.ZipFile(path) as zf:
                chapters = _read_book(_Archive(zf))
        # 条目读取错误已在 _Archive 内转成 _EpubFormatError；这里另外兜住打开 zip 本身的失败。
        except (zipfile.BadZipFile, OSError, _EpubFormatError) as exc:
            raise CorruptFileError(filename=path.name, reason=f"EPUB 解析失败: {exc}") from exc

        if not chapters:
            raise CorruptFileError(filename=path.name, reason="EPUB 不含正文章节")

        parts: list[str] = []
        for idx, (title, content) in enumerate(chapters, start=1):
            soup = BeautifulSoup(content, "lxml")
            # 只取 <body>：<head> 里的 <title> 等元数据不是正文。
            body_text = (soup.body or soup).get_text("\n").strip()
            parts.append(f"\n\n# {title or f'第 {idx} 章'}\n\n{body_text}")

        return ExtractedText(
            text="".join(parts).lstrip(),
            used_encoding=None,
            chapter_count=len(chapters),
        )
