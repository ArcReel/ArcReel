"""项目最近活动时间：取账本里的业务时间戳与内容文件修改时间中最晚的一个。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from lib.project.project_activity import ACTIVITY_FILENAME
from server.services.project.project_activity import project_last_activity_at

OLD = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
NEW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _write(path: Path, when: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.utime(path, (when.timestamp(), when.timestamp()))


def _ledger(updated_at: datetime | str) -> dict:
    stamp = updated_at if isinstance(updated_at, str) else updated_at.isoformat()
    return {"metadata": {"updated_at": stamp}}


def test_takes_the_latest_of_ledger_timestamps_and_content_files(tmp_path):
    _write(tmp_path / "characters" / "hero.png", OLD)

    assert project_last_activity_at(tmp_path, _ledger(OLD), [_ledger(NEW)]) == NEW
    assert project_last_activity_at(tmp_path, _ledger(NEW), [_ledger(OLD)]) == NEW

    _write(tmp_path / "videos" / "E1S01.mp4", NEW)
    assert project_last_activity_at(tmp_path, _ledger(OLD), [_ledger(OLD)]) == NEW


def test_files_rewritten_outside_creative_work_do_not_count(tmp_path):
    # 迁移与配置同步会统一改写这些文件；按它们算，所有项目都会显示为刚刚更新。
    _write(tmp_path / "source" / "chapter.txt", OLD)
    for rewritten in (
        "project.json",
        "scripts/episode_1.json",
        "project.json.bak.v16-1790995807",
        "project.lock",
        ".arcreel_artifacts.json",
        ".claude/agents/review.md",
        "CLAUDE.md",
    ):
        _write(tmp_path / rewritten, NEW)

    assert project_last_activity_at(tmp_path, _ledger(OLD), []) == OLD


def test_naive_ledger_timestamps_are_local_time(tmp_path):
    local = datetime(2026, 5, 27, 10, 0)

    assert project_last_activity_at(tmp_path, _ledger(local.isoformat()), []) == local.astimezone(UTC)


def test_no_time_available(tmp_path):
    assert project_last_activity_at(tmp_path, {"metadata": {"updated_at": "not-a-time"}}, [{}]) is None


def test_project_memory_counts_but_internal_state_does_not(tmp_path):
    _write(tmp_path / ".arcreel" / "memory" / "MEMORY.md", NEW)
    _write(tmp_path / ".arcreel" / "memory" / ".temporary.md", NEW)
    _write(tmp_path / ".arcreel" / "session.log", NEW)
    assert project_last_activity_at(tmp_path, _ledger(OLD), []) == NEW
    (tmp_path / ".arcreel" / "memory" / "MEMORY.md").unlink()
    assert project_last_activity_at(tmp_path, _ledger(OLD), []) == OLD


def test_activity_recorded_by_business_writes_counts(tmp_path):
    # 草稿等 JSON 的改写与删除只在活动账本里留下时刻。
    _write(tmp_path / "source" / "chapter.txt", OLD)
    (tmp_path / ACTIVITY_FILENAME).write_text(NEW.isoformat(), encoding="utf-8")

    assert project_last_activity_at(tmp_path, _ledger(OLD), []) == NEW

    (tmp_path / ACTIVITY_FILENAME).write_text(OLD.isoformat(), encoding="utf-8")
    assert project_last_activity_at(tmp_path, _ledger(NEW), []) == NEW
