"""Migration runner: version detection, idempotency, error isolation, and backup cleanup."""

import json
import time
from pathlib import Path

import pytest

from lib.project.project_migrations.runner import (
    CURRENT_SCHEMA_VERSION,
    cleanup_stale_backups,
    migrate_project_dir,
    run_project_migrations,
)


@pytest.fixture
def tmp_projects(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def _write_project(root: Path, name: str, data: dict) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def test_skip_already_current(tmp_projects: Path):
    _write_project(tmp_projects, "p1", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "p1"})
    summary = run_project_migrations(tmp_projects)
    assert summary.migrated == []
    assert summary.skipped == ["p1"]


def test_migrate_bumps_through_all_versions(tmp_projects: Path, monkeypatch):
    """The runner applies each step through CURRENT_SCHEMA_VERSION (v0→v1→v2→v3 here)."""
    _write_project(tmp_projects, "p1", {"name": "p1"})  # No schema_version.

    called: list[int] = []

    def _fake(from_version: int):
        def migrator(project_dir: Path) -> None:
            called.append(from_version)
            data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            data["schema_version"] = from_version + 1
            (project_dir / "project.json").write_text(json.dumps(data), encoding="utf-8")

        return migrator

    monkeypatch.setattr(
        "lib.project.project_migrations.runner.MIGRATORS",
        {v: _fake(v) for v in range(CURRENT_SCHEMA_VERSION)},
    )

    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    assert called == list(range(CURRENT_SCHEMA_VERSION))
    data = json.loads((tmp_projects / "p1" / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION


def test_real_v1_to_v2_normalizes_via_runner(tmp_projects: Path):
    """With real MIGRATORS, the runner normalizes legacy provider names and upgrades v1 projects to the latest version."""
    _write_project(
        tmp_projects,
        "p1",
        {"schema_version": 1, "video_backend": "seedance/x", "image_backend": "vertex/y"},
    )
    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    data = json.loads((tmp_projects / "p1" / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION
    assert data["video_backend"] == "ark/x"
    assert data["image_provider_t2i"] == "gemini-vertex/y"
    assert "image_backend" not in data


def test_real_v2_to_v3_stamps_version_via_runner(tmp_projects: Path):
    """With real MIGRATORS, the runner stamps v2 projects, creating versioned backups without changing episodes."""
    novel = "第一集的正文内容。第二集还没拆出来的余文。"
    p = _write_project(
        tmp_projects,
        "p1",
        {
            "schema_version": 2,
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    source = p / "source"
    source.mkdir()
    (source / "novel.txt").write_text(novel, encoding="utf-8")
    (source / "episode_1.txt").write_text("第一集的正文内容。", encoding="utf-8")
    (source / "_remaining.txt").write_text("第二集还没拆出来的余文。", encoding="utf-8")

    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    data = json.loads((p / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION
    # test_project_migration_v2_v3 covers stamping details; only verify MIGRATORS registration
    # and surrounding runner behavior here.
    # Legacy entries without source ranges but with episode files and a whole-book source under source/ are classified by v15→v16 as whole-source excerpts.
    assert data["episodes"] == [
        {"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json", "source_origin": "whole_source"}
    ]
    assert data["whole_source_files"] == [{"source_file": "source/novel.txt"}]
    assert "planning_cursor" not in data
    assert (source / "_remaining.txt").exists()  # Migration changes no files under source/.
    assert list(p.glob("project.json.bak.v2-*"))  # The runner automatically creates versioned backups.


def test_migrate_project_dir_single_project(tmp_projects: Path):
    """Single-project entry for import reuse: run the full chain from v1 to v2, normalizing legacy names."""
    d = _write_project(tmp_projects, "imported", {"schema_version": 1, "image_backend": "vertex/y"})
    assert migrate_project_dir(d) is True
    data = json.loads((d / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION
    assert data["image_provider_t2i"] == "gemini-vertex/y"
    assert "image_backend" not in data
    # Idempotency: latest-version projects return False without changes on repeated calls.
    assert migrate_project_dir(d) is False


def test_skip_non_project_dirs(tmp_projects: Path):
    (tmp_projects / "global_assets").mkdir()
    (tmp_projects / "global_assets" / "keep.txt").write_text("x", encoding="utf-8")
    _write_project(tmp_projects, "p1", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "p1"})
    summary = run_project_migrations(tmp_projects)
    assert "global_assets" not in summary.skipped
    assert "global_assets" not in summary.migrated


def test_corrupted_schema_version_skipped_not_abort(tmp_projects: Path):
    """Skip unparseable schema_version as corruption: no version stamping and no interruption of other project migrations."""
    _write_project(tmp_projects, "broken", {"schema_version": "corrupted"})
    _write_project(tmp_projects, "ok", {"schema_version": 1, "video_backend": "seedance/x"})

    summary = run_project_migrations(tmp_projects)

    assert "broken" not in summary.migrated + summary.failed + summary.skipped
    assert "ok" in summary.migrated
    data = json.loads((tmp_projects / "broken" / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == "corrupted"  # Preserve unchanged for manual repair.


def test_numeric_string_schema_version_is_not_classified_as_current(tmp_projects: Path):
    """Numeric strings are also corrupt persisted versions; do not mistake them for completed migrations."""
    _write_project(tmp_projects, "numeric-string", {"schema_version": str(CURRENT_SCHEMA_VERSION)})

    summary = run_project_migrations(tmp_projects)

    assert "numeric-string" not in summary.migrated + summary.failed + summary.skipped


def test_falsy_or_bool_schema_version_skipped_not_v0(tmp_projects: Path):
    """Skip unparseable versions such as empty strings / bool as corruption, not v0 needing migration."""
    for name, bad in [("empty", ""), ("bool-true", True), ("bool-false", False)]:
        _write_project(tmp_projects, name, {"schema_version": bad})

    summary = run_project_migrations(tmp_projects)

    for name in ("empty", "bool-true", "bool-false"):
        assert name not in summary.migrated + summary.failed + summary.skipped


def test_explicit_null_schema_version_treated_as_v0(tmp_projects: Path):
    """Explicit null equals a missing field (v0) and follows the full migration chain."""
    _write_project(tmp_projects, "p1", {"schema_version": None, "episodes": []})
    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    data = json.loads((tmp_projects / "p1" / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION


def test_error_isolated_not_abort(tmp_projects: Path, monkeypatch):
    _write_project(tmp_projects, "broken", {"name": "broken"})
    _write_project(tmp_projects, "ok", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "ok"})

    def bad(_d):
        raise RuntimeError("boom")

    monkeypatch.setattr("lib.project.project_migrations.runner.MIGRATORS", {0: bad})
    summary = run_project_migrations(tmp_projects)
    assert "broken" in summary.failed
    assert "ok" in summary.skipped


def test_cleanup_old_backups(tmp_projects: Path):
    p = _write_project(
        tmp_projects,
        "p1",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        },
    )
    old = p / "project.json.bak.v0-100000000"
    new = p / "project.json.bak.v0-9999999999"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    old_script = p / "scripts" / "episode_1.json.bak.v7-100000000"
    new_manifest = p / ".arcreel_artifacts.json.bak.v7-9999999999"
    old_script.parent.mkdir()
    old_script.write_text("old-script", encoding="utf-8")
    new_manifest.write_text("new-manifest", encoding="utf-8")
    user_source = p / "source" / "novel.bak.v7-final.txt"
    user_source.parent.mkdir()
    user_source.write_text("user-owned", encoding="utf-8")

    old_clues_dir = p / "clues.bak.v0-100000000"
    new_clues_dir = p / "clues.bak.v0-9999999999"
    old_clues_dir.mkdir()
    (old_clues_dir / "a.png").write_bytes(b"x")
    new_clues_dir.mkdir()

    # Control mtime: set old file/directory timestamps to 8 days ago.
    eight_days_ago = time.time() - 8 * 86400
    import os

    os.utime(old, (eight_days_ago, eight_days_ago))
    os.utime(old_script, (eight_days_ago, eight_days_ago))
    os.utime(user_source, (eight_days_ago, eight_days_ago))
    os.utime(old_clues_dir, (eight_days_ago, eight_days_ago))

    cleanup_stale_backups(tmp_projects, max_age_days=7)
    assert not old.exists()
    assert new.exists()
    assert not old_script.exists()
    assert new_manifest.exists()
    assert user_source.read_text(encoding="utf-8") == "user-owned"
    assert not old_clues_dir.exists()
    assert new_clues_dir.exists()


def test_cleanup_retains_v7_recovery_backups_until_schema_promotion_succeeds(tmp_projects: Path) -> None:
    import os

    project_dir = _write_project(
        tmp_projects,
        "p1",
        {
            "schema_version": 7,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        },
    )
    backups = [
        project_dir / "project.json.bak.v7-100000000",
        project_dir / "scripts" / "episode_1.json.bak.v7-100000000",
        project_dir / ".arcreel_artifacts.json.bak.v7-100000000",
    ]
    expired = time.time() - 8 * 86400
    for backup in backups:
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text("recovery", encoding="utf-8")
        os.utime(backup, (expired, expired))

    cleanup_stale_backups(tmp_projects, max_age_days=7)

    assert all(backup.exists() for backup in backups)


def test_hardlink_backup_clues_creates_mirror(tmp_projects: Path, monkeypatch):
    """Before v0→v1, hard-link clues/ backups into clues.bak.v0-<ts>/."""
    p = _write_project(tmp_projects, "p1", {"name": "p1"})  # v0
    (p / "clues").mkdir()
    (p / "clues" / "玉佩.png").write_bytes(b"prop-image")
    (p / "clues" / "nested").mkdir()
    (p / "clues" / "nested" / "deep.png").write_bytes(b"deep")

    def noop_migrator(project_dir: Path) -> None:
        data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        data["schema_version"] = 1
        (project_dir / "project.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr("lib.project.project_migrations.runner.MIGRATORS", {0: noop_migrator})
    run_project_migrations(tmp_projects)

    backups = list(p.glob("clues.bak.v0-*"))
    assert len(backups) == 1
    bak = backups[0]
    assert (bak / "玉佩.png").read_bytes() == b"prop-image"
    assert (bak / "nested" / "deep.png").read_bytes() == b"deep"


def test_cleanup_reclaims_every_backup_the_draft_rename_leaves_behind(tmp_projects: Path) -> None:
    """v9→v10 backs up every renamed draft and artifact manifest; cleanup must cover each or they remain forever."""
    import os

    project_dir = _write_project(
        tmp_projects,
        "p1",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        },
    )
    drafts_dir = project_dir / "drafts" / "episode_1"
    drafts_dir.mkdir(parents=True)
    backups = [
        drafts_dir / "step1_normalized_script.json.bak.v9-100000000",
        drafts_dir / "step1_segments.invalid.json.bak.v9-100000000",
        drafts_dir / "step1_reference_units.md.bak.v9-100000000",
        drafts_dir / "script_plan_segments.json.bak.v7-100000000",
        project_dir / ".arcreel_artifacts.json.bak.v9-100000000",
    ]
    expired = time.time() - 8 * 86400
    for backup in backups:
        backup.write_text("stale", encoding="utf-8")
        os.utime(backup, (expired, expired))

    cleanup_stale_backups(tmp_projects, max_age_days=7)

    assert [backup for backup in backups if backup.exists()] == []


def test_failed_retry_does_not_duplicate_an_identical_project_backup(tmp_projects: Path, monkeypatch):
    """Keep one project.json backup per starting version and identical content; create another only when content changes."""
    p = _write_project(tmp_projects, "p1", {"schema_version": 2, "name": "p1"})

    def bad(_d):
        raise RuntimeError("boom")

    monkeypatch.setattr("lib.project.project_migrations.runner.MIGRATORS", {2: bad})
    for _ in range(3):
        with pytest.raises(RuntimeError):
            migrate_project_dir(p)
    assert len(list(p.glob("project.json.bak.v2-*"))) == 1

    (p / "project.json").write_text(json.dumps({"schema_version": 2, "name": "p1", "title": "改过"}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        migrate_project_dir(p)
    backups = sorted(p.glob("project.json.bak.v2-*"))
    assert len(backups) == 2
    assert {b.read_bytes() for b in backups} == {
        json.dumps({"schema_version": 2, "name": "p1"}, ensure_ascii=False).encode(),
        json.dumps({"schema_version": 2, "name": "p1", "title": "改过"}).encode(),
    }
