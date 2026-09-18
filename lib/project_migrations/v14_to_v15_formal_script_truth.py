"""v14→v15 迁移：正式脚本成为该集唯一的内容真相，脚本规划的条目指纹与增量合并随之退役（ADR 0080）。

存量项目在这一步收编成新机制的形态：

- **集绑定**：``script_file`` 只认该集的规范剧本 ``scripts/episode_N.json``（``episode_N.json``
  这类归一后同名的写法算同一条绑定，账本字面不动）。指向别处的绑定与同一集号在账本里出现多条时
  整个项目在写盘前被拒，失败裁决写明集号与绑定；本步不改名、不另存、不改写任何引用，运维把剧本
  挪到规范路径并改绑后重跑。绑定缺席的集按规范路径处理。
- **指纹字段**：剧本条目的 ``script_plan_entry_revision`` 与剧本 metadata 的 ``script_plan_revision``
  删除。
- **已确认、尚无正式脚本的集**：按确认过的脚本规划整份转为正式脚本（全部条目待编写），与内容
  确认同一份投影（``lib.script_document.build_materialized_script``）。投影不出来或结构校验不过的集
  不转换，进迁移报告。
- **已有正式脚本的集**：分镜（segments / scenes / shots）视觉层两侧都为空的盖上待编写标记；
  drama 分镜缺 ``scene_description`` 的从脚本规划回填；参考生视频单元缺 ``source_text`` 的从脚本
  规划补录，取不到留空。
- **grandfather 集**（有正式脚本、有脚本规划、无确认记录）：把当前规划指纹记为确认基线，此后
  重跑脚本规划即回到待确认。账本 stale 的集不记：它的
  确认要由重规划后的首次确认给出。
- **剧本清单登记**：剧本 basis 自 v3 起不以脚本规划为输入。改写前正是时新的登记（v2 按当前规划
  算出的摘要，或无计划依据的摘要）改写为 v3 登记，本就过期的登记原样保留；新转出与此前未登记
  的在场剧本按 v3 登记。

ad 项目没有脚本规划，本步只提升版本号。提交顺序是剧本 → 清单 → ``project.json``：中途崩溃时整步
重跑，已写的剧本与清单都按「已是目标形态」跳过，``project.json`` 的版本号最后落盘。
"""

from __future__ import annotations

import contextlib
import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_planner import ArtifactTargetStatePlan, TargetStatePlanner, normalize_script_binding
from lib.artifact_provenance import SCRIPT_PLAN_BASIS_INPUT_KEY, project_episode_script_prompt_inputs
from lib.episode_paths import episode_script_relpath
from lib.formal_write import project_metadata_lock
from lib.json_io import atomic_write_json
from lib.path_safety import try_safe_join
from lib.project_manager import is_episode_number
from lib.project_migration_failure import ProjectMigrationError
from lib.project_migration_report import ArtifactBackfillOutcome, MigrationSkippedArtifact
from lib.project_migrations.backups import ensure_versioned_backup
from lib.project_schema import parse_project_schema_version
from lib.script_document import build_materialized_script
from lib.script_models import PENDING_AUTHORING_FIELD
from lib.script_plan_entries import ScriptPlanKind, entry_id_field, plan_entries_from_document, plan_variant
from lib.script_review import (
    REVIEW_FIELD,
    content_fingerprint,
    script_plan_kind,
    script_plan_path,
    stored_review,
)
from lib.script_skeleton import SKELETONS, rewrite_episode_prefix
from lib.script_structure_validator import validate_script_structure

TARGET_SCHEMA_VERSION = 15

#: 存量剧本上退役的两个指纹字段名。历史事实，写死在这一步。
_ENTRY_REVISION_FIELD = "script_plan_entry_revision"
_SCRIPT_REVISION_FIELD = "script_plan_revision"

#: 分镜形态条目的视觉层字段；参考生视频单元的视觉层是正文本身，不按「为空」判待编写。
_STORYBOARD_KINDS = ("segments", "scenes", "shots")
_VISUAL_FIELDS = ("image_prompt", "video_prompt")

#: 剧本 basis 的历史版本：v2 以脚本规划内容为输入，无计划依据的版本只含集号。
_LEGACY_SCRIPT_BASIS_KIND = "structured-content/episode-script"
_LEGACY_PLANLESS_SCRIPT_BASIS_KIND = "structured-content/episode-script-without-plan"


@dataclass
class _EpisodeWork:
    episode: int
    #: 剧本在盘上的项目内相对路径，恒为该集规范路径。
    script_rel: str
    #: 改写后的剧本；None 表示剧本不需要改写。
    script: dict[str, Any] | None = None
    #: 本集是新转出的正式脚本。
    materialized: bool = False


