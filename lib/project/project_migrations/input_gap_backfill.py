"""v17→v18 的补录重跑：登记原先因生成缺口被跳过的存量产物（``docs/adr/0098``）。

v18 起生成缺口只拦生成，期望依据把缺的项记为缺席（``lib.artifacts.input_gap``）。更早各步的激活与
来源补写按「有缺口即投影不出」的口径跑过，被跳过的产物在清单里没有登记。这一步：

1. 按缺口记为缺席的口径重跑旧媒体来源补写（``docs/adr/0075``），只补写仍没有类型化来源的选中
   视频记录，手动上传不动；
2. 改前（schema 17，缺口即不登记）与改后（schema 18）各规划一次目标态；
3. 只把「改前没有登记目标、改后有、清单里也没有」的条目补进清单，已登记条目逐字节不变。

补录的产物判 current；之后补齐缺口让依据变化，按常规判 stale。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_planner import (
    ArtifactTargetStatePlan,
    gap_backfill_entries,
    plan_artifact_target_state_by_gap_rule,
)
from lib.artifacts.input_gap import GAP_TOLERANT_BASIS_SCHEMA_VERSION
from lib.infra.json_io import atomic_write_bytes
from lib.project.legacy_media_provenance import backfill_legacy_media_provenance
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.backups import ensure_versioned_backup

_FROM_VERSION = GAP_TOLERANT_BASIS_SCHEMA_VERSION - 1


def backfill_input_gap_artifacts(project_dir: Path, project: Mapping[str, Any]) -> ArtifactBackfillOutcome:
    """补录因生成缺口被跳过的产物。调用方持有项目元数据锁，``project`` 是盘上的 v17 内容。

    清单与版本记录改写前各自备份；提交后依赖若有漂移，按改写前的字节回滚两者。
    """

    project_dir = Path(project_dir)
    project_bytes = (project_dir / "project.json").read_bytes()
    versions_path = project_dir / "versions" / "versions.json"
    versions_bytes = versions_path.read_bytes() if versions_path.is_file() else None
    ensure_versioned_backup(versions_path, _FROM_VERSION)
    legacy = backfill_legacy_media_provenance(project_dir, record_gaps=True)
    try:
        before = plan_artifact_target_state_by_gap_rule(project_dir, project, record_gaps=False)
        after = plan_artifact_target_state_by_gap_rule(project_dir, project, record_gaps=True)
        plans = (before, after)
        for plan in plans:
            assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        stored = adapter.snapshot_entries()
        additions = gap_backfill_entries(stored, before.entries, after.entries)
        if additions:
            _commit_additions(project_dir, adapter, additions, plans, project_bytes)
    except BaseException:
        if legacy.amended and versions_bytes is not None:
            atomic_write_bytes(versions_path, versions_bytes)
        raise
    return ArtifactBackfillOutcome.from_entries(
        adapter.snapshot_entries(),
        legacy.skipped,
        after.skipped,
        preserve_previous_skips=True,
        resolved=additions,
    )


def _commit_additions(
    project_dir: Path,
    adapter: ProjectArtifactManifestAdapter,
    additions: Mapping[ArtifactKey, ArtifactManifestEntry],
    plans: tuple[ArtifactTargetStatePlan, ...],
    project_bytes: bytes,
) -> None:
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, _FROM_VERSION)
    expected: dict[ArtifactKey, ArtifactManifestEntry | None] = dict.fromkeys(additions)
    if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=additions):
        raise RuntimeError("artifact manifest changed while backfilling input-gap artifacts")
    try:
        for plan in plans:
            assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
    except BaseException as original_error:
        restored = adapter.replace_entries_if_matches_atomically(expected=additions, replacements=expected)
        if not restored and any(adapter.get_entry(key) is not None for key in additions):
            raise RuntimeError("artifact manifest dependency drifted and rollback was incomplete") from original_error
        raise


__all__ = ["backfill_input_gap_artifacts"]
