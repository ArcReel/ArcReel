"""SourceLoader: orchestrate extractors, conflicts, raw backups, and atomic writes."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from lib.infra.path_safety import safe_join

from .base import ExtractedText, NormalizeResult
from .docx import DocxExtractor
from .epub import EpubExtractor
from .errors import (
    ConflictError,
    FileSizeExceededError,
    UnsupportedFormatError,
)
from .pdf import PdfOxideExtractor
from .txt import TxtExtractor

OnConflict = Literal["fail", "replace", "rename"]

_MAX_CONFLICT_ITER = 10000

_EXTRACTORS = {
    ".txt": TxtExtractor,
    ".md": TxtExtractor,
    ".docx": DocxExtractor,
    ".epub": EpubExtractor,
    ".pdf": PdfOxideExtractor,
}


class SourceLoader:
    SUPPORTED_EXTS = frozenset(_EXTRACTORS.keys())
    DEFAULT_MAX_BYTES = 50 * 1024 * 1024

    @classmethod
    def detect_conflict(cls, original_filename: str, dst_dir: Path) -> tuple[bool, str]:
        """Return (has_conflict, suggested_stem).

        Conflicts occur when:
        - dst_dir/<stem>.txt exists
        - dst_dir/raw/<original_filename> exists
        Increment suggested_stem through stem_1, stem_2, ... until available.
        """
        stem = Path(original_filename).stem
        normalized = safe_join(dst_dir, f"{stem}.txt")
        raw = safe_join(dst_dir, "raw", original_filename)

        if not normalized.exists() and not raw.exists():
            return False, stem

        idx = 1
        while idx < _MAX_CONFLICT_ITER:
            candidate_stem = f"{stem}_{idx}"
            candidate_norm = safe_join(dst_dir, f"{candidate_stem}.txt")
            candidate_raw = safe_join(dst_dir, "raw", f"{candidate_stem}{Path(original_filename).suffix}")
            if not candidate_norm.exists() and not candidate_raw.exists():
                return True, candidate_stem
            idx += 1
        # Theoretically unreachable (requires 10k same-stem destination files). Raise explicitly rather than looping silently.
        raise ConflictError(
            existing=f"{stem}.txt",
            suggested_name=f"{stem}_{_MAX_CONFLICT_ITER}",
        )

    @classmethod
    def extract(
        cls,
        src: Path,
        *,
        original_filename: str | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> ExtractedText:
        """Normalize uploads to UTF-8 text without writing; format/size validation matches :meth:`load`.

        Used to register episode source: write text to episode files, not original filenames under ``source/``,
        without original-format backups.
        """
        original_filename = original_filename or src.name
        ext = Path(original_filename).suffix.lower()
        if ext not in cls.SUPPORTED_EXTS:
            raise UnsupportedFormatError(ext=ext)
        size = src.stat().st_size
        if size > max_bytes:
            raise FileSizeExceededError(filename=original_filename, size_bytes=size, limit_bytes=max_bytes)
        return _EXTRACTORS[ext]().extract(src)

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
        """Normalize uploads to UTF-8 .txt, backing up originals when conversion is not byte-equivalent.

        Args:
            src: temporary path already persisted by upload layer.
            dst_dir: target project source/ directory.
            original_filename: original uploaded filename with extension; defaults to src.name.
            on_conflict: conflict policy: "fail"/"replace"/"rename".
            max_bytes: original byte limit (default 50 MB).

        Returns:
            NormalizeResult: normalized_path always exists; raw_path is non-None only for byte-inequivalent conversion.

        Raises:
            UnsupportedFormatError: extension outside SUPPORTED_EXTS (routes map to HTTP 400).
            FileSizeExceededError: original exceeds max_bytes (HTTP 413).
            ConflictError: on_conflict="fail" and destination name exists (HTTP 409).
            SourceDecodeError: decoding failed (HTTP 422).
            CorruptFileError: corrupted DOCX/EPUB/PDF or scanned PDF (HTTP 422).

        Atomicity: if raw backup fails after normalized .txt writing (disk full / IO errors), this function
        rolls back normalized_path and propagates the original exception; callers need no extra cleanup.

        Concurrency: not thread/process-safe. Multiworker uvicorn callers must ensure dst_dir mutual exclusion.
        """
        original_filename = original_filename or src.name
        ext = Path(original_filename).suffix.lower()

        if ext not in cls.SUPPORTED_EXTS:
            raise UnsupportedFormatError(ext=ext)

        size = src.stat().st_size
        if size > max_bytes:
            raise FileSizeExceededError(filename=original_filename, size_bytes=size, limit_bytes=max_bytes)

        # Conflict negotiation
        has_conflict, suggested_stem = cls.detect_conflict(original_filename, dst_dir)
        target_stem = Path(original_filename).stem
        effective_filename = original_filename
        if has_conflict:
            if on_conflict == "fail":
                raise ConflictError(existing=f"{target_stem}.txt", suggested_name=suggested_stem)
            if on_conflict == "rename":
                target_stem = suggested_stem
                effective_filename = f"{suggested_stem}{ext}"
            # on_conflict == "replace" → retain the stem and overwrite. Callers first trash old files and same-stem originals
            # (lib.episode.source_trash), otherwise original-format downloads point to pre-overwrite content.

        extracted = _EXTRACTORS[ext]().extract(src)
        normalized_path = safe_join(dst_dir, f"{target_stem}.txt")
        normalized_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_path.write_text(extracted.text, encoding="utf-8")

        try:
            raw_path = cls._maybe_backup_raw(
                src=src,
                ext=ext,
                extracted=extracted,
                dst_dir=dst_dir,
                effective_filename=effective_filename,
            )
        except Exception:
            # Raw-backup failure: roll back written normalized output, preventing orphan files from misleading detect_conflict
            # hard-delete: rollback -- remove destination normalized text on original-backup failure; replace does not restore overwritten content
            normalized_path.unlink(missing_ok=True)
            raise

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
        # Skip backups only for byte-equivalent normalized .txt (plain UTF-8 without BOM).
        # Any conversion (BOM stripping, GBK→UTF-8, docx/epub/pdf extraction) counts as lossy,
        # retaining raw originals for frontend original-format downloads and QA replay.
        if ext in {".txt", ".md"} and extracted.used_encoding == "utf-8":
            return None
        raw_dir = safe_join(dst_dir, "raw")
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = safe_join(raw_dir, effective_filename)
        shutil.copyfile(src, raw_path)
        return raw_path
