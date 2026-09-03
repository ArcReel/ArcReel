"""衍生资产图随改名一起搬迁：图、版本历史与产物清单键（见 ``docs/adr/0072``）。

衍生资产图的落盘坐标完全由两段名字决定（``characters/derivatives/{本体}/{衍生}.png``、
版本资源 id ``本体/衍生``、清单键 ``asset_sheet("character", "本体/衍生")``）。任一段名字
一改，这三样都得跟着走，否则改名后那张图既不在新坐标下，也不再被任何登记引用。

本体改名与衍生改名只差在改的是哪一段，落盘动作同形，因此共用同一份规划：调用方给出
（旧本体名 → 新本体名）与一组（旧衍生名 → 新衍生名），本模块把全部动作算齐再交还，
让「零写入的预检」与「按序落盘」落在调用方的同一把锁与同一次正式事务里。

与 :mod:`lib.asset_derivative_cleanup` 是一对：那边是登记消失时三样一起清，这边是登记
改名时三样一起搬。
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from lib.artifact_manifest import ArtifactEntryRekeyPlan, ArtifactManifest, ArtifactManifestAdapter
from lib.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    derivative_artifact_id,
    derivative_artifact_key,
    derivative_sheet_dir,
    derivative_sheet_relative_path,
    derivative_table,
    derivative_version_dir,
)
from lib.asset_rename import AssetRenameFileCollisionError
from lib.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, normalize_asset_name
from lib.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.version_manager import VersionManager

_SHEET_FIELD = ASSET_SPECS[DERIVATIVE_ASSET_TYPE].sheet_field


@dataclass(frozen=True, slots=True)
class DerivativeSheetRelocation:
    """一次改名要对衍生资产图做的全部落盘动作，规划期算齐、落盘期照做。

    ``relocate`` 搬图与版本历史，``commit_manifest`` 在调用方的正式事务里重键清单。
    两步都幂等：源已不在就跳过，中途失败重跑同一次改名即可收敛。
    """

    moves: tuple[tuple[Path, Path], ...]
    version_renames: tuple[tuple[str, str], ...]
    manifest_plans: tuple[ArtifactEntryRekeyPlan, ...]
    retired_dirs: tuple[Path, ...]
    files: int
    _version_manager: VersionManager

    def relocate(self) -> None:
        """搬图与版本快照，最后收掉空掉的旧目录。"""
        for source, destination in self.moves:
            if source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
        for old_id, new_id in self.version_renames:
            self._version_manager.rename_resource(CHARACTER_DERIVATIVE_RESOURCE_TYPE, old_id, new_id)
        for directory in self.retired_dirs:
            # 非空、不存在或无权限都不是错误：目录只是收纳，留着不影响任何登记。
            with contextlib.suppress(OSError):
                directory.rmdir()

    def commit_manifest(self) -> None:
        """把每个衍生的清单条目重键到新 id。各计划的键互不相交，逐个 CAS 提交安全。"""
        for plan in self.manifest_plans:
            plan.commit()


def plan_derivative_sheet_relocation(
    project_dir: Path,
    *,
    manifest_adapter: ArtifactManifestAdapter | None,
    old_owner: str,
    new_owner: str,
    renames: Sequence[tuple[str, str]],
) -> DerivativeSheetRelocation:
    """规划一组衍生资产图的搬迁；只读，不写任何字节。

    ``renames`` 是（旧衍生名 → 新衍生名）：本体改名时每对两段同名，衍生改名时只有一对。
    本体改名时额外把旧本体目录下**全部**文件一并搬走并收掉旧目录——与
    :func:`lib.asset_rename.plan_asset_file_renames` 同理，字段未写全的生成中间产物
    不该顶着旧名残留；本体没换名时只动被改名的那一个衍生，同本体的兄弟图不受牵连。

    Raises:
        AssetRenameFileCollisionError: 某个迁移目标路径已被他人占用。
        AssetRenameHistoryCollisionError: 新 id 下已有属于别的衍生的版本历史。
    """
    owner_moved = normalize_asset_name(old_owner) != normalize_asset_name(new_owner)
    stem_map = {normalize_asset_name(old): new for old, new in renames}

    moves: list[tuple[Path, Path]] = []
    planned: set[Path] = set()
    source_dir = project_dir / derivative_sheet_dir(old_owner)
    destination_dir = project_dir / derivative_sheet_dir(new_owner)
    if source_dir.is_dir():
        for file in sorted(source_dir.iterdir()):
            if not file.is_file():
                continue
            new_stem = stem_map.get(normalize_asset_name(file.stem)) or (file.stem if owner_moved else None)
            if new_stem is None:
                continue
            destination = destination_dir / (new_stem + file.suffix)
            if destination == file:
                continue
            if destination in planned or (destination.exists() and not destination.samefile(file)):
                raise AssetRenameFileCollisionError(destination)
            planned.add(destination)
            moves.append((file, destination))

    version_manager = VersionManager(project_dir)
    manifest = ArtifactManifest(manifest_adapter) if manifest_adapter is not None else None
    version_renames: list[tuple[str, str]] = []
    manifest_plans: list[ArtifactEntryRekeyPlan] = []
    snapshots = 0
    for old_name, new_name in renames:
        old_id = derivative_artifact_id(old_owner, old_name)
        new_id = derivative_artifact_id(new_owner, new_name)
        if old_id == new_id:
            continue
        # dry-run 在此把「新 id 下已有别人的历史」拦成零写入，与本体改名同口径。
        snapshots += version_manager.rename_resource(CHARACTER_DERIVATIVE_RESOURCE_TYPE, old_id, new_id, dry_run=True)
        version_renames.append((old_id, new_id))
        if manifest is not None:
            manifest_plans.append(
                manifest.plan_entry_rekey(
                    derivative_artifact_key(old_owner, old_name),
                    derivative_artifact_key(new_owner, new_name),
                    artifact_path_rewrites={
                        derivative_sheet_relative_path(old_owner, old_name): derivative_sheet_relative_path(
                            new_owner, new_name
                        )
                    },
                )
            )

    return DerivativeSheetRelocation(
        moves=tuple(moves),
        version_renames=tuple(version_renames),
        manifest_plans=tuple(manifest_plans),
        # 本体换了名，旧本体的两个收纳目录就此作废；同本体内的衍生改名不动目录。
        retired_dirs=((source_dir, project_dir / derivative_version_dir(old_owner)) if owner_moved else ()),
        files=len(moves) + snapshots,
        _version_manager=version_manager,
    )


def rewrite_derivative_sheet_paths(
    entry: Mapping[str, Any],
    *,
    old_owner: str,
    new_owner: str,
    renames: Sequence[tuple[str, str]],
) -> int:
    """就地把衍生条目里的资产图路径改到新坐标，返回改写数。

    只动值恰好等于旧坐标规范路径的字段——与 :func:`lib.asset_rename.rewrite_entry_paths`
    同一条判断：指到别处的路径不在搬迁范围内，改了字段反而把一条有效引用指空。
    """
    table = entry.get(DERIVATIVES_FIELD)
    if not isinstance(table, dict):
        return 0
    count = 0
    for old_name, new_name in renames:
        # 条目的键可能已被调用方改成新名，也可能还是旧名（本体改名时衍生名不变）。
        derivative = table.get(new_name) if new_name in table else table.get(old_name)
        if not isinstance(derivative, dict):
            continue
        value = derivative.get(_SHEET_FIELD)
        renamed = derivative_sheet_relative_path(new_owner, new_name)
        if not isinstance(value, str) or not value or value == renamed:
            continue
        current = PurePosixPath(value.replace("\\", "/")).as_posix()
        if normalize_asset_name(current) != normalize_asset_name(derivative_sheet_relative_path(old_owner, old_name)):
            continue
        derivative[_SHEET_FIELD] = renamed
        count += 1
    return count


def owner_derivative_renames(entry: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """本体改名时的改名对：每个衍生自身不改名，只随本体换目录。"""
    return tuple((name, name) for name in derivative_table(entry))


__all__ = [
    "DerivativeSheetRelocation",
    "owner_derivative_renames",
    "plan_derivative_sheet_relocation",
    "rewrite_derivative_sheet_paths",
]
