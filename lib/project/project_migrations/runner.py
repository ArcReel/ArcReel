"""Runner: scan projects/ and run migrators in version order."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lib.episode.episode_ledger import parse_positive_episode_num
from lib.episode.episode_paths import episode_drafts_dir
from lib.infra.data_root_layout import DataRootLayout, list_project_dirs
from lib.infra.path_safety import try_safe_join
from lib.project.project_migration_failure import (
    MigrationFailureRecord,
    ProjectMigrationError,
    clear_migration_failure,
    record_migration_failure,
)
from lib.project.project_migration_report import (
    ArtifactBackfillOutcome,
    MigrationAdjustment,
    build_migration_report,
    complete_migration_report,
    load_migration_report,
    merge_skipped,
    write_migration_report,
)
from lib.project.project_migrations.backups import (
    ensure_versioned_backup,
    versioned_backup_candidates,
    versioned_backup_name,
)
from lib.project.project_migrations.legacy_shapes import NORMALIZE_FROM_SCHEMA_VERSION, normalize_legacy_shapes
from lib.project.project_migrations.staged_swap import (
    cleanup_completed_swap_dirs,
    ensure_disk_headroom,
)
from lib.project.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1
from lib.project.project_migrations.v1_to_v2_normalize_providers import migrate_v1_to_v2
from lib.project.project_migrations.v2_to_v3_episode_ledger import migrate_v2_to_v3
from lib.project.project_migrations.v3_to_v4_text_tiers import migrate_v3_to_v4
from lib.project.project_migrations.v4_to_v5_generation_route import migrate_v4_to_v5
from lib.project.project_migrations.v5_to_v6_asset_namespace import migrate_v5_to_v6
from lib.project.project_migrations.v6_to_v7_ad_reference_video_units import migrate_v6_to_v7
from lib.project.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8
from lib.project.project_migrations.v8_to_v9_reference_unit_text import migrate_v8_to_v9
from lib.project.project_migrations.v9_to_v10_script_plan_naming import DRAFT_FILE_RENAMES, migrate_v9_to_v10
from lib.project.project_migrations.v10_to_v11_character_voice_binding import migrate_v10_to_v11
from lib.project.project_migrations.v11_to_v12_character_derivatives import migrate_v11_to_v12
from lib.project.project_migrations.v12_to_v13_legacy_media_provenance import migrate_v12_to_v13
from lib.project.project_migrations.v13_to_v14_legacy_style_values import migrate_v13_to_v14
from lib.project.project_migrations.v14_to_v15_formal_script_truth import migrate_v14_to_v15
from lib.project.project_migrations.v15_to_v16_edit_decisions import RecordedEpisodeIds, migrate_v15_to_v16
from lib.project.project_migrations.v16_to_v17_retired_text_model_ids import migrate_v16_to_v17
from lib.project.project_migrations.v17_to_v18_timeline_agent_turn import (
    migrate_v17_to_v18,
    timeline_backup_sources,
)
from lib.project.project_migrations.v18_to_v19_item_id_high_water import migrate_v18_to_v19
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION, parse_project_schema_version

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = CURRENT_PROJECT_SCHEMA_VERSION

#: All filenames migrations may back up in draft directories. Backups use names at write time; these drafts
#: were renamed in v9→v10, so either old or new names may have pending backups. Reclaim both sides.
_DRAFT_BACKUP_NAMES: tuple[str, ...] = (*DRAFT_FILE_RENAMES, *DRAFT_FILE_RENAMES.values())

#: Migrator results: manifest-rewriting steps return registered/skipped artifacts; the runner folds the last result into the migration report.
#: Steps deterministically changing user data (such as v6→v7 renumbering) append to the runner-provided ``record`` and the report adjustments.
MIGRATORS: dict[int, Callable[[Path], ArtifactBackfillOutcome | None]] = {}
#: Migrators backing up their own inputs (activation backs up dependencies; v8/v9/v14 their rewritten files); no runner project.json backup.
_MIGRATORS_WITH_OWNED_BACKUP = frozenset({7, 8, 9, 12, 14})

# Read-only preflight runs before any runner backup; rejection leaves every project-directory byte untouched.
_MIGRATOR_PREFLIGHTS: dict[int, Callable[[Path], None]] = {5: ensure_disk_headroom}


def _bound_script_sources(project_dir: Path) -> tuple[Path, ...]:
    """Resolve every script-shaped source a migration was allowed to back up.

    Ledger-bound episode scripts plus script_plan drafts for the same episodes: drafts are the previous
    form of the same body, rewritten in the same batch by script migrations. Backups therefore occur
    in pairs and must also be reclaimed in pairs; otherwise draft backups have no cleanup path."""

    try:
        project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ()
    episodes = project.get("episodes") if isinstance(project, dict) else None
    if not isinstance(episodes, list):
        return ()
    sources: list[Path] = []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        script_file = episode.get("script_file")
        if isinstance(script_file, str) and script_file:
            source = try_safe_join(project_dir, script_file)
            if source is not None:
                sources.append(source)
        episode_num = parse_positive_episode_num(episode.get("episode"))
        if episode_num is not None:
            drafts_dir = episode_drafts_dir(project_dir, episode_num)
            sources.extend(drafts_dir / name for name in _DRAFT_BACKUP_NAMES)
    return tuple(sources)


@dataclass
class MigrationSummary:
    migrated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _load_schema_version(project_dir: Path) -> int:
    pj = project_dir / "project.json"
    if not pj.exists():
        return -1  # Skip non-project directories
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("project.json 必须包含对象")
        return parse_project_schema_version(data)
    except Exception as exc:
        logger.warning("project.json 损坏或 schema_version 不可解析，跳过：%s（%s）", project_dir, exc)
        return -1


def _backup_project_json(project_dir: Path, from_version: int) -> None:
    """Keep one backup for identical ``project.json`` contents at the same starting version."""

    ensure_versioned_backup(project_dir / "project.json", from_version)


def _hardlink_backup_clues(project_dir: Path, from_version: int) -> None:
    """v0→v1 only: hard-link clues/ to clues.bak.v0-<ts>/, falling back to copytree. Zero disk overhead and full rollback."""
    src = project_dir / "clues"
    if not src.is_dir():
        return
    ts = int(time.time())
    bak = project_dir / versioned_backup_name("clues", from_version, ts)
    if bak.exists():
        return
    try:
        bak.mkdir()
        for entry in src.rglob("*"):
            rel = entry.relative_to(src)
            target = bak / rel
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(entry, target)
            except OSError:
                # Fall back to copying across filesystems (EXDEV), etc.
                shutil.copy2(entry, target)
    except OSError as exc:
        logger.warning("clues 备份失败（非阻塞）：%s: %s", project_dir, exc)


def migrate_project_dir(project_dir: Path, *, recorded_episode_ids: RecordedEpisodeIds | None = None) -> bool:
    """Upgrade one project directory stepwise to CURRENT_SCHEMA_VERSION; return whether migration occurred.

    Shared by startup ``run_project_migrations`` and project import: the startup runner covers only
    projects present at startup. Old archives imported afterward must run the full chain through this
    function at the import entry point; otherwise parsers, which no longer read legacy fields, silently
    fall back to global defaults. Non-project directories / already-current projects return False.

    ``recorded_episode_ids`` looks up the highest episode ID in task and call records by project name,
    passing it to v15→v16 for inclusion in the project high-water mark. When omitted, only the project
    directory itself is considered."""
    version = _load_schema_version(project_dir)
    if version < 0:
        return False
    if version >= CURRENT_SCHEMA_VERSION:
        _finish_unfinished_report(project_dir)
        return False
    start_version = version
    outcome: ArtifactBackfillOutcome | None = None
    # Previously persisted normalizations will not be rediscovered: carry them forward from the unfinished report.
    carried = load_migration_report(project_dir)
    if carried is not None and not carried.complete:
        start_version = min(start_version, carried.from_schema_version)
        adjusted: list[MigrationAdjustment] = list(carried.adjusted)
    else:
        adjusted = []
    normalized = False
    try:
        while version < CURRENT_SCHEMA_VERSION:
            if not normalized and version >= NORMALIZE_FROM_SCHEMA_VERSION:
                # Normalize legacy shapes before the first target-state plan in this chain; that planner cannot recognize them.
                normalize_legacy_shapes(project_dir, schema_version=version, record=adjusted)
                normalized = True
            # Activation migrations must finish their complete read-only preflight
            # before creating any backup.  Their commit boundary owns the backup so
            # the runner cannot leave writes behind when preflight rejects a project.
            preflight = _MIGRATOR_PREFLIGHTS.get(version)
            if preflight:
                preflight(project_dir)
            if version not in _MIGRATORS_WITH_OWNED_BACKUP:
                _backup_project_json(project_dir, version)
            if version == 0:
                _hardlink_backup_clues(project_dir, version)
            migrator = MIGRATORS.get(version)
            if not migrator:
                raise RuntimeError(f"no migrator from v{version}")
            if migrator is migrate_v15_to_v16:
                step_outcome = migrate_v15_to_v16(project_dir, recorded_episode_ids=recorded_episode_ids)
            elif migrator is migrate_v5_to_v6:
                step_outcome = migrate_v5_to_v6(project_dir, record=adjusted)
            elif migrator is migrate_v6_to_v7:
                step_outcome = migrate_v6_to_v7(project_dir, record=adjusted)
            else:
                step_outcome = migrator(project_dir)
            if step_outcome is not None:
                if step_outcome.preserve_previous_skips:
                    previous_report = load_migration_report(project_dir) if outcome is None else None
                    previous_skips = (
                        outcome.skipped
                        if outcome is not None
                        else (previous_report.skipped if previous_report is not None else ())
                    )
                    # Entries registered after this step invalidate earlier skips (e.g. v17→v18 backfills generation-gap skips).
                    still_skipped = [skip for skip in previous_skips if skip.identity not in step_outcome.resolved]
                    step_outcome = ArtifactBackfillOutcome(
                        registered=step_outcome.registered,
                        skipped=merge_skipped(still_skipped, step_outcome.skipped),
                        resolved=step_outcome.resolved,
                    )
                outcome = step_outcome
            version += 1
        if outcome is not None or adjusted:
            # The final manifest rewrite describes the complete manifest at migration completion; retain only that result.
            write_migration_report(
                project_dir,
                build_migration_report(
                    outcome,
                    from_schema_version=start_version,
                    to_schema_version=CURRENT_SCHEMA_VERSION,
                    adjusted=adjusted,
                ),
            )
    except BaseException:
        # A final report-write failure or failure after the last step commits the current version also leaves an unfinished report:
        # next startup sees the current version and completes it through ``_finish_unfinished_report``.
        # Once the chain finishes, ``outcome`` is the whole manifest, retaining skips in the report; mid-chain retries recompute them.
        finished = outcome if version >= CURRENT_SCHEMA_VERSION else None
        if adjusted or finished is not None:
            _record_unfinished_report(project_dir, finished, adjusted, from_version=start_version, to_version=version)
        raise
    return True


def _finish_unfinished_report(project_dir: Path) -> None:
    """Complete an unfinished report for an already-current project; log write failures and retry next startup.

    When the chain fails after committing the current version (an error after the last step commits,
    or failure writing the final report), rerunning will not reenter the chain. Without completion,
    the unfinished report would never be shown to users."""

    try:
        complete_migration_report(project_dir, to_schema_version=CURRENT_SCHEMA_VERSION)
    except OSError as exc:
        logger.warning("无法补写未完成的迁移报告：%s（%s）", project_dir, exc)


def _record_unfinished_report(
    project_dir: Path,
    outcome: ArtifactBackfillOutcome | None,
    adjusted: list[MigrationAdjustment],
    *,
    from_version: int,
    to_version: int,
) -> None:
    """On chain failure, retain persisted normalizations (and manifest results if finished); log write failures without masking the chain failure."""

    try:
        write_migration_report(
            project_dir,
            build_migration_report(
                outcome,
                from_schema_version=from_version,
                to_schema_version=to_version,
                adjusted=adjusted,
                complete=False,
            ),
        )
    except OSError as exc:
        logger.warning("无法记下未完成迁移的规整记录：%s（%s）", project_dir, exc)


def _append_error_log(project_dir: Path, tb: str) -> None:
    """Keep the full traceback out of the user-facing verdict but on disk for support."""

    error_log = DataRootLayout.for_project_dir(project_dir).project_migration_error_log_path
    try:
        error_log.parent.mkdir(parents=True, exist_ok=True)
        with error_log.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {project_dir.name}\n{tb}\n")
    except OSError as exc:
        logger.warning("无法写入迁移错误日志：%s（%s）", error_log, exc)


def migrate_project_with_verdict(
    project_dir: Path, *, recorded_episode_ids: RecordedEpisodeIds | None = None
) -> MigrationFailureRecord | None:
    """Run the chain for one project and persist the verdict beside its data.

    Returns ``None`` once the project sits at the current schema — the previous
    verdict, if any, is cleared — or the recorded failure. Idempotent: a project
    that is already current is a no-op success, so retrying costs nothing.
    """

    try:
        migrate_project_dir(project_dir, recorded_episode_ids=recorded_episode_ids)
    except Exception as exc:  # Isolate individual project failures without interrupting all migrations
        logger.error("迁移失败 %s: %s", project_dir.name, exc)
        # The persisted verdict is what the production status, the production plan
        # and every generation entry read to refuse work on this project.
        _append_error_log(project_dir, traceback.format_exc())
        return record_migration_failure(project_dir, exc, schema_version=_load_schema_version(project_dir))
    version = _load_schema_version(project_dir)
    if version != CURRENT_SCHEMA_VERSION:
        # ``migrate_project_dir`` returns without raising for a directory it cannot
        # place on the chain at all — no readable ``project.json``, or a version the
        # chain does not cover. Reaching the current schema is the only evidence the
        # project is repaired, so anything else keeps it blocked.
        return record_migration_failure(
            project_dir,
            ProjectMigrationError(
                f"schema version {version} did not reach v{CURRENT_SCHEMA_VERSION}",
                file="project.json",
            ),
            schema_version=version,
        )
    # A retry that finally lands clears the verdict, so the project stops being
    # reported as blocked without any further user action.
    clear_migration_failure(project_dir)
    return None


def run_project_migrations(
    projects_dir: Path, *, recorded_episode_ids: RecordedEpisodeIds | None = None
) -> MigrationSummary:
    """Scan each project in the projects directory and upgrade it to CURRENT_SCHEMA_VERSION."""
    summary = MigrationSummary()
    if not projects_dir.exists():
        return summary

    for child in list_project_dirs(projects_dir):
        version = _load_schema_version(child)
        if version < 0:
            continue  # Corrupt project.json
        # Persisting the verdict is itself disk work: one project whose directory
        # cannot be written must not abort the pass for every project after it.
        try:
            if version >= CURRENT_SCHEMA_VERSION:
                # A project that reached the current schema by any route is not blocked;
                # a verdict left over from an earlier attempt would strand it forever.
                clear_migration_failure(child)
                _finish_unfinished_report(child)
                summary.skipped.append(child.name)
                continue

            if migrate_project_with_verdict(child, recorded_episode_ids=recorded_episode_ids) is None:
                summary.migrated.append(child.name)
            else:
                summary.failed.append(child.name)
        except OSError as exc:
            logger.error("迁移裁决无法落盘 %s：%s", child.name, exc)
            summary.failed.append(child.name)

    return summary


def cleanup_stale_backups(projects_dir: Path, max_age_days: int = 7) -> None:
    """Delete versioned backups attributable to migration inputs and directory-swap intermediates older than max_age_days."""
    if not projects_dir.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    cleanup_completed_swap_dirs(projects_dir, cutoff)
    for project_dir in list_project_dirs(projects_dir):
        # Retain each input backup until that step commits its version bump: while at v<N>, ``*.bak.v<N>-*``
        # is the only recovery clue after failure. Reclaim only backups whose starting version is below the current schema.
        schema_version = _load_schema_version(project_dir)
        project_backup_versions = tuple(
            version for version in range(CURRENT_SCHEMA_VERSION) if version < schema_version
        )
        sources = (
            (project_dir / "project.json", project_backup_versions),
            (project_dir / "versions" / "versions.json", project_backup_versions),
            # The manifest changes beyond activation: v9→v10 rewrites keys/draft paths, v12→v13 reprojects it entirely,
            # v13→v14 rewrites entries affected by style normalization/description backfills, v14→v15 script entries,
            # v15→v16 presentation-model entries.
            (project_dir / ".arcreel_artifacts.json", project_backup_versions),
            *((source, project_backup_versions) for source in _bound_script_sources(project_dir)),
            # v17→v18 removes Agent turns from timeline revisions; reclaim backups even after timeline deletion.
            *((source, project_backup_versions) for source in timeline_backup_sources(project_dir)),
        )
        for source, versions in sources:
            for bak in versioned_backup_candidates(source, versions):
                if bak.is_symlink() or not bak.is_file():
                    continue
                try:
                    if bak.stat().st_mtime < cutoff:
                        bak.unlink()  # hard-delete: infra -- expired migration backups
                except OSError:
                    logger.warning("无法删除备份：%s", bak)
        for bak_dir in versioned_backup_candidates(project_dir / "clues", (0,)):
            if bak_dir.is_symlink() or not bak_dir.is_dir():
                continue
            try:
                if bak_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(
                        bak_dir, ignore_errors=True
                    )  # hard-delete: infra -- expired migration backup directories
            except OSError:
                logger.warning("无法删除 clues 备份：%s", bak_dir)


# Register migrators (imported at top; assignments only here)
MIGRATORS[0] = migrate_v0_to_v1
MIGRATORS[1] = migrate_v1_to_v2
MIGRATORS[2] = migrate_v2_to_v3
MIGRATORS[3] = migrate_v3_to_v4
MIGRATORS[4] = migrate_v4_to_v5
MIGRATORS[5] = migrate_v5_to_v6
MIGRATORS[6] = migrate_v6_to_v7
MIGRATORS[7] = migrate_v7_to_v8
MIGRATORS[8] = migrate_v8_to_v9
MIGRATORS[9] = migrate_v9_to_v10
MIGRATORS[10] = migrate_v10_to_v11
MIGRATORS[11] = migrate_v11_to_v12
MIGRATORS[12] = migrate_v12_to_v13
MIGRATORS[13] = migrate_v13_to_v14
MIGRATORS[14] = migrate_v14_to_v15
MIGRATORS[15] = migrate_v15_to_v16
MIGRATORS[16] = migrate_v16_to_v17
MIGRATORS[17] = migrate_v17_to_v18
MIGRATORS[18] = migrate_v18_to_v19
