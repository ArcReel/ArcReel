"""PDF 抽取：pdf_oxide 主线，扫描件检测后明确报错。"""

from pathlib import Path

from pdf_oxide import PdfDocument

from .base import ExtractedText
from .errors import CorruptFileError

_SCANNED_CHARS_PER_PAGE = 50


class PdfOxideExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            doc_ctx = PdfDocument(str(path))
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(filename=path.name, reason=f"PDF 打开失败: {exc}") from exc

        pages_text: list[str] = []
        total_chars_via_chars_api = 0
        chars_api_ok_pages = 0
        page_count = 0
        with doc_ctx as doc:
            try:
                page_count = doc.page_count()
            except Exception as exc:  # noqa: BLE001
                raise CorruptFileError(filename=path.name, reason=f"PDF 解析失败: {exc}") from exc

            for idx in range(page_count):
                try:
                    page_text = doc.extract_text(idx) or ""
                except Exception as exc:  # noqa: BLE001
                    raise CorruptFileError(filename=path.name, reason=f"PDF 解析失败: {exc}") from exc
                pages_text.append(page_text)
                try:
                    chars = doc.extract_chars(idx) or []
                    total_chars_via_chars_api += len(chars)
                    chars_api_ok_pages += 1
                except Exception:  # noqa: BLE001
                    # chars API 失败不视作致命，回退到文本长度判断
                    pass

        full = "\n\n".join(pages_text).strip()
        page_count = max(page_count, 1)

        # 仅在 chars API 至少有一页成功且总字符数为 0 时，才视作"无文字层"的强信号；
        # 否则回退到字符密度阈值。避免把 chars API 全页异常误判为扫描件。
        chars_indicates_scanned = chars_api_ok_pages > 0 and total_chars_via_chars_api == 0
        if chars_indicates_scanned or len(full) / page_count < _SCANNED_CHARS_PER_PAGE:
            raise CorruptFileError(
                filename=path.name,
                reason="疑似扫描版 PDF，需 OCR，本次不支持",
            )

        return ExtractedText(text=full, used_encoding=None, chapter_count=0)
