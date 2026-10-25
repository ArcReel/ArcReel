"""恢复离开正式脚本的条目（分镜、参考视频单元）：回到原集，插在原前驱之后（``docs/adr/0107``）。

删除见 :mod:`lib.project.script_entry_cleanup`。前驱取自快照里改写前的剧本：前驱此刻还在就插在它之后，
原来排在最前就插回最前，前驱已不在就放到集末尾。

编号只分配、不复用（``docs/adr/0090``）：恢复的条目一律取本集用过的最大号之后的新编号，原编号即使空闲也不沿用，
免得仍引用原编号的剪辑片段与裁切接到恢复回来的条目上。媒体与版本历史随新编号加回，分镜图、视频与配音的产物清单
登记取自迁移后的临时项目、换到新编号下，照常按产物时效判定；条目不再属于原宫格、按普通分镜规划，字幕与呈现是
派生产物，不加回。所属的集也在项目回收站里时只能随集恢复。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import register_artifact_entries_atomically
from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_trash import require_live_episode
from lib.infra.api_errors import ConflictError
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager
from lib.project.script_entry_cleanup import (
    ENTRY_MEDIA_RESOURCE_TYPES,
    SCRIPT_ENTRY_KIND,
    entry_derived_paths,
    entry_files,
    entry_media_paths,
)
from lib.project.trash_restore import Landing, Restoration
from lib.script.script_editor import ScriptEditError, new_item_id, resolve_items

__all__ = ["SCRIPT_ENTRY_KIND", "take_script_entry"]

#: ``ArtifactKey.episode_resource_artifacts`` 里随媒体加回的前三项：分镜图、视频、配音。
_MEDIA_ARTIFACT_COUNT = 3


def take_script_entry(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """按条目编号从迁移后的临时项目取出条目、前驱、文件、版本记录与产物清单登记。"""
    entry_id, script_file, episode = str(obj.get("id")), str(obj.get("script_file")), obj.get("episode")
    try:
        staged_script = json.loads((staged / "scripts" / script_file).read_text(encoding="utf-8"))
        items, id_field, kind = resolve_items(staged_script)
    except (OSError, ValueError, ScriptEditError) as exc:
        raise _failed(entry_id) from exc
    index = next((i for i, item in enumerate(items) if isinstance(item, dict) and item.get(id_field) == entry_id), None)
    if index is None or not isinstance(episode, int):
        raise _failed(entry_id)
    entry: dict[str, Any] = items[index]
    previous = items[index - 1].get(id_field) if index > 0 and isinstance(items[index - 1], dict) else None
    files, _history = entry_files(staged, episode, entry_id)
    # 随媒体加回的产物（分镜图、视频、配音）的登记；字幕与呈现不加回，登记也不带。
    media_keys = ArtifactKey.episode_resource_artifacts(episode, entry_id)[:_MEDIA_ARTIFACT_COUNT]
    staged_claims = ProjectArtifactManifestAdapter(staged).snapshot_entries()
    claims = {key: staged_claims[key] for key in media_keys if key in staged_claims}
    projects = ProjectManager.for_project_dir(project_dir)
    versions = VersionManager(project_dir)
    # 此处按取不出对象降级（集已彻底删除）或拒绝（集在回收站里）；落地时在锁内再核对一次。
    require_live_episode(project_dir, episode, entry_id)
    landed: dict[str, str] = {}
    landed_paths: dict[str, str] = {}

    def land(landing: Landing) -> Mapping[str, Any]:
        require_live_episode(project_dir, episode, entry_id)

        def insert(script: dict) -> None:
            current, current_id_field, current_kind = resolve_items(script)
            if current_kind != kind or current_id_field != id_field:
                raise _failed(entry_id)
            taken = {item.get(id_field) for item in current if isinstance(item, dict)}

            def free(candidate: str) -> bool:
                return (
                    candidate not in taken
                    and all(
                        landing.free(rel)
                        for rel in (*entry_media_paths(candidate), *entry_derived_paths(episode, candidate))
                    )
                    and not versions.resource_records((t, candidate) for t in ENTRY_MEDIA_RESOURCE_TYPES)
                )

            floor = projects.item_id_floor(projects.load_project(project_dir.name), script, episode)
            new_id = _new_id(script, kind, id_field, free, floor)
            placed, relink = _rename(staged, episode, entry_id, new_id, files)
            landed_paths.update(relink)
            for source, rel in placed.items():
                landing.place(staged / source, rel, original=source)
            history = VersionManager(staged).resource_records((t, new_id) for t in ENTRY_MEDIA_RESOURCE_TYPES)
            versions.attach_resources(history)
            restored = _relinked(entry, relink) | {id_field: new_id}
            ids = [item.get(id_field) if isinstance(item, dict) else None for item in current]
            position = ids.index(previous) + 1 if previous in ids else (0 if previous is None else len(current))
            script[kind] = [*current[:position], restored, *current[position:]]
            landed["id"] = new_id

        script_path = project_dir / "scripts" / script_file
        with formal_write_transaction(script_path, project_dir / ProjectManager.PROJECT_FILE, versions.versions_file):
            projects.update_script_locked(project_dir.name, script_file, insert)
            if claims:
                # 登记随新编号补录：依据摘要取自删除前（迁移后）的清单，照常按产物时效判定，
                # 新编号下的输入与删除前不同的读为过期。
                renamed_keys = dict(
                    zip(media_keys, ArtifactKey.episode_resource_artifacts(episode, landed["id"]), strict=False)
                )
                register_artifact_entries_atomically(
                    project_dir,
                    {
                        renamed_keys[key]: ArtifactManifestEntry(
                            landed_paths.get(claim.artifact_path, claim.artifact_path), claim.basis_digest
                        )
                        for key, claim in claims.items()
                    },
                )
        return {**obj, "id": landed["id"]}

    return Restoration(
        land=land,
        locks=lambda: projects.script_lock(project_dir.name, script_file),
        after=lambda: emit_project_change_hint(
            project_dir.name, changed_paths=[f"scripts/{script_file}", ProjectManager.PROJECT_FILE]
        ),
    )


def _new_id(script: dict, kind: str, id_field: str, free: Callable[[str], bool], floor: int) -> str:
    """按新增条目的取号规则发一个新编号：从本集用过的最大号之后取，跳过媒体或版本历史仍占着的编号。"""
    probe = {**script, kind: list(script.get(kind) or [])}
    while not free(candidate := new_item_id(probe, floor=floor)):
        probe[kind].append({id_field: candidate})
    return candidate


def _rename(
    staged: Path, episode: int, old_id: str, new_id: str, files: list[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """返回 ``(要加回的文件：临时项目内路径 → 落点, 条目字段里要改指的路径)``。

    媒体与版本快照改用新编号，字幕与呈现不加回；条目字段里按旧编号命名的路径一律改指新编号，
    文件不在的也改，免得指向旧编号。
    """
    staged_versions = VersionManager(staged)
    # 快照是删除时的整个项目：新编号当时可能属于另一个条目，它的历史不随这条记录回来，先从临时项目摘掉。
    staged_versions.detach_resources((resource_type, new_id) for resource_type in ENTRY_MEDIA_RESOURCE_TYPES)
    for resource_type in ENTRY_MEDIA_RESOURCE_TYPES:
        # 在临时项目里改名：快照文件与版本记录一起换成新编号，随后整段加回。
        staged_versions.rename_resource(resource_type, old_id, new_id)
    renamed = dict(zip(entry_media_paths(old_id), entry_media_paths(new_id), strict=True))
    new_files, _history = entry_files(staged, episode, new_id)
    snapshots = {rel: rel for rel in new_files if rel.startswith("versions/")}
    return {rel: renamed[rel] for rel in files if rel in renamed} | snapshots, renamed


def _relinked(entry: Mapping[str, Any], relink: Mapping[str, str]) -> dict[str, Any]:
    """条目里指向旧编号文件的路径字段（产物与尾帧）改指新编号；换了编号的条目不再是原宫格的成员。"""
    restored = json.loads(json.dumps(entry))
    assets = restored.get("generated_assets")
    if relink and isinstance(assets, dict):
        # 宫格记录的成员仍是旧编号：留着宫格字段，产物规划会因找不到宫格目标而跳过这张分镜图。
        assets.update(grid_id=None, grid_cell_index=None)
    for holder in (restored, assets if isinstance(assets, dict) else {}):
        for key, value in holder.items():
            if isinstance(value, str) and value in relink:
                holder[key] = relink[value]
    return restored


def _failed(identity: str) -> ConflictError:
    return ConflictError("trash_restore_failed", id=identity)
