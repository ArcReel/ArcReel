"""Version-management API routes.

Handle version queries and restoration."""

import asyncio
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

from lib.artifacts.artifact_activation import (
    forget_current_resource_artifact,
    forget_unbound_grid_artifacts,
    forget_unbound_storyboard_artifacts,
    register_current_resource_artifact,
)
from lib.artifacts.artifact_version_provenance import parse_image_version_basis
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.infra.api_errors import BadRequestError
from lib.infra.async_thread import run_sync_transaction
from lib.material.work_files import local_work_files
from lib.project.asset_derivatives import DerivativeSheetTarget, derivative_artifact_id, split_derivative_artifact_id
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.script.grid.grid_access import ensure_grid_writable
from server.services.currency.artifact_version_restore import (
    is_typed_media_restore_resource,
    is_typed_media_version_restorable,
)
from server.services.currency.typed_media_restore import resolve_media_file, restore_typed_media_resource
from server.services.presentation.presentation_read_model import is_presentation_version_available
from server.services.tasks.derivative_sheet_tasks import point_derivative_at_sheet

router = APIRouter()

# Restorable resource types are API policy; lib.project.resource_paths defines paths. Allow only types
# with post-restoration metadata synchronization. Grid restoration replaces composites and resets split
# state only, without splitting/touching storyboards; explicit grid splitting assigns cells.
_RESTORABLE_RESOURCE_TYPES = frozenset(
    {
        "storyboards",
        "videos",
        "audio",
        "characters",
        "scenes",
        "props",
        "products",
        "reference_videos",
        "grids",
        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
    }
)


def get_version_manager(project_name: str) -> VersionManager:
    """Get the project version manager."""
    project_path = get_project_manager().get_project_path(project_name)
    return VersionManager(project_path)


def _resolve_resource_path(
    resource_type: str,
    resource_id: str,
    project_path: Path,
) -> tuple[Path, str]:
    """Return (current_file_absolute, relative_file_path); raise domain exceptions for unrestorable types or out-of-bounds IDs."""
    if resource_type not in _RESTORABLE_RESOURCE_TYPES:
        raise BadRequestError("unsupported_resource_type", resource_type=resource_type)
    return resolve_media_file(resource_type, resource_id, project_path)


def _sync_grid_record(
    project_path: Path,
    resource_id: str,
    *,
    on_commit: Callable[[], None] | None = None,
    on_miss: Callable[[], None] | None = None,
) -> None:
    """Reset grid records after restoring composites: historical content replaces current, so old split results no longer match.

    Change only grid split_at/failure state, without synchronizing scripts/storyboards; splitting requires explicit endpoint execution.
    Retain frame_chain unchanged. Skip missing/corrupt records as best effort because composite restoration already succeeded.

    Restoration has no in-flight gate, like storyboard restoration. ``GridGeneration.mark_composite_replaced`` retains
    in-flight state during generation rather than falsely marking the record idle."""
    from lib.script.grid.grid_manager import GridManager

    manager = GridManager(project_path)
    manager.update_formal(
        resource_id,
        lambda grid: grid.mark_composite_replaced(),
        on_commit=on_commit,
        on_miss=on_miss,
        ignore_invalid=True,
    )


# resource_type (plural URL segment) → asset_type (singular ASSET_SPECS key).
_RESOURCE_TO_ASSET_TYPE: dict[str, str] = {
    "characters": "character",
    "scenes": "scene",
    "props": "prop",
    "products": "product",
}
# Asset/derivative/storyboard images restored to manual-upload records register basis from uploaded bytes.
_UPLOAD_CLAIMED_RESOURCE_TYPES = frozenset(
    {*_RESOURCE_TO_ASSET_TYPE, CHARACTER_DERIVATIVE_RESOURCE_TYPE, "storyboards"}
)


