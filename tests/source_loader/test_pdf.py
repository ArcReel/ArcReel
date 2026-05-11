import pytest

from lib.source_loader.errors import CorruptFileError
from lib.source_loader.pdf import PdfOxideExtractor

# 取样自 data/sample_text.pdf 中确定出现的中文短语
_EXPECTED_SNIPPET_PAGE_1 = "发货人"
_EXPECTED_SNIPPET_PAGE_2 = "注意事项"


def test_pdf_extracts_text(pdf_factory):
    src = pdf_factory()
    result = PdfOxideExtractor().extract(src)
    assert _EXPECTED_SNIPPET_PAGE_1 in result.text
    assert _EXPECTED_SNIPPET_PAGE_2 in result.text
    # 页间双换行
    assert "\n\n" in result.text
    assert result.chapter_count == 0


def test_pdf_scanned_raises(pdf_factory):
    src = pdf_factory.scanned()
    with pytest.raises(CorruptFileError) as exc_info:
        PdfOxideExtractor().extract(src)
    assert "扫描" in exc_info.value.reason or "OCR" in exc_info.value.reason
