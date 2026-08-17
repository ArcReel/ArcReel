"""v5→v6：把四类项目资产收敛到一个名称空间。"""

from __future__ import annotations

import copy
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from lib.asset_rename import rewrite_entry_paths
from lib.asset_types import (
    ASSET_SPECS,
    AssetSpec,
    asset_name_comparison_key,
    ensure_project_asset_namespace,
    normalize_asset_name,
    validate_asset_name,
)
from lib.episode_paths import REFERENCE_VIDEO_STEP1_LEGACY_FILENAME, STEP1_LEGACY_FILENAMES
from lib.json_io import atomic_write_json, load_json
from lib.project_migrations.staged_swap import (
    ensure_disk_headroom,
    new_rollback_dir,
    staging_dir_prefix,
)
from lib.reference_video.shot_parser import match_dialogue_line, rewrite_mentions, strip_shot_header

logger = logging.getLogger(__name__)

_LEGACY_MARKDOWN_DRAFTS = {
    REFERENCE_VIDEO_STEP1_LEGACY_FILENAME,
    *(name for names in STEP1_LEGACY_FILENAMES.values() for name in names),
}


@dataclass
class _AssetOccurrence:
    asset_type: str
    spec: AssetSpec
    old_name: str
    entry: Any
    ordinal: int
    key: str
    new_name: str = ""


def _ordered_specs() -> list[AssetSpec]:
    return sorted(ASSET_SPECS.values(), key=lambda spec: spec.namespace_priority)


def _managed_media_roots(spec: AssetSpec) -> tuple[tuple[PurePosixPath, bool], ...]:
    base = PurePosixPath(spec.subdir)
    return (
        (base, False),
        (base / "refs", "reference_images" in spec.extra_list_fields),
        (base / "refs_audio", False),
    )


def _migration_write_roots() -> tuple[PurePosixPath, ...]:
    """Return every subtree in which this migration may replace or rename files."""
    roots = {PurePosixPath("scripts"), PurePosixPath("drafts"), PurePosixPath("versions")}
    for spec in _ordered_specs():
        roots.update(relative for relative, _allow_sequence in _managed_media_roots(spec))
    return tuple(sorted(roots, key=str))


def _assert_migration_write_target(project_dir: Path, target: Path) -> None:
    """Reject a write target whose own path or any project-relative parent is a symlink."""
    try:
        relative = target.relative_to(project_dir)
    except ValueError as exc:
        raise ValueError(f"迁移写入路径越出项目目录: {target}") from exc
    current = project_dir
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"迁移写入路径不得为符号链接: {current.relative_to(project_dir)}")


def _assert_migration_write_roots(project_dir: Path) -> None:
    """Fail before transformation when any declared migration write root is a symlink."""
    for relative in _migration_write_roots():
        _assert_migration_write_target(project_dir, project_dir.joinpath(*relative.parts))


def _reserved_media_keys(project_dir: Path) -> dict[str, set[str]]:
    """Collect canonical asset-name stems already occupying managed media roots."""
    reserved: dict[str, set[str]] = {}
    for spec in _ordered_specs():
        keys = reserved.setdefault(spec.asset_type, set())
        for relative, allow_sequence in _managed_media_roots(spec):
            directory = project_dir.joinpath(*relative.parts)
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if not path.is_file():
                    continue
                stem = path.stem
                if allow_sequence:
                    base, separator, sequence = stem.rpartition("_")
                    if separator and sequence.isdigit():
                        stem = base
                keys.add(asset_name_comparison_key(stem))
    return reserved


