import zipfile
from pathlib import Path

import pytest

from lib.script.source_loader.epub import EpubExtractor
from lib.script.source_loader.errors import CorruptFileError


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
    # Chapter order follows spine.
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
        toc=None,
    )
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 2
    # Without TOC, titles fall back to "第 N 章".
    assert "# 第 1 章" in result.text
    assert "# 第 2 章" in result.text


def test_epub_excludes_nav_with_nonstandard_id(epub_factory):
    """Recognize navigation by manifest properties="nav", independent of IDs such as navdoc/toc commonly used by Calibre/Sigil."""
    src = epub_factory([("Ch1", "真实正文。")], nav_id="navdoc")
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 1
    assert "真实正文" in result.text
    assert "<nav" not in result.text


def test_epub3_without_ncx_takes_titles_from_nav(epub_factory):
    src = epub_factory([("序章", "正文一。"), ("终章", "正文二。")], toc="nav")
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 2
    assert "# 序章" in result.text
    assert "# 终章" in result.text


def test_epub_toc_first_entry_per_file_wins_over_fragment_entries(epub_factory):
    """Multiple TOC entries for one chapter file (#anchor subsections) must not overwrite its chapter title."""
    src = epub_factory(
        [("第一章", "正文一。"), ("第二章", "正文二。")],
        toc_entries=[
            ("第一章 起点", "chap_1.xhtml"),
            ("第一节 小节", "chap_1.xhtml#s1"),
            ("第二章 转折", "chap_2.xhtml#top"),
        ],
    )
    result = EpubExtractor().extract(src)
    assert "# 第一章 起点" in result.text
    assert "# 第一节 小节" not in result.text
    assert "# 第二章 转折" in result.text


def test_epub_with_package_document_at_archive_root(epub_factory):
    src = epub_factory([("第一章", "根目录正文。")], opf_dir="")
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 1
    assert "# 第一章" in result.text
    assert "根目录正文" in result.text


def test_epub_package_path_is_resolved_as_url(epub_factory, tmp_path):
    """EPUB 3.3 full-path is a container-root-relative URL; percent-decode before zip lookup."""
    src = epub_factory([("第一章", "编码路径正文。")], opf_dir="Book Name")
    src = _rewrite_members(
        src,
        tmp_path / "encoded_full_path.epub",
        replace={'full-path="Book Name/content.opf"': 'full-path="Book%20Name/content.opf"'},
    )
    result = EpubExtractor().extract(src)
    assert "# 第一章" in result.text
    assert "编码路径正文" in result.text


def test_epub_selects_package_rootfile_by_media_type(epub_factory, tmp_path):
    """container.xml may list multiple rootfiles, such as alternative PDF renditions; select package documents by OPF media type."""
    src = epub_factory([("第一章", "正文。")])
    src = _rewrite_members(
        src,
        tmp_path / "multi_rootfile.epub",
        replace={"<rootfiles>": '<rootfiles><rootfile full-path="preview.pdf" media-type="application/pdf"/>'},
    )
    result = EpubExtractor().extract(src)
    assert "# 第一章" in result.text
    assert "正文。" in result.text


def test_epub_resolves_percent_encoded_hrefs(epub_factory, tmp_path):
    src = epub_factory([("第一章", "空格文件名正文。")])
    src = _rewrite_members(
        src,
        tmp_path / "encoded.epub",
        rename={"OEBPS/chap_1.xhtml": "OEBPS/chap 1.xhtml"},
        replace={"chap_1.xhtml": "chap%201.xhtml"},
    )
    result = EpubExtractor().extract(src)
    assert "# 第一章" in result.text
    assert "空格文件名正文" in result.text


