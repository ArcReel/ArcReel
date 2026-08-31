import asyncio
from pathlib import Path

from server.app import _migrate_source_encoding_on_startup  # 即将新增的内部函数


def test_startup_migration_creates_marker_after_run(tmp_path: Path):
    project = tmp_path / "p1"
    (project / "source").mkdir(parents=True)
    (project / "source" / "n.txt").write_bytes(("第一章\n" * 30).encode("gbk"))

    summary = asyncio.run(_migrate_source_encoding_on_startup(tmp_path))

    marker = project / ".arcreel" / "source_encoding_migrated"
    assert marker.exists()
    assert "p1" in summary  # 返回每项目的简报


def test_startup_migration_skips_already_marked(tmp_path: Path):
    project = tmp_path / "p1"
    (project / "source").mkdir(parents=True)
    bad = project / "source" / "n.txt"
    bad.write_bytes(("第一章\n" * 30).encode("gbk"))
    marker_dir = project / ".arcreel"
    marker_dir.mkdir()
    (marker_dir / "source_encoding_migrated").touch()

    asyncio.run(_migrate_source_encoding_on_startup(tmp_path))
    # 文件未被重写（仍是 GBK）
    assert bad.read_bytes().startswith("第一章".encode("gbk"))


def test_startup_migration_isolates_project_failures(tmp_path: Path, monkeypatch):
    import random

    good = tmp_path / "good"
    (good / "source").mkdir(parents=True)
    (good / "source" / "ok.txt").write_text("已是 UTF-8", encoding="utf-8")

    bad = tmp_path / "bad"
    (bad / "source").mkdir(parents=True)
    (bad / "source" / "broken.txt").write_bytes(random.Random(42).randbytes(4000))

    # 即使 bad 项目内文件解码失败，迁移函数本身不应抛错（只记录到 errors.log）
    summary = asyncio.run(_migrate_source_encoding_on_startup(tmp_path))
    assert (good / ".arcreel" / "source_encoding_migrated").exists()
    assert (bad / ".arcreel" / "source_encoding_migrated").exists()
    assert (bad / ".arcreel" / "migration_errors.log").exists()
    assert "good" in summary
    assert "bad" in summary
