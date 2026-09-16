"""v13→v14 迁移：把存量项目的遗留风格值一次性归一到当前形态。

两种遗留形态：风格值开头的「画风：」前缀（旧版风格模版的写法，叠加英文 ``Style:`` 标签会
渲染成「Style: 画风：…」的中英混叠），以及 ``Photographic`` / ``Anime`` / ``3D Animation``
三个短标签（更早的风格枚举，此前在 ``ProjectManager`` 每次读项目时解析成模版 id 并展开快照）。
归一落盘后运行时只读已归一的值，前缀剥离与短标签解析都不再存在。

风格值是多种产物依据的输入。分镜图与参考视频的存量摘要按归一后的值算出，归一落盘后它们
逐字不变；资产图、宫格与剧本的依据记的是 ``project.json`` 里的原始值，归一会让它们的目标
摘要变化，既有产物因此翻过期。故本步在改写前后各规划一次目标态，把「改写前正是 current、
且目标摘要因风格值而变」的清单条目改写为改写后的登记：产物不因这次清理翻过期，而改写前
就已过期的条目原样保留，不伪造时新性。

提交顺序是先清单后 ``project.json``：清单改写落盘而 schema 尚未提升时崩溃，本步会整步重跑，
届时改写前规划仍从未动过的 ``project.json`` 算出，已改写的条目不再匹配「改写前摘要」而被跳过，
既不重复改写也不丢失修复结果。反序则会在重跑时把已修复的条目认成陌生摘要，永久留下过期标记。
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_planner import ArtifactTargetStatePlan, TargetStatePlanner
from lib.formal_write import project_metadata_lock
from lib.json_io import atomic_write_json
from lib.project_migration_report import ArtifactBackfillOutcome
from lib.project_migrations.backups import ensure_versioned_backup
from lib.project_schema import parse_project_schema_version
from lib.style_templates import resolve_template_prompt

TARGET_SCHEMA_VERSION = 14

#: 更早的风格枚举值 → 当前风格模版 id。只有本迁移读它：解析一次、展开快照后该值不再出现。
LEGACY_STYLE_MAP: dict[str, str] = {
    "Photographic": "live_premium_drama",
    "Anime": "anim_kyoto",
    "3D Animation": "anim_3d_cg",
}

#: 风格值开头的「画风：」前缀（全角/半角冒号）。``anim_arcane`` 的「油画三渲二画风：」不以
#: 「画风」起头，因而不在此匹配之列——它的「画风」是复合词的一部分，不是可删前缀。
_STYLE_PREFIX_RE = re.compile(r"^画风[：:]\s*")


def migrate_project_dict(project: dict[str, Any]) -> dict[str, Any]:
    """纯函数：把 v13 形态的 project dict 转为 v14 形态。幂等。

    ``style`` 不是字符串时原样保留，其结构错误由 DataValidator 另行报告。短标签解析只在项目
    还没有 ``style_template_id`` 时发生：已有该字段说明用户此后选过风格，那时的选择是准的。
    参考图优先的口径沿用解析期的既有行为——有 ``style_image`` 时清空风格值、模版 id 置 null。
    不改 ``schema_version``（由文件级 migrate 提交时写入）。
    """
    data = copy.deepcopy(project)
    raw = data.get("style")
    if not isinstance(raw, str):
        return data
    if "style_template_id" not in data and raw in LEGACY_STYLE_MAP:
        if data.get("style_image"):
            data["style_template_id"] = None
            data["style"] = ""
        else:
            template_id = LEGACY_STYLE_MAP[raw]
            data["style_template_id"] = template_id
            data["style"] = resolve_template_prompt(template_id)
        return data
    stripped = raw.strip()
    if _STYLE_PREFIX_RE.match(stripped):
        data["style"] = _STYLE_PREFIX_RE.sub("", stripped)
    return data


def _plan(project_dir: Path, *, project_bytes: bytes | None = None) -> ArtifactTargetStatePlan:
    """规划一次完整目标态。``project_bytes`` 让改写后的规划不必先落盘。"""
    return TargetStatePlanner(project_dir, project_bytes=project_bytes).plan()


def _rebase_entries(
    stored: Mapping[ArtifactKey, ArtifactManifestEntry],
    before: Mapping[ArtifactKey, ArtifactManifestEntry],
    after: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> dict[ArtifactKey, ArtifactManifestEntry]:
    """挑出「改写前 current、改写后目标登记变了」的条目，给出它们改写后的登记。

    ``current != target_before`` 就是改写前已过期，原样留下。产物路径也必须一致：路径不同
    就不是同一件产物，重新指向它不属于本步该做的事。
    """
    rebased: dict[ArtifactKey, ArtifactManifestEntry] = {}
    for key, current in stored.items():
        target_before = before.get(key)
        target_after = after.get(key)
        if target_before is None or target_after is None:
            continue
        if current != target_before or target_after == target_before:
            continue
        if target_after.artifact_path != current.artifact_path:
            continue
        rebased[key] = target_after
    return rebased


def migrate_v13_to_v14(project_dir: Path) -> ArtifactBackfillOutcome | None:
    """v13→v14 文件级迁移。"""
    project_dir = Path(project_dir)
    pj = project_dir / "project.json"
    if not pj.exists():
        return None
    project_bytes = pj.read_bytes()
    data = json.loads(project_bytes)
    if not isinstance(data, dict):
        raise ValueError("project.json 必须是对象")
    if parse_project_schema_version(data) >= TARGET_SCHEMA_VERSION:
        return None
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = TARGET_SCHEMA_VERSION
    if migrated.get("style") != data.get("style"):
        # 只有风格值真的变了才规划：绝大多数项目的风格值已是归一形态，升级不该为它们付出
        # 两次完整目标态规划的代价。
        with project_metadata_lock(project_dir):
            before_plan = _plan(project_dir, project_bytes=project_bytes)
            after_plan = _plan(project_dir, project_bytes=json.dumps(migrated, ensure_ascii=False).encode("utf-8"))
            assert_artifact_target_state_plan_unchanged(project_dir, before_plan)
            assert_artifact_target_state_plan_unchanged(
                project_dir,
                after_plan,
                expected_project_bytes=project_bytes,
            )
            adapter = ProjectArtifactManifestAdapter(project_dir)
            rebased = _rebase_entries(adapter.snapshot_entries(), before_plan.entries, after_plan.entries)
            if rebased:
                ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
                expected = {key: before_plan.entries[key] for key in rebased}
                if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=rebased):
                    raise RuntimeError("artifact manifest changed while rebasing legacy style bases")
                try:
                    assert_artifact_target_state_plan_unchanged(project_dir, before_plan)
                    assert_artifact_target_state_plan_unchanged(
                        project_dir,
                        after_plan,
                        expected_project_bytes=project_bytes,
                    )
                except BaseException as original_error:
                    restored = adapter.replace_entries_if_matches_atomically(
                        expected=rebased,
                        replacements=expected,
                    )
                    if not restored and any(adapter.get_entry(key) != entry for key, entry in expected.items()):
                        raise RuntimeError(
                            "artifact manifest dependency drifted and rollback was incomplete"
                        ) from original_error
                    raise
            outcome = ArtifactBackfillOutcome.from_entries(adapter.snapshot_entries(), after_plan.skipped)
            atomic_write_json(pj, migrated)
    else:
        outcome = None
        atomic_write_json(pj, migrated)
    return outcome


__all__ = ["LEGACY_STYLE_MAP", "TARGET_SCHEMA_VERSION", "migrate_project_dict", "migrate_v13_to_v14"]
