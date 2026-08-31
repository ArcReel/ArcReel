"""Persistent record of a failed project schema migration.

A project whose migration chain (including artifact backfill) did not finish is
a blocking project-level problem: its production status, its production plan and
every generation entry report the same failure until it is repaired and retried.
The record lives beside ``project.json`` so the verdict survives restarts and is
readable by any consumer without a database round trip — the startup runner is
the only writer, plus the agent-facing retry tool.

Absence of the record does not mean "not blocked": a project short of the
current schema has no backfilled artifact claims to read either way, so
:func:`load_migration_verdict` reports it blocked on the schema discriminator
alone. Only an actual failed attempt writes a record, and that record wins
because it names the exact inputs to repair.
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
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION, parse_project_schema_version

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
    """When the attempt failed; empty when the verdict rests on no attempt at all."""
    reason: str
    """The failure message exactly as raised — surfaced to the user unchanged."""
    details: list[MigrationFailureDetail] = Field(default_factory=list)


def migration_failure_path(project_dir: Path) -> Path:
    return project_dir / MIGRATION_FAILURE_FILENAME


def _readable_schema_version(project_dir: Path) -> int | None:
    """Return the project's schema version, or ``None`` when it cannot be read.

    A directory without a parseable ``project.json`` is not a project this guard
    can rule on; whoever resolved the name owns that verdict.
    """

    try:
        data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return parse_project_schema_version(data)
    except ValueError:
        return None


def pending_migration_record(schema_version: int) -> MigrationFailureRecord:
    """The verdict for a project the migration chain has not yet carried to v8."""

    reason = (
        f"project schema v{schema_version} has not been upgraded to "
        f"v{CURRENT_PROJECT_SCHEMA_VERSION}; produced artifacts cannot be read until it is"
    )
    # 该项目没有失败过的尝试，``failed_at`` 就留空：这个字段会原样透给用户与 Agent，
    # 填一个当下时刻等于伪造一次从未发生的失败。
    return MigrationFailureRecord(
        schema_version=schema_version,
        failed_at="",
        reason=reason,
        details=[MigrationFailureDetail(file="project.json", violation=reason)],
    )


def load_migration_verdict(project_dir: Path) -> MigrationFailureRecord | None:
    """Return the blocking verdict for a project — recorded failure or still pending.

    A recorded failure wins: it names the exact inputs the chain refused. Absent
    one, a project short of the current schema is still blocked, because the
    Artifact Manifest is the only rule for reading produced artifacts and an
    unmigrated project has no claims in it.
    """

    recorded = load_migration_failure(project_dir)
    if recorded is not None:
        return recorded
    version = _readable_schema_version(project_dir)
    if version is None or version >= CURRENT_PROJECT_SCHEMA_VERSION:
        return None
    return pending_migration_record(version)


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
    """Persist the verdict for a failed attempt, replacing any earlier one.

    Raises when the record cannot be written: the returned record would otherwise
    claim the project is blocked while no guard can see it on disk.
    """

    record = MigrationFailureRecord(
        schema_version=schema_version,
        failed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        reason=str(exc),
        details=migration_failure_details(exc),
    )
    _write_atomically(migration_failure_path(project_dir), record)
    return record


def clear_migration_failure(project_dir: Path) -> None:
    """Drop the verdict once the chain completes — the project is no longer blocked.

    Raises when the file survives: the verdict on disk is what every guard reads,
    so swallowing the error would report "unblocked" while the project stays shut.
    """

    path = migration_failure_path(project_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("无法清除迁移失败记录，该项目仍被阻断：%s（%s）", path, exc)
        raise


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
        raise


__all__ = [
    "MIGRATION_FAILURE_CODE",
    "MIGRATION_FAILURE_FILENAME",
    "RETRY_MIGRATION_ACTION",
    "MigrationFailureDetail",
    "MigrationFailureRecord",
    "ProjectMigrationError",
    "clear_migration_failure",
    "load_migration_failure",
    "load_migration_verdict",
    "migration_failure_details",
    "migration_failure_path",
    "pending_migration_record",
    "record_migration_failure",
]