@dataclass
class _Plan:
    project: dict[str, Any]
    works: list[_EpisodeWork] = field(default_factory=list)
    #: 集号 → 该集参与清单改写的剧本路径与改写前的脚本规划内容（无规划为 None）。
    registrations: dict[int, tuple[str, object | None]] = field(default_factory=dict)
    skipped: list[MigrationSkippedArtifact] = field(default_factory=list)


def _visual_empty(value: object) -> bool:
    return not (value.strip() if isinstance(value, str) else value)


def _load_object(path: Path) -> dict[str, Any] | None:
    """读一个 JSON 对象；缺失、损坏或非对象都返回 None（不在本步修复）。"""

    try:
        parsed = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_canonical_binding(raw_binding: str, episode: int) -> bool:
    r"""这条绑定指的是不是该集的规范剧本 ``scripts/episode_N.json``。

    归一直接调用规划器的 ``normalize_script_binding``，不另写一份：账本里的绑定最终要由目标态规划
    解析，判据分叉就会让规划认不出的写法（``./scripts/episode_1.json``、带目录段的
    ``scripts/archive\custom.json``，反斜杠写法同样算）从这里漏过去，随后整个项目在规划处被拒，
    而那时剧本已经改写落盘。规划认得出、且正指向该集规范剧本的写法（``episode_1.json`` 与
    ``scripts/episode_1.json``）是同一条绑定，账本字面不动。
    """

    try:
        return normalize_script_binding(raw_binding) == episode_script_relpath(episode)
    except ValueError:
        return False


def _strip_revisions(script: dict[str, Any]) -> None:
    metadata = script.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop(_SCRIPT_REVISION_FIELD, None)
    for skeleton in SKELETONS:
        items = script.get(skeleton)
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                item.pop(_ENTRY_REVISION_FIELD, None)


def _plan_items_by_id(kind: ScriptPlanKind, plan_document: object, episode: int) -> dict[str, dict[str, object]]:
    entries = plan_entries_from_document(kind, plan_document)
    id_field = entry_id_field(kind)
    by_id: dict[str, dict[str, object]] = {}
    for entry in entries:
        entry_id = rewrite_episode_prefix(entry.get(id_field), episode)
        if isinstance(entry_id, str) and entry_id and entry_id not in by_id:
            by_id[entry_id] = entry
    return by_id


def _fill_from_plan(
    kind: ScriptPlanKind | None, script: dict[str, Any], plan_document: object | None, episode: int
) -> None:
    """已有正式脚本的集：标记视觉层为空的分镜，回填 drama 视觉基底与参考单元对应原文。"""

    if kind is None:
        return
    variant = plan_variant(kind)
    items = script.get(variant.skeleton_kind)
    if not isinstance(items, list):
        return
    id_field = entry_id_field(kind)
    plan_items = _plan_items_by_id(kind, plan_document, episode) if plan_document is not None else {}
    for item in items:
        if not isinstance(item, dict):
            continue
        plan_item = plan_items.get(str(item.get(id_field)))
        if variant.skeleton_kind in _STORYBOARD_KINDS and all(_visual_empty(item.get(f)) for f in _VISUAL_FIELDS):
            item[PENDING_AUTHORING_FIELD] = True
        if kind == "drama" and plan_item is not None and not item.get("scene_description"):
            description = plan_item.get("scene_description")
            if isinstance(description, str) and description.strip():
                item["scene_description"] = description
        if kind == "reference_video" and not item.get("source_text"):
            source_text = plan_item.get("source_text") if plan_item is not None else None
            item["source_text"] = source_text if isinstance(source_text, str) else ""


def _materialize(
    project: dict[str, Any], kind: ScriptPlanKind, plan_document: object, episode: int
) -> tuple[dict[str, Any] | None, str | None]:
    """按确认过的脚本规划投影整份正式脚本；投影不出来时返回 (None, 原因)。"""

    entries = plan_entries_from_document(kind, plan_document)
    if not entries:
        return None, "confirmed script_plan has no readable entries"
    id_field = entry_id_field(kind)
    ids = [rewrite_episode_prefix(entry.get(id_field), episode) for entry in entries]
    if any(not isinstance(entry_id, str) or not entry_id for entry_id in ids) or len(set(ids)) != len(ids):
        return None, "confirmed script_plan entry ids are missing or duplicated"
    raw_title = plan_document.get("title") if isinstance(plan_document, Mapping) else None
    title = raw_title if kind == "drama" and isinstance(raw_title, str) and raw_title.strip() else None
    script = build_materialized_script(
        project,
        episode,
        plan_kind=kind,
        plan_entries=entries,
        title=title,
    )
    if not validate_script_structure(script).valid:
        return None, "materialized script from the confirmed script_plan fails structure validation"
    # 与 ``ProjectManager.save_script`` 写盘时补的默认状态一致。
    script["metadata"].setdefault("status", "draft")
    return script, None


