"""Persistent report of what a project's last migration changed and lost.

The report holds two groups the user is told about once (ADR 0098): what the
migration adjusted (a legacy shape normalized with the other copy kept as a
backup, a placeholder added, a structural defect repaired) and what it could not
carry over (files that cannot be read, legacy narration audio whose basis cannot
be computed). Generation gaps and units that need re-planning are not in it. It
is informational and never blocks anything, unlike the migration verdict
(:mod:`lib.project.project_migration_failure`). It lives beside ``project.json``
so the production status can surface it without a database round trip.

The migration runner writes it when a chain finishes. A chain that fails after
adjusting files writes an incomplete report holding those adjustments only, so
the retry that finishes the chain still reports them; an incomplete report is
never shown. A chain that fails only after committing the current schema is not
rerun, so the runner completes its report when it finds the project current. Archive import adds its placeholder adjustments to the staged
report and acknowledges it once the user confirms the import.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactKind
from lib.infra.json_io import atomic_write_bytes
from lib.infra.path_safety import try_safe_join
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.script_editor import ScriptEditError, resolve_items

logger = logging.getLogger(__name__)

MIGRATION_REPORT_FILENAME = ".migration_report.json"


type SkippedArtifactIdentity = tuple[str, int | None, str]


SkippedArtifactCategory = Literal["unreadable", "legacy_audio_basis"]
"""Why a skipped artifact is shown to the user under 「未能带入」.

``unreadable``: the artifact file itself (or its own control record, such as a grid record or a
script_plan draft) cannot be read. ``legacy_audio_basis``: legacy narration audio whose version
record carries no TTS settings. A skip without a category is a basis that cannot be computed: the
artifact reads as missing at runtime and the skip is kept for bookkeeping, never shown.
"""


class MigrationSkippedArtifact(BaseModel):
    """One formal artifact the backfill could not register, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    episode: int | None = None
    resource_id: str
    artifact_path: str
    reason: str
    category: SkippedArtifactCategory | None = None

    @classmethod
    def for_key(
        cls,
        key: ArtifactKey,
        *,
        artifact_path: str,
        reason: str,
        category: SkippedArtifactCategory | None = None,
    ) -> MigrationSkippedArtifact:
        kind, episode, resource_id = skipped_artifact_identity(key)
        return cls(
            kind=kind,
            episode=episode,
            resource_id=resource_id,
            artifact_path=artifact_path,
            reason=reason,
            category=category,
        )

    @property
    def identity(self) -> SkippedArtifactIdentity:
        return (self.kind, self.episode, self.resource_id)


def skipped_artifact_identity(key: ArtifactKey) -> SkippedArtifactIdentity:
    """一件产物在迁移报告里的身份：产物种类、集号（项目级产物为空）与资源 id。"""

    episode = key.components[0]
    return (key.kind.value, episode if type(episode) is int else None, str(key.components[-1]))


MigrationAdjustmentCode = Literal[
    "draft_name_conflict_resolved",
    "duplicate_episode_entry_removed",
    "shared_script_binding_split",
    "script_episode_corrected",
    "script_binding_canonicalized",
    "item_id_reassigned",
    "duplicate_asset_removed",
    "duplicate_asset_derivative_merged",
    "duplicate_asset_references_rewritten",
    "shared_reference_file_copied",
    "placeholder_asset_added",
]
"""What kind of change one adjustment made; the frontend renders each code with its own text."""


class MigrationAdjustment(BaseModel):
    """One change the migration or the import made to the user's data, and where the other copy went."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: MigrationAdjustmentCode
    episode: int | None = None
    file: str | None = None
    """The project-relative file the change applies to."""
    backup: str | None = None
    """The project-relative copy kept of what the change set aside; never cleaned up automatically."""
    params: dict[str, str] = Field(default_factory=dict)


class MigrationReport(BaseModel):
    """What the last migration adjusted and could not carry over."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    migrated_at: str
    from_schema_version: int
    to_schema_version: int
    complete: bool = True
    """False while the chain that made ``adjusted`` has not finished; such a report is not shown."""
    registered: dict[str, int] = Field(default_factory=dict)
    """Registered manifest entries counted per artifact kind; bookkeeping, not shown to the user."""
    adjusted: list[MigrationAdjustment] = Field(default_factory=list)
    skipped: list[MigrationSkippedArtifact] = Field(default_factory=list)
    """Artifacts that were not carried over: left on disk, not registered."""
    acknowledged_at: str | None = None
    """When the user dismissed the notice or confirmed the import that showed this report."""

    @property
    def item_count(self) -> int:
        return len(self.adjusted) + len(self.skipped)


