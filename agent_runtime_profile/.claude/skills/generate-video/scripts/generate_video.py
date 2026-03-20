#!/usr/bin/env python3
"""
Video Generator - 使用 Veo 3.1 API 生成视频分镜

Usage:
    # 按 episode 生成（推荐）
    python generate_video.py episode_N.json --episode N

    # 断点续传
    python generate_video.py episode_N.json --episode N --resume

    # 单场景模式
    python generate_video.py episode_N.json --scene SCENE_ID

    # 批量模式（独立生成每个场景）
    python generate_video.py episode_N.json --all

每个场景独立生成视频，使用分镜图作为起始帧，然后使用 ffmpeg 拼接。
"""

import argparse
import json
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from lib.generation_queue_client import (
    BatchTaskSpec,
    BatchTaskResult,
    batch_enqueue_and_wait_sync,
    enqueue_and_wait_sync as enqueue_and_wait,
)
from lib.project_manager import ProjectManager
from lib.prompt_utils import (
    video_prompt_to_yaml,
    is_structured_video_prompt
)


# ============================================================================
# Prompt 构建
# ============================================================================

def get_video_prompt(item: dict) -> str:
    """
    获取视频生成 Prompt

    支持结构化 prompt 格式：如果 video_prompt 是 dict，则转换为 YAML 格式。

    Args:
        item: 片段/场景字典

    Returns:
        video_prompt 字符串（可能是 YAML 格式或普通字符串）
    """
    prompt = item.get('video_prompt')
    if not prompt:
        item_id = item.get('segment_id') or item.get('scene_id')
        raise ValueError(f"片段/场景缺少 video_prompt 字段: {item_id}")

    # 检测是否为结构化格式
    if is_structured_video_prompt(prompt):
        # 转换为 YAML 格式
        return video_prompt_to_yaml(prompt)

    # 避免将 dict 直接下传导致类型错误
    if isinstance(prompt, dict):
        item_id = item.get('segment_id') or item.get('scene_id')
        raise ValueError(f"片段/场景 video_prompt 为对象但格式不符合结构化规范: {item_id}")

    if not isinstance(prompt, str):
        item_id = item.get('segment_id') or item.get('scene_id')
        raise TypeError(f"片段/场景 video_prompt 类型无效（期望 str 或 dict）: {item_id}")

    return prompt


def get_items_from_script(script: dict) -> tuple:
    """
    根据内容模式获取场景/片段列表和相关字段名

    Args:
        script: 剧本数据

    Returns:
        (items_list, id_field, char_field, clue_field) 元组
    """
    content_mode = script.get('content_mode', 'narration')
    if content_mode == 'narration' and 'segments' in script:
        return (
            script['segments'],
            'segment_id',
            'characters_in_segment',
            'clues_in_segment'
        )
    return (
        script.get('scenes', []),
        'scene_id',
        'characters_in_scene',
        'clues_in_scene'
    )


def parse_scene_ids(scenes_arg: str) -> list:
    """解析逗号分隔的场景 ID 列表"""
    return [s.strip() for s in scenes_arg.split(',') if s.strip()]


def validate_duration(duration: int) -> str:
    """
    验证并返回有效的时长参数

    Veo API 仅支持 4s/6s/8s

    Args:
        duration: 输入的时长（秒）

    Returns:
        有效的时长字符串
    """
    valid_durations = [4, 6, 8]
    if duration in valid_durations:
        return str(duration)
    # 向上取整到最近的有效值
    for d in valid_durations:
        if d >= duration:
            return str(d)
    return "8"  # 最大值


# ============================================================================
# Checkpoint 管理
# ============================================================================

def get_checkpoint_path(project_dir: Path, episode: int) -> Path:
    """获取 checkpoint 文件路径"""
    return project_dir / 'videos' / f'.checkpoint_ep{episode}.json'


def load_checkpoint(project_dir: Path, episode: int) -> Optional[dict]:
    """
    加载 checkpoint

    Returns:
        checkpoint 字典或 None
    """
    checkpoint_path = get_checkpoint_path(project_dir, episode)
    if checkpoint_path.exists():
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def save_checkpoint(
    project_dir: Path,
    episode: int,
    completed_scenes: list,
    started_at: str
):
    """保存 checkpoint"""
    checkpoint_path = get_checkpoint_path(project_dir, episode)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "episode": episode,
        "completed_scenes": completed_scenes,
        "started_at": started_at,
        "updated_at": datetime.now().isoformat()
    }

    with open(checkpoint_path, 'w', encoding='utf-8') as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