def _preflight(project_dir: Path, project: dict[str, Any]) -> _Plan:
    """只读：算出全部剧本改写、转换与 project.json 改写，不落盘。"""

    migrated = copy.deepcopy(project)
    plan = _Plan(project=migrated)
    kind = script_plan_kind(migrated)
    raw_episodes = migrated.get("episodes")
    episodes: list[Any] = raw_episodes if isinstance(raw_episodes, list) else []
    seen_episodes: set[int] = set()
    for entry in episodes:
        if not isinstance(entry, dict):
            continue
        entry_episode = entry.get("episode")
        if not is_episode_number(entry_episode):
            continue
        if entry_episode in seen_episodes:
            # 同一集号出现两次时账本认不出哪一条是这一集：目标态规划按「绑定不唯一」拒绝整个项目，
            # 而那时剧本已经改写落盘。在只读预检里拒绝，裁决写明集号与绑定。
            raise ProjectMigrationError(
                "ledger has more than one entry for this episode",
                episode=entry_episode,
                file=entry.get("script_file") if isinstance(entry.get("script_file"), str) else "project.json",
            )
        seen_episodes.add(entry_episode)
        raw_binding = entry.get("script_file")
        if isinstance(raw_binding, str) and raw_binding and not _is_canonical_binding(raw_binding, entry_episode):
            # 非规范绑定一律不认：本步不改名也不改写引用，拒绝整个项目并把集号与绑定写进裁决，
            # 运维把剧本挪到规范路径、改绑后重跑。
            raise ProjectMigrationError(
                "script binding is not this episode's canonical script", episode=entry_episode, file=raw_binding
            )
    confirmed_at = datetime.now(UTC).isoformat()
    for entry in episodes:
        if not isinstance(entry, dict):
            continue
        episode = entry.get("episode")
        if not is_episode_number(episode):
            continue
        canonical = episode_script_relpath(episode)
        binding_path = try_safe_join(project_dir, canonical)
        plan_path = script_plan_path(project_dir, migrated, episode) if kind is not None else None
        plan_document = _load_object(plan_path) if plan_path is not None else None
        # 读不成对象的规划文件没有可转换或回填的条目，也不据它记确认基线。
        plan_fingerprint = (
            content_fingerprint(plan_path) if plan_path is not None and plan_document is not None else None
        )

        if binding_path is not None and binding_path.is_file():
            script = _load_object(binding_path)
            if script is None:
                continue
            original = copy.deepcopy(script)
            _strip_revisions(script)
            if migrated.get("content_mode") != "ad":
                _fill_from_plan(kind, script, plan_document, episode)
            work = _EpisodeWork(episode=episode, script_rel=canonical, script=script if script != original else None)
            plan.works.append(work)
            plan.registrations[episode] = (canonical, plan_document)
            if (
                plan_fingerprint is not None
                and entry.get("ledger_status") != "stale"
                and stored_review(migrated, episode).get("fingerprint") is None
            ):
                entry[REVIEW_FIELD] = {"fingerprint": plan_fingerprint, "confirmed_at": confirmed_at}
            continue

        if kind is None or plan_document is None or plan_fingerprint is None:
            continue
        if stored_review(migrated, episode).get("fingerprint") != plan_fingerprint:
            continue
        script, reason = _materialize(migrated, kind, plan_document, episode)
        if script is None:
            _skip_script(plan, episode, canonical, reason or "confirmed script_plan could not be materialized")
            continue
        entry["title"] = script.get("title", "")
        entry["script_file"] = canonical
        plan.works.append(_EpisodeWork(episode=episode, script_rel=canonical, script=script, materialized=True))
        plan.registrations[episode] = (canonical, plan_document)
    return plan


def _skip_script(plan: _Plan, episode: int, artifact_path: str, reason: str) -> None:
    """已确认的集转不出正式脚本：记进迁移报告。"""

    plan.skipped.append(
        MigrationSkippedArtifact(
            kind=ArtifactKey.episode_script(episode).kind.value,
            episode=episode,
            resource_id=str(episode),
            artifact_path=artifact_path,
            reason=reason,
        )
    )


