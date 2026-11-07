"""Targets deleted before generation completion: skip original-path/version writes, creating project-trash deletion records instead.

Creators/Agents may delete assets/storyboards/video units/grids during execution.
:class:`~lib.generation.media_generator.MediaGenerator` rechecks through
:func:`trash_result_if_target_deleted` after settlement/before formal commit. Missing targets move staged artifacts into trash
under original paths, raising :class:`GenerationTargetDeletedError` to skip commits. Task entries catch it and complete normally with
:attr:`~GenerationTargetDeletedError.result`, preserving costs without resurrecting deleted content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.project import project_trash
from lib.project.reference_index import ReferenceIndex
from lib.project.resource_paths import resource_relative_path

__all__ = [
    "GENERATION_RESULT_KIND",
    "TARGET_DELETED_NOTE",
    "GenerationTargetDeletedError",
    "trash_result_if_target_deleted",
]

#: Deletion-record object kind: generated results.
GENERATION_RESULT_KIND = "generation_result"
#: Target-deleted-at-completion labels let frontend adjust initiator wording.
TARGET_DELETED_NOTE = "target_deleted_during_generation"


class GenerationTargetDeletedError(Exception):
    """Results entered project trash, not failure: tasks complete normally with :attr:`result`."""

    def __init__(self, record: project_trash.TrashRecord, *, resource_type: str, resource_id: str) -> None:
        super().__init__(f"generation target deleted: {resource_type}/{resource_id}")
        self.record = record
        self.result: dict[str, Any] = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "file_path": None,
            "target_deleted": True,
            "trash_record_id": record.id,
        }


def trash_result_if_target_deleted(
    project_dir: Path,
    *,
    resource_type: str,
    resource_id: str,
    staged_file: Path,
    target: tuple[str, str] | None = None,
) -> None:
    """When ``target``, defaulting to artifact resources, no longer exists, record ``staged_file`` in trash and raise.

    Record original intended artifact paths, restored through trash rules.
    """
    target_type, target_id = target or (resource_type, resource_id)
    # shortcut: Recheck/commit acquire separate locks because project locks are nonreentrant and commits take script then project locks.
    # Deletion between them follows existing missing-target commit handling; strict atomicity requires moving rechecks inside commit locks.
    with project_metadata_lock(project_dir):
        if ReferenceIndex.build(project_dir).contains_resource(target_type, target_id):
            return
        rel = resource_relative_path(resource_type, resource_id)
        record = project_trash.record_deletion(
            project_dir,
            {
                "kind": GENERATION_RESULT_KIND,
                "path": rel,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "note": TARGET_DELETED_NOTE,
            },
            [rel],
            sources={rel: staged_file},
        )
    raise GenerationTargetDeletedError(record, resource_type=resource_type, resource_id=resource_id)
