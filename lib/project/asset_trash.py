"""资产与衍生进出项目回收站：删除成为一条带结构快照的删除记录，恢复按落地规则加回。

一条资产记录收资产按名命名的文件（资产图、原图、参考音频）、它与名下衍生的版本快照和衍生图；资产条目、
版本记录与清单登记留在快照里。删除资产、资产合并清掉被并方、删除衍生都经这里。

恢复时原名空闲（没有按资产名归一规则重名的资产、没有残留的同名版本历史、文件落点空闲）就沿用，
剧本里原有的引用自然接上；被占用则加「 (n)」后缀，后缀在迁移后的临时项目里走完整的改名级联
（文件、版本历史、衍生、清单键一并改名），再整体加回。合并清掉的资产恢复后可以再走资产合并。
"""

from __future__ import annotations

import contextlib
import copy
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_registration import forget_current_resource_artifact, register_artifact_entries_atomically
from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.asset_derivatives import (
    derivative_artifact_id,
    derivative_artifact_key,
    derivative_sheet_dir,
    derivative_sheet_relative_path,
    derivative_table,
    derivative_version_dir,
    ensure_derivative_table,
    find_derivative_name,
)
from lib.project.asset_rename import (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    asset_files_named,
)
from lib.project.asset_types import (
    ALIASES_FIELD,
    ASSET_SPECS,
    AssetSpec,
    ProjectAssetNameConflictError,
    asset_name_namespace_key,
    find_project_asset_name,
    resolve_asset_key,
)
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.project.trash_restore import Landing, Restoration, RestoreBlocked

__all__ = [
    "ASSET_DERIVATIVE_KIND",
    "ASSET_KIND",
    "deleted_asset_record",
    "state_before",
    "state_paths",
    "take_asset",
    "take_asset_derivative",
    "trash_asset",
    "trash_asset_derivative",
]

ASSET_KIND = "asset"
ASSET_DERIVATIVE_KIND = "asset_derivative"

_VERSIONS_FILE = "versions/versions.json"
#: 删除资产在同一临界区里改写的结构文件：资产条目、版本记录与清单登记。
_STATE_FILES = ("project.json", _VERSIONS_FILE, MANIFEST_FILENAME)
#: 换名时最多试的后缀数。
_MAX_SUFFIX = 1000
_NAME_TAKEN = (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    ProjectAssetNameConflictError,
)


# ---------------------------------------------------------------- 删除


def state_paths(project_dir: Path) -> tuple[Path, ...]:
    """删除资产会改写的结构文件；调用方把它们放进同一个正式写入事务，删除记录建不成时一起回到删除前。"""
    return tuple(project_dir / rel for rel in _STATE_FILES)


def state_before(project_dir: Path) -> dict[str, bytes]:
    """在改写之前（持项目锁）读下 :func:`state_paths` 的内容，供删除记录的结构快照用。"""
    return {
        rel: path.read_bytes()
        for rel in _STATE_FILES
        if (path := project_dir / rel).is_file() and not path.is_symlink()
    }


def trash_asset(
    project_dir: Path,
    spec: AssetSpec,
    name: str,
    entry: Mapping[str, Any],
    before: Mapping[str, bytes],
    **details: Any,
) -> project_trash.TrashRecord:
    """把已从 project.json 去掉的资产 ``name`` 的文件、版本历史与名下衍生移进一条删除记录。

    调用方持项目锁，``before`` 是改写 project.json 之前读下的 :func:`state_before`，并在包住
    :func:`state_paths` 的正式写入事务里调用：记录建不成时清单与版本记录随之回滚。``details`` 记进对象描述。
    """
    derivatives = list(derivative_table(entry)) if spec.supports_derivatives else []
    return _record(
        project_dir,
        {"kind": ASSET_KIND, "asset_type": spec.asset_type, "name": name, **details},
        owner=name,
        resources=[(spec.bucket_key, name), *_derivative_resources(name, derivatives)],
        files=[
            *_relative(project_dir, asset_files_named(project_dir, spec, name)),
            *_derivative_sheets(project_dir, name, derivatives),
        ],
        before=before,
    )


