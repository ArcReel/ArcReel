"""Source files entering/leaving project trash: deletion/replacement uploads create deletion records containing sources/raw originals plus structure snapshots.

Raw originals under ``source/raw/`` match source filename stems. Restoration always adds sources outside whole-source manifests,
letting creators reconnect through Episodes. Reuse paths only if absent on disk/manifests and not episode filenames;
otherwise rename both source/original to <stem> (n), preserving associations.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any

from lib.episode.episode_ledger import is_derived_episode_name
from lib.episode.episode_sources import whole_source_files
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager
from lib.project.trash_restore import Landing, Restoration

__all__ = ["SOURCE_FILE_KIND", "take_source_file", "trash_source_file"]

SOURCE_FILE_KIND = "source_file"
_RAW_DIR = "source/raw"


def trash_source_file(project_dir: Path, rel: str, undo: ExitStack) -> project_trash.TrashRecord | None:
    """Move source ``rel`` (``source/<文件名>``) plus raw originals into deletion records; None if neither exists.

    Callers hold project locks; ``undo`` is shared transaction rollback, retracting records/restoring files on failure.
    """
    files = [rel] if _plain_file(project_dir / rel) else []
    raw = _raw_files(project_dir, PurePosixPath(rel).stem)
    if not files and not raw:
        return None
    record = project_trash.record_deletion(
        project_dir, {"kind": SOURCE_FILE_KIND, "path": rel, "raw": raw}, [*files, *raw], snapshot=True
    )
    undo.callback(project_trash.withdraw, project_dir, record.id)
    return record


def take_source_file(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Take sources/raw originals from migrated temporary projects, adding outside whole-source manifests."""
    rel = str(obj.get("path"))
    raw = [str(item) for item in obj.get("raw") or [] if _plain_file(staged / str(item))]
    text = [rel] if _plain_file(staged / rel) else []
    if not text and not raw:
        raise ConflictError("trash_restore_failed", id=rel)
    stem, suffix = PurePosixPath(rel).stem, PurePosixPath(rel).suffix
    projects = ProjectManager.for_project_dir(project_dir)
    landed: list[str] = []

    def land(landing: Landing) -> Mapping[str, Any]:
        registered = set(whole_source_files(projects.load_project(project_dir.name)))
        for n in range(1000):
            name = stem if n == 0 else f"{stem} ({n})"
            text_rel = f"source/{name}{suffix}"
            raw_rels = [f"{_RAW_DIR}/{name}{PurePosixPath(item).suffix}" for item in raw]
            if (
                text_rel not in registered
                and not is_derived_episode_name(f"{name}{suffix}")
                and all(landing.free(target) for target in [text_rel, *raw_rels])
            ):
                break
        else:
            raise FileExistsError(rel)
        targets: list[str] = [*([text_rel] if text else []), *raw_rels]
        for original, target in zip([*text, *raw], targets, strict=True):
            landing.place(staged / original, target, original=original)
            landed.append(target)
        return {**obj, "path": text_rel, "raw": raw_rels}

    return Restoration(land=land, after=lambda: emit_project_change_hint(project_dir.name, changed_paths=landed))


def _raw_files(project_dir: Path, stem: str) -> list[str]:
    raw_dir = project_dir / _RAW_DIR
    if raw_dir.is_symlink() or not raw_dir.is_dir():
        return []
    return sorted(f"{_RAW_DIR}/{path.name}" for path in raw_dir.iterdir() if path.stem == stem and _plain_file(path))


def _plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()
