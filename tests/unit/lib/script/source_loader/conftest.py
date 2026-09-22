"""共享 fixtures：尽量在运行期构造测试样本，避免二进制入库。

例外：PDF 因 pdf_oxide 暂无稳定的 Python 创建 API，改用 data/ 下预生成的
真实 PDF（来源详见 data/SOURCES.md）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PDF_DATA_DIR = Path(__file__).parent / "data"


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
def pdf_factory():
    """返回预生成的 PDF fixture 路径（来自真实 PDF，见 data/SOURCES.md）。"""

    def _make() -> Path:
        return _PDF_DATA_DIR / "sample_text.pdf"

    def _make_scanned() -> Path:
        return _PDF_DATA_DIR / "sample_scanned.pdf"

    _make.scanned = _make_scanned  # type: ignore[attr-defined]
    return _make
