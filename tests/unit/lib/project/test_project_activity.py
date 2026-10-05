"""项目活动账本：写入出口记下的时刻能原样读回，坏账本按没有账本处理。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.project.project_activity import ACTIVITY_FILENAME, record_project_activity, recorded_project_activity


def test_recorded_time_reads_back_as_the_moment_of_recording(tmp_path: Path) -> None:
    before = datetime.now(UTC)
    record_project_activity(tmp_path)
    after = datetime.now(UTC)

    recorded = recorded_project_activity(tmp_path)
    assert recorded is not None
    assert before <= recorded <= after


def test_recording_again_moves_the_time_forward(tmp_path: Path) -> None:
    (tmp_path / ACTIVITY_FILENAME).write_text("2026-03-01T08:00:00+00:00", encoding="utf-8")

    record_project_activity(tmp_path)

    recorded = recorded_project_activity(tmp_path)
    assert recorded is not None
    assert recorded > datetime(2026, 3, 1, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize("content", [b"", b"not-a-time", b"2026-03-01T08:00:00", b"\xff\xfe"])
def test_unreadable_ledger_counts_as_no_ledger(tmp_path: Path, content: bytes) -> None:
    (tmp_path / ACTIVITY_FILENAME).write_bytes(content)

    assert recorded_project_activity(tmp_path) is None


def test_no_ledger(tmp_path: Path) -> None:
    assert recorded_project_activity(tmp_path) is None


def test_failed_recording_does_not_fail_the_business_write(tmp_path: Path) -> None:
    record_project_activity(tmp_path / "deleted-project")

    assert recorded_project_activity(tmp_path / "deleted-project") is None
