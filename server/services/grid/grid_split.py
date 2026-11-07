"""Grid-splitting service: split current composites into storyboard cells.

Splitting alone overwrites storyboard cells, independently of composite generation, manual uploads or version restoration.
Composite changes refresh only composites; assigning cells requires explicit service execution. Shared by HTTP routes and Agent tools."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, register_artifact_entries_atomically
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.artifacts.version_manager import StagedVersionCommit, VersionManager
from lib.artifacts.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_grid_member_storyboard_visual_basis,
    build_stale_grid_member_storyboard_visual_basis,
    project_basis_records_asset_names,
    project_basis_style_description,
    snapshot_visual_references,
    visual_file_digest,
    visual_references_match_snapshot,
)
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.path_safety import safe_join
from lib.material.formal_objects import formal_commit
from lib.material.work_files import local_work_files
from lib.project.project_manager import get_project_manager
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.models import GridGeneration

logger = logging.getLogger(__name__)


def _register_split_entries_atomically(
    project_path: Path,
    *,
    entries: Mapping[ArtifactKey, ArtifactManifestEntry | None],
    expected_entries: Mapping[ArtifactKey, ArtifactManifestEntry | None],
) -> None:
    """Registration boundary for all cells selected by one split."""

    register_artifact_entries_atomically(project_path, entries, expected_entries=expected_entries)


class GridImageNotReadyError(Exception):
    """The grid has no composite yet, neither completed generation nor upload, and cannot be split."""


@dataclass
class GridSplitResult:
    updated_scene_ids: list[str]
    missing_scene_ids: list[str]
    asset_fingerprints: dict[str, int]


async def apply_grid_split(
    project_name: str,
    grid: GridGeneration,
    *,
    register_entries: Callable[..., None] | None = None,
) -> GridSplitResult:
    """Split the current ``grid`` composite and overwrite storyboard cells.

    - Register old files as versions before overwriting each cell, then register new versions with source="grid_split".
    - Skip and warn for frame_chain scene IDs no longer in the script.
    - Write ``grid.split_at`` and broadcast project changes with per-cell fingerprints for frontend cache busting.
    - Retrieve composites and original-generation asset references when they are remote versions with cleared caches;
      record reference basis in split storyboard images as usual. Save split images to the global location for new content versions."""
    grid_image_path = grid.grid_image_path
    if not grid_image_path:
        raise GridImageNotReadyError(f"grid {grid.id} has no grid image to split")
    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    reads = [grid_image_path, *(ref.path for ref in grid.reference_images or [])]
    async with local_work_files(project_path, reads), formal_commit(project_name):
        return await _apply_grid_split(project_name, grid, grid_image_path, register_entries=register_entries)


async def _apply_grid_split(
    project_name: str,
    grid: GridGeneration,
    grid_image_path: str,
    *,
    register_entries: Callable[..., None] | None,
) -> GridSplitResult:
    from PIL import Image

    from lib.script.grid.splitter import split_grid_image
    from server.services.tasks.formal_image_commit import get_aspect_ratio
    from server.services.tasks.generation_tasks import emit_generation_success_batch

    pm = get_project_manager()

    def _prepare() -> tuple[Path, GridManager, Path, VersionManager]:
        project_path = pm.get_project_path(project_name)
        grid_manager = GridManager(project_path)
        grid_image_file = grid_manager.image_path(grid.id)
        if not grid_image_file.exists():
            raise GridImageNotReadyError(f"grid {grid.id} has no grid image to split")
        return project_path, grid_manager, grid_image_file, VersionManager(project_path)

    project_path, grid_manager, grid_image_file, versions = await asyncio.to_thread(_prepare)
    script_file = grid.script_file

    def _registered_grid_source() -> tuple[ArtifactKey, ArtifactManifestEntry, ArtifactStatus]:
        """Composite registration entry and currency; admission requires artifact-manifest registration, not merely an image on disk.

        Read once in preflight and again inside the final transaction; both must obtain the same stable entry to derive split-artifact
        basis. Reread entry after retrieval to exclude entries changed between retrieval and comparison."""

        key = ArtifactKey.episode_grid(grid.episode, grid.id)
        adapter = ProjectArtifactManifestAdapter(project_path)
        entry = adapter.get_entry(key)
        comparison = ArtifactCurrencyResolver(project_path).compare(key, artifact_path=grid_image_path)
        if entry is None or not comparison.usable or adapter.get_entry(key) != entry:
            raise GridImageNotReadyError(f"grid {grid.id} has no registered grid image to split")
        return key, entry, comparison.status

    def _split_and_assign() -> tuple[list[str], list[str]]:
        from lib.script.script_editor import resolve_items

        source_status: ArtifactStatus | None = None
        source_key: ArtifactKey | None = None
        source_entry: ArtifactManifestEntry | None = None
        with pm.locked_project_script_snapshot(project_name, script_file) as (frozen_project, script):
            source_key, source_entry, source_status = _registered_grid_source()
            project_snapshot = frozen_project

        # Use the ratio frozen in the record: center-cropping historical composites with a changed project aspect_ratio
        # would remove most of each cell, such as landscape cut as portrait. Legacy records without it use current project settings.
        video_aspect_ratio = grid.video_aspect_ratio or get_aspect_ratio(project_snapshot, "videos")

        def _snapshot_and_split() -> tuple[Path, list[Any]]:
            fd, snapshot_name = tempfile.mkstemp(
                prefix=f".{grid.id}.",
                suffix=".split-source.png",
                dir=grid_image_file.parent,
            )
            snapshot = Path(snapshot_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(grid_image_file.read_bytes())
                # Image.open reads lazily and retains handles. Copy complete composite bytes first, then split and build basis from
                # that snapshot; later upload/restoration may replace canonical PNG without mixing old/new evidence.
                with Image.open(snapshot) as src:
                    src.load()
                    return snapshot, split_grid_image(src, grid.rows, grid.cols, video_aspect_ratio)
            except BaseException:
                snapshot.unlink(missing_ok=True)  # hard-delete: temp -- composite snapshot used for splitting
                raise

        composite_snapshot, cells = _snapshot_and_split()
        staged_commits: list[StagedVersionCommit] = []
        staged_paths: list[Path] = []
        version_metadata_by_resource: dict[str, dict[str, Any]] = {}
        try:
            composite_digest = visual_file_digest(composite_snapshot)
            storyboards_dir = project_path / "storyboards"
            storyboards_dir.mkdir(parents=True, exist_ok=True)

            # batch_update_scene_assets rolls back the entire batch loudly on any missing scene_id. To avoid
            # orphan PNGs after cell.save() followed by KeyError rollback, load the current script first
            # for valid IDs. Skip cell PNG saving for frame_chain shots removed after grid planning by Agent
            # split/remove operations, collecting them into the missing list and warning instead.
            items, id_field, _kind = resolve_items(script)
            valid_ids = {str(item.get(id_field)) for item in items if isinstance(item, dict)}

            asset_updates: list[tuple[str, str, Any]] = []
            updated_ids: list[str] = []
            missing_ids: list[str] = []
            cell_assignments: list[tuple[int, str, str]] = []

            # Cells stay invisible until the script, complete version batch, grid
            # record, and complete Manifest claim set can all commit.
            for cell, frame in zip(cells, grid.frame_chain, strict=True):
                if frame.frame_type == "placeholder":
                    continue
                if frame.frame_type not in ("first", "transition"):
                    continue
                if not frame.next_scene_id:
                    continue

                resource_id = str(frame.next_scene_id)
                if resource_id not in valid_ids:
                    missing_ids.append(resource_id)
                    continue

                cell_rel = f"storyboards/scene_{resource_id}.png"
                cell_path = storyboards_dir / f"scene_{resource_id}.png"
                fd, staged_name = tempfile.mkstemp(
                    prefix=f".{cell_path.stem}.",
                    suffix=f".grid-split{cell_path.suffix}",
                    dir=storyboards_dir,
                )
                staged_path = Path(staged_name)
                staged_paths.append(staged_path)
                os.close(fd)
                staged_path.unlink()  # hard-delete: temp -- mkstemp placeholder
                cell.save(staged_path, format="PNG")
                version_metadata = {"source": "grid_split", "grid_id": grid.id}
                version_metadata_by_resource[resource_id] = version_metadata
                staged_commits.append(
                    StagedVersionCommit(
                        resource_type="storyboards",
                        resource_id=resource_id,
                        prompt="",
                        staged_file=staged_path,
                        current_file=cell_path,
                        metadata=version_metadata,
                    )
                )
                cell_assignments.append((frame.index, resource_id, cell_rel))
                updated_ids.append(resource_id)
                asset_updates.append((resource_id, "storyboard_image", cell_rel))
                asset_updates.append((resource_id, "grid_id", grid.id))
                asset_updates.append((resource_id, "grid_cell_index", frame.index))

            if missing_ids:
                logger.warning(
                    "grid %s: frame_chain 中以下分镜在剧本 %s 已不存在,跳过 cell 保存: %s",
                    grid.id,
                    script_file,
                    sorted(set(missing_ids)),
                )

            manifest_entries: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
            references: tuple[VisualReference, ...] | None = ()
            reference_list: list[VisualReference] = []
            for reference in grid.reference_images or []:
                try:
                    reference_path = safe_join(project_path, reference.path)
                    if not reference_path.is_file():
                        references = None
                        break
                    reference_list.append(
                        VisualReference(
                            path=reference_path,
                            role="asset_sheet",
                            logical_type=reference.ref_type,
                            logical_id=reference.name,
                            kind="sheet",
                        )
                    )
                except (OSError, TypeError, ValueError):
                    references = None
                    break
            if references is not None:
                try:
                    references = snapshot_visual_references(reference_list)
                except OSError:
                    references = None
            member_ratio = video_aspect_ratio

            def _prepare_manifest_state(current_project: dict[str, Any], current_script: dict[str, Any]) -> None:
                """Refresh the registered grid source and derived bases inside the final transaction."""

                nonlocal source_entry, source_key, source_status
                source_key, source_entry, source_status = _registered_grid_source()

                current_items, current_id_field, _kind = resolve_items(current_script)
                item_by_id = {
                    str(item.get(current_id_field)): item for item in current_items if isinstance(item, Mapping)
                }
                members: tuple[GridStoryboardVisual, ...] | None = None
                if len(set(grid.scene_ids)) == len(grid.scene_ids) and all(
                    resource_id in item_by_id for resource_id in grid.scene_ids
                ):
                    members = tuple(
                        GridStoryboardVisual(
                            resource_id=resource_id,
                            image_prompt=item_by_id[resource_id].get("image_prompt"),
                            video_prompt=item_by_id[resource_id].get("video_prompt"),
                        )
                        for resource_id in grid.scene_ids
                    )

                manifest_entries.clear()
                manifest_entries.update(
                    {ArtifactKey.episode_storyboard(grid.episode, resource_id): None for resource_id in updated_ids}
                )
                for metadata in version_metadata_by_resource.values():
                    metadata.pop(IMAGE_ARTIFACT_BASIS_FIELD, None)

                if source_status is ArtifactStatus.STALE:
                    for cell_index, resource_id, cell_rel in cell_assignments:
                        try:
                            basis = build_stale_grid_member_storyboard_visual_basis(
                                group_id=grid.id,
                                resource_id=resource_id,
                                cell_index=cell_index,
                                composite_image=composite_snapshot,
                                rows=grid.rows,
                                columns=grid.cols,
                                member_aspect_ratio=member_ratio,
                                source_grid_basis_digest=source_entry.basis_digest,
                                source_composite_digest=composite_digest,
                            )
                        except (OSError, TypeError, ValueError):
                            continue
                        manifest_entries[ArtifactKey.episode_storyboard(grid.episode, resource_id)] = (
                            ArtifactManifestEntry(
                                artifact_path=cell_rel,
                                basis_digest=basis.digest,
                            )
                        )
                        version_metadata_by_resource[resource_id][IMAGE_ARTIFACT_BASIS_FIELD] = basis.to_evidence_dict()
                elif members is not None and references is not None:
                    for cell_index, resource_id, cell_rel in cell_assignments:
                        try:
                            basis = build_grid_member_storyboard_visual_basis(
                                group_id=grid.id,
                                members=members,
                                cell_index=cell_index,
                                composite_image=composite_snapshot,
                                rows=grid.rows,
                                columns=grid.cols,
                                style=str(current_project.get("style") or ""),
                                style_description=project_basis_style_description(current_project),
                                member_aspect_ratio=member_ratio,
                                references=references,
                                source_composite_digest=composite_digest,
                                asset_names=project_basis_records_asset_names(current_project),
                            )
                        except (OSError, TypeError, ValueError):
                            continue
                        manifest_entries[ArtifactKey.episode_storyboard(grid.episode, resource_id)] = (
                            ArtifactManifestEntry(
                                artifact_path=cell_rel,
                                basis_digest=basis.digest,
                            )
                        )
                        version_metadata_by_resource[resource_id][IMAGE_ARTIFACT_BASIS_FIELD] = basis.to_evidence_dict()

            split_at = datetime.now(UTC).isoformat()
            initial_grid = grid.to_dict()
            committed_grid_box: list[GridGeneration] = []

            def _register() -> None:
                if source_key is not None and source_status is not None:
                    try:
                        source_unchanged = visual_file_digest(grid_image_file) == composite_digest
                    except OSError:
                        source_unchanged = False
                    references_unchanged = (
                        source_status is not ArtifactStatus.CURRENT
                        or references is None
                        or visual_references_match_snapshot(references)
                    )
                    latest = ArtifactCurrencyResolver(project_path).compare(
                        source_key,
                        artifact_path=grid_image_path,
                    )
                    if (
                        not source_unchanged
                        or not references_unchanged
                        or not latest.usable
                        or latest.status is not source_status
                    ):
                        raise GridImageNotReadyError(f"grid {grid.id} changed while being split")
                expected_entries = (
                    {source_key: source_entry} if source_key is not None and source_entry is not None else {}
                )
                register = register_entries or _register_split_entries_atomically
                register(
                    project_path,
                    entries=manifest_entries,
                    expected_entries=expected_entries,
                )

            def _commit_grid() -> None:
                assignment_by_index = {index: path for index, _resource_id, path in cell_assignments}

                def _mutate(current: GridGeneration) -> None:
                    if current.to_dict() != initial_grid:
                        raise RuntimeError("grid changed while its composite was being split")
                    for frame in current.frame_chain:
                        if frame.index in assignment_by_index:
                            frame.image_path = assignment_by_index[frame.index]
                    current.split_at = split_at

                committed = grid_manager.update(grid.id, _mutate, on_commit=_register)
                if committed is None:
                    raise RuntimeError(f"grid disappeared while being split: {grid.id}")
                committed_grid_box.append(committed)

            if staged_commits:

                def _activate_versions(_script_path: Path) -> None:
                    versions.commit_staged_versions(staged_commits, on_commit=_commit_grid)

                def _prepare_versions(current_script: dict[str, Any]) -> Callable[[Path], None]:
                    _prepare_manifest_state(
                        pm.load_project(project_name),
                        current_script,
                    )
                    return _activate_versions

                pm.batch_update_scene_assets(
                    project_name=project_name,
                    script_filename=script_file,
                    updates=asset_updates,
                    prepare_on_commit=_prepare_versions,
                )
            else:
                _commit_grid()

            if len(committed_grid_box) != 1:
                raise RuntimeError("grid split transaction skipped its grid record commit")
            committed_grid = committed_grid_box[0]
            grid.frame_chain = committed_grid.frame_chain
            grid.split_at = committed_grid.split_at
            return updated_ids, missing_ids
        finally:
            for staged_path in staged_paths:
                staged_path.unlink(missing_ok=True)  # hard-delete: temp -- staged split cell
            composite_snapshot.unlink(missing_ok=True)  # hard-delete: temp -- composite snapshot used for splitting

    updated_ids, missing_ids = await run_noninterruptible_sync(_split_and_assign)

    fingerprints = await run_noninterruptible_sync(
        emit_generation_success_batch,
        task_type="grid_split",
        project_name=project_name,
        resource_id=grid.id,
        payload={"script_file": script_file},
    )

    return GridSplitResult(
        updated_scene_ids=updated_ids,
        missing_scene_ids=sorted(set(missing_ids)),
        asset_fingerprints=fingerprints,
    )