def trash_asset_derivative(
    project_dir: Path, owner: str, name: str, before: Mapping[str, bytes]
) -> project_trash.TrashRecord:
    """把已从本体衍生表去掉的衍生 ``owner/name`` 的图与版本历史移进一条删除记录；前提同 :func:`trash_asset`。"""
    return _record(
        project_dir,
        {"kind": ASSET_DERIVATIVE_KIND, "asset_type": "character", "owner": owner, "name": name},
        owner=owner,
        resources=_derivative_resources(owner, [name]),
        files=_derivative_sheets(project_dir, owner, [name]),
        before=before,
    )


def _record(
    project_dir: Path,
    obj: Mapping[str, Any],
    *,
    owner: str,
    resources: Sequence[tuple[str, str]],
    files: Sequence[str],
    before: Mapping[str, bytes],
) -> project_trash.TrashRecord:
    """解除这些资源的清单登记与版本记录，把文件与版本快照记成一条删除记录，最后收掉空掉的衍生目录。"""
    for resource_type, resource_id in resources:
        forget_current_resource_artifact(project_dir, resource_type=resource_type, resource_id=resource_id)
    histories = _histories(project_dir, resources)
    snapshots = _history_files(project_dir, histories)
    VersionManager(project_dir).detach_resources(_ids(histories))
    record = project_trash.record_deletion(project_dir, obj, [*files, *snapshots], snapshot=True, before=before)
    for directory in (derivative_sheet_dir(owner), derivative_version_dir(owner)):
        # 非空、不存在或无权限都不是错误：目录只是收纳，留着不影响任何登记。
        with contextlib.suppress(OSError):
            (project_dir / directory).rmdir()  # hard-delete: infra -- 衍生图移进回收站后的空目录
    return record


def deleted_asset_record(project_dir: Path, asset_type: str, name: str) -> str | None:
    """回收站里同类型、按资产名归一规则同名的资产删除记录（最新的一条）的 ID；没有时为 ``None``。"""
    key = asset_name_namespace_key(name)
    return next(
        (
            record.id
            for record in project_trash.list_records(project_dir)
            if record.has_snapshot
            and record.object.get("kind") == ASSET_KIND
            and record.object.get("asset_type") == asset_type
            and asset_name_namespace_key(str(record.object.get("name", ""))) == key
        ),
        None,
    )


# ---------------------------------------------------------------- 恢复


