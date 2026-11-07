import asyncio
from pathlib import Path

from server.app import _migrate_source_encoding_on_startup


def test_startup_migration_creates_marker_after_run(tmp_path: Path):
    project = tmp_path / "p1"
    (project / "source").mkdir(parents=True)
    (project / "project.json").write_text("{}")
    (project / "source" / "n.txt").write_bytes(("第一章\n" * 30).encode("gbk"))

    summary = asyncio.run(_migrate_source_encoding_on_startup(tmp_path))

    marker = project / ".arcreel" / "source_encoding_migrated"
    assert marker.exists()
    assert "p1" in summary  # Return per-project summaries.


def test_startup_migration_skips_already_marked(tmp_path: Path):
    project = tmp_path / "p1"
    (project / "source").mkdir(parents=True)
    (project / "project.json").write_text("{}")
    bad = project / "source" / "n.txt"
    bad.write_bytes(("第一章\n" * 30).encode("gbk"))
    marker_dir = project / ".arcreel"
    marker_dir.mkdir()
    (marker_dir / "source_encoding_migrated").touch()

    asyncio.run(_migrate_source_encoding_on_startup(tmp_path))
    # Files remain GBK without rewrites.
    assert bad.read_bytes().startswith("第一章".encode("gbk"))


def test_startup_migration_isolates_project_failures(tmp_path: Path, monkeypatch):
    import random

    good = tmp_path / "good"
    (good / "source").mkdir(parents=True)
    (good / "project.json").write_text("{}")
    (good / "source" / "ok.txt").write_text("已是 UTF-8", encoding="utf-8")

    bad = tmp_path / "bad"
    (bad / "source").mkdir(parents=True)
    (bad / "project.json").write_text("{}")
    (bad / "source" / "broken.txt").write_bytes(random.Random(42).randbytes(4000))

    # Decode failures in bad projects must not raise from migration itself, only record errors.log.
    summary = asyncio.run(_migrate_source_encoding_on_startup(tmp_path))
    assert (good / ".arcreel" / "source_encoding_migrated").exists()
    assert (bad / ".arcreel" / "source_encoding_migrated").exists()
    assert (bad / ".arcreel" / "migration_errors.log").exists()
    assert "good" in summary
    assert "bad" in summary