def _legacy_script_bases(project: Mapping[str, Any], episode: int, plan_document: object | None) -> list[str]:
    """改写前剧本登记可能的两种时新摘要：v2（按当前规划）与无计划依据。"""

    digests = [
        ArtifactBasis.build(
            _LEGACY_PLANLESS_SCRIPT_BASIS_KIND,
            kind_version=1,
            inputs={"episode": episode},
        ).digest
    ]
    if plan_document is not None:
        with contextlib.suppress(TypeError, ValueError):
            digests.append(
                ArtifactBasis.build(
                    _LEGACY_SCRIPT_BASIS_KIND,
                    kind_version=2,
                    inputs={
                        "content_mode": project.get("content_mode"),
                        "generation_mode": project.get("generation_mode"),
                        SCRIPT_PLAN_BASIS_INPUT_KEY: plan_document,
                        "prompt_context": project_episode_script_prompt_inputs(project),
                    },
                ).digest
            )
    return digests


def _rewrite_manifest_entries(
    project_dir: Path,
    before_project: Mapping[str, Any],
    target: ArtifactTargetStatePlan,
    plan: _Plan,
) -> int:
    """剧本登记改写为 v3。

    改写前时新或未登记的改写过去，本就过期的原样保留。目标登记取自整份目标态规划（不校验项目
    是否为当前 schema，链上后续版本存在时照常可用）。返回改写条数。
    """

    adapter = ProjectArtifactManifestAdapter(project_dir)
    snapshot = adapter.snapshot_entries()
    expected: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    replacements: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    rebase_scripts = before_project.get("content_mode") != "ad"
    for episode, (_script_rel, plan_document) in sorted(plan.registrations.items()) if rebase_scripts else []:
        key = ArtifactKey.episode_script(episode)
        entry = target.entries.get(key)
        if entry is None:
            continue
        stored = snapshot.get(key)
        if stored == entry:
            continue
        if stored is not None and (
            stored.artifact_path != entry.artifact_path
            or stored.basis_digest not in _legacy_script_bases(before_project, episode, plan_document)
        ):
            continue
        expected[key] = stored
        replacements[key] = entry
    if replacements:
        ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
        if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=replacements):
            raise RuntimeError("artifact manifest changed while rewriting episode script entries")
    return len(replacements)


def migrate_v14_to_v15(project_dir: Path) -> ArtifactBackfillOutcome | None:
    """v14→v15 文件级迁移。"""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return None
    project_bytes = project_file.read_bytes()
    project = json.loads(project_bytes)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
        return None

    plan = _preflight(project_dir, project)
    plan.project["schema_version"] = TARGET_SCHEMA_VERSION

    ensure_versioned_backup(project_file, TARGET_SCHEMA_VERSION - 1)
    for work in plan.works:
        if work.script is not None and not work.materialized:
            ensure_versioned_backup(project_dir / work.script_rel, TARGET_SCHEMA_VERSION - 1)
    for work in plan.works:
        if work.script is not None:
            path = try_safe_join(project_dir, work.script_rel)
            if path is None:
                raise ValueError(f"第 {work.episode} 集剧本路径越界: {work.script_rel}")
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, work.script)

    after_bytes = json.dumps(plan.project, ensure_ascii=False).encode("utf-8")
    reported = bool(plan.skipped)
    if not plan.registrations or project.get("content_mode") == "ad":
        outcome = _outcome(project_dir, _target_plan(project_dir, after_bytes), plan) if reported else None
        atomic_write_json(project_file, plan.project)
        return outcome
    with project_metadata_lock(project_dir):
        target = _target_plan(project_dir, after_bytes)
        rewritten = _rewrite_manifest_entries(project_dir, project, target, plan)
        outcome = _outcome(project_dir, target, plan) if rewritten or reported else None
        atomic_write_json(project_file, plan.project)
    return outcome


def _target_plan(project_dir: Path, after_bytes: bytes) -> ArtifactTargetStatePlan:
    return TargetStatePlanner(project_dir, project_bytes=after_bytes).plan()


def _outcome(project_dir: Path, target: ArtifactTargetStatePlan, plan: _Plan) -> ArtifactBackfillOutcome:
    """本步的迁移报告：runner 只留链上最后一份清单全貌，所以并入整份目标态规划的跳过项。"""

    return ArtifactBackfillOutcome.from_entries(
        ProjectArtifactManifestAdapter(project_dir).snapshot_entries(),
        plan.skipped,
        target.skipped,
    )


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_v14_to_v15"]