def _plan_occurrences(
    project: dict[str, Any], retained_history_keys: dict[str, set[str]] | None = None
) -> list[_AssetOccurrence]:
    occurrences: list[_AssetOccurrence] = []
    ordinal = 0
    for spec in _ordered_specs():
        bucket = project.get(spec.bucket_key, {})
        if not isinstance(bucket, dict):
            raise ValueError(f"project[{spec.bucket_key!r}] 必须是对象，无法迁移资产名称空间")
        for raw_name, entry in bucket.items():
            clean = validate_asset_name(raw_name)
            occurrences.append(
                _AssetOccurrence(
                    asset_type=spec.asset_type,
                    spec=spec,
                    old_name=raw_name,
                    entry=entry,
                    ordinal=ordinal,
                    key=asset_name_comparison_key(clean),
                )
            )
            ordinal += 1

    groups: dict[str, list[_AssetOccurrence]] = {}
    for occurrence in occurrences:
        groups.setdefault(occurrence.key, []).append(occurrence)

    # 先预留所有存量规范名，后缀分配不得抢走另一资产原本合法的名字。
    occupied = set(groups)
    for key, group in groups.items():
        best_priority = min(item.spec.namespace_priority for item in group)
        # 同类等价 key 延续存量读侧“后写入胜出”语义；跨类型先比稳定优先级。
        winner = [item for item in group if item.spec.namespace_priority == best_priority][-1]
        winner.new_name = validate_asset_name(winner.old_name)
        if len(group) > 1:
            logger.warning(
                "项目资产名 %r 存在冲突；无类型 mention 按稳定所有者 %s/%r 归属，其余资产级联改名",
                key,
                winner.asset_type,
                winner.new_name,
            )
        suffix_counters: dict[str, int] = {}
        for item in group:
            if item is winner:
                continue
            base = f"{validate_asset_name(item.old_name)}_{item.asset_type}"
            index = suffix_counters.get(base, 1)
            candidate = base if index == 1 else f"{base}_{index}"
            reserved_for_type = (retained_history_keys or {}).get(item.asset_type, set())
            while (
                asset_name_comparison_key(candidate) in occupied
                or asset_name_comparison_key(candidate) in reserved_for_type
            ):
                index += 1
                candidate = f"{base}_{index}"
            suffix_counters[base] = index + 1
            item.new_name = validate_asset_name(candidate)
            occupied.add(asset_name_comparison_key(item.new_name))
    return occurrences


def _typed_owner_map(occurrences: list[_AssetOccurrence]) -> dict[tuple[str, str], _AssetOccurrence]:
    owners: dict[tuple[str, str], _AssetOccurrence] = {}
    for item in occurrences:
        # 同类型等价条目按存量读侧后写胜出。
        owners[(item.asset_type, item.key)] = item
    return owners


def _mention_owner_map(occurrences: list[_AssetOccurrence]) -> dict[str, _AssetOccurrence]:
    owners: dict[str, _AssetOccurrence] = {}
    for item in occurrences:
        current = owners.get(item.key)
        if current is None or item.spec.namespace_priority < current.spec.namespace_priority:
            owners[item.key] = item
        elif item.spec.namespace_priority == current.spec.namespace_priority:
            owners[item.key] = item
    return owners


def _contextual_targets(node: dict[str, Any], typed: dict[tuple[str, str], _AssetOccurrence]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for spec in _ordered_specs():
        for field in spec.reference_list_fields:
            values = node.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, str):
                    continue
                owner = typed.get((spec.asset_type, asset_name_comparison_key(value)))
                if owner is not None:
                    candidates.setdefault(owner.key, set()).add(owner.new_name)
    references = node.get("references")
    if isinstance(references, list):
        for reference in references:
            if not isinstance(reference, dict):
                continue
            asset_type = reference.get("type")
            name = reference.get("name")
            if not isinstance(asset_type, str) or not isinstance(name, str):
                continue
            owner = typed.get((asset_type, asset_name_comparison_key(name)))
            if owner is not None:
                candidates.setdefault(owner.key, set()).add(owner.new_name)
    return {key: next(iter(targets)) for key, targets in candidates.items() if len(targets) == 1}


