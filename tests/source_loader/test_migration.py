import random
from pathlib import Path

from lib.source_loader.migration import migrate_project_source_encoding


def _make_project(tmp_path: Path, name: str) -> Path:
    project_dir = tmp_path / name
    (project_dir / "source").mkdir(parents=True)
    return project_dir


def test_migration_rewrites_non_utf8_txt_in_place(tmp_path: Path):
    project = _make_project(tmp_path, "p1")
    target = project / "source" / "novel.txt"
    target.write_bytes(("第一章\n" * 30).encode("gbk"))

    summary = migrate_project_source_encoding(project)
    assert summary.migrated == [target.name]
    assert summary.failed == []
    assert target.read_text(encoding="utf-8").startswith("第一章")
    # 原文件备份到 source/raw/
    assert (project / "source" / "raw" / "novel.txt").exists()


def test_migration_skips_already_utf8(tmp_path: Path):
    project = _make_project(tmp_path, "p2")
    target = project / "source" / "novel.txt"
    target.write_text("已是 UTF-8", encoding="utf-8")

    summary = migrate_project_source_encoding(project)
    assert summary.migrated == []
    assert summary.skipped == [target.name]
    # 原内容不变
    assert target.read_text(encoding="utf-8") == "已是 UTF-8"
    # 不创建 raw 备份
    assert not (project / "source" / "raw").exists()


def test_migration_records_failures_without_raising(tmp_path: Path):
    project = _make_project(tmp_path, "p3")
    bad = project / "source" / "garbage.txt"
    # 使用固定种子的伪随机字节，确保 gb18030 兜底产生 >5% 的 \ufffd，触发 SourceDecodeError
    rng = random.Random(42)
    original_bytes = bytes(rng.randint(0, 255) for _ in range(4000))
    bad.write_bytes(original_bytes)

    summary = migrate_project_source_encoding(project)
    assert summary.failed == [bad.name]
    # 文件未被改动
    assert bad.read_bytes() == original_bytes


def test_migration_no_source_dir_is_noop(tmp_path: Path):
    project = tmp_path / "empty_project"
    project.mkdir()
    summary = migrate_project_source_encoding(project)
    assert summary.migrated == []
    assert summary.skipped == []
    assert summary.failed == []
