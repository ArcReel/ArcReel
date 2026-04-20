"""共享 fixtures：尽量在运行期构造测试样本，避免二进制入库。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def docx_factory(tmp_path: Path):
    """构造一个含两段文本的 .docx；无 python-docx 时跳过。"""
    docx_mod = pytest.importorskip("docx", reason="需要 python-docx 构造 fixture")

    def _make(paragraphs: list[str], filename: str = "sample.docx") -> Path:
        doc = docx_mod.Document()
        for p in paragraphs:
            doc.add_paragraph(p)
        out = tmp_path / filename
        doc.save(out)
        return out

    return _make


@pytest.fixture
def epub_factory(tmp_path: Path):
    """构造一个含 N 章 + toc 的 .epub。"""
    pytest.importorskip("ebooklib", reason="需要 ebooklib")
    from ebooklib import epub

    def _make(
        chapter_titles_and_bodies: list[tuple[str, str]],
        filename: str = "sample.epub",
        with_toc: bool = True,
    ) -> Path:
        book = epub.EpubBook()
        book.set_identifier("test-id")
        book.set_title("Test Book")
        book.set_language("zh")

        chapters = []
        for idx, (title, body) in enumerate(chapter_titles_and_bodies, start=1):
            ch = epub.EpubHtml(
                title=title,
                file_name=f"chap_{idx}.xhtml",
                lang="zh",
            )
            ch.content = f"<html><body><h1>{title}</h1><p>{body}</p></body></html>"
            book.add_item(ch)
            chapters.append(ch)

        if with_toc:
            book.toc = tuple(chapters)
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())
            book.spine = ["nav", *chapters]
        else:
            # 不设置 book.toc / 不加 nav；但仍需 NCX 才能被 ebooklib 读回
            book.add_item(epub.EpubNcx())
            book.spine = list(chapters)

        out = tmp_path / filename
        epub.write_epub(out, book)
        return out

    return _make


@pytest.fixture
def pdf_factory(tmp_path: Path):
    """构造文字型或扫描型 PDF。"""
    fitz = pytest.importorskip("fitz", reason="需要 PyMuPDF")

    def _make(pages_text: list[str], filename: str = "sample.pdf") -> Path:
        doc = fitz.open()
        for body in pages_text:
            page = doc.new_page()
            # 使用 china-s（PyMuPDF 内置 CJK 字体），否则默认 Helvetica 无法渲染中文；
            # 使用 textbox 自动换行，避免长文本溢出页面被截断
            rect = fitz.Rect(72, 72, page.rect.width - 72, page.rect.height - 72)
            page.insert_textbox(rect, body, fontsize=12, fontname="china-s")
        out = tmp_path / filename
        doc.save(out)
        doc.close()
        return out

    def _make_scanned(num_pages: int, filename: str = "scanned.pdf") -> Path:
        # 仅插入空白页 → 模拟扫描件无文本
        doc = fitz.open()
        for _ in range(num_pages):
            doc.new_page()
        out = tmp_path / filename
        doc.save(out)
        doc.close()
        return out

    _make.scanned = _make_scanned
    return _make