def _rewrite_text(
    text: str,
    contextual: dict[str, str],
    typed: dict[tuple[str, str], _AssetOccurrence],
    mention_owners: dict[str, _AssetOccurrence],
) -> str:
    char_targets = {key: item.new_name for (kind, key), item in typed.items() if kind == "character"}
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        dialogue = match_dialogue_line(strip_shot_header(body))
        protected: set[str] = set()
        if dialogue is not None:
            speaker_key = asset_name_comparison_key(dialogue[0])
            target = char_targets.get(speaker_key)
            if target is not None:
                body, _ = rewrite_mentions(body, speaker_key, target)
                protected.add(speaker_key)
        for key, owner in mention_owners.items():
            if key in protected:
                continue
            body, _ = rewrite_mentions(body, key, contextual.get(key, owner.new_name))
        output.append(body + ending)
    return "".join(output)


def _rewrite_payload(
    payload: dict[str, Any],
    occurrences: list[_AssetOccurrence],
) -> None:
    typed = _typed_owner_map(occurrences)
    mention_owners = _mention_owner_map(occurrences)
    field_types = {field: spec.asset_type for spec in _ordered_specs() for field in spec.reference_list_fields}

    # 旧式剧本顶层角色镜像同样独立 re-key，不用会合并等价 key 的普通 rename helper。
    embedded = payload.get("characters")
    if isinstance(embedded, dict):
        rebuilt: dict[str, Any] = {}
        exact = {item.old_name: item for item in occurrences if item.asset_type == "character"}
        for old_name, entry in embedded.items():
            item = exact.get(old_name) or typed.get(("character", asset_name_comparison_key(str(old_name))))
            target = item.new_name if item is not None else old_name
            cloned = copy.deepcopy(entry)
            if item is not None and isinstance(cloned, dict):
                rewrite_entry_paths(cloned, item.spec, item.old_name, target)
            rebuilt[target] = cloned
        payload["characters"] = rebuilt

    def walk(node: object, inherited_context: dict[str, str] | None = None) -> None:
        if isinstance(node, dict):
            contextual = dict(inherited_context or {})
            contextual.update(_contextual_targets(node, typed))
            for key, value in list(node.items()):
                asset_type = field_types.get(key)
                if asset_type is not None and isinstance(value, list):
                    for index, raw in enumerate(value):
                        if isinstance(raw, str):
                            owner = typed.get((asset_type, asset_name_comparison_key(raw)))
                            if owner is not None:
                                value[index] = owner.new_name
                        else:
                            walk(raw, contextual)
                    continue
                if key == "references" and isinstance(value, list):
                    for reference in value:
                        if not isinstance(reference, dict):
                            continue
                        asset_kind = reference.get("type")
                        raw_name = reference.get("name")
                        if isinstance(asset_kind, str) and isinstance(raw_name, str):
                            owner = typed.get((asset_kind, asset_name_comparison_key(raw_name)))
                            if owner is not None:
                                reference["name"] = owner.new_name
                    continue
                if key == "speaker" and isinstance(value, str):
                    owner = typed.get(("character", asset_name_comparison_key(value)))
                    if owner is not None:
                        node[key] = owner.new_name
                    continue
                if key == "text" and isinstance(value, str):
                    node[key] = _rewrite_text(value, contextual, typed, mention_owners)
                    continue
                walk(value, contextual)
        elif isinstance(node, list):
            for item in node:
                walk(item, inherited_context)

    walk(payload)


def _rewrite_legacy_markdown_drafts(project_dir: Path, occurrences: list[_AssetOccurrence]) -> None:
    drafts_dir = project_dir / "drafts"
    if not drafts_dir.is_dir():
        return
    typed = _typed_owner_map(occurrences)
    mention_owners = _mention_owner_map(occurrences)
    for path in sorted(drafts_dir.rglob("*.md")):
        if path.name not in _LEGACY_MARKDOWN_DRAFTS:
            continue
        _assert_migration_write_target(project_dir, path)
        text = path.read_text(encoding="utf-8")
        rewritten = _rewrite_text(text, {}, typed, mention_owners)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")


