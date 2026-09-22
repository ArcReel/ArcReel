"""Failure compensation for selected formal image artifacts."""

from __future__ import annotations

from pathlib import Path

from lib.artifacts.version_manager import VersionManager


def reject_failed_image_selection(
    *,
    versions: VersionManager,
    resource_type: str,
    resource_id: str,
    version: int,
    current_file: Path,
) -> None:
    """Reject a generated selection whose metadata/Manifest finalization failed."""

    restored = versions.reject_current_version(
        resource_type,
        resource_id,
        rejected_version=version,
        current_file=current_file,
    )
    if not restored and versions.get_current_version(resource_type, resource_id) == version:
        raise RuntimeError("failed image artifact remains selected after compensation")


__all__ = ["reject_failed_image_selection"]