def clear_checkpoint(project_dir: Path, episode: int):
    """清除 checkpoint"""
    checkpoint_path = get_checkpoint_path(project_dir, episode)
    if checkpoint_path.exists():
        checkpoint_path.unlink()


# ============================================================================
# FFmpeg 拼接
# ============================================================================

def concatenate_videos(video_paths: list, output_path: Path) -> Path:
    """
    使用 ffmpeg 拼接多个视频片段

    Args:
        video_paths: 视频文件路径列表
        output_path: 输出路径

    Returns:
        输出视频路径
    """
    if len(video_paths) == 1:
        # 只有一个片段，直接复制
        import shutil
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(video_paths[0], output_path)
        return output_path

    # 创建临时文件列表
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        for video_path in video_paths:
            f.write(f"file '{video_path}'\n")
        list_file = f.name

    try:
        # 使用 ffmpeg concat demuxer
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', list_file,
            '-c', 'copy',
            str(output_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"✅ 视频已拼接: {output_path}")
        return output_path
    finally:
        Path(list_file).unlink()


# ============================================================================
# 批量任务构建辅助
# ============================================================================

def _build_video_specs(
    *,
    items: list,
    id_field: str,
    content_mode: str,
    script_filename: str,
    project_dir: Path,
    skip_ids: Optional[List[str]] = None,
) -> tuple[list[BatchTaskSpec], dict[str, int]]:
    """
    从场景/片段列表构建 BatchTaskSpec 和 resource_id -> order_index 映射。

    跳过缺少分镜图或 prompt 无效的项，并打印警告。

    Returns:
        (specs, order_map)  order_map: resource_id -> 原始 items 中的索引
    """
    item_type = "片段" if content_mode == 'narration' else "场景"
    default_duration = 4 if content_mode == 'narration' else 8
    skip_set = set(skip_ids or [])

    specs: list[BatchTaskSpec] = []
    order_map: dict[str, int] = {}

    for idx, item in enumerate(items):
        item_id = item.get(id_field) or item.get('scene_id') or item.get('segment_id') or f'item_{idx}'

        if item_id in skip_set:
            continue

        storyboard_image = (item.get('generated_assets') or {}).get('storyboard_image')
        if not storyboard_image:
            print(f"⚠️  {item_type} {item_id} 没有分镜图，跳过")
            continue
        storyboard_path = project_dir / storyboard_image
        if not storyboard_path.exists():
            print(f"⚠️  分镜图不存在: {storyboard_path}，跳过")
            continue

        try:
            prompt = get_video_prompt(item)
        except Exception as e:
            print(f"⚠️  {item_type} {item_id} 的 video_prompt 无效，跳过: {e}")
            continue

        duration = item.get('duration_seconds', default_duration)
        duration_str = validate_duration(duration)

        specs.append(BatchTaskSpec(
            task_type="video",
            media_type="video",
            resource_id=item_id,
            payload={
                "prompt": prompt,
                "script_file": script_filename,
                "duration_seconds": int(duration_str),
            },
            script_file=script_filename,
        ))
        order_map[item_id] = idx

    return specs, order_map


def _scan_completed_items(
    items: list,
    id_field: str,
    item_type: str,
    completed_scenes: list[str],
    videos_dir: Path,
) -> tuple[list[Optional[Path]], list[str]]:
    """Scan items for already-completed videos; return ordered paths and done IDs."""
    ordered_paths: list[Optional[Path]] = [None] * len(items)
    already_done: list[str] = []
    for idx, item in enumerate(items):
        item_id = item.get(id_field, item.get('scene_id', f'item_{idx}'))
        video_output = videos_dir / f"scene_{item_id}.mp4"
        if item_id in completed_scenes and video_output.exists():
            print(f"  [{idx + 1}/{len(items)}] {item_type} {item_id} ✓ 已完成")
            ordered_paths[idx] = video_output
            already_done.append(item_id)
        elif item_id in completed_scenes:
            completed_scenes.remove(item_id)
    return ordered_paths, already_done