def _record_for_stem(
    records: list[_AssetOccurrence], stem: str, *, allow_sequence: bool
) -> tuple[_AssetOccurrence, str] | None:
    for item in records:
        if stem == item.old_name:
            return item, ""
    if allow_sequence:
        for item in sorted(records, key=lambda value: len(value.old_name), reverse=True):
            prefix = f"{item.old_name}_"
            if stem.startswith(prefix) and stem[len(prefix) :].isdigit():
                return item, stem[len(item.old_name) :]
    key = asset_name_comparison_key(stem)
    matches = [item for item in records if item.key == key]
    if matches:
        return matches[-1], ""
    return None


def _media_path_key(relative: PurePosixPath) -> tuple[PurePosixPath, str]:
    return relative.parent, normalize_asset_name(relative.name)


def _declared_media_owners(
    occurrences: list[_AssetOccurrence],
) -> tuple[
    dict[PurePosixPath, _AssetOccurrence],
    dict[tuple[PurePosixPath, str], _AssetOccurrence | None],
]:
    exact_owners: dict[PurePosixPath, _AssetOccurrence] = {}
    normalized_owners: dict[tuple[PurePosixPath, str], _AssetOccurrence | None] = {}
    for item in occurrences:
        if not isinstance(item.entry, dict):
            continue
        base = PurePosixPath(item.spec.subdir)
        migrated_dirs = {base, base / "refs", base / "refs_audio"}
        values = [
            item.entry.get(item.spec.sheet_field),
            item.entry.get("reference_image"),
            item.entry.get("reference_audio"),
        ]
        images = item.entry.get("reference_images")
        if isinstance(images, list):
            values.extend(images)
        for value in values:
            if not isinstance(value, str):
                continue
            relative = PurePosixPath(value.replace("\\", "/"))
            if relative.parent not in migrated_dirs:
                continue
            previous_exact = exact_owners.get(relative)
            if previous_exact is not None and previous_exact is not item:
                raise ValueError(f"资产迁移媒体路径被多个条目引用: {relative}")
            exact_owners[relative] = item

            key = _media_path_key(relative)
            if key not in normalized_owners:
                normalized_owners[key] = item
            elif normalized_owners[key] is not item:
                normalized_owners[key] = None
    return exact_owners, normalized_owners


def _plan_media_moves(project_dir: Path, occurrences: list[_AssetOccurrence]) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    exact_owners, normalized_owners = _declared_media_owners(occurrences)
    for spec in _ordered_specs():
        records = [item for item in occurrences if item.asset_type == spec.asset_type]
        for relative, allow_sequence in _managed_media_roots(spec):
            directory = project_dir.joinpath(*relative.parts)
            if not directory.is_dir():
                continue
            for source in sorted(directory.iterdir()):
                if not source.is_file():
                    continue
                relative = PurePosixPath(source.relative_to(project_dir).as_posix())
                declared_owner = exact_owners.get(relative)
                if declared_owner is None:
                    declared_owner = normalized_owners.get(_media_path_key(relative))
                match = _record_for_stem(
                    [declared_owner] if declared_owner is not None else records,
                    source.stem,
                    allow_sequence=allow_sequence,
                )
                if match is None:
                    continue
                item, sequence = match
                destination = source.with_name(f"{item.new_name}{sequence}{source.suffix}")
                if source != destination:
                    _assert_migration_write_target(project_dir, source)
                    _assert_migration_write_target(project_dir, destination)
                    moves.append((source, destination))
    return moves


