"""宫格切分服务：把宫格当前联合图切割落格到各分镜。

切分是覆写分镜格的唯一步骤，与联合图的产生（生成任务 / 手动上传 / 版本还原）解耦：
联合图内容变更只刷新联合图自身，落格必须经本服务显式执行。HTTP 路由与 SDK 工具共用。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager
from lib.project_manager import get_project_manager
from lib.version_manager import VersionManager

logger = logging.getLogger(__name__)


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

        # Image.open 惰性读取并持有文件句柄；切格与逐格 save 期间上传/还原可能要覆写
        # 同一个 PNG，Windows 上未释放的句柄会让覆写失败。解码后立即 copy 成脱离
        # 源文件的内存图像，句柄随 with 退出即释放（与 image_utils._open_oriented 同款）。
        with Image.open(grid_image_file) as src:
            src.load()
            grid_image = src.copy()
        video_aspect_ratio = get_aspect_ratio(project, "videos")
        cells = split_grid_image(grid_image, grid.rows, grid.cols, video_aspect_ratio)

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

        # 宫格已统一走普通图生视频（不再使用 first_last 模式），cell 仅作为
        # next_scene_id 的起始分镜图，文件名与普通分镜对齐为 scene_{id}.png。
        for cell, frame in zip(cells, grid.frame_chain):
            if frame.frame_type == "placeholder":
                continue
            if frame.frame_type not in ("first", "transition"):
                continue
            if not frame.next_scene_id:
                continue

            if str(frame.next_scene_id) not in valid_ids:
                missing_ids.append(str(frame.next_scene_id))
                continue

            cell_rel = f"storyboards/scene_{frame.next_scene_id}.png"
            cell_path = storyboards_dir / f"scene_{frame.next_scene_id}.png"
            # 与 MediaGenerator 版本顺序一致：旧文件先补登再覆写、覆写后登记新版本。
            # 否则宫格重切的单元格不进版本史，版本面板的「当前版本」与磁盘内容脱节，
            # 且下一次还原/上传会让未登记的格子字节永久丢失。
            versions.ensure_current_tracked("storyboards", str(frame.next_scene_id), cell_path, "")
            cell.save(cell_path, format="PNG")
            versions.add_version(
                resource_type="storyboards",
                resource_id=str(frame.next_scene_id),
                prompt="",
                source_file=cell_path,
                source="grid_split",
                grid_id=grid.id,
            )
            frame.image_path = cell_rel
            updated_ids.append(str(frame.next_scene_id))
            asset_updates.append((frame.next_scene_id, "storyboard_image", cell_rel))
            asset_updates.append((frame.next_scene_id, "grid_id", grid.id))
            asset_updates.append((frame.next_scene_id, "grid_cell_index", frame.index))

        if missing_ids:
            logger.warning(
                "grid %s: frame_chain 中以下分镜在剧本 %s 已不存在,跳过 cell 保存: %s",
                grid.id,
                script_file,
                sorted(set(missing_ids)),
            )

        # Batch-write all asset updates in one script read+write pass
        if asset_updates:
            pm.batch_update_scene_assets(
                project_name=project_name,
                script_filename=script_file,
                updates=asset_updates,
            )

        grid.split_at = datetime.now(UTC).isoformat()
        grid_manager.save(grid)
        return updated_ids, missing_ids

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
