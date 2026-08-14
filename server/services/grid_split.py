"""宫格切分服务：把宫格当前联合图切割落格到各分镜。

切分是覆写分镜格的唯一步骤，与联合图的产生（生成任务 / 手动上传 / 版本还原）解耦：
联合图内容变更只刷新联合图自身，落格必须经本服务显式执行。HTTP 路由与 SDK 工具共用。
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifact_activation import (
    TARGET_SCHEMA_VERSION,
    ArtifactCurrencyResolver,
    register_artifact_entries_atomically,
)
from lib.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager
from lib.path_safety import safe_join
from lib.project_manager import get_project_manager
from lib.version_manager import StagedVersionCommit, VersionManager
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_grid_member_storyboard_visual_basis,
    build_stale_grid_member_storyboard_visual_basis,
    snapshot_visual_references,
    visual_file_digest,
    visual_references_match_snapshot,
)

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
    """宫格尚无联合图（未生成完成且未上传），无法切分。"""


@dataclass
class GridSplitResult:
    updated_scene_ids: list[str]
    missing_scene_ids: list[str]
    asset_fingerprints: dict[str, int]


async def apply_grid_split(project_name: str, grid: GridGeneration) -> GridSplitResult:
    """按 ``grid`` 当前联合图切割并覆写各分镜格。

    - 每格覆写前旧文件先补登版本、覆写后登记新版本（source="grid_split"）；
    - frame_chain 中已不在剧本内的 scene id 跳过并告警；
    - 完成后写 ``grid.split_at`` 并广播项目变更事件（含逐格指纹供前端 cache-bust）。
    """
    from PIL import Image

    from lib.grid.splitter import split_grid_image
    from server.services.generation_tasks import emit_generation_success_batch, get_aspect_ratio

    pm = get_project_manager()
    project_path = await asyncio.to_thread(pm.get_project_path, project_name)
    project = await asyncio.to_thread(pm.load_project, project_name)

    grid_manager = GridManager(project_path)
    grid_image_file = grid_manager.image_path(grid.id)
    grid_image_path = grid.grid_image_path
    if not grid_image_path or not grid_image_file.exists():
        raise GridImageNotReadyError(f"grid {grid.id} has no grid image to split")

    versions = VersionManager(project_path)
    script_file = grid.script_file

    def _split_and_assign() -> tuple[list[str], list[str]]:
        from lib.script_editor import resolve_items

        source_status: ArtifactStatus | None = None
        source_key: ArtifactKey | None = None
        source_entry: ArtifactManifestEntry | None = None
        if project.get("schema_version") == TARGET_SCHEMA_VERSION:
            with pm.locked_project_script_snapshot(project_name, script_file) as (frozen_project, script):
                source_key = ArtifactKey.episode_grid(grid.episode, grid.id)
                adapter = ProjectArtifactManifestAdapter(project_path)
                source_entry = adapter.get_entry(source_key)
                comparison = ArtifactCurrencyResolver(project_path).compare(
                    source_key,
                    artifact_path=grid_image_path,
                )
                if source_entry is None or not comparison.usable or adapter.get_entry(source_key) != source_entry:
                    raise GridImageNotReadyError(f"grid {grid.id} has no registered grid image to split")
                source_status = comparison.status
                project_snapshot = frozen_project
        else:
            project_snapshot = project
            script = pm.load_script(project_name, script_file)

        # 比例取记录冻结值：项目 aspect_ratio 改过之后再切历史联合图，按新比例中心裁切
        # 会把每格削掉大半（横版图按竖版切）。存量记录无该字段，回退到项目当前设置。
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
                # Image.open 惰性读取并持有文件句柄。先复制联合图的完整字节，再从同一快照
                # 切格与建 basis；上传/还原可在随后替换 canonical PNG，不会混入半新半旧证据。
                with Image.open(snapshot) as src:
                    src.load()
                    return snapshot, split_grid_image(src, grid.rows, grid.cols, video_aspect_ratio)
            except BaseException:
                snapshot.unlink(missing_ok=True)
                raise

        composite_snapshot, cells = _snapshot_and_split()
        staged_commits: list[StagedVersionCommit] = []
        staged_paths: list[Path] = []
        version_metadata_by_resource: dict[str, dict[str, Any]] = {}
        try:
            composite_digest = visual_file_digest(composite_snapshot)
            storyboards_dir = project_path / "storyboards"
            storyboards_dir.mkdir(parents=True, exist_ok=True)

            # batch_update_scene_assets 在任一 scene_id 未命中时整批 fail-loud 回滚——避免
            # cell.save() 已写 PNG 落盘后又因 KeyError 整批回滚留下 orphan PNG,这里先 load
            # 当前剧本拿 valid id 集合,frame_chain 中已不存在的分镜(grid plan 生成后 agent
            # split/remove 改动了剧本)跳过 cell PNG 保存 + 收集到 missing 列表 + warning。
            items, id_field, _kind = resolve_items(script)
            valid_ids = {str(item.get(id_field)) for item in items if isinstance(item, dict)}

            asset_updates: list[tuple[str, str, Any]] = []
            updated_ids: list[str] = []
            missing_ids: list[str] = []
            cell_assignments: list[tuple[int, str, str]] = []

            # Cells stay invisible until the script, complete version batch, grid
            # record, and complete Manifest claim set can all commit.
            for cell, frame in zip(cells, grid.frame_chain):
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
                staged_path.unlink()
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

            manifest_entries: dict[ArtifactKey, ArtifactManifestEntry | None] = {
                ArtifactKey.episode_storyboard(grid.episode, resource_id): None for resource_id in updated_ids
            }
            item_by_id = {str(item.get(id_field)): item for item in items if isinstance(item, Mapping)}
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
            if source_status is ArtifactStatus.STALE and source_entry is not None:
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
                    manifest_entries[ArtifactKey.episode_storyboard(grid.episode, resource_id)] = ArtifactManifestEntry(
                        artifact_path=cell_rel,
                        basis_digest=basis.digest,
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
                            style=str(project_snapshot.get("style") or ""),
                            member_aspect_ratio=member_ratio,
                            references=references,
                            source_composite_digest=composite_digest,
                        )
                    except (OSError, TypeError, ValueError):
                        continue
                    manifest_entries[ArtifactKey.episode_storyboard(grid.episode, resource_id)] = ArtifactManifestEntry(
                        artifact_path=cell_rel, basis_digest=basis.digest
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
                _register_split_entries_atomically(
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

                pm.batch_update_scene_assets(
                    project_name=project_name,
                    script_filename=script_file,
                    updates=asset_updates,
                    on_commit=_activate_versions,
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
                staged_path.unlink(missing_ok=True)
            composite_snapshot.unlink(missing_ok=True)

    updated_ids, missing_ids = await asyncio.to_thread(_split_and_assign)

    fingerprints = await asyncio.to_thread(
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
