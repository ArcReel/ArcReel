from pathlib import Path

import pytest

from lib.source_loader import SourceLoader
from lib.source_loader.errors import (
    ConflictError,
    FileSizeExceededError,
    UnsupportedFormatError,
)


def test_load_txt_utf8_no_raw_backup(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "novel.txt"
    src.write_bytes("纯 UTF-8 内容".encode())

    result = SourceLoader.load(src, project_source, original_filename="novel.txt")
    assert result.normalized_path == project_source / "novel.txt"
    assert result.normalized_path.read_text(encoding="utf-8") == "纯 UTF-8 内容"
    assert result.raw_path is None
    assert result.used_encoding == "utf-8"
    assert result.original_filename == "novel.txt"


def test_load_gbk_txt_writes_raw_backup(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "old_novel.txt"
    src.write_bytes(("第一章\n" * 30).encode("gbk"))

    result = SourceLoader.load(src, project_source, original_filename="old_novel.txt")
    assert result.normalized_path.read_text(encoding="utf-8").startswith("第一章")
    assert result.raw_path == project_source / "raw" / "old_novel.txt"
    assert result.raw_path.read_bytes().startswith(b"\xb5\xda")  # GBK "第"
    assert result.used_encoding and result.used_encoding.lower() != "utf-8"


def test_load_docx_writes_raw_backup(tmp_path: Path, docx_factory):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = docx_factory(["docx 内容"])

    result = SourceLoader.load(src, project_source, original_filename=src.name)
    assert result.normalized_path == project_source / src.with_suffix(".txt").name
    assert "docx 内容" in result.normalized_path.read_text(encoding="utf-8")
    assert result.raw_path == project_source / "raw" / src.name
    assert result.raw_path.exists()


def test_load_unsupported_format_raises(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "x.doc"
    src.write_bytes(b"binary")
    with pytest.raises(UnsupportedFormatError) as exc_info:
        SourceLoader.load(src, project_source, original_filename="x.doc")
    assert exc_info.value.ext == ".doc"


def test_load_size_limit_raises(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "big.txt"
    src.write_bytes(b"a" * 100)
    with pytest.raises(FileSizeExceededError):
        SourceLoader.load(src, project_source, original_filename="big.txt", max_bytes=50)


def test_detect_conflict_finds_existing_normalized(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    (project_source / "novel.txt").write_text("已存在", encoding="utf-8")

    has_conflict, suggested = SourceLoader.detect_conflict("novel.epub", project_source)
    assert has_conflict is True
    assert suggested == "novel_1"


def test_detect_conflict_finds_existing_raw(tmp_path: Path):
    project_source = tmp_path / "source"
    (project_source / "raw").mkdir(parents=True)
    (project_source / "raw" / "novel.epub").write_bytes(b"raw")

    has_conflict, suggested = SourceLoader.detect_conflict("novel.epub", project_source)
    assert has_conflict is True
    assert suggested == "novel_1"


def test_detect_conflict_no_conflict(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    has_conflict, suggested = SourceLoader.detect_conflict("novel.epub", project_source)
    assert has_conflict is False
    assert suggested == "novel"


def test_load_on_conflict_fail_raises(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    (project_source / "novel.txt").write_text("已存在", encoding="utf-8")

    src = tmp_path / "novel.txt"
    src.write_bytes("新内容".encode())
    with pytest.raises(ConflictError) as exc_info:
        SourceLoader.load(src, project_source, original_filename="novel.txt", on_conflict="fail")
    assert exc_info.value.suggested_name == "novel_1"


def test_load_on_conflict_replace_overwrites(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    (project_source / "novel.txt").write_text("旧内容", encoding="utf-8")

    src = tmp_path / "novel.txt"
    src.write_bytes("新内容".encode())
    result = SourceLoader.load(src, project_source, original_filename="novel.txt", on_conflict="replace")
    assert result.normalized_path.read_text(encoding="utf-8") == "新内容"


def test_load_on_conflict_rename_uses_suggested(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    (project_source / "novel.txt").write_text("已存在", encoding="utf-8")

    src = tmp_path / "novel.txt"
    src.write_bytes("新内容".encode())
    result = SourceLoader.load(src, project_source, original_filename="novel.txt", on_conflict="rename")
    assert result.normalized_path == project_source / "novel_1.txt"
    assert result.original_filename == "novel_1.txt"


def test_load_chapter_count_propagates_from_epub(tmp_path: Path, epub_factory):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = epub_factory([("第一章", "正文1"), ("第二章", "正文2")])
    result = SourceLoader.load(src, project_source, original_filename=src.name)
    assert result.chapter_count == 2
