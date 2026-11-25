"""Canonical result-payload builder for formal and historical video tasks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from lib.project.resource_paths import resource_relative_path


def build_video_task_result(
    *,
    resource_type: str,
    resource_id: str,
    version: int,
    file_path: str | None = None,
    created_at: str | None,
    video_uri: str | None,
    selected_current: bool = True,
    warnings: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build the single-source task result dict shared by finalize, history, and recovery paths."""

    resolved_path = (
        resource_relative_path(resource_type, resource_id) if selected_current and file_path is None else file_path
    )
    result: dict[str, Any] = {
        "version": version,
        "file_path": resolved_path,
        "created_at": created_at,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "video_uri": video_uri,
    }
    if not selected_current:
        result["selected_current"] = False
    if resource_type == "reference_videos" or warnings:
        result["warnings"] = list(warnings)
    return result
