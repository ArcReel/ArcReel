"""源文件解析与规范化层。

上传路由调用 SourceLoader.load() 把 .txt/.md/.docx/.epub/.pdf 转成 UTF-8 纯文本，
下游消费者零感知。
"""

from .base import ExtractedText, FormatExtractor, NormalizeResult
from .errors import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    SourceDecodeError,
    SourceLoaderError,
    UnsupportedFormatError,
)
from .loader import OnConflict, SourceLoader

__all__ = [
    "ConflictError",
    "CorruptFileError",
    "ExtractedText",
    "FileSizeExceededError",
    "FormatExtractor",
    "NormalizeResult",
    "OnConflict",
    "SourceDecodeError",
    "SourceLoader",
    "SourceLoaderError",
    "UnsupportedFormatError",
]
