"""PDF 抽取：PyMuPDF 主线，扫描件检测后明确报错。"""

import logging
from pathlib import Path

import fitz  # PyMuPDF

from .base import ExtractedText
from .errors import CorruptFileError

logger = logging.getLogger(__name__)

_SCANNED_CHARS_PER_PAGE = 50


class PyMuPDFExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            doc = fitz.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(filename=path.name, reason=f"PDF 打开失败: {exc}") from exc

        try:
            pages_text: list[str] = []
            for page in doc:
                pages_text.append(page.get_text("text"))
        finally:
            doc.close()

        full = "\n\n".join(pages_text).strip()
        page_count = max(len(pages_text), 1)

        if len(full) / page_count < _SCANNED_CHARS_PER_PAGE:
            raise CorruptFileError(
                filename=path.name,
                reason="疑似扫描版 PDF，需 OCR，本次不支持",
            )

        return ExtractedText(text=full, used_encoding=None, chapter_count=0)
