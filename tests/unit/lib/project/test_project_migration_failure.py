"""迁移失败判定记录的路径口径：只用项目内相对路径，且仍能定位问题文件。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.project.project_migration_failure import (
    ProjectMigrationError,
    load_migration_failure,
    migration_failure_path,
    record_migration_failure,
)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project_dir = tmp_path / "projects" / "demo"
    script = project_dir / "scripts" / "episode_1.json"
    script.parent.mkdir(parents=True)
    script.write_text("{}", encoding="utf-8")
    return project_dir, script


def test_recorded_verdict_names_the_offending_file_relative_to_the_project(tmp_path: Path) -> None:
    project_dir, script = _project(tmp_path)

    record = record_migration_failure(
        project_dir,
        ProjectMigrationError(f"cannot parse {script}", episode=1, file=str(script)),
        schema_version=7,
    )

    persisted = json.loads(migration_failure_path(project_dir).read_text(encoding="utf-8"))
    assert persisted == record.model_dump(mode="json")
    assert record.reason == "cannot parse scripts/episode_1.json"
    [detail] = record.details
    assert detail.file == "scripts/episode_1.json"
    assert detail.violation == "cannot parse scripts/episode_1.json"
    assert (project_dir / detail.file).is_file()


def test_legacy_verdict_with_absolute_paths_is_read_project_relative(tmp_path: Path) -> None:
    project_dir, script = _project(tmp_path)
    reason = f"cannot read {script.resolve()}; sibling {project_dir}-2/project.json untouched"
    migration_failure_path(project_dir).write_text(
        json.dumps(
            {
                "schema_version": 7,
                "failed_at": "2026-01-01T00:00:00",
                "reason": reason,
                "details": [{"file": str(script), "violation": reason}],
            }
        ),
        encoding="utf-8",
    )

    record = load_migration_failure(project_dir)

    assert record is not None
    expected = f"cannot read scripts/episode_1.json; sibling {project_dir}-2/project.json untouched"
    assert record.reason == expected
    assert record.details[0].file == "scripts/episode_1.json"
    assert record.details[0].violation == expected
