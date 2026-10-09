"""v17→v18 迁移：剪辑时间线修订删去 Agent 轮次字段。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from lib.edit_timeline import EditTimelineService
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import cleanup_stale_backups, migrate_project_dir
from tests.legacy_project_shapes import write_legacy_timeline_agent_turn_project


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_schema_17_timeline_with_agent_turn_reads_after_migration(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    timeline_path = project_dir / "edit_timelines" / "episode_1" / "tl-0000abcd.json"
    legacy = _read_json(timeline_path)
    assert _read_json(project_dir / "project.json")["schema_version"] == 17

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_SCHEMA_VERSION
    migrated = _read_json(timeline_path)
    assert migrated == {
        **legacy,
        "revisions": [
            {key: value for key, value in revision.items() if key != "agent_turn"} for revision in legacy["revisions"]
        ],
    }
    assert [path.name for path in timeline_path.parent.glob("tl-0000abcd.json.bak.v17-*")]

    service = EditTimelineService(ProjectManager(tmp_path))
    (summary,) = await service.list_timelines(project_dir.name)
    assert (summary.id, summary.revision, summary.update_summary) == ("tl-0000abcd", 2, "调整音量")
    history = await service.list_revisions(project_dir.name, "tl-0000abcd")
    assert [(item.number, item.author.kind) for item in history.revisions] == [(1, "arcreel_agent"), (2, "creator")]


def test_stale_timeline_backup_is_reclaimed_after_its_timeline_is_deleted(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    timeline_path = project_dir / "edit_timelines" / "episode_1" / "tl-0000abcd.json"
    assert migrate_project_dir(project_dir) is True
    (backup,) = timeline_path.parent.glob("tl-0000abcd.json.bak.v17-*")
    timeline_path.unlink()
    os.utime(backup, (0, 0))

    cleanup_stale_backups(projects_root)

    assert not backup.exists()


def test_symlinked_timeline_directories_outside_project_are_left_untouched(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    episode_dir = project_dir / "edit_timelines" / "episode_1"
    external_dir = tmp_path / "external-timelines"
    episode_dir.rename(external_dir)
    try:
        episode_dir.symlink_to(external_dir, target_is_directory=True)
        (project_dir / "edit_timelines" / "episode_2").mkdir()
        (project_dir / "edit_timelines" / "episode_2" / "tl-linked.json").symlink_to(external_dir / "tl-0000abcd.json")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")
    external_file = external_dir / "tl-0000abcd.json"
    before = external_file.read_bytes()

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_SCHEMA_VERSION
    assert external_file.read_bytes() == before
    assert sorted(path.name for path in external_dir.iterdir()) == ["tl-0000abcd.json"]
    assert not list((project_dir / "edit_timelines").rglob("*.bak.v17-*"))