@dataclass(frozen=True, slots=True)
class ArtifactBackfillOutcome:
    """What one manifest-writing migration step registered and skipped.

    Returned by the migrators that activate the manifest; the runner folds the
    last one of a chain into the persisted :class:`MigrationReport`.
    """

    registered: Mapping[str, int]
    skipped: tuple[MigrationSkippedArtifact, ...]
    preserve_previous_skips: bool = False
    """Rewriting existing claims alone cannot resolve skips from earlier steps."""
    resolved: frozenset[SkippedArtifactIdentity] = frozenset()
    """Artifacts this step newly registered; earlier skips naming them no longer hold."""

    @classmethod
    def from_entries(
        cls,
        entries: Mapping[ArtifactKey, object],
        *skipped_groups: Sequence[MigrationSkippedArtifact],
        preserve_previous_skips: bool = False,
        resolved: Iterable[ArtifactKey] = (),
    ) -> ArtifactBackfillOutcome:
        """Count registered entries per kind; skip groups merge with the first reason winning."""

        registered = Counter(key.kind.value for key in entries)
        return cls(
            registered=dict(registered),
            skipped=merge_skipped(*skipped_groups),
            preserve_previous_skips=preserve_previous_skips,
            resolved=frozenset(skipped_artifact_identity(key) for key in resolved),
        )


def merge_skipped(
    *groups: Sequence[MigrationSkippedArtifact],
) -> tuple[MigrationSkippedArtifact, ...]:
    """Merge skip lists; the first group naming an artifact keeps its reason.

    A record the user is shown (``category`` set) wins over an unclassified one for the same
    artifact: an earlier step may have skipped it for a generation gap before a later one found
    the file unreadable, and the unclassified record would hide it from the report.
    """

    merged: dict[SkippedArtifactIdentity, MigrationSkippedArtifact] = {}
    for group in groups:
        for item in group:
            kept = merged.get(item.identity)
            if kept is None or (kept.category is None and item.category is not None):
                merged[item.identity] = item
    return tuple(merged.values())


def migration_report_path(project_dir: Path) -> Path:
    return project_dir / MIGRATION_REPORT_FILENAME


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_migration_report(
    outcome: ArtifactBackfillOutcome | None,
    *,
    from_schema_version: int,
    to_schema_version: int,
    adjusted: Sequence[MigrationAdjustment] = (),
    complete: bool = True,
) -> MigrationReport:
    return MigrationReport(
        migrated_at=_utc_now(),
        from_schema_version=from_schema_version,
        to_schema_version=to_schema_version,
        complete=complete,
        registered=dict(sorted(outcome.registered.items())) if outcome is not None else {},
        adjusted=list(adjusted),
        skipped=list(outcome.skipped) if outcome is not None else [],
    )


#: 以剧本条目 id 为资源 id 的产物种类：条目需要重新规划时，它们不进报告。
_UNIT_ARTIFACT_KINDS = frozenset(
    {ArtifactKind.EPISODE_STORYBOARD.value, ArtifactKind.EPISODE_VIDEO.value, ArtifactKind.EPISODE_AUDIO.value}
)


