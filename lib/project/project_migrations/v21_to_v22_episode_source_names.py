"""v21→v22：归一化分集源文文件名，消除同集号补零别名（ADR 0125、0131）。

正常业务只识别规范路径 ``source/episode_N.txt``（``N >= 1`` 且无前导零）。本步作为一次性整理边界，
按文件级可重建证明与用户数据保护原则处置 ``source/`` 下形如 ``episode_[0-9]+.txt`` 的非规范别名：

- **规范文件已存在**：原样保留 ``source/episode_N.txt``。每个非规范别名逐个核验：非符号链接且字节与
  该集唯一可读 ``source_range`` 切片一致，或与规范文件字节完全一致时允许清理；其余另存为永久保留副本
  （``source/<别名>.kept.v21-<ts>``）并记入迁移报告。
- **规范文件不存在**：仅在账本存在该正整数集号且原文来源非 ``none``、且尚未因本步冲突留底过副本时，
  在无歧义情形下将唯一可读常规别名（或多份字节完全一致的常规别名之一，或与 ``source_range`` 切片一致的
  常规别名）改名落位到 ``source/episode_N.txt``；其余可证明可重建或同字节的重复别名清理，无法证明可重建
  或归属不明确的别名（含符号链接、``source_origin == "none"``、未登记孤儿别名及 ``episode_0.txt``）
  全部另存为保留副本。

每组文件变更先落位规范文件、再留底冲突副本、最后清理可重建别名，并在组内以撤销栈保护异常回滚；
即便进程在组内意外中止，重跑也能依据已落位的规范文件或已留底的 ``.kept.v21-*`` 副本收敛到同一终态。
``project.json`` 仅在全部文件整理完成后原子写入 ``schema_version = 22``，保留原有 ``metadata.updated_at``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.episode.episode_ledger import parse_episode_num, parse_source_range
from lib.episode.episode_paths import episode_source_path, episode_source_relpath
from lib.episode.episode_sources import (
    SourceOrigin,
    discover_sources,
    episode_source_origin,
    is_cut_episode,
    span_text,
    whole_source_files,
)
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_migration_report import MigrationAdjustment
from lib.project.project_migrations.backups import ensure_versioned_backup, set_aside_as_kept_copy
from lib.project.project_schema import parse_project_schema_version

logger = logging.getLogger(__name__)

FROM_SCHEMA_VERSION = 21
TARGET_SCHEMA_VERSION = 22

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)

_LEGACY_EPISODE_FILE_RE = re.compile(r"episode_([0-9]+)\.txt")
_PRIOR_V21_KEPT_RE = re.compile(r"(episode_([0-9]+)\.txt)\.kept\.v21-[0-9]+$")


@dataclass(frozen=True)
class _EpisodeFilePlan:
    episode: int
    report_episode: int | None
    promote: Path | None
    replace_canonical_symlink: bool
    preserve: tuple[Path, ...]
    delete: tuple[tuple[Path, bytes], ...]


@dataclass(frozen=True)
class _MigrationScan:
    plans: tuple[_EpisodeFilePlan, ...]
    prior_kept_adjustments: tuple[MigrationAdjustment, ...]
    materialize_symlinks: tuple[tuple[Path, bytes], ...]


def _read_regular_bytes(source_dir: Path, name: str) -> bytes | None:
    if os.open in os.supports_dir_fd and _O_NOFOLLOW:
        try:
            dir_fd = os.open(source_dir, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
        except OSError:
            return None
        try:
            fd = os.open(name, os.O_RDONLY | _O_NONBLOCK | _O_NOFOLLOW, dir_fd=dir_fd)
        except OSError:
            return None
        finally:
            os.close(dir_fd)
        try:
            is_regular = stat.S_ISREG(os.fstat(fd).st_mode)
        except OSError:
            os.close(fd)
            return None
        if not is_regular:
            os.close(fd)
            return None
        try:
            with os.fdopen(fd, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    if source_dir.is_symlink() or source_dir.is_junction():
        return None
    path = source_dir / name
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | _O_BINARY)
    except OSError:
        return None
    try:
        opened = os.fstat(fd)
        after = os.lstat(path)
        identity_ok = (
            stat.S_ISREG(opened.st_mode)
            and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino)
            and (opened.st_dev, opened.st_ino) == (after.st_dev, after.st_ino)
        )
    except OSError:
        os.close(fd)
        return None
    if not identity_ok:
        os.close(fd)
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _entries_by_episode(project: Mapping[str, Any]) -> dict[int, list[Mapping[str, Any]]]:
    raw_episodes = project.get("episodes")
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for entry in raw_episodes if isinstance(raw_episodes, list) else []:
        if not isinstance(entry, Mapping):
            continue
        num = parse_episode_num(entry.get("episode"))
        if num is not None:
            grouped.setdefault(num, []).append(entry)
    return grouped


def _expected_derived_bytes(
    num: int,
    entries: list[Mapping[str, Any]],
    texts: Mapping[str, str],
    order: list[str],
) -> bytes | None:
    if num < 1 or not entries or not all(is_cut_episode(entry) for entry in entries):
        return None
    spans = {parse_source_range(entry) for entry in entries}
    span = spans.pop() if len(spans) == 1 else None
    if span is None:
        return None
    content = span_text(texts, order, span)
    return content.encode("utf-8") if content is not None else None


def _plan_episode_group(
    source_dir: Path,
    num: int,
    canonical: Path | None,
    aliases: list[Path],
    *,
    entries: list[Mapping[str, Any]],
    expected: bytes | None,
    had_prior_kept: bool,
    canonical_symlink_target: Path | None = None,
) -> _EpisodeFilePlan:
    report_episode = num if num >= 1 and entries else None
    if canonical is not None:
        if canonical_symlink_target is not None:
            canonical_bytes = _read_regular_bytes(source_dir, canonical_symlink_target.name)
            other_aliases = [alias for alias in aliases if alias != canonical_symlink_target]
            promote: Path | None = canonical_symlink_target
            replace_symlink = True
        else:
            canonical_bytes = _read_regular_bytes(source_dir, canonical.name)
            other_aliases = aliases
            promote = None
            replace_symlink = False

        preserve: list[Path] = []
        delete: list[tuple[Path, bytes]] = []
        for alias in other_aliases:
            alias_bytes = _read_regular_bytes(source_dir, alias.name)
            if alias_bytes is not None and (
                (expected is not None and alias_bytes == expected)
                or (canonical_bytes is not None and alias_bytes == canonical_bytes)
            ):
                delete.append((alias, alias_bytes))
            else:
                preserve.append(alias)
        return _EpisodeFilePlan(
            episode=num,
            report_episode=report_episode,
            promote=promote,
            replace_canonical_symlink=replace_symlink,
            preserve=tuple(preserve),
            delete=tuple(delete),
        )

    active_owner = (
        num >= 1 and bool(entries) and any(episode_source_origin(entry) is not SourceOrigin.NONE for entry in entries)
    )
    regular = [path for path in aliases if not path.is_symlink() and path.is_file()]
    symlinks = [path for path in aliases if path.is_symlink()]
    winner: Path | None = None
    preserve = list(symlinks)
    delete = []

    if active_owner and not had_prior_kept and regular:
        if len(regular) == 1:
            winner = regular[0]
        else:
            payloads = {path: _read_regular_bytes(source_dir, path.name) for path in regular}
            unique_payloads = {data for data in payloads.values() if data is not None}
            if all(data is not None for data in payloads.values()) and len(unique_payloads) == 1:
                winner = regular[0]
                for duplicate in regular[1:]:
                    data = payloads[duplicate]
                    if data is not None:
                        delete.append((duplicate, data))
            elif expected is not None:
                matching = [path for path in regular if payloads[path] == expected]
                if matching:
                    winner = matching[0]
                    for duplicate in matching[1:]:
                        delete.append((duplicate, expected))
                    preserve.extend(path for path in regular if path not in matching)
                else:
                    preserve.extend(regular)
            else:
                preserve.extend(regular)
    else:
        preserve.extend(regular)

    preserve.sort(key=lambda path: path.name)
    return _EpisodeFilePlan(
        episode=num,
        report_episode=report_episode,
        promote=winner,
        replace_canonical_symlink=False,
        preserve=tuple(preserve),
        delete=tuple(delete),
    )


def _scan_episode_source_plans(project_dir: Path, project: Mapping[str, Any]) -> _MigrationScan:
    source_dir = project_dir / "source"
    if source_dir.is_symlink() or source_dir.is_junction():
        raise ProjectMigrationError("source/ 不能是符号链接或目录联接，拒绝归一化集原文文件名", file="source")
    if not source_dir.is_dir():
        return _MigrationScan(plans=(), prior_kept_adjustments=(), materialize_symlinks=())

    canonical_by_num: dict[int, Path] = {}
    aliases_by_num: dict[int, list[Path]] = {}
    prior_kept_nums: set[int] = set()
    prior_kept_entries: list[tuple[int, str, Path]] = []

    for path in sorted(source_dir.iterdir(), key=lambda item: item.name):
        kept_match = _PRIOR_V21_KEPT_RE.fullmatch(path.name)
        if kept_match and (path.is_file() or path.is_symlink()):
            num = int(kept_match.group(2))
            prior_kept_nums.add(num)
            prior_kept_entries.append((num, kept_match.group(1), path))
            continue
        match = _LEGACY_EPISODE_FILE_RE.fullmatch(path.name)
        if not match or not (path.is_file() or path.is_symlink()):
            continue
        num = int(match.group(1))
        if num >= 1 and path.name == f"episode_{num}.txt":
            canonical_by_num[num] = path
        else:
            aliases_by_num.setdefault(num, []).append(path)

    entries_by_num = _entries_by_episode(project)
    prior_kept_adjustments = tuple(
        MigrationAdjustment(
            code="episode_source_alias_preserved",
            episode=num if num >= 1 and entries_by_num.get(num) else None,
            file=f"source/{original_name}",
            backup=f"source/{kept_path.name}",
        )
        for num, original_name, kept_path in prior_kept_entries
    )

    if not aliases_by_num:
        return _MigrationScan(
            plans=(),
            prior_kept_adjustments=prior_kept_adjustments,
            materialize_symlinks=(),
        )

    regular_alias_by_id: dict[tuple[int, int], tuple[int, Path]] = {}
    for num, aliases in aliases_by_num.items():
        for alias in aliases:
            try:
                st = os.lstat(alias)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                regular_alias_by_id[(st.st_dev, st.st_ino)] = (num, alias)

    canonical_symlink_targets: dict[int, Path] = {}
    materialize_symlinks: list[tuple[Path, bytes]] = []
    for num, canonical in sorted(canonical_by_num.items()):
        if not canonical.is_symlink():
            continue
        try:
            target_stat = os.stat(canonical)
        except OSError:
            continue
        hit = regular_alias_by_id.get((target_stat.st_dev, target_stat.st_ino))
        if hit is None:
            continue
        alias_num, alias_path = hit
        if alias_num == num:
            canonical_symlink_targets[num] = alias_path
        else:
            data = _read_regular_bytes(source_dir, alias_path.name)
            if data is not None:
                materialize_symlinks.append((canonical, data))

    texts = {doc.rel_path: doc.text for doc in discover_sources(project_dir, project)}
    order = whole_source_files(project)
    plans: list[_EpisodeFilePlan] = []
    for num in sorted(aliases_by_num):
        entries = entries_by_num.get(num, [])
        expected = _expected_derived_bytes(num, entries, texts, order)
        plans.append(
            _plan_episode_group(
                source_dir,
                num,
                canonical_by_num.get(num),
                aliases_by_num[num],
                entries=entries,
                expected=expected,
                had_prior_kept=num in prior_kept_nums,
                canonical_symlink_target=canonical_symlink_targets.get(num),
            )
        )
    return _MigrationScan(
        plans=tuple(plans),
        prior_kept_adjustments=prior_kept_adjustments,
        materialize_symlinks=tuple(materialize_symlinks),
    )


def _restore_symlink_over_file(canonical: Path, link_target: str, original_alias: Path | None = None) -> None:
    if original_alias is not None:
        canonical.replace(original_alias)
    elif canonical.exists() or canonical.is_symlink():
        canonical.unlink()  # hard-delete: temp -- 迁移回滚恢复原有规范符号链接
    canonical.symlink_to(link_target)


def _apply_episode_plan(project_dir: Path, plan: _EpisodeFilePlan) -> list[MigrationAdjustment]:
    adjustments: list[MigrationAdjustment] = []
    with ExitStack() as undo:
        if plan.promote is not None:
            source = plan.promote
            target = episode_source_path(project_dir, plan.episode)
            old_link_target = os.readlink(target) if plan.replace_canonical_symlink else None
            source.replace(target)
            if old_link_target is not None:
                undo.callback(_restore_symlink_over_file, target, old_link_target, source)
            else:
                undo.callback(target.replace, source)
            canonical_rel = episode_source_relpath(plan.episode)
            adjustments.append(
                MigrationAdjustment(
                    code="episode_source_canonicalized",
                    episode=plan.episode,
                    file=canonical_rel,
                    params={"from": f"source/{source.name}", "to": canonical_rel},
                )
            )
        for alias in plan.preserve:
            kept = set_aside_as_kept_copy(alias, FROM_SCHEMA_VERSION)
            undo.callback(kept.replace, alias)
            adjustments.append(
                MigrationAdjustment(
                    code="episode_source_alias_preserved",
                    episode=plan.report_episode,
                    file=f"source/{alias.name}",
                    backup=kept.relative_to(project_dir).as_posix(),
                )
            )
        for alias, original in plan.delete:
            alias.unlink()  # hard-delete: derived -- 迁移归一化可证明可重建或与规范文件字节完全一致的旧补零别名
            undo.callback(alias.write_bytes, original)
        undo.pop_all()
    if plan.delete:
        logger.info(
            "迁移归一化已清理可重建或同字节旧别名 %s（集 %d）：%s",
            project_dir.name,
            plan.episode,
            ", ".join(path.name for path, _ in plan.delete),
        )
    return adjustments


def migrate_v21_to_v22(project_dir: Path, *, record: list[MigrationAdjustment] | None = None) -> None:
    """v21→v22 文件级迁移：将 ``source/`` 下的分集源文别名归一化为唯一规范路径 ``source/episode_N.txt``。"""
    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    with project_metadata_lock(project_dir, journal=False):
        project = json.loads(project_file.read_bytes())
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return
        ensure_versioned_backup(project_file, FROM_SCHEMA_VERSION)
        scan = _scan_episode_source_plans(project_dir, project)
        if record is not None:
            for item in scan.prior_kept_adjustments:
                if item not in record:
                    record.append(item)
        with ExitStack() as materialize_undo:
            for canonical, data in scan.materialize_symlinks:
                old_link_target = os.readlink(canonical)
                atomic_write_bytes(canonical, data, journal=False)
                materialize_undo.callback(_restore_symlink_over_file, canonical, old_link_target)
            materialize_undo.pop_all()
        for plan in scan.plans:
            group_adjustments = _apply_episode_plan(project_dir, plan)
            if record is not None:
                for item in group_adjustments:
                    if item not in record:
                        record.append(item)
        atomic_write_json(project_file, {**project, "schema_version": TARGET_SCHEMA_VERSION})


__all__ = ["FROM_SCHEMA_VERSION", "TARGET_SCHEMA_VERSION", "migrate_v21_to_v22"]