def _retained_history_keys(project_dir: Path, project: dict[str, Any]) -> dict[str, set[str]]:
    versions_file = project_dir / "versions" / "versions.json"
    if not versions_file.is_file():
        return {}
    payload = load_json(versions_file)
    if not isinstance(payload, dict):
        raise ValueError("versions/versions.json 必须是对象")
    retained: dict[str, set[str]] = {}
    for spec in _ordered_specs():
        current_bucket = project.get(spec.bucket_key)
        active_keys = (
            {asset_name_comparison_key(str(name)) for name in current_bucket}
            if isinstance(current_bucket, dict)
            else set()
        )
        history_bucket = payload.get(spec.bucket_key)
        if isinstance(history_bucket, dict):
            retained[spec.asset_type] = {
                key for name in history_bucket if (key := asset_name_comparison_key(str(name))) not in active_keys
            }
    return retained


def _confined_version_path(project_dir: Path, spec: AssetSpec, relative: PurePosixPath) -> Path:
    """Resolve a version entry only inside its declared project version bucket."""
    if relative.is_absolute():
        raise ValueError(f"版本快照路径不得为绝对路径: {relative}")
    expected_root = project_dir / "versions" / spec.bucket_key
    candidate = project_dir.joinpath(*relative.parts)
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise ValueError(f"版本快照路径必须位于 versions/{spec.bucket_key}: {relative}") from exc

    project_root = project_dir.resolve()
    resolved_root = expected_root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    if not resolved_root.is_relative_to(project_root) or not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(f"版本快照路径越出项目目录: {relative}")
    _assert_migration_write_target(project_dir, candidate)
    return candidate


def _rewrite_versions(project_dir: Path, occurrences: list[_AssetOccurrence]) -> list[tuple[Path, Path]]:
    versions_file = project_dir / "versions" / "versions.json"
    if not versions_file.is_file():
        return []
    _assert_migration_write_target(project_dir, versions_file)
    payload = load_json(versions_file)
    if not isinstance(payload, dict):
        raise ValueError("versions/versions.json 必须是对象")
    moves: list[tuple[Path, Path]] = []
    typed = _typed_owner_map(occurrences)
    for spec in _ordered_specs():
        bucket = payload.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        exact = {item.old_name: item for item in occurrences if item.asset_type == spec.asset_type}
        rebuilt: dict[str, Any] = {}
        for old_name, record in bucket.items():
            item = exact.get(old_name) or typed.get((spec.asset_type, asset_name_comparison_key(str(old_name))))
            if item is None:
                rebuilt[old_name] = record
                continue
            cloned = copy.deepcopy(record)
            versions = cloned.get("versions") if isinstance(cloned, dict) else None
            if isinstance(versions, list):
                for version in versions:
                    if not isinstance(version, dict) or not isinstance(version.get("file"), str):
                        continue
                    relative = PurePosixPath(version["file"].replace("\\", "/"))
                    old_prefix = f"{old_name}_v"
                    basename = relative.name
                    if basename.startswith(old_prefix):
                        suffix = basename[len(old_name) :]
                    else:
                        marker = basename.rfind("_v")
                        if marker < 0 or asset_name_comparison_key(basename[:marker]) != item.key:
                            continue
                        suffix = basename[marker:]
                    new_relative = relative.with_name(f"{item.new_name}{suffix}")
                    source = _confined_version_path(project_dir, spec, relative)
                    destination = _confined_version_path(project_dir, spec, new_relative)
                    if source != destination:
                        moves.append((source, destination))
                    version["file"] = str(new_relative)
            rebuilt[item.new_name] = cloned
        payload[spec.bucket_key] = rebuilt
    atomic_write_json(versions_file, payload)
    return moves


