"""SourceLoader：编排各 extractor，处理冲突、raw 备份与原子写入。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from .base import ExtractedText, NormalizeResult
from .docx import DocxExtractor
from .epub import EpubExtractor
from .errors import (
    ConflictError,
    FileSizeExceededError,
    UnsupportedFormatError,
)
from .pdf import PyMuPDFExtractor
from .txt import TxtExtractor

OnConflict = Literal["fail", "replace", "rename"]

_EXTRACTORS = {
    ".txt": TxtExtractor,
    ".md": TxtExtractor,
    ".docx": DocxExtractor,
    ".epub": EpubExtractor,
    ".pdf": PyMuPDFExtractor,
}


class SourceLoader:
    SUPPORTED_EXTS = frozenset(_EXTRACTORS.keys())
    DEFAULT_MAX_BYTES = 50 * 1024 * 1024

    @classmethod
    def detect_conflict(cls, original_filename: str, dst_dir: Path) -> tuple[bool, str]:
        """返回 (has_conflict, suggested_stem).

        冲突条件：
        - dst_dir/<stem>.txt 存在
        - dst_dir/raw/<original_filename> 存在
        suggested_stem 从 stem_1, stem_2, ... 递增到不冲突为止。
        """
        stem = Path(original_filename).stem
        normalized = dst_dir / f"{stem}.txt"
        raw = dst_dir / "raw" / original_filename

        if not normalized.exists() and not raw.exists():
            return False, stem

        idx = 1
        while True:
            candidate_stem = f"{stem}_{idx}"
            candidate_norm = dst_dir / f"{candidate_stem}.txt"
            candidate_raw = dst_dir / "raw" / f"{candidate_stem}{Path(original_filename).suffix}"
            if not candidate_norm.exists() and not candidate_raw.exists():
                return True, candidate_stem
            idx += 1

    @classmethod
    def load(
        cls,
        src: Path,
        dst_dir: Path,
        *,
        original_filename: str | None = None,
        on_conflict: OnConflict = "fail",
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> NormalizeResult:
        original_filename = original_filename or src.name
        ext = Path(original_filename).suffix.lower()

        if ext not in cls.SUPPORTED_EXTS:
            raise UnsupportedFormatError(ext=ext)

        size = src.stat().st_size
        if size > max_bytes:
            raise FileSizeExceededError(filename=original_filename, size_bytes=size, limit_bytes=max_bytes)

        # 冲突协商
        has_conflict, suggested_stem = cls.detect_conflict(original_filename, dst_dir)
        target_stem = Path(original_filename).stem
        effective_filename = original_filename
        if has_conflict:
            if on_conflict == "fail":
                raise ConflictError(existing=f"{target_stem}.txt", suggested_name=suggested_stem)
            if on_conflict == "rename":
                target_stem = suggested_stem
                effective_filename = f"{suggested_stem}{ext}"
            # on_conflict == "replace" → 沿用原 stem，覆盖

        extracted = _EXTRACTORS[ext]().extract(src)
        normalized_path = dst_dir / f"{target_stem}.txt"
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_path.write_text(extracted.text, encoding="utf-8")

        raw_path = cls._maybe_backup_raw(
            src=src,
            ext=ext,
            extracted=extracted,
            dst_dir=dst_dir,
            effective_filename=effective_filename,
        )

        return NormalizeResult(
            normalized_path=normalized_path,
            raw_path=raw_path,
            used_encoding=extracted.used_encoding,
            chapter_count=extracted.chapter_count,
            original_filename=effective_filename,
        )

    @staticmethod
    def _maybe_backup_raw(
        *,
        src: Path,
        ext: str,
        extracted: ExtractedText,
        dst_dir: Path,
        effective_filename: str,
    ) -> Path | None:
        # 决策 7：纯 UTF-8 .txt/.md 不备份；其余一律备份
        if ext in {".txt", ".md"} and extracted.used_encoding == "utf-8":
            return None
        raw_dir = dst_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / effective_filename
        shutil.copyfile(src, raw_path)
        return raw_path
