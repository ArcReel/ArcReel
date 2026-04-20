"""TXT/MD 文本解码：4 层策略。

1. BOM 优先（UTF-8-SIG / UTF-16 LE/BE）
2. 严格 UTF-8
3. charset-normalizer 概率检测（chaos < 0.5）
4. gb18030 + errors='replace' 兜底（>5% 乱码判定失败）
"""

import logging
from pathlib import Path

import charset_normalizer

from .base import ExtractedText
from .errors import SourceDecodeError

logger = logging.getLogger(__name__)

_REPLACE_THRESHOLD = 0.05


def decode_txt(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be"), "utf-16-be"

    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    best = charset_normalizer.from_bytes(raw).best()
    detected_enc: str | None = None
    if best is not None and best.chaos is not None and best.chaos < 0.5 and best.encoding:
        detected_enc = best.encoding
        try:
            return raw.decode(best.encoding), best.encoding
        except (UnicodeDecodeError, LookupError):
            pass

    decoded = raw.decode("gb18030", errors="replace")
    if decoded:
        replace_ratio = decoded.count("\ufffd") / len(decoded)
    else:
        replace_ratio = 0.0
    if replace_ratio > _REPLACE_THRESHOLD:
        raise SourceDecodeError(
            filename="<bytes>",
            tried_encodings=["utf-8", detected_enc, "gb18030"],
        )
    if "\ufffd" in decoded:
        logger.warning(
            "gb18030 fallback with %d replacements (ratio=%.4f)",
            decoded.count("\ufffd"),
            replace_ratio,
        )
    return decoded, "gb18030-lossy"


class TxtExtractor:
    def extract(self, path: Path) -> ExtractedText:
        raw = path.read_bytes()
        try:
            text, enc = decode_txt(raw)
        except SourceDecodeError as exc:
            raise SourceDecodeError(filename=path.name, tried_encodings=exc.tried_encodings) from exc
        return ExtractedText(text=text, used_encoding=enc, chapter_count=0)
