"""宫格、尾帧与角色参考音频进出项目回收站（``docs/adr/0107``）。

三类对象都挂在上级对象上：宫格属于集，尾帧属于剧本里的分镜，参考音频属于角色。删除成为一条带结构快照的删除记录；
恢复时上级对象已不在，按取不出对象降级为只加回文件；落地时在锁内再核对一次，期间上级被删则本次恢复失败、记录保留。
宫格所属的集也在项目回收站里时不降级：先恢复集、再恢复宫格（:func:`~lib.episode.episode_trash.require_live_episode`）。

- 宫格：记录收宫格记录、联合图与它的版本历史快照。原 ID 空闲时整体加回，版本历史与产物清单登记取自迁移后的临时项目，
  照常按产物时效判定；ID 被占用时按取不出对象降级。
- 尾帧、参考音频：原位置空闲则回原位，被占用则换新名加回。回原位且上级对象此刻没有指向可用文件时重新指向加回的文件，
  否则加回的文件不被引用。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import register_artifact_entries_atomically
from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_trash import require_live_episode
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.asset_types import resolve_asset_key
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager, is_episode_number
from lib.project.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path
from lib.project.trash_restore import Landing, Restoration
from lib.script.grid.models import GridGeneration
from lib.script.item_excerpt import position_snapshot
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items

__all__ = [
    "END_FRAME_KIND",
    "GRID_KIND",
    "REFERENCE_AUDIO_KIND",
    "take_end_frame",
    "take_grid",
    "take_reference_audio",
    "trash_end_frame",
    "trash_grid",
    "trash_reference_audio",
]

GRID_KIND = "grid"
END_FRAME_KIND = "end_frame"
REFERENCE_AUDIO_KIND = "reference_audio"

_GRID_TYPE = "grids"


# ---------------------------------------------------------------- 删除


def trash_grid(project_dir: Path, grid_id: str, episode: int | None) -> project_trash.TrashRecord:
    """把一条宫格记录连同联合图与版本历史移进项目回收站；调用方持项目锁与该宫格的记录锁。

    ``episode`` 为空（记录读不出）时没有可证明的清单登记，只移走文件。
    """
    versions = VersionManager(project_dir)
    resource = [(_GRID_TYPE, grid_id)]
    candidates = [
        f"grids/{grid_id}.json",
        resource_relative_path(_GRID_TYPE, grid_id),
        *versions.snapshot_files(versions.resource_records(resource)),
    ]
    files = [rel for rel in dict.fromkeys(candidates) if _is_plain_file(project_dir / rel)]
    # 快照先拍：版本历史与清单登记留在快照里，恢复时从迁移后的临时项目取出。
    record = project_trash.record_deletion(
        project_dir, {"kind": GRID_KIND, "id": grid_id, "episode": episode}, files, snapshot=True
    )
    try:
        with formal_write_transaction(versions.versions_file):
            versions.detach_resources(resource)
            if episode is not None:
                register_artifact_entries_atomically(project_dir, {ArtifactKey.episode_grid(episode, grid_id): None})
    except BaseException:
        project_trash.withdraw(project_dir, record.id)
        raise
    return record


def trash_end_frame(
    project_dir: Path, script_file: str, shot_id: str, script: Mapping[str, Any]
) -> project_trash.TrashRecord:
    """把分镜的尾帧快照移进项目回收站；调用方持该剧本的锁与项目锁，字段已在同一临界区里置空。

    ``script`` 是同一临界区里写回的剧本：记录带上分镜所在的集与它此刻的位置、正文摘录，回收站凭它们称呼分镜。
    剧本内没有整数 ``episode`` 时，集号取自规范的 ``script_file`` 文件名。
    """
    rel = resource_relative_path(END_FRAME_RESOURCE_TYPE, shot_id)
    episode = script.get("episode")
    if not is_episode_number(episode):
        episode = ProjectManager.canonical_script_episode(script_file)
    obj = {
        "kind": END_FRAME_KIND,
        "script_file": script_file,
        "shot_id": shot_id,
        "path": rel,
        "episode": episode,
        **position_snapshot(script, shot_id),
    }
    return project_trash.record_deletion(project_dir, obj, [rel], snapshot=True)


def trash_reference_audio(project_dir: Path, character: str, rel: str) -> project_trash.TrashRecord:
    """把角色不再引用的参考音频移进项目回收站；调用方持项目锁，字段已在同一临界区里改掉。"""
    obj = {"kind": REFERENCE_AUDIO_KIND, "character": character, "path": rel}
    return project_trash.record_deletion(project_dir, obj, [rel], snapshot=True)


# ---------------------------------------------------------------- 恢复


def take_grid(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """按宫格 ID 从迁移后的临时项目取出宫格记录、联合图、版本历史与清单登记。"""
    grid_id = str(obj.get("id"))
    try:
        grid = GridGeneration.from_dict(json.loads((staged / f"grids/{grid_id}.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _failed(grid_id) from exc
    if grid.id != grid_id:
        raise _failed(grid_id)
    resource = [(_GRID_TYPE, grid_id)]
    staged_versions = VersionManager(staged)
    history = staged_versions.resource_records(resource)
    rels = [
        rel
        for rel in dict.fromkeys(
            [
                f"grids/{grid_id}.json",
                resource_relative_path(_GRID_TYPE, grid_id),
                *staged_versions.snapshot_files(history),
            ]
        )
        if _is_plain_file(staged / rel)
    ]
    key = ArtifactKey.episode_grid(grid.episode, grid.id)
    claim = ProjectArtifactManifestAdapter(staged).snapshot_entries().get(key)
    versions = VersionManager(project_dir)

    def require_free() -> None:
        require_live_episode(project_dir, grid.episode, grid_id)
        if any(not _free(project_dir, rel) for rel in rels) or versions.resource_records(resource):
            raise _failed(grid_id)

    # 此处按取不出对象降级（集已彻底删除、位置被占）或拒绝（集在回收站里）；落地时在锁内再核对一次。
    require_free()

    def land(landing: Landing) -> None:
        require_free()
        for rel in rels:
            landing.place(staged / rel, rel, original=rel)
        with formal_write_transaction(versions.versions_file):
            versions.attach_resources(history)
            if claim is not None:
                # 补录：登记取自删除前（迁移后）的清单，依据摘要不变，输入此后变过的照常读为过期。
                register_artifact_entries_atomically(
                    project_dir, {key: ArtifactManifestEntry(claim.artifact_path, claim.basis_digest)}
                )

    return Restoration(land=land)


def take_end_frame(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """取出分镜的尾帧快照：原位置空闲则回原位并让分镜重新指向它，被占用则换新名、不改分镜。"""
    rel, script_file, shot_id = str(obj.get("path")), str(obj.get("script_file")), str(obj.get("shot_id"))
    if not _is_plain_file(staged / rel):
        raise _failed(rel)
    projects = ProjectManager.for_project_dir(project_dir)

    def shot_of(script: dict) -> dict:
        items, id_field, *_ = get_storyboard_items(script)
        matched = find_storyboard_item(items, id_field, shot_id)
        if matched is None:
            # 分镜或所属的剧本已删除：加回的尾帧没有归属。
            raise _failed(rel)
        return matched[0]

    try:
        shot_of(projects.load_script(project_dir.name, script_file))
    except (FileNotFoundError, ValueError) as exc:
        raise _failed(rel) from exc

    def land(landing: Landing) -> Mapping[str, Any]:
        landed = rel if landing.free(rel) else _new_name(landing, rel)

        def relink(script: dict) -> None:
            shot = shot_of(script)
            if landed == rel:
                shot["end_frame_image"] = rel

        with formal_write_transaction(project_dir / "scripts" / script_file, project_dir / ProjectManager.PROJECT_FILE):
            landing.place(staged / rel, landed, original=rel)
            projects.update_script_locked(project_dir.name, script_file, relink)
        return {**obj, "path": landed}

    return Restoration(
        land=land,
        locks=lambda: projects.script_lock(project_dir.name, script_file),
        after=lambda: emit_project_change_hint(
            project_dir.name, changed_paths=[f"scripts/{script_file}", ProjectManager.PROJECT_FILE]
        ),
    )


def take_reference_audio(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """取出角色的参考音频：原位置空闲则回原位、被占用则换新名；回原位且角色没有可用参考音频时重新关联。"""
    rel, character = str(obj.get("path")), str(obj.get("character"))
    if not _is_plain_file(staged / rel):
        raise _failed(rel)
    projects = ProjectManager.for_project_dir(project_dir)

    def slot_free() -> bool:
        characters = projects.load_project(project_dir.name).get("characters")
        key = resolve_asset_key(characters, character)
        if key is None or not isinstance(characters, dict):
            # 角色已删除：加回的参考音频没有归属。
            raise _failed(rel)
        current = characters[key].get("reference_audio")
        return not (isinstance(current, str) and current and _is_plain_file(project_dir / current))

    slot_free()

    def land(landing: Landing) -> Mapping[str, Any]:
        link = slot_free()
        target = rel if landing.free(rel) else _new_name(landing, rel)

        def relink(project: dict) -> None:
            key = resolve_asset_key(project.get("characters"), character)
            if key is not None:
                ProjectManager._set_character_reference_audio(project, key, target)

        with formal_write_transaction(project_dir / ProjectManager.PROJECT_FILE):
            landing.place(staged / rel, target, original=rel)
            # 换名后的文件不在角色自己名下，不能重新关联为参考音频。
            if link and target == rel:
                projects.update_project_locked(project_dir.name, relink)
        return {**obj, "path": target}

    return Restoration(
        land=land,
        after=lambda: emit_project_change_hint(project_dir.name, changed_paths=[ProjectManager.PROJECT_FILE]),
    )


# ---------------------------------------------------------------- 内部


def _failed(identity: str) -> ConflictError:
    return ConflictError("trash_restore_failed", id=identity)


def _new_name(landing: Landing, rel: str) -> str:
    path = PurePosixPath(rel)
    for n in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}").as_posix()
        if landing.free(candidate):
            return candidate
    raise FileExistsError(rel)


def _free(project_dir: Path, rel: str) -> bool:
    target = project_dir / rel
    return not (target.exists() or target.is_symlink())


def _is_plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()
