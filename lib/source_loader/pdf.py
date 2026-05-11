"""PDF 抽取：pdf_oxide 主线，扫描件检测后明确报错。"""

from pathlib import Path

from pdf_oxide import PdfDocument

from .base import ExtractedText
from .errors import CorruptFileError

_SCANNED_CHARS_PER_PAGE = 50


class PdfOxideExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            doc = PdfDocument(str(path))
            page_count = doc.page_count()
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(filename=path.name, reason=f"PDF 打开失败: {exc}") from exc

        pages_text: list[str] = []
        total_chars_via_chars_api = 0
        for idx in range(page_count):
            pages_text.append(doc.extract_text(idx))
            try:
                total_chars_via_chars_api += len(doc.extract_chars(idx) or [])
            except Exception:  # noqa: BLE001
                pass

        full = "\n\n".join(pages_text).strip()
        page_count = max(page_count, 1)

        if total_chars_via_chars_api == 0 or len(full) / page_count < _SCANNED_CHARS_PER_PAGE:
            raise CorruptFileError(
                filename=path.name,
                reason="疑似扫描版 PDF，需 OCR，本次不支持",
            )

        return ExtractedText(text=full, used_encoding=None, chapter_count=0)
