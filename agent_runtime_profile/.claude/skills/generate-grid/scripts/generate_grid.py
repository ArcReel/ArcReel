#!/usr/bin/env python3
"""宫格分镜图生成 — 按 segment_break 分组，每组生成一张宫格大图"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from lib.generation_queue_client import (
    enqueue_task_only_sync,
    wait_for_task_sync,
)
from lib.grid.layout import calculate_grid_layout
from lib.grid.models import GridGeneration
from lib.grid.prompt_builder import build_grid_prompt
from lib.grid_manager import GridManager
from lib.project_manager import ProjectManager
from lib.storyboard_sequence import get_storyboard_items


def _group_by_break(items: list[dict], id_field: str) -> list[list[dict]]:
    """Group scenes by segment_break=True boundaries."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for item in items:
        if item.get("segment_break", False) and current:
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)
    return groups


def _print_groups(groups: list[list[dict]], id_field: str, aspect_ratio: str) -> None:
    """Print group summary with layout info."""
    for i, group in enumerate(groups):
        ids = [item[id_field] for item in group]
        layout = calculate_grid_layout(len(ids), aspect_ratio)
        status = f"{layout.grid_size} ({layout.rows}×{layout.cols})" if layout else "single (< 4 场景)"
        print(f"  组 {i + 1}: {ids[0]}..{ids[-1]} ({len(ids)} 场景) → {status}")


def generate_grid(
    script_filename: str,
    episode: int,
    scene_ids: list[str] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """
    为 grid 模式项目生成宫格分镜图。

    Returns:
        (成功的 grid_id 列表, 失败列表[(grid_id, error)])
    """
    pm, project_name = ProjectManager.from_cwd()
    project = pm.load_project(project_name)

    if project.get("generation_mode") != "grid":
        print("⚠️  项目未启用宫格模式（generation_mode != 'grid'）")
        sys.exit(1)

    script = pm.load_script(project_name, script_filename)
    project_path = pm.get_project_path(project_name)
    items, id_field, _, _ = get_storyboard_items(script)
    aspect_ratio = project.get("aspect_ratio", "9:16")
    style = project.get("style", "")

    groups = _group_by_break(items, id_field)

    # Filter groups if scene-ids specified
    if scene_ids:
        sid_set = set(scene_ids)
        groups = [g for g in groups if any(item[id_field] in sid_set for item in g)]

    if not groups:
        print("没有匹配的场景组")
        return [], []

    gm = GridManager(project_path)

    # Build GridGeneration records and enqueue tasks
    grid_task_pairs: list[tuple[GridGeneration, str]] = []  # (grid, task_id)

    for group in groups:
        group_ids = [item[id_field] for item in group]
        layout = calculate_grid_layout(len(group_ids), aspect_ratio)
        if layout is None:
            print(f"⏭️  跳过：{group_ids[0]}...{group_ids[-1]}（{len(group_ids)} 场景，不足 4 个）")
            continue

        prompt = build_grid_prompt(
            scenes=group,
            id_field=id_field,
            rows=layout.rows,
            cols=layout.cols,
            style=style,
            aspect_ratio=aspect_ratio,
            grid_aspect_ratio=layout.grid_aspect_ratio,
        )

        grid = GridGeneration.create(
            episode=episode,
            script_file=script_filename,
            scene_ids=group_ids,
            rows=layout.rows,
            cols=layout.cols,
            grid_size=layout.grid_size,
            provider="",
            model="",
            prompt=prompt,
        )
        gm.save(grid)

        print(
            f"📐 {group_ids[0]}..{group_ids[-1]}: {len(group_ids)} 场景 → {layout.grid_size} ({layout.rows}×{layout.cols})"
        )

        enqueue_result = enqueue_task_only_sync(
            project_name=project_name,
            task_type="grid",
            media_type="image",
            resource_id=grid.id,
            payload={
                "prompt": prompt,
                "script_file": script_filename,
                "scene_ids": group_ids,
                "grid_size": layout.grid_size,
                "rows": layout.rows,
                "cols": layout.cols,
                "grid_aspect_ratio": layout.grid_aspect_ratio,
                "video_aspect_ratio": aspect_ratio,
            },
            script_file=script_filename,
            source="skill",
        )
        grid_task_pairs.append((grid, enqueue_result["task_id"]))

    if not grid_task_pairs:
        print("没有需要生成的宫格组")
        return [], []

    print(f"\n🚀 已提交 {len(grid_task_pairs)} 个宫格生成任务，等待完成...\n")

    successes: list[str] = []
    failures: list[tuple[str, str]] = []

    for grid, task_id in grid_task_pairs:
        try:
            task = wait_for_task_sync(task_id)
            if task.get("status") == "succeeded":
                print(f"  ✅ {grid.id}（{grid.scene_ids[0]}..{grid.scene_ids[-1]}）")
                successes.append(grid.id)
            else:
                error = task.get("error_message") or "unknown"
                print(f"  ❌ {grid.id}（{grid.scene_ids[0]}..{grid.scene_ids[-1]}）: {error}")
                failures.append((grid.id, error))
        except Exception as e:
            print(f"  ❌ {grid.id}（{grid.scene_ids[0]}..{grid.scene_ids[-1]}）: {e}")
            failures.append((grid.id, str(e)))

    return successes, failures


def main():
    parser = argparse.ArgumentParser(description="宫格分镜图生成")
    parser.add_argument("script_file", help="剧本文件名（例如 episode_1.json）")
    parser.add_argument("--episode", type=int, default=1, help="集数（默认 1）")
    parser.add_argument("--scene-ids", nargs="+", help="指定场景 ID（只生成包含这些场景的分组）")
    parser.add_argument("--list", action="store_true", help="列出分组信息，不执行生成")
    args = parser.parse_args()

    if args.list:
        pm, project_name = ProjectManager.from_cwd()
        project = pm.load_project(project_name)
        script = pm.load_script(project_name, args.script_file)
        items, id_field, _, _ = get_storyboard_items(script)
        aspect_ratio = project.get("aspect_ratio", "9:16")
        groups = _group_by_break(items, id_field)
        print(f"共 {len(groups)} 个分组：")
        _print_groups(groups, id_field, aspect_ratio)
        return

    try:
        successes, failures = generate_grid(
            script_filename=args.script_file,
            episode=args.episode,
            scene_ids=args.scene_ids,
        )
        print(f"\n📊 完成：{len(successes)} 成功，{len(failures)} 失败")
        if failures:
            print("⚠️  失败的宫格组：")
            for grid_id, error in failures:
                print(f"    {grid_id}: {error}")
    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
