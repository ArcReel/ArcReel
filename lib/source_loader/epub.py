"""EPUB 抽取：按 spine 顺序遍历章节，注入 # 标题 标记。"""

import logging
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from .base import ExtractedText
from .errors import CorruptFileError

logger = logging.getLogger(__name__)


def _resolve_titles(book: epub.EpubBook, doc_items: list) -> list[str]:
    """三级退化：toc → nav.xhtml → "第 N 章"。"""
    by_href: dict[str, str] = {}

    def _walk(toc):
        for entry in toc:
            if isinstance(entry, tuple):
                section, children = entry
                if hasattr(section, "href") and section.href:
                    by_href[section.href.split("#")[0]] = section.title
                _walk(children)
            elif hasattr(entry, "href") and entry.href:
                by_href[entry.href.split("#")[0]] = entry.title

    if book.toc:
        toc = book.toc if isinstance(book.toc, list | tuple) else [book.toc]
        _walk(toc)

    titles: list[str] = []
    for idx, item in enumerate(doc_items, start=1):
        title = by_href.get(item.file_name)
        if not title:
            title = f"第 {idx} 章"
        titles.append(title)
    return titles


class EpubExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            book = epub.read_epub(str(path))
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(filename=path.name, reason=f"EPUB 解析失败: {exc}") from exc

        # 按 spine 顺序拿到 ITEM_DOCUMENT
        spine_ids = [s[0] for s in book.spine]
        items_by_id = {it.id: it for it in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
        doc_items = [items_by_id[sid] for sid in spine_ids if sid in items_by_id]

        # 按类型排除导航文档（适配任意 nav id；ebooklib 默认 id="nav" 仅是其一，
        # 现实中 Calibre/Sigil 会使用其他 id 如 "id.xhtml"、"toc" 等）。
        # 读回时 ebooklib 依据 OPF manifest 的 properties="nav" 识别为 EpubNav 实例。
        doc_items = [it for it in doc_items if not isinstance(it, epub.EpubNav)]
        if not doc_items:
            raise CorruptFileError(filename=path.name, reason="EPUB 不含正文章节")

        titles = _resolve_titles(book, doc_items)

        parts: list[str] = []
        for title, item in zip(titles, doc_items, strict=True):
            soup = BeautifulSoup(item.get_content(), "lxml")
            body_text = soup.get_text("\n").strip()
            parts.append(f"\n\n# {title}\n\n{body_text}")

        return ExtractedText(
            text="".join(parts).lstrip(),
            used_encoding=None,
            chapter_count=len(doc_items),
        )
