import random
from pathlib import Path

from lib.script.source_loader.migration import migrate_project_source_encoding


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
    # Back up original files under source/raw/.
    assert (project / "source" / "raw" / "novel.txt").exists()


def test_migration_skips_already_utf8(tmp_path: Path):
    project = _make_project(tmp_path, "p2")
    target = project / "source" / "novel.txt"
    target.write_text("已是 UTF-8", encoding="utf-8")

    summary = migrate_project_source_encoding(project)
    assert summary.migrated == []
    assert summary.skipped == [target.name]
    # Original content unchanged.
    assert target.read_text(encoding="utf-8") == "已是 UTF-8"
    # Create no raw backup.
    assert not (project / "source" / "raw").exists()


def test_migration_records_failures_without_raising(tmp_path: Path):
    project = _make_project(tmp_path, "p3")
    bad = project / "source" / "garbage.txt"
    # Fixed-seed pseudorandom bytes make gb18030 fallback yield >5% \ufffd, triggering SourceDecodeError.
    rng = random.Random(42)
    original_bytes = bytes(rng.randint(0, 255) for _ in range(4000))
    bad.write_bytes(original_bytes)

    summary = migrate_project_source_encoding(project)
    assert summary.failed == [bad.name]
    # File unchanged.
    assert bad.read_bytes() == original_bytes


def test_migration_no_source_dir_is_noop(tmp_path: Path):
    project = tmp_path / "empty_project"
    project.mkdir()
    summary = migrate_project_source_encoding(project)
    assert summary.migrated == []
    assert summary.skipped == []
    assert summary.failed == []