def _submit_and_wait_with_checkpoint(
    *,
    project_name: str,
    project_dir: Path,
    specs: list[BatchTaskSpec],
    order_map: dict[str, int],
    ordered_paths: list[Optional[Path]],
    completed_scenes: list[str],
    save_fn: callable,
    item_type: str,
) -> list[BatchTaskResult]:
    """Submit specs via batch_enqueue_and_wait_sync with checkpoint on each success."""
    print(f"\n🚀 批量提交 {len(specs)} 个视频到生成队列...\n")
    checkpoint_lock = threading.Lock()

    def on_success(br: BatchTaskResult) -> None:
        result = br.result or {}
        relative_path = result.get("file_path") or f"videos/scene_{br.resource_id}.mp4"
        output_path = project_dir / relative_path
        ordered_paths[order_map[br.resource_id]] = output_path
        with checkpoint_lock:
            completed_scenes.append(br.resource_id)
            save_fn()
        print(f"    ✅ 完成: {output_path.name}")

    def on_failure(br: BatchTaskResult) -> None:
        print(f"    ❌ {br.resource_id} 失败: {br.error}")

    _, failures = batch_enqueue_and_wait_sync(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )

    if failures:
        print(f"\n⚠️  {len(failures)} 个{item_type}生成失败:")
        for f in failures:
            print(f"   - {f.resource_id}: {f.error}")
        print("    💡 使用 --resume 参数可从此处继续")
        raise RuntimeError(f"{len(failures)} 个{item_type}生成失败")

    return failures


# ============================================================================
# Episode 视频生成（每个场景独立生成）
# ============================================================================

def generate_episode_video(
    script_filename: str,
    episode: int,
    resume: bool = False,
) -> list[Path]:
    """
    为指定 episode 生成所有场景的视频片段。

    每个场景独立生成视频，使用分镜图作为起始帧。
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)
    script = pm.load_script(project_name, script_filename)
    content_mode = script.get('content_mode', 'narration')
    all_items, id_field, _, _ = get_items_from_script(script)

    episode_items = [s for s in all_items if s.get('episode', 1) == episode]
    if not episode_items:
        raise ValueError(f"未找到第 {episode} 集的场景/片段")

    item_type = "片段" if content_mode == 'narration' else "场景"
    print(f"📋 第 {episode} 集共 {len(episode_items)} 个{item_type}")

    # Checkpoint
    completed_scenes: list[str] = []
    started_at = datetime.now().isoformat()
    if resume:
        checkpoint = load_checkpoint(project_dir, episode)
        if checkpoint:
            completed_scenes = checkpoint.get('completed_scenes', [])
            started_at = checkpoint.get('started_at', started_at)
            print(f"🔄 从 checkpoint 恢复，已完成 {len(completed_scenes)} 个场景")
        else:
            print("⚠️  未找到 checkpoint，从头开始")

    videos_dir = project_dir / 'videos'
    videos_dir.mkdir(parents=True, exist_ok=True)

    ordered_video_paths, already_done_ids = _scan_completed_items(
        episode_items, id_field, item_type, completed_scenes, videos_dir,
    )
    specs, order_map = _build_video_specs(
        items=episode_items, id_field=id_field, content_mode=content_mode,
        script_filename=script_filename, project_dir=project_dir,
        skip_ids=already_done_ids,
    )

    if not specs and not any(ordered_video_paths):
        raise RuntimeError("没有可生成的视频片段")

    if specs:
        _submit_and_wait_with_checkpoint(
            project_name=project_name, project_dir=project_dir,
            specs=specs, order_map=order_map, ordered_paths=ordered_video_paths,
            completed_scenes=completed_scenes,
            save_fn=lambda: save_checkpoint(project_dir, episode, completed_scenes, started_at),
            item_type=item_type,
        )

    scene_videos = [p for p in ordered_video_paths if p is not None]
    if not scene_videos:
        raise RuntimeError("没有生成任何视频片段")

    clear_checkpoint(project_dir, episode)
    print(f"\n🎉 第 {episode} 集视频生成完成，共 {len(scene_videos)} 个片段")
    return scene_videos


# ============================================================================
# 单场景生成
# ============================================================================

def generate_scene_video(
    script_filename: str,
    scene_id: str
) -> Path:
    """
    生成单个场景/片段的视频

    Args:
        script_filename: 剧本文件名
        scene_id: 场景/片段 ID

    Returns:
        生成的视频路径
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # 加载剧本
    script = pm.load_script(project_name, script_filename)
    content_mode = script.get('content_mode', 'narration')
    all_items, id_field, _, _ = get_items_from_script(script)

    # 找到指定场景/片段
    item = None
    for s in all_items:
        if s.get(id_field) == scene_id or s.get('scene_id') == scene_id:
            item = s
            break

    if not item:
        raise ValueError(f"场景/片段 '{scene_id}' 不存在")

    # 检查分镜图
    storyboard_image = item.get('generated_assets', {}).get('storyboard_image')
    if not storyboard_image:
        raise ValueError(f"场景/片段 '{scene_id}' 没有分镜图，请先运行 generate-storyboard")

    storyboard_path = project_dir / storyboard_image
    if not storyboard_path.exists():
        raise FileNotFoundError(f"分镜图不存在: {storyboard_path}")

    # 直接使用 video_prompt 字段
    prompt = get_video_prompt(item)

    # 获取时长（说书模式默认 4 秒，剧集动画默认 8 秒）
    default_duration = 4 if content_mode == 'narration' else 8
    duration = item.get('duration_seconds', default_duration)
    duration_str = validate_duration(duration)

    print(f"🎬 正在生成视频: 场景/片段 {scene_id}")
    print("   预计等待时间: 1-6 分钟")

    queued = enqueue_and_wait(
        project_name=project_name,
        task_type="video",
        media_type="video",
        resource_id=scene_id,
        payload={
            "prompt": prompt,
            "script_file": script_filename,
            "duration_seconds": int(duration_str),
        },
        script_file=script_filename,
        source="skill",
    )
    result = queued.get("result") or {}
    relative_path = result.get("file_path") or f"videos/scene_{scene_id}.mp4"
    output_path = project_dir / relative_path

    print(f"✅ 视频已保存: {output_path}")
    return output_path


