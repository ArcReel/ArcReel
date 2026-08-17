"""Persistent record of a failed project schema migration.

A project whose migration chain (including artifact backfill) did not finish is
a blocking project-level problem: its production status, its production plan and
every generation entry report the same failure until it is repaired and retried.
The record lives beside ``project.json`` so the verdict survives restarts and is
readable by any consumer without a database round trip — the startup runner is
the only writer, plus the agent-facing retry tool.

Absence of the record means "not blocked". A project that is merely older than
the current schema and has never been offered to the runner is not reported as
broken; only an actual failed attempt writes the record.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.artifact_manifest import ArtifactManifestError

logger = logging.getLogger(__name__)

MIGRATION_FAILURE_FILENAME = ".migration_failure.json"

MIGRATION_FAILURE_CODE = "project_migration_failed"
"""Stable code shared by the workflow blocker, the plan problem and the REST refusal."""

RETRY_MIGRATION_ACTION = "retry_project_migration"
"""``next_action.type`` and the name of the MCP tool that reruns the chain."""


class ProjectMigrationError(ArtifactManifestError, ValueError):
    """Migration preflight rejected a project at a location it can name.

    Inherits both ancestries on purpose: every existing handler around the
    activation and archive-import paths already catches ``ArtifactManifestError``
    (a ``RuntimeError``) or ``ValueError``, so attaching location facts to a
    rejection never changes which handler sees it.
    """

    def __init__(self, violation: str, *, episode: int | None = None, file: str | None = None) -> None:
        super().__init__(violation)
        self.violation = violation
        self.episode = episode
        self.file = file


class MigrationFailureDetail(BaseModel):
    """One named violation: which episode, which file, what was wrong."""

    model_config = ConfigDict(extra="forbid")

    episode: int | None = None
    file: str | None = None
    violation: str


class MigrationFailureRecord(BaseModel):
    """The persisted verdict for one project's last migration attempt."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    """The version the project was stuck on when the attempt failed."""
    failed_at: str
    reason: str
    """The failure message exactly as raised — surfaced to the user unchanged."""
    details: list[MigrationFailureDetail] = Field(default_factory=list)


def migration_failure_path(project_dir: Path) -> Path:
    return project_dir / MIGRATION_FAILURE_FILENAME


def migration_failure_details(exc: BaseException) -> list[MigrationFailureDetail]:
    """Project one exception onto the structured detail list.

    Only :class:`ProjectMigrationError` carries machine-readable location facts;
    anything else degrades to a single detail holding the raw message, which is
    still enough for the agent to read but not to navigate.
    """

    if isinstance(exc, ProjectMigrationError):
        return [MigrationFailureDetail(episode=exc.episode, file=exc.file, violation=exc.violation)]
    return [MigrationFailureDetail(violation=str(exc))]


def record_migration_failure(
    project_dir: Path,
    exc: BaseException,
    *,
    schema_version: int,
) -> MigrationFailureRecord:
    """Persist the verdict for a failed attempt, replacing any earlier one."""

    record = MigrationFailureRecord(
        schema_version=schema_version,
        failed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        reason=str(exc),
        details=migration_failure_details(exc),
    )
    _write_atomically(migration_failure_path(project_dir), record)
    return record


def clear_migration_failure(project_dir: Path) -> None:
    """Drop the verdict once the chain completes — the project is no longer blocked."""

    path = migration_failure_path(project_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("无法清除迁移失败记录：%s（%s）", path, exc)


def load_migration_failure(project_dir: Path) -> MigrationFailureRecord | None:
    """Read the verdict, or ``None`` when the project is not blocked.

    A record that cannot be parsed still means "this project failed to migrate":
    it degrades to a minimal record rather than silently unblocking generation.
    """

    path = migration_failure_path(project_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("无法读取迁移失败记录：%s（%s）", path, exc)
        return _unreadable_record(str(exc))
    try:
        return MigrationFailureRecord.model_validate_json(raw)
    except ValueError as exc:
        logger.warning("迁移失败记录损坏：%s（%s）", path, exc)
        return _unreadable_record(str(exc))


def _unreadable_record(detail: str) -> MigrationFailureRecord:
    reason = f"migration failure record is unreadable: {detail}"
    return MigrationFailureRecord(
        schema_version=-1,
        failed_at="",
        reason=reason,
        details=[MigrationFailureDetail(file=MIGRATION_FAILURE_FILENAME, violation=reason)],
    )


def _write_atomically(path: Path, record: MigrationFailureRecord) -> None:
    payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except OSError as exc:
        # Without the record on disk the project is not blocked anywhere, so a
        # failed write is louder than the migration failure it was recording:
        # generation stays open on data the migration already refused.
        logger.error("无法写入迁移失败记录，该项目不会被阻断：%s（%s）", path, exc)
        Path(tmp_name).unlink(missing_ok=True)


__all__ = [
    "MIGRATION_FAILURE_CODE",
    "MIGRATION_FAILURE_FILENAME",
    "RETRY_MIGRATION_ACTION",
    "MigrationFailureDetail",
    "MigrationFailureRecord",
    "ProjectMigrationError",
    "clear_migration_failure",
    "load_migration_failure",
    "migration_failure_details",
    "migration_failure_path",
    "record_migration_failure",
]
