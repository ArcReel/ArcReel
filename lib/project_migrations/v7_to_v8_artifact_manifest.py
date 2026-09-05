"""v7 → v8: eagerly activate the complete Artifact Manifest target state."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from lib.artifact_activation import activate_artifact_target_state, plan_artifact_target_state
from lib.project_migration_report import ArtifactBackfillOutcome
from lib.project_migrations.v9_to_v10_script_plan_naming import (
    apply_script_plan_draft_renames,
    pending_draft_rename_map,
    plan_script_plan_draft_renames,
)


def migrate_v7_to_v8(project_dir: Path) -> ArtifactBackfillOutcome:
    project_dir = Path(project_dir)

    # 只读预检段：激活按当前代码解析脚本规划草稿路径（新名），故 v9→v10 的草稿改名前置到这里；
    # 改名与激活的拒绝判定都在这一段完成，任一拒绝时项目目录一个字节都没被动过。改名尚未落盘，
    # 规划因此按旧名读取草稿、按提交后的新名记录依赖与产物路径。
    renames = plan_script_plan_draft_renames(project_dir)
    plan = plan_artifact_target_state(
        project_dir,
        pending_renames=pending_draft_rename_map(renames, project_dir),
    )

    # 写盘段：改名先落盘，计划记录的路径由此成为盘上事实；激活的稳定性闸门随后逐一复核。
    # 改名幂等，v8 之后再跑一次不会有任何动作。
    apply_script_plan_draft_renames(renames, from_version=7)
    activate_artifact_target_state(project_dir, bump_schema=True, plan=plan)
    return ArtifactBackfillOutcome(
        registered=dict(Counter(key.kind.value for key in plan.entries)),
        skipped=plan.skipped,
    )


__all__ = ["migrate_v7_to_v8"]
