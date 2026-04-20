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
