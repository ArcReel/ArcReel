"""
Version management API routes.

Handles version listing and restore requests.
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

from lib import PROJECT_ROOT
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from lib.version_manager import VersionManager
from server.auth import CurrentUser

router = APIRouter()

# Initialize project manager
pm = ProjectManager(PROJECT_ROOT / "projects")

_RESOURCE_FILE_PATTERNS: dict[str, tuple[str, str]] = {
    "storyboards": ("storyboards", "scene_{id}.png"),
    "videos": ("videos", "scene_{id}.mp4"),
    "characters": ("characters", "{id}.png"),
    "clues": ("clues", "{id}.png"),
}


def get_project_manager() -> ProjectManager:
    return pm


def get_version_manager(project_name: str) -> VersionManager:
    """Get the version manager for a project."""
    project_path = get_project_manager().get_project_path(project_name)
    return VersionManager(project_path)


def _resolve_resource_path(
    resource_type: str,
    resource_id: str,
    project_path: Path,
) -> tuple[Path, str]:
    """Return (current_file_absolute, relative_file_path); raise HTTPException if resource_type is invalid."""
    pattern = _RESOURCE_FILE_PATTERNS.get(resource_type)
    if pattern is None:
        raise HTTPException(status_code=400, detail=f"Unsupported resource type: {resource_type}")
    subdir, name_tpl = pattern
    name = name_tpl.format(id=resource_id)
    return project_path / subdir / name, f"{subdir}/{name}"


def _sync_storyboard_metadata(
    project_name: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
) -> None:
    scripts_dir = project_path / "scripts"
    if not scripts_dir.exists():
        return
    for script_file in scripts_dir.glob("*.json"):
        try:
            with project_change_source("webui"):
                get_project_manager().update_scene_asset(
                    project_name=project_name,
                    script_filename=script_file.name,
                    scene_id=resource_id,
                    asset_type="storyboard_image",
                    asset_path=file_path,
                )
        except KeyError:
            continue
        except Exception as exc:
            logger.warning("Failed to sync storyboard metadata: %s", exc)
            continue


def _sync_metadata(
    resource_type: str,
    project_name: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
) -> None:
    """Sync metadata after a restore to ensure references point to the canonical file path."""
    if resource_type == "characters":
        try:
            with project_change_source("webui"):
                get_project_manager().update_project_character_sheet(project_name, resource_id, file_path)
        except KeyError:
            pass  # Character entry may have been deleted from project.json; skip metadata sync
    elif resource_type == "clues":
        try:
            with project_change_source("webui"):
                get_project_manager().update_clue_sheet(project_name, resource_id, file_path)
        except KeyError:
            pass  # Clue entry may have been deleted from project.json; skip metadata sync
    elif resource_type == "storyboards":
        _sync_storyboard_metadata(project_name, resource_id, file_path, project_path)


# ==================== Version listing ====================


@router.get("/projects/{project_name}/versions/{resource_type}/{resource_id}")
async def get_versions(
    project_name: str,
    resource_type: str,
    resource_id: str,
    _user: CurrentUser,
):
    """
    Get all versions for a resource.

    Args:
        project_name: Project name
        resource_type: Resource type (storyboards, videos, characters, clues)
        resource_id: Resource ID
    """
    try:

        def _sync():
            vm = get_version_manager(project_name)
            versions_info = vm.get_versions(resource_type, resource_id)
            return {"resource_type": resource_type, "resource_id": resource_id, **versions_info}

        return await asyncio.to_thread(_sync)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Version restore ====================


@router.post("/projects/{project_name}/versions/{resource_type}/{resource_id}/restore/{version}")
async def restore_version(
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
    _user: CurrentUser,
):
    """
    Switch to the specified version.

    Copies the specified version to the current path and moves the current-version pointer to that version.

    Args:
        project_name: Project name
        resource_type: Resource type
        resource_id: Resource ID
        version: Version number to restore
    """
    try:

        def _sync():
            vm = get_version_manager(project_name)
            project_path = get_project_manager().get_project_path(project_name)
            current_file, file_path = _resolve_resource_path(resource_type, resource_id, project_path)

            result = vm.restore_version(
                resource_type=resource_type,
                resource_id=resource_id,
                version=version,
                current_file=current_file,
            )

            _sync_metadata(resource_type, project_name, resource_id, file_path, project_path)

            # Compute the fingerprint of the restored file; for videos, also delete the thumbnail (content is now stale)
            asset_fingerprints: dict[str, int] = {}
            if current_file.exists():
                asset_fingerprints[file_path] = current_file.stat().st_mtime_ns

            if resource_type == "videos":
                thumbnail_path = project_path / "thumbnails" / f"scene_{resource_id}.jpg"
                thumbnail_key = f"thumbnails/scene_{resource_id}.jpg"
                thumbnail_path.unlink(missing_ok=True)
                # fingerprint=0 signals the frontend that the file is stale (poster disappears until regenerated)
                asset_fingerprints[thumbnail_key] = 0

            return {
                "success": True,
                **result,
                "file_path": file_path,
                "asset_fingerprints": asset_fingerprints,
            }

        return await asyncio.to_thread(_sync)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))
