"""剪辑时间线进出项目回收站：删除成为一条带结构快照的删除记录，恢复按落地规则加回。

一条记录收时间线文档与它在 ``renders/`` 下的成片、剪映草稿（含渲染记录）。恢复时原 ID 空闲（没有同 ID 的文档，
渲染目录也不在）就沿用，渲染产物的清单登记取自迁移后的临时项目，照常按产物时效判定；ID 被占用时发新 ID，
渲染文件随新 ID 加回、不带登记。显示名在集内被占用时加「 (n)」后缀。
"""

from __future__ import annotations

import contextlib
import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKind, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import forget_timeline_render_artifacts, register_artifact_entries_atomically
from lib.artifacts.rendered_artifact import timeline_renders_dir
from lib.edit_timeline.model import TIMELINE_NAME_MAX_LENGTH, EditTimelineDocument
from lib.edit_timeline.store import (
    EDIT_TIMELINES_DIRNAME,
    EditTimelineStore,
    document_relpath,
    read_timeline_document,
)
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from lib.project.trash_restore import Landing, Restoration

__all__ = ["EDIT_TIMELINE_KIND", "delete_edit_timeline", "take_edit_timeline"]

EDIT_TIMELINE_KIND = "edit_timeline"
_RENDER_KINDS = frozenset({ArtifactKind.EPISODE_FINAL_CUT, ArtifactKind.EPISODE_JIANYING_DRAFT})


def delete_edit_timeline(project_dir: Path, document: EditTimelineDocument) -> project_trash.TrashRecord:
    """把一条剪辑时间线连同它的渲染产物移进项目回收站；调用方依次持有该集的锁与项目锁。"""
    renders = timeline_renders_dir(document.episode, document.id)
    files = [document_relpath(document.episode, document.id), *_files_under(project_dir, renders)]
    record = project_trash.record_deletion(
        project_dir,
        {"kind": EDIT_TIMELINE_KIND, "id": document.id, "episode": document.episode, "name": document.name},
        files,
        snapshot=True,
    )
    # 时间线 ID 不复用，成片与剪映草稿的身份挂在它上面：时间线没了，登记就没有归属。
    forget_timeline_render_artifacts(project_dir, document.id)
    _remove_empty_dirs(project_dir / renders)
    return record


def take_edit_timeline(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """按时间线 ID 从迁移后的临时项目取出文档、渲染文件与它们的清单登记。"""
    timeline_id = str(obj.get("id"))
    document = next(
        (
            found
            for path in sorted(staged.glob(f"{EDIT_TIMELINES_DIRNAME}/episode_*/{timeline_id}.json"))
            if (found := read_timeline_document(staged, _episode_of(path), timeline_id)) is not None
        ),
        None,
    )
    if document is None:
        raise ConflictError("trash_restore_failed", id=timeline_id)
    staged_renders = timeline_renders_dir(document.episode, document.id)
    render_files = _files_under(staged, staged_renders)
    claims = {
        key: entry
        for key, entry in ProjectArtifactManifestAdapter(staged).snapshot_entries().items()
        if key.kind in _RENDER_KINDS and key.components[1] == document.id
    }
    store = EditTimelineStore(ProjectManager.for_project_dir(project_dir), project_dir.name)

    def land(landing: Landing) -> Mapping[str, Any]:
        taken_ids = {existing.id for existing in store.list_documents()}
        landed_id = document.id
        while (
            landed_id in taken_ids
            or not landing.free(document_relpath(document.episode, landed_id))
            or not landing.free(timeline_renders_dir(document.episode, landed_id))
        ):
            landed_id = f"tl-{secrets.token_hex(4)}"
        names = {existing.name.casefold() for existing in store.list_documents(document.episode)}
        landed = document.model_copy(update={"id": landed_id, "name": _free_name(document.name, names)})
        landing.write_json(
            document_relpath(landed.episode, landed.id),
            landed.model_dump(mode="json"),
            original=document_relpath(document.episode, document.id),
        )
        renders = timeline_renders_dir(landed.episode, landed.id)
        for rel in render_files:
            landing.place(staged / rel, renders + rel[len(staged_renders) :], original=rel)
        if landed.id == document.id and claims:
            # 补录：登记取自删除前（迁移后）的清单，依据摘要不变，输入此后变过的照常读为过期。
            register_artifact_entries_atomically(
                project_dir,
                {key: ArtifactManifestEntry(entry.artifact_path, entry.basis_digest) for key, entry in claims.items()},
            )
        return {**obj, "id": landed.id, "name": landed.name}

    return Restoration(
        land=land,
        locks=lambda: store.locked_episode(document.episode),
        after=lambda: store.changed(document.episode),
    )


def _episode_of(path: Path) -> int:
    number = path.parent.name.removeprefix("episode_")
    return int(number) if number.isdigit() else 0


def _free_name(name: str, taken: set[str]) -> str:
    candidate, n = name, 0
    while candidate.casefold() in taken:
        n += 1
        suffix = f" ({n})"
        candidate = name[: TIMELINE_NAME_MAX_LENGTH - len(suffix)].rstrip() + suffix
    return candidate


def _files_under(root: Path, rel_dir: str) -> list[str]:
    """``rel_dir`` 下的普通文件（项目内相对 POSIX 路径），不跟随符号链接。"""
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root / rel_dir):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _remove_empty_dirs(directory: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(directory, topdown=False):
        # 还有没移走的条目（如符号链接）时目录留着
        with contextlib.suppress(OSError):
            # hard-delete: derived -- 文件都已移进项目回收站，只剩空目录
            os.rmdir(dirpath)