def _commit_non_typed_restore_claim(
    *,
    resource_type: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
    record: Mapping[str, Any] | None,
    owner_present: bool,
) -> None:
    """Apply the claim half of a non-typed restore inside its metadata locks."""

    if not owner_present:
        if resource_type == "storyboards":
            forget_unbound_storyboard_artifacts(project_path, resource_id)
        elif resource_type == "grids":
            forget_unbound_grid_artifacts(project_path, resource_id)
        else:
            forget_current_resource_artifact(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        return

    if resource_type in _UPLOAD_CLAIMED_RESOURCE_TYPES and (record or {}).get("source") == MANUAL_UPLOAD_VERSION_SOURCE:
        # For restored finished asset/derivative images, uploaded/library-applied, or uploaded storyboards,
        # the upload record is selected; project basis from its bytes, forgetting basis if projection fails.
        register_current_resource_artifact(
            project_path,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        return

    try:
        basis = parse_image_version_basis(resource_type, resource_id, record or {})
    except (TypeError, ValueError):
        basis = None
    if basis is None:
        forget_current_resource_artifact(
            project_path,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        return
    register_current_resource_artifact(
        project_path,
        resource_type=resource_type,
        resource_id=resource_id,
        artifact_path=file_path,
        basis=basis,
    )


def _restore_non_typed_version(
    *,
    versions: VersionManager,
    resource_type: str,
    project_name: str,
    resource_id: str,
    version: int,
    current_file: Path,
    file_path: str,
    project_path: Path,
) -> dict[str, Any]:
    """Restore metadata → version bytes/pointer → claim in canonical lock order.

    Asset rename, grid split, formal generation, and typed restore all acquire
    script/file/project locks before the versions lock. Non-typed restore enters
    the same metadata transaction first so none of those paths can form an ABBA
    cycle while their nested rollback scopes remain intact.
    """

    restored: list[dict[str, Any]] = []

    def _restore(*, owner_present: bool) -> None:
        def _commit_claim(record: dict[str, Any]) -> None:
            _commit_non_typed_restore_claim(
                resource_type=resource_type,
                resource_id=resource_id,
                file_path=file_path,
                project_path=project_path,
                record=record,
                owner_present=owner_present,
            )

        restored.append(
            versions.restore_version(
                resource_type=resource_type,
                resource_id=resource_id,
                version=version,
                current_file=current_file,
                on_restore=_commit_claim,
            )
        )

    if resource_type == "storyboards":
        scripts_dir = project_path / "scripts"
        script_names = [path.name for path in sorted(scripts_dir.glob("*.json"))] if scripts_dir.exists() else []
        with project_change_source("webui"):
            get_project_manager().update_scene_asset_across_scripts(
                project_name,
                script_names,
                resource_id,
                "storyboard_image",
                file_path,
                on_commit=lambda: _restore(owner_present=True),
                on_miss=lambda: _restore(owner_present=False),
            )
    elif resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE:
        owner_key, derivative_key = split_derivative_artifact_id(resource_id)
        try:
            with project_change_source("webui"):
                point_derivative_at_sheet(
                    project_name=project_name,
                    target=DerivativeSheetTarget(owner_key=owner_key, derivative_key=derivative_key),
                    on_commit=lambda _project_file: _restore(owner_present=True),
                    project_manager=get_project_manager(),
                )
        except KeyError:
            with project_metadata_lock(project_path):
                _restore(owner_present=False)
    elif (asset_type := _RESOURCE_TO_ASSET_TYPE.get(resource_type)) is not None:
        try:
            with project_change_source("webui"):
                get_project_manager()._update_asset_sheet(
                    asset_type,
                    project_name,
                    resource_id,
                    file_path,
                    on_commit=lambda _project_file: _restore(owner_present=True),
                )
        except KeyError:
            with project_metadata_lock(project_path):
                _restore(owner_present=False)
    elif resource_type == "grids":
        _sync_grid_record(
            project_path,
            resource_id,
            on_commit=lambda: _restore(owner_present=True),
            on_miss=lambda: _restore(owner_present=False),
        )
    else:
        with project_metadata_lock(project_path):
            _restore(owner_present=True)

    if len(restored) != 1:
        raise RuntimeError("non-typed restore metadata transaction skipped version selection")
    return restored[0]


# ==================== Version queries ====================


@router.get("/projects/{project_name}/versions/{resource_type}/{resource_id}")
async def get_versions(
    project_name: str,
    resource_type: str,
    resource_id: str,
):
    """List all versions of a resource.

    Args:
        project_name: Project name.
        resource_type: Resource type (storyboards, videos, characters, scenes, props).
        resource_id: Resource ID."""
    return await read_resource_versions(project_name, resource_type, resource_id)


@router.get("/projects/{project_name}/versions/character-derivative/{owner_name}/{derivative_name}")
async def get_derivative_versions(
    project_name: str,
    owner_name: str,
    derivative_name: str,
):
    """List versions of a character-derivative image.

    Derivative IDs ``本体名/衍生名`` do not fit generic single-segment parameters, so use a separate two-segment path."""
    return await read_resource_versions(
        project_name,
        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        derivative_artifact_id(owner_name, derivative_name),
    )


@router.post("/projects/{project_name}/versions/character-derivative/{owner_name}/{derivative_name}/restore/{version}")
async def restore_derivative_version(
    project_name: str,
    owner_name: str,
    derivative_name: str,
    version: int,
):
    """Restore a character-derivative image to a specified historical version."""
    return await restore_resource_version(
        project_name,
        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        derivative_artifact_id(owner_name, derivative_name),
        version,
    )


async def read_resource_versions(project_name: str, resource_type: str, resource_id: str) -> dict[str, Any]:
    """Version-list reading shared by routes with hierarchical IDs, such as derivatives, that cannot use single-segment parameters."""
    try:

        def _sync():
            vm = get_version_manager(project_name)
            versions_info = vm.get_versions(resource_type, resource_id)
            for record in versions_info.get("versions", []):
                if isinstance(record, dict):
                    record["restorable"] = is_typed_media_version_restorable(resource_type, record)
                    record["presentation_available"] = is_presentation_version_available(resource_type, record)
            return {"resource_type": resource_type, "resource_id": resource_id, **versions_info}

        return await asyncio.to_thread(_sync)

    except ValueError as e:
        # Invalid project names, corrupt versions.json and other bad requests may put paths in str(e); log only.
        logger.warning("版本查询请求非法: %s", e)
        raise BadRequestError("request_invalid") from e


# ==================== Version restoration ====================


@router.post("/projects/{project_name}/versions/{resource_type}/{resource_id}/restore/{version}")
async def restore_version(
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
):
    """Switch to a specified version.

    Copy the selected version to the current path and select it as current.

    Args:
        project_name: Project name.
        resource_type: Resource type.
        resource_id: Resource ID.
        version: Version number to restore."""
    return await restore_resource_version(project_name, resource_type, resource_id, version)


async def _locate_version_work_file(
    project_name: str, resource_type: str, resource_id: str, version: int
) -> tuple[Path, str | None]:
    """Locate the snapshot to restore; callers register use throughout retrieval and restoration."""

    def _locate() -> tuple[Path, str | None]:
        project_path = get_project_manager().get_project_path(project_name)
        records = get_version_manager(project_name).get_versions(resource_type, resource_id)["versions"]
        record = next((item for item in records if item.get("version") == version), None)
        file = record.get("file") if isinstance(record, dict) else None
        return project_path, file if isinstance(file, str) else None

    return await asyncio.to_thread(_locate)


async def restore_resource_version(
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
) -> dict[str, Any]:
    """Restoration shared by routes with hierarchical IDs, such as derivatives, that cannot use single-segment parameters."""
    try:
        if is_typed_media_restore_resource(resource_type):
            with project_change_source("webui"):
                return await restore_typed_media_resource(
                    project_manager=get_project_manager(),
                    versions=get_version_manager(project_name),
                    project_name=project_name,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    version=version,
                )

        project_path, file = await _locate_version_work_file(project_name, resource_type, resource_id, version)

        def _sync():
            # Restoration also writes composites, restoring historical content and resetting grid records,
            # so share regeneration/split/upload admission; omission would let blocked projects bypass via restoration.
            if resource_type == "grids":
                ensure_grid_writable(get_project_manager().load_project(project_name))

            vm = get_version_manager(project_name)
            project_path = get_project_manager().get_project_path(project_name)
            current_file, file_path = _resolve_resource_path(resource_type, resource_id, project_path)

            result = _restore_non_typed_version(
                versions=vm,
                resource_type=resource_type,
                project_name=project_name,
                resource_id=resource_id,
                version=version,
                current_file=current_file,
                file_path=file_path,
                project_path=project_path,
            )

            # Compute the restored file fingerprint.
            asset_fingerprints: dict[str, int] = {}
            if current_file.exists():
                asset_fingerprints[file_path] = current_file.stat().st_mtime_ns

            return {
                "success": True,
                **result,
                "file_path": file_path,
                "asset_fingerprints": asset_fingerprints,
            }

        async with local_work_files(project_path, [file] if file is not None else []):
            return await run_sync_transaction(_sync)

    except ValueError as e:
        # Invalid project names/out-of-bounds resource_id may put paths in str(e); log only.
        logger.warning("版本还原请求非法: %s", e)
        raise BadRequestError("request_invalid") from e
