import pytest

from lib.source_loader.epub import EpubExtractor


def test_epub_injects_chapter_markers_and_counts(epub_factory):
    src = epub_factory(
        [
            ("第一章 起点", "第一章正文内容。"),
            ("第二章 转折", "第二章正文内容。"),
            ("第三章 终局", "第三章正文内容。"),
        ]
    )
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 3
    assert "# 第一章 起点" in result.text
    assert "# 第二章 转折" in result.text
    assert "# 第三章 终局" in result.text
    # 章节顺序与 spine 一致
    pos1 = result.text.find("第一章正文")
    pos2 = result.text.find("第二章正文")
    pos3 = result.text.find("第三章正文")
    assert 0 < pos1 < pos2 < pos3


def test_epub_falls_back_to_index_when_no_toc(epub_factory):
    src = epub_factory(
        [
            ("不会被使用的标题1", "正文1。"),
            ("不会被使用的标题2", "正文2。"),
        ],
        with_toc=False,
    )
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 2
    # 没有 toc → 标题降级为 "第 N 章"
    assert "# 第 1 章" in result.text
    assert "# 第 2 章" in result.text


def test_epub_excludes_nav_with_nonstandard_id(tmp_path):
    """Nav filter must work regardless of nav item id (some tools use 'navdoc' / 'toc')."""
    pytest.importorskip("ebooklib")
    from ebooklib import epub as ebook_epub

    book = ebook_epub.EpubBook()
    book.set_identifier("test-nav-id")
    book.set_title("Nav ID Test")
    book.set_language("zh")

    ch = ebook_epub.EpubHtml(title="Ch1", file_name="c1.xhtml", lang="zh")
    ch.content = "<html><body><p>真实正文。</p></body></html>"
    book.add_item(ch)

    # Nav with a non-standard id
    nav = ebook_epub.EpubNav(uid="navdoc")
    book.add_item(nav)
    book.add_item(ebook_epub.EpubNcx())

    book.toc = (ch,)
    book.spine = [nav, ch]  # nav referenced in spine

    out = tmp_path / "nav_id.epub"
    ebook_epub.write_epub(out, book)

    result = EpubExtractor().extract(out)
    assert result.chapter_count == 1
    assert "真实正文" in result.text
    # Nav document HTML must not appear
    assert "<nav" not in result.text
