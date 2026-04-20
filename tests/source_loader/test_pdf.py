import pytest

from lib.source_loader.errors import CorruptFileError
from lib.source_loader.pdf import PyMuPDFExtractor

# 每页需 ≥ _SCANNED_CHARS_PER_PAGE(=50) 字符，否则会被判为扫描件
_PAGE_1 = "这是第一页内容。" * 8  # 8 字符 × 8 = 64 chars，> 50
_PAGE_2 = "这是第二页内容。" * 8


def test_pdf_extracts_text(pdf_factory):
    src = pdf_factory([_PAGE_1, _PAGE_2])
    result = PyMuPDFExtractor().extract(src)
    assert "第一页" in result.text
    assert "第二页" in result.text
    # 页间双换行
    assert "\n\n" in result.text
    assert result.chapter_count == 0


def test_pdf_scanned_raises(pdf_factory):
    src = pdf_factory.scanned(num_pages=3)
    with pytest.raises(CorruptFileError) as exc_info:
        PyMuPDFExtractor().extract(src)
    assert "扫描" in exc_info.value.reason or "OCR" in exc_info.value.reason
