"""v7 → v8: eagerly activate the complete Artifact Manifest target state."""

from __future__ import annotations

from pathlib import Path

from lib.artifact_activation import activate_artifact_target_state
from lib.project_migrations.v9_to_v10_script_plan_naming import rename_script_plan_drafts


def migrate_v7_to_v8(project_dir: Path) -> None:
    # 激活按当前代码解析脚本规划草稿路径（新名），故先把 v9→v10 的草稿改名前置到这里；
    # 改名幂等，v8 之后再跑一次不会有任何动作。
    rename_script_plan_drafts(project_dir, from_version=7)
    activate_artifact_target_state(project_dir, bump_schema=True)


__all__ = ["migrate_v7_to_v8"]
