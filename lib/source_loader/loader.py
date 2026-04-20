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

_MAX_CONFLICT_ITER = 10000

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
        while idx < _MAX_CONFLICT_ITER:
            candidate_stem = f"{stem}_{idx}"
            candidate_norm = dst_dir / f"{candidate_stem}.txt"
            candidate_raw = dst_dir / "raw" / f"{candidate_stem}{Path(original_filename).suffix}"
            if not candidate_norm.exists() and not candidate_raw.exists():
                return True, candidate_stem
            idx += 1
        # 理论上不可达（用户需在 dst 造 10k 同 stem 文件）。明确抛错而非静默死循环。
        raise ConflictError(
            existing=f"{stem}.txt",
            suggested_name=f"{stem}_{_MAX_CONFLICT_ITER}",
        )

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
        """规范化上传文件为 UTF-8 .txt 并按"决策 7"备份原始字节。

        Args:
            src: 临时文件路径（上传层已落盘）。
            dst_dir: 目标项目的 source/ 目录。
            original_filename: 用户上传时的原始文件名（含扩展名）。默认取 src.name。
            on_conflict: 冲突策略 — "fail"/"replace"/"rename"。
            max_bytes: 原始文件字节上限（默认 50 MB）。

        Returns:
            NormalizeResult：normalized_path 必定存在；raw_path 仅当字节非等价时非 None。

        Raises:
            UnsupportedFormatError: 扩展名不在 SUPPORTED_EXTS（路由层映射到 HTTP 400）。
            FileSizeExceededError: 原始文件超过 max_bytes（HTTP 413）。
            ConflictError: on_conflict="fail" 且 dst_dir 已存在同名（HTTP 409）。
            SourceDecodeError: 文本解码失败（HTTP 422）。
            CorruptFileError: DOCX/EPUB/PDF 损坏或为扫描件（HTTP 422）。

        原子性：本函数不保证原子性。若 extract 成功、normalized .txt 已写入、随后
        raw 备份失败，调用方（Task 11 路由）需在 except 分支清理 normalized_path，
        否则下次同名上传的 detect_conflict 会给出误导性建议。

        并发：非线程/进程安全。多进程 uvicorn worker 下调用方需保证 dst_dir 互斥。
        """
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
        # 决策 7：仅当 normalized .txt 与原始字节等价时跳过备份（纯 UTF-8 无 BOM）。
        # 任何编码转换（BOM 剥离、GBK→UTF-8、docx/epub/pdf 解析）都视为 lossy，
        # 需保留 raw 以支持前端"下载原始格式"按钮与 QA 回放。
        if ext in {".txt", ".md"} and extracted.used_encoding == "utf-8":
            return None
        raw_dir = dst_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / effective_filename
        shutil.copyfile(src, raw_path)
        return raw_path
