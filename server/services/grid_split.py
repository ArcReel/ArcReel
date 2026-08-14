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

from lib.artifact_activation import register_artifact_entries_atomically
from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry
from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager
from lib.path_safety import safe_join
from lib.project_manager import get_project_manager
from lib.version_manager import StagedVersionCommit, VersionManager
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_grid_member_storyboard_visual_basis,
)

logger = logging.getLogger(__name__)


def _register_split_entries_atomically(
    project_path: Path,
    *,
    entries: Mapping[ArtifactKey, ArtifactManifestEntry | None],
) -> None:
    """Registration boundary for all cells selected by one split."""

    register_artifact_entries_atomically(project_path, entries)


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
    if not grid.grid_image_path or not grid_image_file.exists():
        raise GridImageNotReadyError(f"grid {grid.id} has no grid image to split")

    versions = VersionManager(project_path)
    script_file = grid.script_file

    def _split_and_assign() -> tuple[list[str], list[str]]:
        from lib.script_editor import resolve_items

        # 比例取记录冻结值：项目 aspect_ratio 改过之后再切历史联合图，按新比例中心裁切
        # 会把每格削掉大半（横版图按竖版切）。存量记录无该字段，回退到项目当前设置。
        video_aspect_ratio = grid.video_aspect_ratio or get_aspect_ratio(project, "videos")
        # Image.open 惰性读取并持有文件句柄，而逐格 save 期间上传/还原可能要覆写同一个 PNG，
        # Windows 上未释放的句柄会让覆写失败。切格在 with 内完成，切出的 cell 已是各自独立的
        # 内存图像，句柄随 with 退出即释放；不再额外 copy 整张联合图，省下一份满尺寸副本。
        with Image.open(grid_image_file) as src:
            src.load()
            cells = split_grid_image(src, grid.rows, grid.cols, video_aspect_ratio)

        storyboards_dir = project_path / "storyboards"
        storyboards_dir.mkdir(parents=True, exist_ok=True)

        # batch_update_scene_assets 在任一 scene_id 未命中时整批 fail-loud 回滚——避免
        # cell.save() 已写 PNG 落盘后又因 KeyError 整批回滚留下 orphan PNG,这里先 load
        # 当前剧本拿 valid id 集合,frame_chain 中已不存在的分镜(grid plan 生成后 agent
        # split/remove 改动了剧本)跳过 cell PNG 保存 + 收集到 missing 列表 + warning。
        script = pm.load_script(project_name, script_file)
        items, id_field, _kind = resolve_items(script)
        valid_ids = {str(item.get(id_field)) for item in items if isinstance(item, dict)}

        asset_updates: list[tuple[str, str, Any]] = []
        updated_ids: list[str] = []
        missing_ids: list[str] = []
        staged_commits: list[StagedVersionCommit] = []
        cell_assignments: list[tuple[int, str, str]] = []

        try:
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
                os.close(fd)
                staged_path = Path(staged_name)
                staged_path.unlink()
                cell.save(staged_path, format="PNG")
                staged_commits.append(
                    StagedVersionCommit(
                        resource_type="storyboards",
                        resource_id=resource_id,
                        prompt="",
                        staged_file=staged_path,
                        current_file=cell_path,
                        metadata={"source": "grid_split", "grid_id": grid.id},
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
                references = tuple(reference_list)
            member_ratio = grid.video_aspect_ratio or get_aspect_ratio(project, "videos")
            if members is not None and references is not None:
                for cell_index, resource_id, cell_rel in cell_assignments:
                    try:
                        basis = build_grid_member_storyboard_visual_basis(
                            group_id=grid.id,
                            members=members,
                            cell_index=cell_index,
                            composite_image=grid_image_file,
                            rows=grid.rows,
                            columns=grid.cols,
                            style=str(project.get("style") or ""),
                            member_aspect_ratio=member_ratio,
                            references=references,
                        )
                    except (OSError, TypeError, ValueError):
                        continue
                    manifest_entries[ArtifactKey.episode_storyboard(grid.episode, resource_id)] = ArtifactManifestEntry(
                        artifact_path=cell_rel, basis_digest=basis.digest
                    )

            split_at = datetime.now(UTC).isoformat()
            initial_grid = grid.to_dict()
            committed_grid_box: list[GridGeneration] = []

            def _register() -> None:
                _register_split_entries_atomically(project_path, entries=manifest_entries)

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
            for commit in staged_commits:
                Path(commit.staged_file).unlink(missing_ok=True)

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