def take_asset(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """按资产名从迁移后的临时项目取出资产；落地时名字被占用就在临时项目里改名后再加回。"""
    spec = ASSET_SPECS[str(obj.get("asset_type"))]
    original = resolve_asset_key(_read_project(staged).get(spec.bucket_key), str(obj.get("name")))
    if original is None:
        raise ConflictError("trash_restore_failed", id=str(obj.get("name")))
    #: 合并时迁到保留方名下的衍生属于保留方，不随被并方加回。
    moved = {str(item) for item in obj.get("derivatives_moved") or ()}
    projects = ProjectManager.for_project_dir(project_dir)
    staged_projects = ProjectManager.for_project_dir(staged)

    def resources(name: str) -> list[tuple[str, str]]:
        entry = _read_project(staged)[spec.bucket_key][name]
        owned = [(spec.bucket_key, name)]
        if spec.supports_derivatives:
            owned += [
                (CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_artifact_id(name, derivative))
                for derivative in derivative_table(entry)
                if derivative not in moved
            ]
        return owned

    def files(name: str) -> list[str]:
        named = _relative(staged, asset_files_named(staged, spec, name))
        derivatives = [
            derivative_sheet_relative_path(*artifact_id.split("/", 1))
            for resource_type, artifact_id in resources(name)
            if resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE
        ]
        return [*named, *_existing(staged, derivatives), *_history_files(staged, _histories(staged, resources(name)))]

    def free(name: str) -> bool:
        current = projects.load_project(project_dir.name)
        key = asset_name_namespace_key(name)
        derivatives = (
            _history_keys(project_dir, CHARACTER_DERIVATIVE_RESOURCE_TYPE) if spec.supports_derivatives else ()
        )
        return (
            find_project_asset_name(current, name) is None
            and key not in _history_keys(project_dir, spec.bucket_key)
            and not any(item.startswith(f"{key}/") for item in derivatives)
        )

    def rename(old: str, new: str) -> None:
        staged_projects.rename_asset(staged.name, spec.bucket_key, old, new)

    def land(landing: Landing) -> Mapping[str, Any]:
        aliases = _read_project(staged)[spec.bucket_key][original].get(ALIASES_FIELD)
        landed = _land_name(original, landing, free=free, rename=rename, files=files)
        entry = copy.deepcopy(_read_project(staged)[spec.bucket_key][landed])
        if landed != original and ALIASES_FIELD in entry:
            # 改名级联把原名记成别名；原名此刻属于项目里的另一个资产，加回的资产不认领它。
            entry[ALIASES_FIELD] = copy.deepcopy(aliases) if isinstance(aliases, list) else []
        if spec.supports_derivatives:
            table = ensure_derivative_table(entry)
            for derivative in moved:
                table.pop(derivative, None)
        claims = [ArtifactKey.asset_sheet(spec.asset_type, landed)] + [
            derivative_artifact_key(*artifact_id.split("/", 1))
            for resource_type, artifact_id in resources(landed)
            if resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE
        ]

        def add(project: dict) -> None:
            project.setdefault(spec.bucket_key, {})[landed] = entry

        _lift(project_dir, staged, landing, files(landed), resources(landed), claims, projects, add)
        return {**obj, "name": landed}

    return Restoration(land=land, after=lambda: _changed(project_dir))


def take_asset_derivative(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """按本体名与衍生名从临时项目取出衍生；名字被占用时在临时项目里改名后加回。

    本体也在项目回收站里时抛 :class:`RestoreBlocked`：先恢复本体、再恢复衍生，记录不降级。
    本体已彻底删除时无法按原结构恢复，按取不出对象降级。
    """
    spec = ASSET_SPECS["character"]
    owner = resolve_asset_key(_read_project(staged).get(spec.bucket_key), str(obj.get("owner")))
    original = (
        resolve_asset_key(derivative_table(_read_project(staged)[spec.bucket_key][owner]), str(obj.get("name")))
        if owner is not None
        else None
    )
    if owner is None or original is None:
        raise ConflictError("trash_restore_failed", id=str(obj.get("name")))
    projects = ProjectManager.for_project_dir(project_dir)
    staged_projects = ProjectManager.for_project_dir(staged)

    def current_owner() -> tuple[str, dict[str, Any]]:
        bucket = projects.load_project(project_dir.name).get(spec.bucket_key)
        key = resolve_asset_key(bucket, owner)
        if key is None or not isinstance(bucket, dict) or not isinstance(bucket[key], dict):
            if deleted_asset_record(project_dir, spec.asset_type, owner) is not None:
                raise RestoreBlocked("trash_restore_needs_character", name=owner)
            # 本体已彻底删除：加回的衍生没有归属。
            raise ConflictError("trash_restore_failed", id=str(obj.get("name")))
        return key, bucket[key]

    # 此处按取不出对象降级（本体已彻底删除）或拒绝（本体在回收站里）；落地时在锁内再核对一次。
    current_owner()

    def free(name: str) -> bool:
        _key, entry = current_owner()
        artifact_id = asset_name_namespace_key(derivative_artifact_id(owner, name))
        return find_derivative_name(derivative_table(entry), name) is None and artifact_id not in _history_keys(
            project_dir, CHARACTER_DERIVATIVE_RESOURCE_TYPE
        )

    def files(name: str) -> list[str]:
        resource = [(CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_artifact_id(owner, name))]
        return [
            *_existing(staged, [derivative_sheet_relative_path(owner, name)]),
            *_history_files(staged, _histories(staged, resource)),
        ]

    def rename(old: str, new: str) -> None:
        staged_projects.rename_asset_derivative("character", staged.name, owner, old, new)

    def land(landing: Landing) -> Mapping[str, Any]:
        landed = _land_name(original, landing, free=free, rename=rename, files=files)
        row = copy.deepcopy(derivative_table(_read_project(staged)[spec.bucket_key][owner])[landed])

        def add(project: dict) -> None:
            key = resolve_asset_key(project.get(spec.bucket_key), owner)
            ensure_derivative_table(project[spec.bucket_key][key])[landed] = row

        _lift(
            project_dir,
            staged,
            landing,
            files(landed),
            [(CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_artifact_id(owner, landed))],
            [derivative_artifact_key(owner, landed)],
            projects,
            add,
        )
        return {**obj, "name": landed}

    return Restoration(land=land, after=lambda: _changed(project_dir))


def _land_name(
    original: str,
    landing: Landing,
    *,
    free: Callable[[str], bool],
    rename: Callable[[str, str], None],
    files: Callable[[str], list[str]],
) -> str:
    """原名空闲就沿用，否则依次试「 (n)」后缀；换名在临时项目里走改名级联，返回落地的名字。"""
    current = original
    for n in range(_MAX_SUFFIX):
        candidate = original if n == 0 else f"{original} ({n})"
        if not free(candidate):
            continue
        if candidate != current:
            try:
                rename(current, candidate)
            except _NAME_TAKEN:
                continue  # 临时项目里删除时就有这个名字（或它的残留历史）
            current = candidate
        if all(landing.free(rel) for rel in files(current)):
            return current
    raise ConflictError("trash_restore_failed", id=original)


def _lift(
    project_dir: Path,
    staged: Path,
    landing: Landing,
    files: Sequence[str],
    resources: Sequence[tuple[str, str]],
    claims: Sequence[ArtifactKey],
    projects: ProjectManager,
    add: Callable[[dict], None],
) -> None:
    """把文件、版本记录、清单登记与结构条目从临时项目加回当前项目；任一步失败时结构文件回到之前。"""
    histories = _histories(staged, resources)
    staged_manifest = ProjectArtifactManifestAdapter(staged)
    with formal_write_transaction(*state_paths(project_dir)):
        for rel in files:
            landing.place(staged / rel, rel, original=rel)
        VersionManager(project_dir).attach_resources(histories)
        projects.update_project_locked(project_dir.name, add)
        entries = {key: entry for key in claims if (entry := staged_manifest.get_entry(key)) is not None}
        if entries:
            # 补录：登记取自删除前（迁移后）的清单，依据摘要不变，输入此后变过的照常读为过期。
            register_artifact_entries_atomically(
                project_dir,
                {key: ArtifactManifestEntry(entry.artifact_path, entry.basis_digest) for key, entry in entries.items()},
            )


def _history_keys(project_dir: Path, resource_type: str) -> set[str]:
    """当前项目里该资源类型已有版本记录的资源 ID（按资产名归一规则），包括早先删除资产时留下的历史。"""
    bucket = _read_json(project_dir / _VERSIONS_FILE).get(resource_type)
    return (
        {asset_name_namespace_key(key) for key in bucket if isinstance(key, str)} if isinstance(bucket, dict) else set()
    )


def _derivative_resources(owner: str, derivatives: Sequence[str]) -> list[tuple[str, str]]:
    return [(CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_artifact_id(owner, name)) for name in derivatives]


def _derivative_sheets(root: Path, owner: str, derivatives: Sequence[str]) -> list[str]:
    return _existing(root, [derivative_sheet_relative_path(owner, name) for name in derivatives])


def _histories(root: Path, resources: Sequence[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    """这些资源的版本记录；资源 ID 按 NFC 解析到落盘的真实 key（存量记录可能是 NFD）。"""
    data = _read_json(root / _VERSIONS_FILE)
    resolved = [
        (resource_type, key)
        for resource_type, resource_id in resources
        if (key := resolve_asset_key(data.get(resource_type), resource_id)) is not None
    ]
    return VersionManager(root).resource_records(resolved)


def _history_files(root: Path, histories: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return _existing(root, VersionManager(root).snapshot_files(histories))


def _ids(histories: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, str]]:
    return [(resource_type, resource_id) for resource_type, bucket in histories.items() for resource_id in bucket]


def _changed(project_dir: Path) -> None:
    from lib.project.project_change_hints import emit_project_change_hint

    emit_project_change_hint(project_dir.name, changed_paths=[ProjectManager.PROJECT_FILE])


def _read_project(project_dir: Path) -> dict[str, Any]:
    return _read_json(project_dir / ProjectManager.PROJECT_FILE)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _existing(root: Path, rels: Sequence[str]) -> list[str]:
    return [rel for rel in dict.fromkeys(rels) if (root / rel).is_file() and not (root / rel).is_symlink()]


def _relative(root: Path, paths: Sequence[Path]) -> list[str]:
    return _existing(root, [path.relative_to(root).as_posix() for path in paths])