def _units_needing_replan(project_dir: Path) -> frozenset[tuple[int, str]]:
    """绑定剧本里带 ``needs_replan`` 标记的条目，按（集号, 条目 id）。读不出的剧本不贡献条目。"""

    try:
        project = json.loads((project_dir / "project.json").read_bytes())
    except (OSError, ValueError):
        return frozenset()
    episodes = project.get("episodes") if isinstance(project, dict) else None
    units: set[tuple[int, str]] = set()
    for entry in episodes if isinstance(episodes, list) else []:
        if not isinstance(entry, dict):
            continue
        episode, script_file = entry.get("episode"), entry.get("script_file")
        if type(episode) is not int or not isinstance(script_file, str) or not script_file:
            continue
        script_path = try_safe_join(project_dir, script_file, require_file=True)
        if script_path is None:
            continue
        try:
            script = json.loads(script_path.read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(script, dict):
            # 根不是对象的剧本同样读不出：迁移链失败时这里正为它写未完成的报告，不能盖过链本身的失败。
            continue
        try:
            items, id_field, _kind = resolve_items(script)
        except ScriptEditError:
            continue
        units.update(
            (episode, str(item.get(id_field)))
            for item in items
            if isinstance(item, dict) and item.get("needs_replan") is True
        )
    return frozenset(units)


def write_migration_report(project_dir: Path, report: MigrationReport) -> None:
    """Persist the report, replacing any earlier one. Raises when it cannot be written.

    「未能带入」只收读不出的文件与没有合成设置记录的旧版旁白音频（``category`` 非空）；依据算不出
    的跳过与需要重新规划的条目的产物在这里统一去掉（ADR 0098）。迁移链与归档导入的报告都经过
    这里，不靠各条跳过路径各自记得。
    """

    replan = _units_needing_replan(project_dir)
    kept = [
        item
        for item in report.skipped
        if item.category is not None
        and not (item.kind in _UNIT_ARTIFACT_KINDS and (item.episode, item.resource_id) in replan)
    ]
    if len(kept) != len(report.skipped):
        report = report.model_copy(update={"skipped": kept})
    payload = json.dumps(report.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(migration_report_path(project_dir), payload)


def load_migration_report(project_dir: Path) -> MigrationReport | None:
    """Read the report, or ``None`` when there is none or it cannot be parsed.

    The report is informational: an unreadable one is logged and treated as
    absent rather than turned into an error on every status read. A report in
    the earlier format listed generation gaps as skipped artifacts; it is
    treated as absent, and the next chain replaces it.
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
        payload = json.loads(raw)
    except ValueError as exc:
        logger.warning("迁移报告无法解析：%s（%s）", path, exc)
        return None
    if isinstance(payload, dict) and payload.get("schema_version") == 1:
        return None
    try:
        return MigrationReport.model_validate(payload)
    except ValidationError as exc:
        logger.warning("迁移报告无法解析：%s（%s）", path, exc)
        return None


def complete_migration_report(project_dir: Path, *, to_schema_version: int) -> bool:
    """Mark an incomplete report complete once its chain is known to have finished. Returns whether one was.

    The adjustments it holds are already on disk; a chain that failed after committing the
    current schema is never rerun, so this is the only way they reach the user.
    Raises when the report cannot be rewritten.
    """

    report = load_migration_report(project_dir)
    if report is None or report.complete:
        return False
    write_migration_report(
        project_dir,
        report.model_copy(update={"complete": True, "to_schema_version": to_schema_version, "migrated_at": _utc_now()}),
    )
    return True


def visible_migration_report(project_dir: Path) -> MigrationReport | None:
    """The report the user is still to be told about: complete, non-empty, not yet acknowledged."""

    report = load_migration_report(project_dir)
    if report is None or not report.complete or report.acknowledged_at is not None or report.item_count == 0:
        return None
    return report


def acknowledge_migration_report(project_dir: Path, *, migrated_at: str | None = None) -> bool:
    """Mark the report as seen so the notice stops showing. Returns whether a report was marked.

    ``migrated_at`` names the report the user was shown; a different report on disk (a later
    upgrade wrote it meanwhile) stays unacknowledged so it can still be shown.

    Raises when the report exists but cannot be rewritten: the notice would
    otherwise come back on the next read while the caller reports success.
    """

    report = load_migration_report(project_dir)
    if report is None or report.acknowledged_at is not None:
        return False
    if migrated_at is not None and report.migrated_at != migrated_at:
        return False
    write_migration_report(project_dir, report.model_copy(update={"acknowledged_at": _utc_now()}))
    return True


def extend_migration_report(
    project_dir: Path,
    *,
    adjusted: Sequence[MigrationAdjustment] = (),
    skipped: Sequence[MigrationSkippedArtifact] = (),
) -> None:
    """Add what the archive import changed or left out outside the chain, creating the report when absent.

    Items already in the report are not repeated.  New items make the report unacknowledged again
    and give it a new ``migrated_at``, so the user is told about them and an acknowledgement of the
    earlier report does not cover them.
    """

    # 依据算不出的跳过不给用户看，并进来也会在写盘时去掉；先滤掉，免得它让已确认的报告重新出现。
    skipped = [item for item in skipped if item.category is not None]
    report = load_migration_report(project_dir)
    if report is None:
        if not adjusted and not skipped:
            return
        report = build_migration_report(
            None,
            from_schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
            to_schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
        )
    merged_adjusted = list(report.adjusted)
    for item in adjusted:
        if item not in merged_adjusted:
            merged_adjusted.append(item)
    merged_skipped = list(merge_skipped(report.skipped, skipped))
    if merged_adjusted == report.adjusted and merged_skipped == report.skipped:
        return
    write_migration_report(
        project_dir,
        report.model_copy(
            update={
                "adjusted": merged_adjusted,
                "skipped": merged_skipped,
                "migrated_at": _utc_now(),
                "acknowledged_at": None,
            }
        ),
    )


__all__ = [
    "MIGRATION_REPORT_FILENAME",
    "ArtifactBackfillOutcome",
    "MigrationAdjustment",
    "MigrationAdjustmentCode",
    "MigrationReport",
    "MigrationSkippedArtifact",
    "SkippedArtifactCategory",
    "acknowledge_migration_report",
    "build_migration_report",
    "complete_migration_report",
    "extend_migration_report",
    "load_migration_report",
    "merge_skipped",
    "migration_report_path",
    "visible_migration_report",
    "write_migration_report",
]
