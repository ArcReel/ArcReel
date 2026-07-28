"""DOCX 抽取：docx2txt 主路径 + mammoth 兜底。"""

import logging
import re
from pathlib import Path

import docx2txt
import mammoth

from .base import ExtractedText
from .errors import CorruptFileError

logger = logging.getLogger(__name__)

# mammoth 输出的 Markdown 语法标记：仅去除会污染字符计数的标记，保留段落结构
_MD_MARK_PATTERN = re.compile(r"(\*\*|__|\#{1,6}\s+|`+)")


def _strip_markdown_marks(text: str) -> str:
    return _MD_MARK_PATTERN.sub("", text)


class DocxExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            text = docx2txt.process(str(path)) or ""
            if text.strip():
                return ExtractedText(text=text, used_encoding=None, chapter_count=0)
            logger.warning("docx2txt 返回空文本，回退到 mammoth: %s", path.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("docx2txt 抽取失败 (%s)，回退到 mammoth: %s", exc, path.name)

        try:
            with path.open("rb") as fh:
                result = mammoth.convert_to_markdown(fh)
            md_text = _strip_markdown_marks(result.value or "")
            if not md_text.strip():
                raise CorruptFileError(filename=path.name, reason="DOCX 抽取结果为空")
            return ExtractedText(text=md_text, used_encoding=None, chapter_count=0)
        except CorruptFileError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(filename=path.name, reason=f"DOCX 解析失败: {exc}") from exc
