"""迁移链在第一次规划产物目标态之前，把过去正常流程写出的旧形态规整成当前代码认得的形态（ADR 0098）。

v6→v7 的剧本改写、产物目标态规划（v7→v8 起的各步与归档导入）与 v8→v9、v9→v10、v14→v15 曾把
这些形态当作数据错误拒绝整个项目；它们其实是旧版本代码自己写出来的。这里按确定规则规整，有取舍时另一份留作永久保留副本
（:func:`lib.project.project_migrations.backups.set_aside_as_kept_copy`），每处改动都作为
:class:`MigrationAdjustment` 进迁移报告：

- **账本里同一集号出现多条**：留一条（绑定的剧本在盘上、且优先规范路径的那条，都不在时留第一条），
  其余从账本去掉；它们绑定的剧本文件原样留在盘上，改前的 ``project.json`` 留作保留副本。
- **不同集号绑到同一份剧本**（旧版按剧本自报的集号同步账本）：剧本留给它自己声明的那一集，
  都不是时留给账本里靠前的那一集；其余各集改绑到自己的规范路径。
- **集绑定不是 ``scripts/episode_N.json``**（schema < 15；旧版 SSE 索引同步登记的任意剧本）：
  剧本挪到规范路径并改绑。规范路径上已有另一份内容时，那一份先挪成保留副本。产物清单里该集剧本
  的登记、版本记录里的 ``execution_script_file`` 与宫格记录里的 ``script_file`` 随之改写，已登记
  的产物不因改绑丢失归属。
- **剧本顶层的集号缺失或与绑定不符**（旧版同步与保存都接受缺集号的剧本）：以账本绑定为准写回。
- **同一资产表里名字归一后相同**（去两端空白、Unicode NFC、不区分大小写），或名字归一后为空：
  留第一条，其余从资产表去掉；资产图文件不动，改前的 ``project.json`` 留作保留副本。去掉的条目
  名下的衍生按资产合并的规则并进保留条目（迁走的衍生图搬到保留名下），各集剧本与草稿里指向只差
  大小写那条的引用改写为保留名。
- **参考图或参考音频指向别的资产名下的文件**（旧版资产 PATCH 接受任意路径）：复制一份到本资产名下并改指
  过去，原文件留给它的主人。本资产名下的落点已被另一份内容占着时，那一份先挪成保留副本。参考文件按名归属，
  之后的改名、删除与恢复只处理本资产名下的文件。
- **剧本条目缺 id 或 id 重复**：缺的补新 id，重复的从第二条起换新 id（取号规则同新增条目）；换号
  条目上指向原 id 规范媒体路径的产物引用清空——那些文件属于保留原 id 的条目；只描述这些媒体的
  生成状态一并清空，换号条目读作尚未生成。
- **两集里有同号条目**：媒体与登记归 id 前缀所指的那一集，对不上时归账本里靠前的那一集；另一集的
  条目在本集换新 id，不带走媒体。
- **新旧草稿名并存**（schema < 10）：以新名为准，旧名文件挪成保留副本。

读不出的 ``project.json`` 或剧本不在这里处理：之后的迁移步照旧拒绝整个项目。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, cast

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ProjectArtifactManifestAdapter,
    normalize_artifact_path,
)
from lib.artifacts.formal_write import formal_write_transaction
from lib.episode.episode_paths import episode_drafts_dir, episode_script_relpath
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.infra.path_safety import try_safe_join
from lib.project.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    derivative_artifact_id,
    derivative_artifact_key,
    derivative_sheet_relative_path,
    derivative_table,
    ensure_derivative_table,
)
from lib.project.asset_merge import DerivativeMergePlan, merge_payload_references, plan_derivative_merge
from lib.project.asset_rename import REFERENCE_FILE_FIELDS, owns_named_file
from lib.project.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
    asset_name_comparison_key,
    asset_name_namespace_key,
    normalize_asset_name,
    resolve_asset_key,
)
from lib.project.project_migration_report import MigrationAdjustment
from lib.project.project_migrations.backups import ensure_versioned_backup, set_aside_as_kept_copy
from lib.project.project_migrations.v9_to_v10_script_plan_naming import DRAFT_FILE_RENAMES
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_relative_path
from lib.script.script_editor import ScriptEditError, new_item_id, resolve_items

logger = logging.getLogger(__name__)

#: 从这个版本起账本与资产名称空间都已就位；规整先于 v6→v7（它按账本逐集改写剧本）与之后的
#: 每一次产物目标态规划。
NORMALIZE_FROM_SCHEMA_VERSION = 6
_CANONICAL_BINDING_SCHEMA_VERSION = 15
_DRAFT_RENAME_SCHEMA_VERSION = 10
#: v9→v10 改名之前的落盘名：脚本规划草稿的新名 → 旧名，以及集条目上的确认记录字段。
_LEGACY_DRAFT_NAMES = {new: old for old, new in DRAFT_FILE_RENAMES.items()}
_LEGACY_REVIEW_FIELD = "step1_review"
_DERIVATIVE_SHEET_FIELD = ASSET_SPECS[DERIVATIVE_ASSET_TYPE].sheet_field

#: 条目产物字段 → 资源类型；参考生视频单元的视频在 ``reference_videos`` 下。
_MEDIA_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("storyboard_image", "storyboards", "storyboards"),
    ("video_clip", "videos", "reference_videos"),
    ("narration_audio", "audio", "audio"),
)
#: 只描述某个主媒体的生成状态：主媒体不在时它们没有可描述的对象。
_MEDIA_COMPANION_FIELDS: dict[str, tuple[str, ...]] = {
    "storyboard_image": ("storyboard_last_image", "grid_id", "grid_cell_index"),
    "video_clip": ("video_thumbnail", "video_uri", "video_generated_at"),
}


@dataclass
class _Work:
    project_dir: Path
    schema_version: int
    project: dict[str, Any]
    #: 内存里的 ``project`` 有尚未写盘的改动。
    project_changed: bool = False
    #: 已落盘的规整项：重跑发现不了它们，中途失败也要记下。改动 ``project.json`` 的规整项排在最前。
    adjusted: list[MigrationAdjustment] = field(default_factory=list)
    #: 改动 ``project.json`` 的规整项；写盘后才移进 ``adjusted``，保留副本路径要等第一次写盘时才知道。
    project_adjustments: list[MigrationAdjustment] = field(default_factory=list)
    project_recorded: int = 0
    project_committed: bool = False
    project_backup: str | None = None

    def rel(self, path: Path) -> str:
        return path.relative_to(self.project_dir).as_posix()


def _episode_entries(project: dict[str, Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    episodes = project.get("episodes")
    if not isinstance(episodes, list):
        return
    for entry in episodes:
        if not isinstance(entry, dict):
            continue
        episode = entry.get("episode")
        if type(episode) is int and episode >= 1:
            yield episode, entry


def _bound_path(project_dir: Path, binding: object) -> Path | None:
    if not isinstance(binding, str) or not binding:
        return None
    return try_safe_join(project_dir, binding.replace("\\", "/"))


def _load_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _drop_duplicate_episodes(work: _Work) -> None:
    episodes = work.project.get("episodes")
    if not isinstance(episodes, list):
        return
    groups: dict[int, list[dict[str, Any]]] = {}
    for episode, entry in _episode_entries(work.project):
        groups.setdefault(episode, []).append(entry)
    dropped: list[tuple[int, dict[str, Any]]] = []
    for episode, entries in groups.items():
        if len(entries) < 2:
            continue
        canonical = work.project_dir / episode_script_relpath(episode)

        def rank(entry: dict[str, Any], canonical: Path = canonical) -> int:
            path = _bound_path(work.project_dir, entry.get("script_file"))
            if path is None or not path.is_file():
                return 2
            return 0 if path == canonical else 1

        keeper = min(entries, key=rank)
        dropped.extend((episode, entry) for entry in entries if entry is not keeper)
    if not dropped:
        return
    dropped_ids = {id(entry) for _episode, entry in dropped}
    work.project["episodes"] = [entry for entry in episodes if id(entry) not in dropped_ids]
    work.project_changed = True
    for episode, entry in dropped:
        binding = entry.get("script_file")
        work.project_adjustments.append(
            MigrationAdjustment(
                code="duplicate_episode_entry_removed",
                episode=episode,
                file=binding if isinstance(binding, str) and binding else None,
            )
        )


def _split_shared_bindings(work: _Work) -> None:
    """不同集号绑到同一份剧本时，剧本留给它自己声明的那一集（都不是时留给账本里靠前的那一集），
    其余各集改绑到自己的规范路径；那里没有文件时该集就是还没有剧本。"""

    groups: dict[Path, list[tuple[int, dict[str, Any]]]] = {}
    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is not None:
            groups.setdefault(path, []).append((episode, entry))
    for path, sharers in groups.items():
        if len({episode for episode, _entry in sharers}) < 2:
            continue
        script = _load_object(path) if path.is_file() and not path.is_symlink() else None
        if script is None and path.is_file():
            # 读不出的剧本不归规整：绑定原样留下，之后的迁移步照旧拒绝整个项目。
            continue
        claimed = script.get("episode") if script is not None else None
        owner = next((sharer for sharer in sharers if sharer[0] == claimed), sharers[0])
        for episode, entry in sharers:
            if episode == owner[0]:
                continue
            raw = entry["script_file"]
            canonical_rel = episode_script_relpath(episode)
            entry["script_file"] = canonical_rel
            work.project_changed = True
            work.project_adjustments.append(
                MigrationAdjustment(
                    code="shared_script_binding_split",
                    episode=episode,
                    file=canonical_rel,
                    params={"from": raw, "to": canonical_rel, "owner": str(owner[0])},
                )
            )


def _correct_script_episodes(work: _Work) -> None:
    """剧本顶层的集号以账本绑定为准：缺了就补上，与绑定不符就改成绑定的集号。"""

    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is None or not path.is_file() or path.is_symlink():
            continue
        script = _load_object(path)
        if script is None:
            continue
        current = script.get("episode")
        if type(current) is int and current == episode:
            continue
        script["episode"] = episode
        ensure_versioned_backup(path, work.schema_version)
        atomic_write_json(path, script)
        work.adjusted.append(
            MigrationAdjustment(
                code="script_episode_corrected",
                episode=episode,
                file=work.rel(path),
                params={"from": "" if current is None else str(current), "to": str(episode)},
            )
        )


def _commit_project(work: _Work, *, keep_copy: bool = False) -> None:
    """把内存里的 ``project`` 写回盘上，写过的 ``project.json`` 规整项随之记进 ``adjusted``。

    第一次写之前留备份：有改动 ``project.json`` 的规整项时留保留副本（报告指向它），否则留版本化备份。
    ``keep_copy`` 用于规整项尚未登记就要先提交的情形，让之后的规整项仍能指向改前的那份。
    """

    project_file = work.project_dir / "project.json"
    if not work.project_committed:
        if work.project_adjustments or keep_copy:
            work.project_backup = work.rel(set_aside_as_kept_copy(project_file, work.schema_version, copy=True))
        else:
            ensure_versioned_backup(project_file, work.schema_version)
        work.project_committed = True
    atomic_write_json(project_file, work.project)
    work.project_changed = False
    committed = [
        item.model_copy(update={"backup": work.project_backup})
        for item in work.project_adjustments[work.project_recorded :]
    ]
    work.adjusted[work.project_recorded : work.project_recorded] = committed
    work.project_recorded = len(work.project_adjustments)


@dataclass
class _Rebind:
    """一集从非规范绑定改到规范路径。

    ``source`` 是要挪过去的剧本文件，没有可挪的文件时为 ``None``；``old_paths`` 是版本记录与宫格记录里
    可能指向它的旧写法。
    """

    episode: int
    entry: dict[str, Any]
    raw: str
    old_paths: set[str]
    source: Path | None

    @property
    def canonical_rel(self) -> str:
        return episode_script_relpath(self.episode)


def _staged_path(bound: Path, episode: int) -> Path:
    """交叉绑定成环时剧本的临时名。由原绑定与集号确定，中断后重跑据此认回。"""

    return bound.with_name(f".{bound.name}.rebind-{episode}")


def _unstaged_path(path: Path, episode: int) -> Path | None:
    """``path`` 是这一集成环时的临时名时，返回挪到临时名之前的原绑定。"""

    prefix, suffix = ".", f".rebind-{episode}"
    if not (path.name.startswith(prefix) and path.name.endswith(suffix)):
        return None
    name = path.name[len(prefix) : -len(suffix)]
    return path.with_name(name) if name else None


def _rewrite_script_references(work: _Work, episode: int, old_paths: set[str], target_rel: str) -> None:
    """让版本记录与宫格记录里指向该集旧剧本的引用改到 ``target_rel``。

    写入的是不带目录的文件名，与生成任务、宫格请求写下的形态一致（宫格按字面比对剧本）。
    """

    target = target_rel.removeprefix("scripts/")

    def points_at_old(value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        normalized = value.replace("\\", "/").removeprefix("./")
        return normalized in old_paths or f"scripts/{normalized}" in old_paths

    versions_path = work.project_dir / "versions" / "versions.json"
    versions = _load_object(versions_path) if versions_path.is_file() else None
    if versions is not None:
        changed = False
        for bucket in versions.values():
            if not isinstance(bucket, dict):
                continue
            for resource in bucket.values():
                records = resource.get("versions") if isinstance(resource, dict) else None
                for record in records if isinstance(records, list) else []:
                    if isinstance(record, dict) and points_at_old(record.get("execution_script_file")):
                        record["execution_script_file"] = target
                        changed = True
        if changed:
            ensure_versioned_backup(versions_path, work.schema_version)
            atomic_write_json(versions_path, versions)

    grids_dir = work.project_dir / "grids"
    if grids_dir.is_dir() and not grids_dir.is_symlink():
        for path in sorted(grids_dir.glob("grid_*.json")):
            record = _load_object(path)
            if record is None or record.get("episode") != episode or not points_at_old(record.get("script_file")):
                continue
            record["script_file"] = target
            ensure_versioned_backup(path, work.schema_version)
            atomic_write_json(path, record)


def _rebind_manifest_entry(work: _Work, episode: int, canonical: str) -> None:
    if not (work.project_dir / MANIFEST_FILENAME).is_file():
        return
    adapter = ProjectArtifactManifestAdapter(work.project_dir)
    key = ArtifactKey.episode_script(episode)
    stored = adapter.get_entry(key)
    if stored is None or stored.artifact_path == canonical:
        return
    ensure_versioned_backup(work.project_dir / MANIFEST_FILENAME, work.schema_version)
    replacement = replace(stored, artifact_path=canonical)
    if not adapter.replace_entries_if_matches_atomically(expected={key: stored}, replacements={key: replacement}):
        raise RuntimeError("artifact manifest changed while rebinding an episode script")


def _relocate(work: _Work, rebind: _Rebind, target_rel: str) -> None:
    """把一集剧本的引用与绑定改到 ``target_rel`` 并立即提交 ``project.json``。

    调用前剧本文件已经挪到位。每挪一次就提交一次：中断后重跑时，账本已改绑的集按新绑定处理，
    没改绑的集原绑定上已没有文件（或留有临时名），不会把别集挪进来的剧本认成自己的。
    """

    _rewrite_script_references(work, rebind.episode, rebind.old_paths, target_rel)
    rebind.entry["script_file"] = target_rel
    work.project_changed = True
    _commit_project(work)
    rebind.old_paths = {target_rel}


def _canonicalize_bindings(work: _Work) -> None:
    """集绑定不是 ``scripts/episode_N.json`` 时，剧本挪到规范路径并改绑。

    一集的规范路径可能正是另一集还没挪走的剧本（旧版按剧本自报的集号建绑定，文件名与集号可以交叉），
    所以先挪规范路径没被占用的那些；彼此占用成环时，先把其中一份挪到临时名断开。规范路径上剩下的是
    不归任何一集的文件，内容不同时挪成保留副本。
    """

    if work.schema_version >= _CANONICAL_BINDING_SCHEMA_VERSION:
        return
    rebinds: list[_Rebind] = []
    for episode, entry in _episode_entries(work.project):
        raw = entry.get("script_file")
        canonical_rel = episode_script_relpath(episode)
        if not isinstance(raw, str) or not raw or raw == canonical_rel:
            continue
        canonical = work.project_dir / canonical_rel
        bound = _bound_path(work.project_dir, raw)
        old_paths = {raw.replace("\\", "/").removeprefix("./")}
        source: Path | None = None
        if bound is not None:
            old_paths.add(work.rel(bound))
            original = _unstaged_path(bound, episode)
            if original is not None:
                # 上一次成环时改绑到临时名后中断：报告仍写原来的绑定。
                raw = work.rel(original)
            staged = _staged_path(bound, episode)
            if staged.is_file():
                # 上一次在成环处中断：这一集的剧本在临时名下，原绑定上若有文件也是别集挪进来的。
                source = staged
                old_paths.add(work.rel(staged))
            elif bound != canonical and bound.is_file():
                source = bound
        if source is not None and _load_object(source) is None:
            # 读不出的剧本不归规整，之后的迁移步照旧拒绝整个项目；这时不挪任何一集的剧本，
            # 免得它被别集挪成保留副本、或拒绝点名的不再是盘上原来的文件。
            return
        rebinds.append(_Rebind(episode=episode, entry=entry, raw=raw, old_paths=old_paths, source=source))

    # 没有可挪文件的集也排进来：它的规范路径若正是别集待挪的剧本，等那一集挪走后再改它的引用。
    pending = rebinds
    while pending:
        sources = {rebind.source for rebind in pending if rebind.source is not None}
        ready = next((r for r in pending if work.project_dir / r.canonical_rel not in sources), None)
        if ready is None:
            canonicals = {work.project_dir / r.canonical_rel for r in pending}
            blocking = next(r for r in pending if r.source is not None and r.source in canonicals)
            source = cast(Path, blocking.source)
            staged = _staged_path(source, blocking.episode)
            source.replace(staged)
            blocking.source = staged
            _relocate(work, blocking, work.rel(staged))
            continue
        pending.remove(ready)
        backup: str | None = None
        try:
            if ready.source is not None:
                canonical = work.project_dir / ready.canonical_rel
                if canonical.is_file() and canonical.read_bytes() != ready.source.read_bytes():
                    backup = work.rel(set_aside_as_kept_copy(canonical, work.schema_version))
                canonical.parent.mkdir(parents=True, exist_ok=True)
                ready.source.replace(canonical)
            _rebind_manifest_entry(work, ready.episode, ready.canonical_rel)
            _relocate(work, ready, ready.canonical_rel)
        except BaseException:
            if backup is not None:
                # 保留副本已经挪出来，重跑时发现不了它：中途失败也让报告指向它。重跑完成同一处改绑时
                # 记下的那一条不带保留副本，按同一处改动只记一次。
                work.adjusted.append(_binding_canonicalized(ready, backup))
            raise
        work.adjusted.append(_binding_canonicalized(ready, backup))


def _binding_canonicalized(rebind: _Rebind, backup: str | None) -> MigrationAdjustment:
    return MigrationAdjustment(
        code="script_binding_canonicalized",
        episode=rebind.episode,
        file=rebind.canonical_rel,
        backup=backup,
        params={"from": rebind.raw, "to": rebind.canonical_rel},
    )


@dataclass(frozen=True)
class _AssetDrop:
    """资产表里要去掉的一条重名资产，以及它并向的保留条目（归一后为空时 ``kept`` 为空串）。"""

    asset_type: str
    name: str
    kept: str
    derivatives: DerivativeMergePlan

    @property
    def renames_references(self) -> bool:
        """引用要不要改写：只差大小写时要；只差空白与 Unicode 形态的引用本就按比对坐标解析到保留条目。"""
        return bool(self.kept) and asset_name_comparison_key(self.name) != asset_name_comparison_key(self.kept)

    def params(self) -> dict[str, str]:
        return {"asset_type": self.asset_type, "name": self.name, "kept": self.kept}


def _drop_duplicate_assets(work: _Work) -> None:
    """资产表里归一后重名的资产留第一条，其余并进它：衍生迁过去，指向它们的引用改写为保留名。

    落盘顺序保证中途失败后重跑能收敛：引用、产物清单与版本历史先改，删资产的 ``project.json``
    最后才提交；提交前失败时重跑仍能发现这些重名资产，已改过的引用与登记不再变动。
    """

    drops: list[_AssetDrop] = []
    for asset_type, spec in ASSET_SPECS.items():
        bucket = work.project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        seen: dict[str, str] = {}
        # 保留条目的衍生表连同前面几条重名资产迁入的衍生：同一保留条目可能并入多条重名资产。
        kept_tables: dict[str, dict[str, Any]] = {}
        for name in list(bucket):
            if not isinstance(name, str):
                continue
            key = asset_name_namespace_key(name)
            if key and key not in seen:
                seen[key] = name
                continue
            kept = seen.get(key, "")
            source_entry, kept_entry = bucket[name], bucket.get(kept)
            derivatives = DerivativeMergePlan()
            if kept and spec.supports_derivatives and isinstance(source_entry, dict) and isinstance(kept_entry, dict):
                kept_table = kept_tables.setdefault(kept, derivative_table(kept_entry))
                derivatives = plan_derivative_merge(
                    source_entry, {DERIVATIVES_FIELD: kept_table}, name, as_derivative=False
                )
                kept_table.update(dict.fromkeys(derivatives.moved))
            drops.append(_AssetDrop(asset_type=asset_type, name=name, kept=kept, derivatives=derivatives))
    if not drops:
        return
    _rewrite_dropped_asset_references(work, drops)
    _carry_merged_derivative_records(work, drops)
    for drop in drops:
        bucket = work.project[ASSET_SPECS[drop.asset_type].bucket_key]
        source_entry = bucket.pop(drop.name)
        work.project_changed = True
        work.project_adjustments.append(MigrationAdjustment(code="duplicate_asset_removed", params=drop.params()))
        source_table = derivative_table(source_entry)
        if drop.derivatives.moved:
            kept_table = ensure_derivative_table(bucket[drop.kept])
            for derivative in drop.derivatives.moved:
                kept_table[derivative] = source_table[derivative]
        for derivative in source_table:
            if derivative not in drop.derivatives.moved and derivative not in drop.derivatives.folded:
                continue
            into = drop.derivatives.kept_derivative(asset_name_comparison_key(derivative))
            params = {"name": drop.name, "kept": drop.kept, "derivative": derivative, "into": into}
            if derivative in drop.derivatives.folded:
                params["folded"] = "true"
            work.project_adjustments.append(
                MigrationAdjustment(code="duplicate_asset_derivative_merged", params=params)
            )


def _rewrite_dropped_asset_references(work: _Work, drops: list[_AssetDrop]) -> None:
    """把各集剧本与草稿里指向被去掉资产的引用改写为保留名，规则同资产合并（并为本体）。

    改写的是已确认的正式脚本规划时，确认指纹随之平移，且先于草稿落盘提交：草稿已改写、确认还没
    平移时失败，重跑就再也认不出那份确认了。v10 之前的项目按当时的草稿名与确认记录字段认这份规划，
    确认不平移的话，v14→v15 也不会把只有确认规划的集转出正式脚本。
    """

    renaming = [drop for drop in drops if drop.renames_references]
    if not renaming:
        return
    # script_review 经 project_manager 间接依赖迁移链，惰性 import 破环。
    from lib.script.script_review import (
        carry_confirmation_through_migration,
        content_fingerprint_of_data,
        script_plan_path,
    )

    changed: list[tuple[int, Path, dict[str, Any], str | None, list[int]]] = []
    scanned: set[Path] = set()
    for episode, entry in _episode_entries(work.project):
        paths = [_bound_path(work.project_dir, entry.get("script_file"))]
        drafts_dir = episode_drafts_dir(work.project_dir, episode)
        if drafts_dir.is_dir() and not drafts_dir.is_symlink():
            paths.extend(sorted(drafts_dir.glob("*.json")))
        for path in paths:
            if path is None or path in scanned or path.is_symlink() or not path.is_file():
                continue
            scanned.add(path)
            payload = _load_object(path)
            if payload is None:
                continue
            before = content_fingerprint_of_data(payload)
            hits = [
                index
                for index, drop in enumerate(renaming)
                if merge_payload_references(
                    payload,
                    drop.asset_type,
                    drop.name,
                    asset_name_comparison_key(drop.kept),
                    derivatives=drop.derivatives,
                ).total
            ]
            if not hits:
                continue
            is_plan = path.parent == drafts_dir
            changed.append((episode, path, payload, before if is_plan else None, hits))

    legacy_names = work.schema_version < _DRAFT_RENAME_SCHEMA_VERSION
    carried = False
    for episode, path, payload, before, _hits in changed:
        plan_path = script_plan_path(work.project_dir, work.project, episode) if before is not None else None
        if plan_path is not None and legacy_names:
            plan_path = plan_path.with_name(_LEGACY_DRAFT_NAMES.get(plan_path.name, plan_path.name))
        if before is None or plan_path != path:
            continue
        after = content_fingerprint_of_data(payload)
        if not legacy_names:
            carried |= carry_confirmation_through_migration(work.project, episode, before, after)
            continue
        entry = next(entry for number, entry in _episode_entries(work.project) if number == episode)
        review = entry.get(_LEGACY_REVIEW_FIELD)
        if isinstance(review, dict) and review.get("fingerprint") == before:
            review["fingerprint"] = after
            carried = True
    if carried:
        work.project_changed = True
        _commit_project(work, keep_copy=True)
    # 每写完一个文件就记下它的改写：中途失败时已改写的那几集重跑发现不了，要靠这份记录带到重试。
    recorded: set[tuple[int, int]] = set()
    for episode, path, payload, _before, hits in changed:
        ensure_versioned_backup(path, work.schema_version)
        atomic_write_json(path, payload)
        for index in hits:
            if (index, episode) in recorded:
                continue
            recorded.add((index, episode))
            work.adjusted.append(
                MigrationAdjustment(
                    code="duplicate_asset_references_rewritten", episode=episode, params=renaming[index].params()
                )
            )


def _carry_merged_derivative_records(work: _Work, drops: list[_AssetDrop]) -> None:
    """并进保留条目的衍生带上自己的图、清单登记与版本历史，与资产合并处理衍生同一口径。

    - 迁走的衍生：图从被去掉条目的规范路径搬到保留名下，条目里的路径随之改写；清单登记换键并改路径，
      依据摘要原样保留；版本历史换键。图不搬的话，之后改名衍生或本体时按规范路径搬图，条目里的路径
      却对不上，引用就指空了。
    - 并入已有衍生的：撤掉清单登记与版本历史。留着的登记按不区分大小写的路径占住保留方那个衍生的
      登记位。它们的图与版本快照文件留在盘上。

    新键下已有登记或历史时不动；重跑时旧键已不在、图已在新路径，同样收敛。
    """

    # （被去掉条目名, 保留名, 衍生名, 清单要改的路径）与（被去掉条目名, 衍生名）。
    moved: list[tuple[str, str, str, dict[str, str]]] = []
    retired: list[tuple[str, str]] = []
    for drop in drops:
        source_table = derivative_table(work.project[ASSET_SPECS[drop.asset_type].bucket_key][drop.name])
        for name in drop.derivatives.moved:
            path_rewrites = _relocate_derivative_sheet(work, source_table[name], drop.name, drop.kept, name)
            moved.append((drop.name, drop.kept, name, path_rewrites))
        retired.extend((drop.name, name) for name in drop.derivatives.folded)
    if not moved and not retired:
        return
    if (work.project_dir / MANIFEST_FILENAME).is_file():
        adapter = ProjectArtifactManifestAdapter(work.project_dir)
        for owner, kept, name, path_rewrites in moved:
            old_key, new_key = derivative_artifact_key(owner, name), derivative_artifact_key(kept, name)
            stored = adapter.get_entry(old_key)
            if stored is None or adapter.get_entry(new_key) is not None:
                continue
            ensure_versioned_backup(work.project_dir / MANIFEST_FILENAME, work.schema_version)
            claim = replace(stored, artifact_path=path_rewrites.get(stored.artifact_path, stored.artifact_path))
            if not adapter.replace_entries_if_matches_atomically(
                expected={old_key: stored, new_key: None}, replacements={old_key: None, new_key: claim}
            ):
                raise RuntimeError("artifact manifest changed while moving a derivative claim")
        for owner, name in retired:
            old_key = derivative_artifact_key(owner, name)
            stored = adapter.get_entry(old_key)
            if stored is None:
                continue
            ensure_versioned_backup(work.project_dir / MANIFEST_FILENAME, work.schema_version)
            if not adapter.replace_entries_if_matches_atomically(
                expected={old_key: stored}, replacements={old_key: None}
            ):
                raise RuntimeError("artifact manifest changed while retiring a derivative claim")
    versions_path = work.project_dir / "versions" / "versions.json"
    versions = _load_object(versions_path) if versions_path.is_file() else None
    history = versions.get(CHARACTER_DERIVATIVE_RESOURCE_TYPE) if versions is not None else None
    if not isinstance(history, dict):
        return
    changed = False
    for owner, kept, name, _path_rewrites in moved:
        old_key = resolve_asset_key(history, derivative_artifact_id(owner, name))
        new_id = derivative_artifact_id(kept, name)
        if old_key is None or resolve_asset_key(history, new_id) is not None:
            continue
        history[new_id] = history.pop(old_key)
        changed = True
    for owner, name in retired:
        old_key = resolve_asset_key(history, derivative_artifact_id(owner, name))
        if old_key is not None:
            del history[old_key]
            changed = True
    if changed:
        ensure_versioned_backup(versions_path, work.schema_version)
        atomic_write_json(versions_path, versions)


def _relocate_derivative_sheet(work: _Work, derivative: object, owner: str, kept: str, name: str) -> dict[str, str]:
    """把迁走衍生的图从 ``owner`` 名下的规范路径搬到 ``kept`` 名下，改写条目里的路径；返回清单要改的路径。

    只动指在旧规范路径上的图。不区分大小写的文件系统上新旧路径可能是同一个文件，只改写路径；
    盘上没有这张图时同样只改写路径，与改名衍生一致；新路径已被别的文件占着时不搬，路径照旧。
    """

    if not isinstance(derivative, dict):
        return {}
    old_rel = derivative_sheet_relative_path(owner, name)
    new_rel = derivative_sheet_relative_path(kept, name)
    value = derivative.get(_DERIVATIVE_SHEET_FIELD)
    if (
        old_rel == new_rel
        or not isinstance(value, str)
        or normalize_asset_name(value.replace("\\", "/")) != normalize_asset_name(old_rel)
    ):
        return {}
    source, target = work.project_dir / old_rel, work.project_dir / new_rel
    if source.is_file() and not (target.exists() and target.samefile(source)):
        if target.exists():
            return {}
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        # 只是收纳目录：非空或删不掉都不影响任何登记。
        with contextlib.suppress(OSError):
            source.parent.rmdir()  # hard-delete: infra -- 迁移搬走后的空目录
    derivative[_DERIVATIVE_SHEET_FIELD] = new_rel
    return {normalize_artifact_path(value): normalize_artifact_path(new_rel)}


#: 参考文件字段 → 本资产名下的落点（资产类型目录下的子目录）；多图序列按 ``名字_{序号}`` 命名。
_REFERENCE_FILE_SUBDIRS = {"reference_image": "refs", "reference_audio": "refs_audio", "reference_images": "refs"}


def normalize_shared_reference_files(project_dir: Path, *, schema_version: int) -> list[MigrationAdjustment]:
    """只规整共用参考文件，供资产名称空间迁移在暂存树里提前拆分引用。"""
    project = _load_object(project_dir / "project.json")
    if project is None:
        return []
    work = _Work(project_dir=project_dir, schema_version=schema_version, project=project)
    _copy_shared_reference_files(work)
    return work.adjusted


def _reference_fields(work: _Work) -> Iterator[tuple[str, str, dict[str, Any], str]]:
    for asset_type, spec in ASSET_SPECS.items():
        bucket = work.project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for name, entry in bucket.items():
            # 复制目标用资产名拼文件名；导入包在校验资产名之前就跑迁移链，含路径分隔符的名字会把副本写出目录。
            if not isinstance(name, str) or not isinstance(entry, dict) or "/" in name or "\\" in name:
                continue
            for field_name in REFERENCE_FILE_FIELDS:
                if field_name in spec.extra_string_fields or field_name in spec.extra_list_fields:
                    yield asset_type, name, entry, field_name


def _copy_shared_reference_files(work: _Work) -> None:
    """参考文件复制到本资产名下；互相引用时冻结源字节，引用提交失败则恢复源文件供重试。"""
    fields = list(_reference_fields(work))
    sources: dict[str, Path] = {}
    for asset_type, name, entry, field_name in fields:
        value = entry.get(field_name)
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, str) or not item:
                continue
            rel = _canonical_reference(work.project_dir, item)
            if owns_named_file(ASSET_SPECS[asset_type], name, rel) or _named_file_owner(work.project, rel) is None:
                continue
            if field_name == "reference_audio" and PurePosixPath(rel).parent != PurePosixPath("characters/refs_audio"):
                continue
            source = try_safe_join(work.project_dir, rel)
            if source is not None and source.is_file() and not source.is_symlink():
                sources[rel] = source
    content = {rel: source.read_bytes() for rel, source in sources.items()}
    before = len(work.adjusted)
    with formal_write_transaction(work.project_dir / "project.json", *sources.values()):
        for asset_type, name, entry, field_name in fields:
            value = entry.get(field_name)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    copied = _own_reference_copy(work, asset_type, name, field_name, item, content, siblings=value)
                    if copied is not None:
                        value[index] = copied
            elif (copied := _own_reference_copy(work, asset_type, name, field_name, value, content)) is not None:
                entry[field_name] = copied
        if len(work.adjusted) > before:
            _commit_project(work)


def _own_reference_copy(
    work: _Work,
    asset_type: str,
    name: str,
    field_name: str,
    value: object,
    sources: dict[str, bytes],
    *,
    siblings: list[Any] | None = None,
) -> str | None:
    """*value* 是别的资产名下的文件时复制到 *name* 名下，返回新路径；不必复制时返回 ``None``。"""

    spec = ASSET_SPECS[asset_type]
    if not isinstance(value, str) or not value:
        return None
    rel = _canonical_reference(work.project_dir, value)
    if owns_named_file(spec, name, rel):
        return None
    owner = _named_file_owner(work.project, rel)
    target_dir = PurePosixPath(spec.subdir) / _REFERENCE_FILE_SUBDIRS[field_name]
    # 参考音频只认角色参考音频目录里的文件，别处的值本就不生效，复制过来反倒让它生效。
    if owner is None or (field_name == "reference_audio" and PurePosixPath(rel).parent != target_dir):
        return None
    content = sources.get(rel)
    if content is None:
        return None
    stem, suffix = normalize_asset_name(name), PurePosixPath(rel).suffix
    backup: str | None = None
    if siblings is None:
        target_rel = (target_dir / f"{stem}{suffix}").as_posix()
        target = work.project_dir / target_rel
        if target.exists() and not (target.is_file() and target.read_bytes() == content):
            backup = work.rel(set_aside_as_kept_copy(target, work.schema_version))
    else:
        target_rel = _sequenced_copy_target(work, spec, target_dir, stem, suffix, content, siblings)
        target = work.project_dir / target_rel
    work.adjusted.append(
        MigrationAdjustment(
            code="shared_reference_file_copied",
            file=target_rel,
            backup=backup,
            params={"asset_type": asset_type, "name": name, "owner": owner[1], "from": rel},
        )
    )
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, content)
    work.project_changed = True
    return target_rel


def _sequenced_copy_target(
    work: _Work,
    spec: AssetSpec,
    target_dir: PurePosixPath,
    stem: str,
    suffix: str,
    content: bytes,
    siblings: list[Any],
) -> str:
    """多图序列里新副本的落点：已有同字节、尚未登记的本资产文件就用它（重跑收敛），否则取最小的空闲序号。"""

    listed = {item.replace("\\", "/") for item in siblings if isinstance(item, str)}
    directory = work.project_dir.joinpath(*target_dir.parts)
    taken: set[str] = set()
    for existing in sorted(directory.iterdir()) if directory.is_dir() else ():
        existing_rel = (target_dir / existing.name).as_posix()
        if not owns_named_file(spec, stem, existing_rel):
            continue
        taken.add(normalize_asset_name(existing.stem))
        if (
            existing_rel not in listed
            and existing.suffix == suffix
            and existing.is_file()
            and existing.read_bytes() == content
        ):
            return existing_rel
    sequence = 1
    while f"{stem}_{sequence}" in taken:
        sequence += 1
    return (target_dir / f"{stem}_{sequence}{suffix}").as_posix()


def _canonical_reference(project_dir: Path, value: str) -> str:
    """参考文件字段值的项目相对写法。

    旧版 PATCH 原样收下任意字符串，运行时按项目内的落点解析：``refs/../refs/名.png``、``./``、反斜杠与
    指向项目内的绝对路径都归到同一写法，归属判定才认得出它们。落在项目之外的值原样返回，不归任何资产。
    """

    # 按运行平台的路径语义拼接：Windows 上 ``C:/…`` 才会被认作绝对路径。
    joined = os.path.normpath(os.path.join(project_dir, value.replace("\\", "/")))
    for base in dict.fromkeys((os.path.normpath(project_dir), os.path.realpath(project_dir))):
        if joined.startswith(base + os.sep):
            return PurePath(joined[len(base) + 1 :]).as_posix()
    return value


def _named_file_owner(project: dict[str, Any], rel: str) -> tuple[str, str] | None:
    """按名归属 *rel* 的资产（类型、名字）；不在任何资产名下时为 ``None``。"""

    for asset_type, spec in ASSET_SPECS.items():
        bucket = project.get(spec.bucket_key)
        for name in bucket if isinstance(bucket, dict) else ():
            if isinstance(name, str) and owns_named_file(spec, name, rel):
                return asset_type, name
    return None


#: 条目 id 的集号前缀（与 ``script_editor`` 的条目 id 形态一致，含 ``_N`` 拆分后缀）。
_ITEM_ID_EPISODE = re.compile(r"\AE(\d+)[SU]\d+(?:_\d+)?\Z")
#: ``new_item_id`` 取出的号：集号、字母、序号。
_ITEM_ID_PARTS = re.compile(r"\AE(\d+)([SU])(\d+)\Z")


def _id_owner_episode(item_id: str) -> int | None:
    match = _ITEM_ID_EPISODE.match(item_id)
    return int(match.group(1)) if match else None


def _fresh_item_id(script: dict[str, Any], taken: set[str]) -> str:
    """按新增条目的取号规则取号，并避开其他集已经在用的 id。"""

    candidate = new_item_id(script)
    match = _ITEM_ID_PARTS.match(candidate)
    if match is None:
        return candidate
    prefix, number = f"E{match.group(1)}{match.group(2)}", int(match.group(3))
    width = len(match.group(3)) if match.group(2) == "S" else 0
    while candidate in taken:
        number += 1
        candidate = f"{prefix}{number:0{width}d}"
    return candidate


def _repair_item_ids(work: _Work) -> None:
    """条目 id 在剧本内缺失或重复、或与另一集的条目同号时换号。

    产物路径只由条目 id 决定，两集同号的条目会认领同一个媒体文件。媒体与登记归 id 前缀 ``E<n>``
    所指的那一集，前缀对不上任何一个同号集时归账本里靠前的那一集；另一集的条目在本集换新 id、
    不带走媒体。
    """

    scripts: list[tuple[int, Path, dict[str, Any], list[Any], str, str]] = []
    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is None or not path.is_file() or path.is_symlink():
            continue
        script = _load_object(path)
        if script is None:
            continue
        try:
            items, id_field, kind = resolve_items(script)
        except ScriptEditError:
            continue
        scripts.append((episode, path, script, items, id_field, kind))

    holders: dict[str, list[int]] = {}
    for episode, _path, _script, items, id_field, _kind in scripts:
        for item in items:
            current = item.get(id_field) if isinstance(item, dict) else None
            if isinstance(current, str) and current and episode not in holders.setdefault(current, []):
                holders[current].append(episode)
    owners: dict[str, int] = {}
    for item_id, episodes in holders.items():
        if len(episodes) > 1:
            prefixed = _id_owner_episode(item_id)
            owners[item_id] = prefixed if prefixed in episodes else episodes[0]
    taken = set(holders)

    for episode, path, script, items, id_field, kind in scripts:
        seen: set[str] = set()
        changes: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            current = item.get(id_field)
            foreign = isinstance(current, str) and owners.get(current, episode) != episode
            if isinstance(current, str) and current and current not in seen and not foreign:
                seen.add(current)
                continue
            old_id = current if isinstance(current, str) else ""
            # 先清掉坏 id 再取号：取号只看现存条目，坏 id 不该参与顺延。
            item.pop(id_field, None)
            new_id = _fresh_item_id(script, taken)
            item[id_field] = new_id
            seen.add(new_id)
            taken.add(new_id)
            if old_id:
                _release_media_of(item, old_id, kind)
            changes.append((old_id, new_id))
        if not changes:
            continue
        ensure_versioned_backup(path, work.schema_version)
        atomic_write_json(path, script)
        for old_id, new_id in changes:
            work.adjusted.append(
                MigrationAdjustment(
                    code="item_id_reassigned",
                    episode=episode,
                    file=work.rel(path),
                    params={"from": old_id, "to": new_id},
                )
            )


def _release_media_of(item: dict[str, Any], old_id: str, kind: str) -> None:
    """换号条目放下原 id 的规范媒体，连同只描述那些媒体的生成状态，读作尚未生成。

    缩略图、视频 URI、生成时刻跟着视频走，尾帧快照、宫格归属跟着分镜图走：主媒体不在时它们描述的是
    原 id 那一条的产物，一并清掉。指向别处的媒体引用不是原 id 的规范路径，留着，它的伴随状态也留着。
    落盘的 ``status`` 按剩下的媒体重算，口径同 ``ProjectManager.update_scene_status``。
    """

    if item.get("end_frame_image") == resource_relative_path("end_frames", old_id):
        item.pop("end_frame_image")
    assets = item.get("generated_assets")
    if not isinstance(assets, dict):
        return
    for field_name, storyboard_type, unit_type in _MEDIA_FIELDS:
        resource_type = unit_type if kind == "video_units" else storyboard_type
        if assets.get(field_name) == resource_relative_path(resource_type, old_id):
            assets[field_name] = None
    for field_name, companions in _MEDIA_COMPANION_FIELDS.items():
        if not assets.get(field_name):
            for companion in companions:
                if assets.get(companion) is not None:
                    assets[companion] = None
    if "status" in assets:
        if assets.get("video_clip"):
            assets["status"] = "completed"
        elif assets.get("storyboard_image"):
            assets["status"] = "storyboard_ready"
        else:
            assets["status"] = "pending"


def _resolve_draft_name_conflicts(work: _Work) -> None:
    if work.schema_version >= _DRAFT_RENAME_SCHEMA_VERSION:
        return
    for episode, _entry in _episode_entries(work.project):
        drafts_dir = episode_drafts_dir(work.project_dir, episode)
        if not drafts_dir.is_dir() or drafts_dir.is_symlink():
            continue
        for old_name, new_name in DRAFT_FILE_RENAMES.items():
            old, new = drafts_dir / old_name, drafts_dir / new_name
            if old.is_symlink() or new.is_symlink() or not (old.is_file() and new.is_file()):
                continue
            kept = set_aside_as_kept_copy(old, work.schema_version)
            work.adjusted.append(
                MigrationAdjustment(
                    code="draft_name_conflict_resolved",
                    episode=episode,
                    file=work.rel(new),
                    backup=work.rel(kept),
                )
            )


def _adjustment_identity(item: MigrationAdjustment) -> str:
    return item.model_dump_json(exclude={"backup"})


def normalize_legacy_shapes(project_dir: Path, *, schema_version: int, record: list[MigrationAdjustment]) -> None:
    """规整 ``project_dir`` 里的旧形态，做过的改动追加进 ``record``；无事可做时不写盘。

    中途失败时，此前已落盘的改动照样追加进 ``record``：重跑时规整已经发现不了它们，要靠未完成的
    报告带到重试。``project.json`` 上的改动只在它写盘之后才算落盘。
    """

    project_file = project_dir / "project.json"
    project = _load_object(project_file)
    if project is None:
        return
    work = _Work(project_dir=project_dir, schema_version=schema_version, project=project)
    try:
        _drop_duplicate_episodes(work)
        _split_shared_bindings(work)
        # 资产去重先于改绑：第一次提交 project.json 时，它的规整项已在待提交之列，留下的就是保留副本，
        # 能覆盖全部 project.json 规整项。它平移确认指纹时自己先行提交，同样留保留副本。
        _drop_duplicate_assets(work)
        _copy_shared_reference_files(work)
        _canonicalize_bindings(work)
        _correct_script_episodes(work)
        _repair_item_ids(work)
        _resolve_draft_name_conflicts(work)
        if work.project_changed:
            _commit_project(work)
    finally:
        # 上一次中途失败时已记下的改动，重跑时可能再次发现（如改绑没来得及提交）：同一处改动只记一次，
        # 留先记下的那条，它带着当时挪出的保留副本。
        recorded = {_adjustment_identity(item) for item in record}
        record.extend(item for item in work.adjusted if _adjustment_identity(item) not in recorded)
    if work.adjusted:
        logger.info("规整旧形态 %s：%d 处", project_dir.name, len(work.adjusted))


__all__ = ["NORMALIZE_FROM_SCHEMA_VERSION", "normalize_legacy_shapes", "normalize_shared_reference_files"]
