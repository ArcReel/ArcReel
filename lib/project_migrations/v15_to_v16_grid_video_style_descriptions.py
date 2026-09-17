"""v15→v16 迁移：宫格与参考视频的产物依据补记风格描述。

自定义风格项目（上传过风格参考图）的 ``style`` 为空，``style_description`` 是唯一的风格信号
（``server/routers/files.py`` 的 ``upload_style_image`` 生产这一形态，是主路径）。宫格提示词与
参考视频提示词都渲染 ``Visual style:`` 行、消费它，但两者的产物依据此前只记 ``style``：用户换
一张参考图、描述被重新分析覆盖后，提示词已变，工作流仍把旧宫格、切格分镜与参考视频判为
current。这一步把 ``style_description`` 补进 grid-composite / grid-member / video-reference
三类依据的 inputs，口径与分镜图依据一致：保留扁平键 ``style``，描述非空才另记
``style_description``。asset-sheet 的嵌套形态与分镜图依据不动。

改编排不改项目数据：``project.json`` 除 ``schema_version`` 外逐字不变，依据摘要却一定会变——
三类依据的 inputs 从此可能多一个键——故本步在改写前后各规划一次目标态，把「改写前正是
current、且目标摘要因这次补记而变」的清单条目改写为改写后的登记：产物不因口径升级翻过期，
改写前就已过期的条目原样保留，不伪造时新性。

「改写前目标态」用删掉 ``style_description`` 的项目字节规划：条件式记录在描述为空时不写键，
删掉该字段后规划出的正是旧口径的摘要。它必须容忍逾期目标：参考视频的清单条目由记录里冻结的
依据与现算的重建依据比对而来（``lib/artifact_planner.py`` 的 ``_plan_one_typed_media``），而那份
冻结依据正是旧口径的那一件，严格规划会把它判成「依据已过期」而跳过——本步要改写的恰是它。
描述本就为空的存量项目两侧逐字一致，本步不为它们改写任何条目，也不为它们付出两次完整规划的
代价。

宫格只以清单条目承载依据，参考视频不是：成片读模型拿记录里冻结的
``artifact_video_currency`` 与现算的重建依据比对判时新（``server/services/presentation_read_model.py``
的 ``_video_currency``），清单条目改写不到它。故本步另把「冻结依据正是旧口径投影」的参考视频
记录按新口径重新盖章：补上描述键、重算合成依据，并记 ``provenance_backfilled_at``（与
``lib/legacy_media_provenance`` 的重投影同形）。只动两侧仅差描述键的记录，本就过期的记录原样
留下。两个读模型于是同口径：记录没被盖章的参考视频，严格规划也不会把它收进目标态，清单条目
随之保持原样，两侧一致地继续判过期。

提交顺序是记录 → 清单 → ``project.json``。记录改写是一次普通的 ``versions.json`` 原子写，必须排在
规划之前：改写后的目标态要读它，才能把按新口径重建的参考视频收进目标态。清单改写落盘而 schema
尚未提升时崩溃，本步会整步重跑，届时改写前规划仍从未动过的 ``project.json`` 算出——且它不看记录
当下的形态，旧口径由「删掉描述字段」的项目字节表达——已改写的条目不再匹配「改写前摘要」而被跳过，
既不重复改写也不丢失修复结果，重跑时记录改写则因不再匹配「旧口径投影」而无操作。反序（清单之后
才盖记录，或 schema 先提升）会在重跑时把已修复的条目认成陌生摘要，永久留下过期标记。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifact_planner import ArtifactTargetStatePlan, TargetStatePlanner
from lib.formal_write import project_metadata_lock
from lib.json_io import atomic_write_json, load_json
from lib.legacy_media_provenance import PROVENANCE_BACKFILLED_AT_FIELD
from lib.project_migration_report import ArtifactBackfillOutcome
from lib.project_migrations.backups import ensure_versioned_backup
from lib.project_schema import parse_project_schema_version
from lib.resource_paths import resource_relative_path
from lib.video_artifact_facts import VideoArtifactCurrencyFacts

TARGET_SCHEMA_VERSION = 16

#: 参考视频的冻结依据所在。两条视频路线的依据不同：本步只补记参考生视频那一条。
_REFERENCE_VIDEO_RESOURCE_TYPE = "reference_videos"


def has_style_description(project: Mapping[str, Any]) -> bool:
    """描述非空才可能让两套口径产生分歧：空描述下条件式记录不生效。"""

    value = project.get("style_description")
    return isinstance(value, str) and bool(value.strip())


def legacy_projection_bytes(project: Mapping[str, Any]) -> bytes:
    """改写前口径的规划输入：删掉描述字段，三类依据因此回到只记 ``style`` 的形态。"""

    stripped = copy.deepcopy(dict(project))
    stripped.pop("style_description", None)
    return json.dumps(stripped, ensure_ascii=False).encode("utf-8")


def _plan(
    project_dir: Path,
    *,
    project_bytes: bytes,
    allow_stale_formal_targets: bool = False,
) -> ArtifactTargetStatePlan:
    """规划一次完整目标态。``project_bytes`` 让改写前后的规划都不必先落盘。"""

    return TargetStatePlanner(
        project_dir,
        project_bytes=project_bytes,
        allow_stale_formal_targets=allow_stale_formal_targets,
    ).plan()


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


def _selected_record(resource: object) -> dict[str, Any] | None:
    """取该资源的当前版本记录：选中版本不唯一或形态不对都不改。"""

    if not isinstance(resource, Mapping):
        return None
    selected_version = resource.get("current_version")
    records = resource.get("versions")
    if type(selected_version) is not int or not isinstance(records, list):
        return None
    selected = [record for record in records if isinstance(record, dict) and record.get("version") == selected_version]
    return selected[0] if len(selected) == 1 else None


def _restamped_facts(
    facts: VideoArtifactCurrencyFacts,
    *,
    description: str,
) -> VideoArtifactCurrencyFacts | None:
    """给冻结的视觉依据补上描述键并重算合成依据，形态不合则交回 ``None``。

    ``VideoArtifactCurrencyFacts`` 构造即自校验：合成依据必须由三个分量重算得出、视觉 inputs
    必须合本路线的形状。所以这一步产出的就是「同一份内容按新口径生成」会记下的那一份，改写不会
    伪造时新性。判据只看冻结依据本身，不看现盘内容。
    """

    inputs = facts.visual_basis.to_evidence_dict().get("inputs")
    if not isinstance(inputs, Mapping) or "style_description" in inputs:
        return None
    # 与构造口径一致：描述去掉首尾空白后非空才落键。
    normalized = description.strip()
    if not normalized:
        return None
    try:
        visual = ArtifactBasis.build(
            facts.visual_basis.kind,
            kind_version=facts.visual_basis.kind_version,
            inputs={**inputs, "style_description": normalized},
        )
        return replace(
            facts,
            visual_basis=visual,
            video_basis=compose_video_artifact_basis(
                visual=visual,
                speech=facts.speech_basis,
                duration=facts.duration_basis,
            ),
        )
    except (TypeError, ValueError):
        return None


def restamp_reference_video_provenance(
    project_dir: Path,
    before: Mapping[ArtifactKey, ArtifactManifestEntry],
    *,
    description: str,
) -> frozenset[str]:
    """把「冻结依据正是旧口径投影」的参考视频记录按新口径重新盖章。

    返回被改写的产物路径。``before`` 里那份摘要就是旧口径下这份内容的投影，冻结依据只有与它
    相符才能说明描述键是唯一差异；本就过期的记录原样留下，不趁机洗成时新。只有真的改写了才写盘。
    """

    versions_path = project_dir / "versions" / "versions.json"
    if not versions_path.is_file():
        return frozenset()
    versions_data = load_json(versions_path)
    if not isinstance(versions_data, dict):
        return frozenset()
    bucket = versions_data.get(_REFERENCE_VIDEO_RESOURCE_TYPE)
    if not isinstance(bucket, Mapping):
        return frozenset()
    legacy_digests = {
        entry.artifact_path: entry.basis_digest
        for key, entry in before.items()
        if key.kind is ArtifactKind.EPISODE_VIDEO
    }
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    restamped: set[str] = set()
    for resource_id, resource in bucket.items():
        if not isinstance(resource_id, str):
            continue
        record = _selected_record(resource)
        if record is None:
            continue
        artifact_path = resource_relative_path(_REFERENCE_VIDEO_RESOURCE_TYPE, resource_id)
        legacy_digest = legacy_digests.get(artifact_path)
        if legacy_digest is None:
            continue
        try:
            facts = VideoArtifactCurrencyFacts.from_dict(record.get("artifact_video_currency"))
        except (TypeError, ValueError):
            continue
        if facts.video_descriptor.digest != legacy_digest:
            continue
        amended = _restamped_facts(facts, description=description)
        if amended is None:
            continue
        record["artifact_video_currency"] = amended.to_dict()
        record[PROVENANCE_BACKFILLED_AT_FIELD] = stamp
        restamped.add(artifact_path)
    if restamped:
        ensure_versioned_backup(versions_path, TARGET_SCHEMA_VERSION - 1)
        atomic_write_json(versions_path, versions_data)
    return frozenset(restamped)


def migrate_v15_to_v16(project_dir: Path) -> ArtifactBackfillOutcome | None:
    """v15→v16 文件级迁移。"""

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
    migrated = {**data, "schema_version": TARGET_SCHEMA_VERSION}
    if not has_style_description(data):
        # 描述为空的存量项目两套口径逐字一致：升级不该为它们付出两次完整目标态规划的代价。
        atomic_write_json(pj, migrated)
        return None
    description = str(data.get("style_description") or "").strip()
    with project_metadata_lock(project_dir):
        # 两次规划都不看盘上的字节：改写前的口径由「删掉描述字段」表达，改写后仍是当前数据。
        # 改写前这次容忍逾期目标——参考视频的冻结依据正是旧口径的那一件，严格规划会跳过它。
        before_plan = _plan(
            project_dir,
            project_bytes=legacy_projection_bytes(data),
            allow_stale_formal_targets=True,
        )
        assert_artifact_target_state_plan_unchanged(project_dir, before_plan, expected_project_bytes=project_bytes)
        restamp_reference_video_provenance(
            project_dir,
            before_plan.entries,
            description=description,
        )
        after_plan = _plan(project_dir, project_bytes=json.dumps(migrated, ensure_ascii=False).encode("utf-8"))
        assert_artifact_target_state_plan_unchanged(project_dir, after_plan, expected_project_bytes=project_bytes)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        rebased = _rebase_entries(adapter.snapshot_entries(), before_plan.entries, after_plan.entries)
        if rebased:
            ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
            expected = {key: before_plan.entries[key] for key in rebased}
            if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=rebased):
                raise RuntimeError("artifact manifest changed while rebasing style-description bases")
            try:
                # 只复核改写后这份：改写前那份的前提是记录里冻结的旧口径依据，本步的重盖章
                # 会把它换掉，那份断言只能留在重盖章之前。
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
        # 只有本步真的改写了登记才交出报告：runner 只留链上最后一份，没改写时覆盖它只会丢掉
        # 上一步记下的转换失败。重盖章的记录不是清单条目，报告口径仍以清单为准。
        outcome = (
            ArtifactBackfillOutcome.from_entries(adapter.snapshot_entries(), after_plan.skipped) if rebased else None
        )
        atomic_write_json(pj, migrated)
    return outcome


__all__ = [
    "TARGET_SCHEMA_VERSION",
    "has_style_description",
    "legacy_projection_bytes",
    "migrate_v15_to_v16",
    "restamp_reference_video_provenance",
]