def _execute_moves(moves: list[tuple[Path, Path]]) -> None:
    sources = {source for source, _ in moves if source.exists()}
    destinations: set[Path] = set()
    pending: list[tuple[Path, Path, Path]] = []
    for source, destination in moves:
        if not source.exists():
            continue
        if destination.exists():
            try:
                if destination.samefile(source):
                    continue
            except OSError:
                logger.debug("无法确认迁移目标与源文件是否相同，继续按独立路径检查冲突", exc_info=True)
        if destination in destinations:
            raise ValueError(f"资产迁移目标重复: {destination}")
        if destination.exists() and destination not in sources:
            raise ValueError(f"资产迁移目标已被占用: {destination}")
        destinations.add(destination)
        temporary = source.with_name(f".{source.name}.v6-{uuid.uuid4().hex}.tmp")
        os.replace(source, temporary)
        pending.append((temporary, source, destination))
    try:
        for temporary, _source, destination in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, destination)
    except Exception:
        for temporary, source, _destination in reversed(pending):
            if temporary.exists():
                os.replace(temporary, source)
        raise


def _migrate_staged_tree(project_dir: Path) -> None:
    project_file = project_dir / "project.json"
    _assert_migration_write_target(project_dir, project_file)
    project = load_json(project_file)
    if int(project.get("schema_version") or 0) >= 6:
        return
    _assert_migration_write_roots(project_dir)
    reserved_keys = _retained_history_keys(project_dir, project)
    for asset_type, keys in _reserved_media_keys(project_dir).items():
        reserved_keys.setdefault(asset_type, set()).update(keys)
    occurrences = _plan_occurrences(project, reserved_keys)
    moves = _plan_media_moves(project_dir, occurrences)

    for item in occurrences:
        if isinstance(item.entry, dict):
            rewrite_entry_paths(item.entry, item.spec, item.old_name, item.new_name)
    for spec in _ordered_specs():
        bucket = project.get(spec.bucket_key, {})
        items = [item for item in occurrences if item.asset_type == spec.asset_type]
        project[spec.bucket_key] = {item.new_name: item.entry for item in items} if isinstance(bucket, dict) else bucket

    for path in sorted((project_dir / "scripts").rglob("*.json")) if (project_dir / "scripts").is_dir() else []:
        _assert_migration_write_target(project_dir, path)
        payload = load_json(path)
        if isinstance(payload, dict):
            _rewrite_payload(payload, occurrences)
            atomic_write_json(path, payload)
    for path in sorted((project_dir / "drafts").rglob("*.json")) if (project_dir / "drafts").is_dir() else []:
        _assert_migration_write_target(project_dir, path)
        payload = load_json(path)
        if isinstance(payload, dict):
            _rewrite_payload(payload, occurrences)
            atomic_write_json(path, payload)
    _rewrite_legacy_markdown_drafts(project_dir, occurrences)

    moves.extend(_rewrite_versions(project_dir, occurrences))
    _execute_moves(moves)
    project["schema_version"] = 6
    ensure_project_asset_namespace(project)
    atomic_write_json(project_file, project)


def migrate_v5_to_v6(project_dir: Path) -> None:
    """Make the multi-file v6 migration atomic by transforming a sibling staging tree then swapping it in.

    进程被硬杀在两次改名之间时，本函数的 ``finally`` 不会执行；项目目录缺失而 rollback 目录残留的
    局面由启动期的 ``reclaim_interrupted_swaps`` 认领。
    """
    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    data = load_json(project_file)
    if int(data.get("schema_version") or 0) >= 6:
        return

    ensure_disk_headroom(project_dir)
    staging = Path(tempfile.mkdtemp(prefix=staging_dir_prefix(project_dir.name), dir=project_dir.parent))
    rollback = new_rollback_dir(project_dir)
    try:
        shutil.copytree(project_dir, staging, symlinks=True, dirs_exist_ok=True)
        _migrate_staged_tree(staging)
        os.replace(project_dir, rollback)
        try:
            os.replace(staging, project_dir)
        except BaseException:
            os.replace(rollback, project_dir)
            raise
        shutil.rmtree(rollback, ignore_errors=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if rollback.exists() and not project_dir.exists():
            os.replace(rollback, project_dir)


__all__ = ["migrate_v5_to_v6"]
