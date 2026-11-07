"""PDF extraction: pdf_oxide primary path, explicitly rejecting detected scans."""

from pathlib import Path

from pdf_oxide import PdfDocument

from .base import ExtractedText
from .errors import CorruptFileError

_SCANNED_CHARS_PER_PAGE = 50


class PdfOxideExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            doc_ctx = PdfDocument(str(path))
        except Exception as exc:
            raise CorruptFileError(filename=path.name, reason=f"PDF 打开失败: {exc}") from exc

        pages_text: list[str] = []
        total_chars_via_chars_api = 0
        chars_api_ok_pages = 0
        page_count = 0
        with doc_ctx as doc:
            try:
                page_count = doc.page_count()
            except Exception as exc:
                raise CorruptFileError(filename=path.name, reason=f"PDF 解析失败: {exc}") from exc

            for idx in range(page_count):
                try:
                    page_text = doc.extract_text(idx) or ""
                except Exception as exc:
                    raise CorruptFileError(filename=path.name, reason=f"PDF 解析失败: {exc}") from exc
                pages_text.append(page_text)
                try:
                    chars = doc.extract_chars(idx) or []
                    total_chars_via_chars_api += len(chars)
                    chars_api_ok_pages += 1
                except Exception:
                    # A chars API failure is nonfatal; fall back to text length
                    pass

        full = "\n\n".join(pages_text).strip()
        page_count = max(page_count, 1)

        # Treat zero total chars as strong no-text-layer evidence only if chars API succeeded for at least one page;
        # otherwise use character-density thresholds, avoiding scan misclassification when every chars call failed.
        chars_indicates_scanned = chars_api_ok_pages > 0 and total_chars_via_chars_api == 0
        if chars_indicates_scanned or len(full) / page_count < _SCANNED_CHARS_PER_PAGE:
            raise CorruptFileError(
                filename=path.name,
                reason="疑似扫描版 PDF，需 OCR，本次不支持",
            )

        return ExtractedText(text=full, used_encoding=None, chapter_count=0)
