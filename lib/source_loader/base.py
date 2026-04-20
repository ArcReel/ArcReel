"""协议与数据类。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class ExtractedText:
    text: str
    used_encoding: str | None = None
    chapter_count: int = 0


@dataclass
class NormalizeResult:
    normalized_path: Path
    raw_path: Path | None
    used_encoding: str | None
    chapter_count: int
    original_filename: str


class FormatExtractor(Protocol):
    def extract(self, path: Path) -> ExtractedText: ...