def test_epub_does_not_expand_external_entities(epub_factory, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    src = epub_factory([("第一章", "正文。")], toc="ncx")
    src = _rewrite_members(
        src,
        tmp_path / "xxe.epub",
        replace={
            '<?xml version="1.0" encoding="utf-8"?><ncx': (
                f'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE ncx [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]><ncx'
            ),
            "<text>第一章</text>": "<text>&xxe;</text>",
        },
    )
    result = EpubExtractor().extract(src)
    assert "TOP-SECRET" not in result.text
    # Never load external entities; treat NCX as corrupt, fall back to numbered titles, and extract body normally.
    assert "# 第 1 章" in result.text
    assert "正文。" in result.text


def test_epub_nav_titles_take_precedence_over_ncx(epub_factory, tmp_path):
    src = epub_factory([("目录标题", "正文一。")])
    src = _rewrite_members(
        src, tmp_path / "ncx_differs.epub", replace={"<text>目录标题</text>": "<text>NCX 标题</text>"}
    )
    result = EpubExtractor().extract(src)
    assert "# 目录标题" in result.text
    assert "# NCX 标题" not in result.text


def test_epub_chapter_with_wrong_media_type_is_still_body(epub_factory, tmp_path):
    src = epub_factory([("第一章", "错标类型的正文。")])
    src = _rewrite_members(
        src,
        tmp_path / "wrong_media_type.epub",
        replace={
            'href="chap_1.xhtml" media-type="application/xhtml+xml"': 'href="chap_1.xhtml" media-type="application/xml"'
        },
    )
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 1
    assert "错标类型的正文" in result.text


def test_epub_corrupted_compressed_data_raises_corrupt(epub_factory, tmp_path):
    src = epub_factory([("第一章", "正文。" * 200)])
    broken = _corrupt_member(src, tmp_path / "bad_deflate.epub", "OEBPS/chap_1.xhtml")
    with pytest.raises(CorruptFileError):
        EpubExtractor().extract(broken)


def test_epub_corrupted_toc_entry_degrades_to_index_titles(epub_factory, tmp_path):
    """Zip-level TOC corruption (bad compressed data/CRC) degrades only titles; body extraction proceeds normally."""
    src = epub_factory([("第一章 起点", "正文一。")], toc="ncx")
    broken = _corrupt_member(src, tmp_path / "bad_ncx.epub", "OEBPS/toc.ncx")
    result = EpubExtractor().extract(broken)
    assert "# 第 1 章" in result.text
    assert "正文一" in result.text


def test_epub_broken_ncx_falls_back_to_nav_titles(epub_factory, tmp_path):
    src = epub_factory([("第一章 起点", "正文一。")])
    src = _rewrite_members(src, tmp_path / "broken_ncx.epub", replace={"</navMap></ncx>": "</navMap>"})
    result = EpubExtractor().extract(src)
    assert "# 第一章 起点" in result.text
    assert "正文一" in result.text


def test_epub_broken_toc_degrades_to_index_titles(epub_factory, tmp_path):
    src = epub_factory([("第一章 起点", "正文一。")], toc="ncx")
    src = _rewrite_members(src, tmp_path / "broken_toc.epub", replace={"</navMap></ncx>": "</navMap>"})
    result = EpubExtractor().extract(src)
    assert "# 第 1 章" in result.text
    assert "正文一" in result.text


def test_epub_chapter_text_excludes_document_head(epub_factory):
    """Chapter <head><title> is not body content and must not duplicate <h1>."""
    src = epub_factory([("起点", "正文一。")])
    result = EpubExtractor().extract(src)
    # One occurrence comes from injected "# 起点", the other from body <h1>.
    assert result.text.count("起点") == 2


@pytest.mark.parametrize("encoding", ["gbk", "shift_jis"])
def test_epub_reads_xml_declared_in_multibyte_encoding(epub_factory, tmp_path, encoding):
    """When the whole book truthfully declares multibyte GBK/Shift_JIS encoding, decode OPF, NCX, and chapters by their declarations."""
    src = epub_factory([("第一章 起点", "正文一。")], toc="ncx")
    src = _rewrite_members(
        src,
        tmp_path / f"{encoding}.epub",
        replace={'encoding="utf-8"': f'encoding="{encoding}"'},
        encodings=dict.fromkeys(("OEBPS/content.opf", "OEBPS/toc.ncx", "OEBPS/chap_1.xhtml"), encoding),
    )
    result = EpubExtractor().extract(src)
    assert "# 第一章 起点" in result.text
    assert "正文一" in result.text


def test_epub_package_with_unknown_xml_encoding_raises_corrupt(epub_factory, tmp_path):
    src = epub_factory([("第一章", "正文。")])
    src = _rewrite_members(
        src,
        tmp_path / "bogus_opf.epub",
        replace={'<?xml version="1.0" encoding="utf-8"?><package': '<?xml version="1.0" encoding="bogus"?><package'},
    )
    with pytest.raises(CorruptFileError):
        EpubExtractor().extract(src)


def test_epub_toc_with_unknown_xml_encoding_degrades_to_index_titles(epub_factory, tmp_path):
    src = epub_factory([("第一章 起点", "正文一。")], toc="ncx")
    src = _rewrite_members(
        src,
        tmp_path / "bogus_ncx.epub",
        replace={'<?xml version="1.0" encoding="utf-8"?><ncx': '<?xml version="1.0" encoding="bogus"?><ncx'},
    )
    result = EpubExtractor().extract(src)
    assert "# 第 1 章" in result.text
    assert "正文一" in result.text


@pytest.mark.parametrize(
    ("nav_open", "link_text"),
    [('<nav epub:type="landmarks">', "Start of Content"), ("<nav>", "1")],
    ids=["landmarks", "untyped-page-list"],
)
def test_epub_non_toc_nav_does_not_supply_titles(epub_factory, tmp_path, nav_open, link_text):
    """Only toc-marked nav supplies chapter titles; landmarks/untyped page lists do not, leaving NCX titles effective."""
    src = epub_factory([("第一章 起点", "正文一。")])
    src = _rewrite_members(
        src,
        tmp_path / "non_toc_nav.epub",
        replace={
            '<nav epub:type="toc"><ol><li><a href="chap_1.xhtml">第一章 起点</a></li></ol></nav>': (
                f'{nav_open}<ol><li><a href="chap_1.xhtml">{link_text}</a></li></ol></nav>'
            )
        },
    )
    result = EpubExtractor().extract(src)
    assert "# 第一章 起点" in result.text
    assert f"# {link_text}" not in result.text


def test_epub_with_corrupt_lzma_member_raises_corrupt(tmp_path):
    """Corrupt LZMA parameters raise lzma.LZMAError during decompression and also count as file corruption."""
    src = tmp_path / "bad_lzma.epub"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", "<container/>" * 50, compress_type=zipfile.ZIP_LZMA)
    data = bytearray(src.read_bytes())
    with zipfile.ZipFile(src) as zf:
        info = zf.getinfo("META-INF/container.xml")
    # LZMA entry data starts with a 4-byte version/length header, then compression properties; corrupt the properties.
    payload_start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    for offset in range(payload_start + 4, payload_start + 9):
        data[offset] = 0xFF
    src.write_bytes(bytes(data))
    with pytest.raises(CorruptFileError):
        EpubExtractor().extract(src)


def test_epub_with_undecodable_utf8_filename_raises_corrupt(tmp_path):
    """Central-directory filenames marked UTF-8 but containing invalid bytes count as file corruption."""
    src = tmp_path / "bad_name.epub"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        # Non-ASCII filenames automatically get UTF-8 flags; then replace bytes with invalid UTF-8.
        zf.writestr("META-INF/章.xhtml", "<html/>")
    name = "章".encode()
    src.write_bytes(src.read_bytes().replace(name, b"\xff" * len(name)))
    with pytest.raises(CorruptFileError):
        EpubExtractor().extract(src)


@pytest.mark.parametrize("text", ["Déjà vu，", "第一章的正文内容，包含常见的中文字符。"])
def test_epub_chapter_without_declaration_defaults_to_utf8(epub_factory, tmp_path, text):
    """Without XML declaration/charset, decode chapters using XML-default UTF-8, not encoding detection."""
    src = epub_factory([("第一章", text)])
    src = _rewrite_members(
        src,
        tmp_path / "no_declaration.epub",
        replace={
            '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head>': (
                '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            )
        },
    )
    result = EpubExtractor().extract(src)
    assert text in result.text


def test_epub_chapter_in_declared_legacy_encoding(epub_factory, tmp_path):
    src = epub_factory([("第一章", "旧编码正文。")])
    src = _rewrite_members(
        src,
        tmp_path / "gbk_chapter.epub",
        replace={
            '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head>': (
                '<?xml version="1.0" encoding="gbk"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
            )
        },
        encodings={"OEBPS/chap_1.xhtml": "gbk"},
    )
    result = EpubExtractor().extract(src)
    assert "旧编码正文" in result.text


def test_epub_chapter_with_meta_charset_only(epub_factory, tmp_path):
    """Without XML declarations, chapters declaring GBK only in <meta charset> decode by meta."""
    src = epub_factory([("第一章", "元信息声明的正文。")])
    src = _rewrite_members(
        src,
        tmp_path / "meta_charset.epub",
        replace={
            '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head>': (
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="gbk"/>'
            )
        },
        encodings={"OEBPS/chap_1.xhtml": "gbk"},
    )
    result = EpubExtractor().extract(src)
    assert "元信息声明的正文" in result.text


def test_epub_not_a_zip_raises_corrupt(tmp_path):
    src = tmp_path / "broken.epub"
    src.write_bytes(b"not a zip archive")
    with pytest.raises(CorruptFileError):
        EpubExtractor().extract(src)


def test_epub_without_container_raises_corrupt(tmp_path):
    src = tmp_path / "no_container.epub"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("chap.xhtml", "<html><body><p>孤立正文</p></body></html>")
    with pytest.raises(CorruptFileError):
        EpubExtractor().extract(src)


def test_epub_without_body_documents_raises_corrupt(epub_factory):
    src = epub_factory([], toc="nav")
    with pytest.raises(CorruptFileError):
        EpubExtractor().extract(src)


def _rewrite_members(
    src: Path,
    out: Path,
    *,
    rename: dict[str, str] | None = None,
    replace: dict[str, str] | None = None,
    encodings: dict[str, str] | None = None,
) -> Path:
    """Copy EPUB, optionally renaming entries/replacing text, and reencode specified entries according to encodings."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename != "mimetype":
                text = data.decode("utf-8")
                for old, new in (replace or {}).items():
                    text = text.replace(old, new)
                data = text.encode((encodings or {}).get(info.filename, "utf-8"))
            zout.writestr((rename or {}).get(info.filename, info.filename), data)
    return out


def _corrupt_member(src: Path, out: Path, name: str) -> Path:
    """Copy EPUB and corrupt specified entries' compressed data."""
    data = bytearray(src.read_bytes())
    with zipfile.ZipFile(src) as zf:
        info = zf.getinfo(name)
    # Fixed local header: 30 bytes, then filename and extra fields, then compressed data.
    payload_start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    for offset in range(payload_start + 4, min(payload_start + 24, payload_start + info.compress_size)):
        data[offset] ^= 0xFF
    out.write_bytes(bytes(data))
    return out
