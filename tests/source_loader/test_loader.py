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


def test_detect_conflict_skips_occupied_indices(tmp_path: Path):
    """stem_1 已占用时 suggested_stem 递增到 stem_2，锁 Task 11 预期的递增语义。"""
    src = tmp_path / "source"
    src.mkdir()
    (src / "novel.txt").write_text("", encoding="utf-8")
    (src / "novel_1.txt").write_text("", encoding="utf-8")
    has_conflict, suggested = SourceLoader.detect_conflict("novel.epub", src)
    assert has_conflict is True
    assert suggested == "novel_2"


def test_load_rolls_back_normalized_when_raw_backup_fails(tmp_path: Path, monkeypatch):
    """raw 备份失败时 normalized .txt 被回滚，不留孤儿文件（原子性契约）。"""
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "old.txt"
    src.write_bytes(("第一章\n" * 30).encode("gbk"))  # 触发 raw 备份分支

    import lib.source_loader.loader as loader_mod

    def _boom(*_a, **_k):
        raise OSError("simulated disk full")

    monkeypatch.setattr(loader_mod.shutil, "copyfile", _boom)

    with pytest.raises(OSError):
        SourceLoader.load(src, project_source, original_filename="old.txt")

    # 原子性：normalized .txt 应已回滚，raw/ 也空
    assert not (project_source / "old.txt").exists()
    assert not (project_source / "raw" / "old.txt").exists()


def test_load_replace_purges_stale_raw_when_new_upload_has_no_raw(tmp_path: Path):
    """replace 冲突 + 新上传无 raw（纯 UTF-8 .txt）时，旧 raw 备份被清理。"""
    project_source = tmp_path / "source"
    raw_dir = project_source / "raw"
    raw_dir.mkdir(parents=True)
    # 旧版遗留：normalized .txt + raw .docx 备份（模拟先前用 .docx 上传过同名项目）
    (project_source / "novel.txt").write_text("旧内容", encoding="utf-8")
    (raw_dir / "novel.docx").write_bytes(b"stale docx bytes")

    # 新上传：纯 UTF-8 .txt → _maybe_backup_raw 返回 None（不产生新 raw）
    src = tmp_path / "novel.txt"
    src.write_bytes("全新纯 UTF-8 内容".encode())

    result = SourceLoader.load(src, project_source, original_filename="novel.txt", on_conflict="replace")

    assert result.raw_path is None
    assert result.normalized_path.read_text(encoding="utf-8") == "全新纯 UTF-8 内容"
    # 关键：旧 raw/novel.docx 必须被清理，否则前端 📎 链接指向陈旧内容
    assert not (raw_dir / "novel.docx").exists()
