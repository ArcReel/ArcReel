"""规划草稿进出项目回收站：未确认的正式脚本规划与待修复、可编辑草稿的删除成为一条带结构快照的删除记录。

恢复时所属的集必须还在。原路径空闲就回原位，脚本规划的产物登记取自删除前（迁移后）的清单一并补录；原路径已有
文件时换成「<原名> (n)」加回，作为未引用文件、不带登记。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import register_artifact_entries_atomically
from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.project_activity import record_project_activity
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager, find_episode
from lib.project.trash_restore import Landing, Restoration
from lib.script.draft_quarantine import quarantine_path

__all__ = ["PLANNING_DRAFT_KIND", "discard_quarantine", "take_planning_draft", "trash_planning_draft"]

PLANNING_DRAFT_KIND = "planning_draft"


def trash_planning_draft(project_dir: Path, episode: int, path: Path) -> project_trash.TrashRecord | None:
    """把第 ``episode`` 集的规划草稿 ``path`` 移进一条删除记录；不是普通文件时返回 None。调用方持有项目锁。"""
    if path.is_symlink() or not path.is_file():
        return None
    rel = path.relative_to(project_dir).as_posix()
    return project_trash.record_deletion(
        project_dir, {"kind": PLANNING_DRAFT_KIND, "path": rel, "episode": episode}, [rel], snapshot=True
    )


def discard_quarantine(project_path: Path, episode: int, kind: str) -> bool:
    """丢弃草稿（创作者或 Agent 放弃它）：移进项目回收站，返回是否真的有草稿；有时推进项目活动。

    调用方持有草稿锁、不持项目锁。
    """
    with project_metadata_lock(project_path):
        record = trash_planning_draft(project_path, episode, quarantine_path(project_path, episode, kind))
    if record is None:
        return False
    record_project_activity(project_path)
    return True


def take_planning_draft(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """按原路径从迁移后的临时项目取出草稿与它的脚本规划登记。"""
    rel, episode = str(obj.get("path")), int(obj.get("episode") or 0)
    staged_file = staged / rel
    if staged_file.is_symlink() or not staged_file.is_file():
        raise ConflictError("trash_restore_failed", id=rel)
    key = ArtifactKey.episode_script_plan(episode)
    claim = ProjectArtifactManifestAdapter(staged).snapshot_entries().get(key)
    projects = ProjectManager.for_project_dir(project_dir)
    landed: list[str] = []

    def require_episode() -> None:
        if find_episode(projects.load_project(project_dir.name), episode) is None:
            # 所属的集已删除：加回的草稿没有归属。
            raise ConflictError("trash_restore_failed", id=rel)

    # 此处抛错按取不出对象降级；落地时在锁内再核对一次，期间被删的集让本次恢复失败、记录保留。
    require_episode()

    def land(landing: Landing) -> Mapping[str, Any]:
        require_episode()
        target = rel
        path = PurePosixPath(rel)
        n = 0
        while not landing.free(target):
            n += 1
            target = path.with_name(f"{path.stem} ({n}){path.suffix}").as_posix()
        landing.place(staged_file, target, original=rel)
        landed.append(target)
        if target == rel and claim is not None and claim.artifact_path == rel:
            # 补录：依据摘要不变，输入此后变过的照常读为过期。
            register_artifact_entries_atomically(
                project_dir, {key: ArtifactManifestEntry(claim.artifact_path, claim.basis_digest)}
            )
        return {**obj, "path": target}

    return Restoration(
        land=land,
        locks=lambda: projects.file_lock(project_dir / rel),
        after=lambda: emit_project_change_hint(project_dir.name, changed_paths=landed),
    )