def generate_all_videos(script_filename: str) -> list:
    """
    生成所有待处理场景的视频（独立模式）

    Returns:
        生成的视频路径列表
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # 加载剧本
    script = pm.load_script(project_name, script_filename)
    content_mode = script.get('content_mode', 'narration')
    all_items, id_field, _, _ = get_items_from_script(script)

    pending_items = [
        item for item in all_items
        if not (item.get('generated_assets') or {}).get('video_clip')
    ]

    if not pending_items:
        print("✨ 所有场景/片段的视频都已生成")
        return []

    item_type = "片段" if content_mode == 'narration' else "场景"
    print(f"📋 共 {len(pending_items)} 个{item_type}待生成视频")
    print("⚠️  每个视频可能需要 1-6 分钟，请耐心等待")
    print("💡 推荐使用 --episode N 模式生成并自动拼接")

    specs, _ = _build_video_specs(
        items=pending_items,
        id_field=id_field,
        content_mode=content_mode,
        script_filename=script_filename,
        project_dir=project_dir,
    )

    if not specs:
        print("⚠️  没有任何可生成的视频任务（可能缺少分镜图或 prompt）")
        return []

    print(f"\n🚀 批量提交 {len(specs)} 个视频到生成队列...\n")

    result_paths: list[Path] = []

    def on_success(br: BatchTaskResult) -> None:
        result = br.result or {}
        relative_path = result.get("file_path") or f"videos/scene_{br.resource_id}.mp4"
        output_path = project_dir / relative_path
        result_paths.append(output_path)
        print(f"✅ 完成: {output_path.name}")

    def on_failure(br: BatchTaskResult) -> None:
        print(f"❌ {br.resource_id} 失败: {br.error}")

    _, failures = batch_enqueue_and_wait_sync(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )

    if failures:
        print(f"\n⚠️  {len(failures)} 个{item_type}生成失败:")
        for f in failures:
            print(f"   - {f.resource_id}: {f.error}")

    print(f"\n🎉 批量视频生成完成，共 {len(result_paths)} 个")
    return result_paths


def generate_selected_videos(
    script_filename: str,
    scene_ids: list,
    resume: bool = False,
) -> list:
    """
    生成指定的多个场景视频

    Args:
        script_filename: 剧本文件名
        scene_ids: 场景 ID 列表
        resume: 是否从断点续传

    Returns:
        生成的视频路径列表
    """
    import hashlib

    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)
    script = pm.load_script(project_name, script_filename)
    content_mode = script.get('content_mode', 'narration')
    all_items, id_field, _, _ = get_items_from_script(script)

    # 筛选指定的场景
    items_by_id = {}
    for item in all_items:
        items_by_id[item.get(id_field, '')] = item
        if 'scene_id' in item:
            items_by_id[item['scene_id']] = item

    selected_items = []
    for scene_id in scene_ids:
        if scene_id in items_by_id:
            selected_items.append(items_by_id[scene_id])
        else:
            print(f"⚠️  场景/片段 '{scene_id}' 不存在，跳过")

    if not selected_items:
        raise ValueError("没有找到任何有效的场景/片段")

    item_type = "片段" if content_mode == 'narration' else "场景"
    print(f"📋 共选择 {len(selected_items)} 个{item_type}")

    # Checkpoint
    scenes_hash = hashlib.md5(','.join(scene_ids).encode()).hexdigest()[:8]
    checkpoint_path = project_dir / 'videos' / f'.checkpoint_selected_{scenes_hash}.json'
    completed_scenes: list[str] = []
    started_at = datetime.now().isoformat()

    if resume and checkpoint_path.exists():
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            checkpoint = json.load(f)
            completed_scenes = checkpoint.get('completed_scenes', [])
            started_at = checkpoint.get('started_at', started_at)
            print(f"🔄 从 checkpoint 恢复，已完成 {len(completed_scenes)} 个场景")

    videos_dir = project_dir / 'videos'
    videos_dir.mkdir(parents=True, exist_ok=True)

    ordered_results, already_done_ids = _scan_completed_items(
        selected_items, id_field, item_type, completed_scenes, videos_dir,
    )
    specs, order_map = _build_video_specs(
        items=selected_items, id_field=id_field, content_mode=content_mode,
        script_filename=script_filename, project_dir=project_dir,
        skip_ids=already_done_ids,
    )

    if specs:
        def _save():
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            with open(checkpoint_path, 'w', encoding='utf-8') as f_ckpt:
                json.dump({
                    "scene_ids": scene_ids,
                    "completed_scenes": completed_scenes,
                    "started_at": started_at,
                    "updated_at": datetime.now().isoformat(),
                }, f_ckpt, ensure_ascii=False, indent=2)

        _submit_and_wait_with_checkpoint(
            project_name=project_name, project_dir=project_dir,
            specs=specs, order_map=order_map, ordered_paths=ordered_results,
            completed_scenes=completed_scenes, save_fn=_save,
            item_type=item_type,
        )

    final_results = [p for p in ordered_results if p is not None]

    # 全部完成后清除 checkpoint
    if checkpoint_path.exists():
        checkpoint_path.unlink()

    print(f"\n🎉 批量视频生成完成，共 {len(final_results)} 个")
    return final_results


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='生成视频分镜',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 按 episode 生成（推荐）
  python generate_video.py episode_1.json --episode 1

  # 断点续传
  python generate_video.py episode_1.json --episode 1 --resume

  # 单场景模式
  python generate_video.py episode_1.json --scene E1S1

  # 批量自选模式
  python generate_video.py episode_1.json --scenes E1S01,E1S05,E1S10

  # 批量模式（独立生成）
  python generate_video.py episode_1.json --all
        """
    )
    parser.add_argument('script', help='剧本文件名')

    # 模式选择
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--scene', help='指定场景 ID（单场景模式）')
    mode_group.add_argument('--scenes', help='指定多个场景 ID（逗号分隔），如: E1S01,E1S05,E1S10')
    mode_group.add_argument('--all', action='store_true', help='生成所有待处理场景（独立模式）')
    mode_group.add_argument('--episode', type=int, help='按 episode 生成并拼接（推荐）')

    # 其他选项
    parser.add_argument('--resume', action='store_true', help='从上次中断处继续')

    args = parser.parse_args()

    try:
        if args.scene:
            generate_scene_video(args.script, args.scene)
        elif args.scenes:
            scene_ids = parse_scene_ids(args.scenes)
            generate_selected_videos(
                args.script,
                scene_ids,
                resume=args.resume,
            )
        elif args.all:
            generate_all_videos(args.script)
        elif args.episode:
            generate_episode_video(
                args.script,
                args.episode,
                resume=args.resume,
            )
        else:
            print("请指定模式: --scene, --scenes, --all, 或 --episode")
            print("使用 --help 查看帮助")
            sys.exit(1)

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
