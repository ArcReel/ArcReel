"""Persistent report of what a project's last finished migration did.

The report answers "what did the migration register, and what did it leave
out" — it is informational and never blocks anything, unlike the migration
verdict (:mod:`lib.project_migration_failure`). It lives beside ``project.json``
so the production status can surface it without a database round trip. The
migration runner is the only writer: a chain that fails writes a verdict, not a
report, so the two files never describe the same attempt.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.artifact_manifest import ArtifactKey
from lib.json_io import atomic_write_bytes

logger = logging.getLogger(__name__)

MIGRATION_REPORT_FILENAME = ".migration_report.json"


class MigrationSkippedArtifact(BaseModel):
    """One formal artifact the backfill could not register, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    episode: int | None = None
    resource_id: str
    artifact_path: str
    reason: str


class MigrationReport(BaseModel):
    """What the last finished migration chain registered and skipped."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    migrated_at: str
    from_schema_version: int
    to_schema_version: int
    registered: dict[str, int] = Field(default_factory=dict)
    """Registered manifest entries counted per artifact kind."""
    skipped: list[MigrationSkippedArtifact] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ArtifactBackfillOutcome:
    """What one manifest-writing migration step registered and skipped.

    Returned by the migrators that activate the manifest; the runner folds the
    last one of a chain into the persisted :class:`MigrationReport`.
    """

    registered: Mapping[str, int]
    skipped: tuple[MigrationSkippedArtifact, ...]

    @classmethod
    def from_entries(
        cls,
        entries: Mapping[ArtifactKey, object],
        *skipped_groups: Sequence[MigrationSkippedArtifact],
    ) -> ArtifactBackfillOutcome:
        """Count registered entries per kind; skip groups merge with the first reason winning."""

        registered = Counter(key.kind.value for key in entries)
        return cls(registered=dict(registered), skipped=merge_skipped(*skipped_groups))


def merge_skipped(
    *groups: Sequence[MigrationSkippedArtifact],
) -> tuple[MigrationSkippedArtifact, ...]:
    """Merge skip lists; the first group naming an artifact keeps its reason."""

    merged: dict[tuple[str, int | None, str], MigrationSkippedArtifact] = {}
    for group in groups:
        for item in group:
            merged.setdefault((item.kind, item.episode, item.resource_id), item)
    return tuple(merged.values())


def migration_report_path(project_dir: Path) -> Path:
    return project_dir / MIGRATION_REPORT_FILENAME


def build_migration_report(
    outcome: ArtifactBackfillOutcome,
    *,
    from_schema_version: int,
    to_schema_version: int,
) -> MigrationReport:
    return MigrationReport(
        migrated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        from_schema_version=from_schema_version,
        to_schema_version=to_schema_version,
        registered=dict(sorted(outcome.registered.items())),
        skipped=list(outcome.skipped),
    )


def write_migration_report(project_dir: Path, report: MigrationReport) -> None:
    """Persist the report, replacing any earlier one. Raises when it cannot be written."""

    payload = json.dumps(report.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(migration_report_path(project_dir), payload)


def load_migration_report(project_dir: Path) -> MigrationReport | None:
    """Read the report, or ``None`` when there is none or it cannot be parsed.

    The report is informational: an unreadable one is logged and treated as
    absent rather than turned into an error on every status read.
    """

    path = migration_report_path(project_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("无法读取迁移报告：%s（%s）", path, exc)
        return None
    try:
        return MigrationReport.model_validate_json(raw)
    except ValidationError as exc:
        logger.warning("迁移报告无法解析：%s（%s）", path, exc)
        return None


__all__ = [
    "MIGRATION_REPORT_FILENAME",
    "ArtifactBackfillOutcome",
    "MigrationReport",
    "MigrationSkippedArtifact",
    "build_migration_report",
    "load_migration_report",
    "merge_skipped",
    "migration_report_path",
    "write_migration_report",
]
