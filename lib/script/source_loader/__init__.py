"""Source-file parsing and normalization.

Upload routes use SourceLoader.load() to convert .txt/.md/.docx/.epub/.pdf to UTF-8 plain text,
transparent to downstream consumers.
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
