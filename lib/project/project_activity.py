"""Project activity ledger: business-write exits record the current timestamp after persistence.

Project listings read the latest of ledger, `metadata.updated_at`, and content-file modification times
(`server.services.project.project_activity`). The ledger covers writes invisible to those sources:
drafts/script-plan JSON updates (JSON mtimes cannot distinguish business writes from migrations and
are excluded) and deletion (removing the newest file makes mtimes recede).

The root `.last_activity` contains only one timezone-aware ISO 8601 timestamp. Dotfiles are excluded
from content scans/archive exports; migrations, artifact backfills, and configuration synchronization
do not record activity and therefore never advance timestamps.

Log recording failures only: business writes already persisted, so activity-ledger failure must not fail them."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from lib.infra.json_io import atomic_write_bytes

logger = logging.getLogger(__name__)

ACTIVITY_FILENAME = ".last_activity"


def record_project_activity(project_dir: Path) -> None:
    """Record now as latest project activity using atomic whole-file replacement."""
    stamp = datetime.now(UTC).isoformat()
    try:
        atomic_write_bytes(project_dir / ACTIVITY_FILENAME, stamp.encode("utf-8"))
    except OSError as exc:
        logger.warning("记录项目活动时间失败 dir=%s err=%s", project_dir, exc)


def recorded_project_activity(project_dir: Path) -> datetime | None:
    """Recorded latest activity (UTC); None for absent/unparseable ledgers."""
    try:
        raw = (project_dir / ACTIVITY_FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


__all__ = ["ACTIVITY_FILENAME", "record_project_activity", "recorded_project_activity"]
