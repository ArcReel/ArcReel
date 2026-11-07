import pytest

from lib.script.source_loader.errors import CorruptFileError
from lib.script.source_loader.pdf import PdfOxideExtractor

# Sample a Chinese phrase known to occur in data/sample_text.pdf.
_EXPECTED_SNIPPET_PAGE_1 = "新股研究"
_EXPECTED_SNIPPET_PAGE_2 = "打新基金"


def test_pdf_extracts_text(pdf_factory):
    src = pdf_factory()
    result = PdfOxideExtractor().extract(src)
    assert _EXPECTED_SNIPPET_PAGE_1 in result.text
    assert _EXPECTED_SNIPPET_PAGE_2 in result.text
    # Double newlines between pages.
    assert "\n\n" in result.text
    assert result.chapter_count == 0


def test_pdf_scanned_raises(pdf_factory):
    src = pdf_factory.scanned()
    with pytest.raises(CorruptFileError) as exc_info:
        PdfOxideExtractor().extract(src)
    assert "扫描" in exc_info.value.reason or "OCR" in exc_info.value.reason
