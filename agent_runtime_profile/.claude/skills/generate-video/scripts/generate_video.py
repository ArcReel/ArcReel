#!/usr/bin/env python3
"""
Video Generator - 使用 Veo 3.1 API 生成视频分镜

Usage:
    # 按 episode 生成（推荐）
    python generate_video.py <project_name> <script_file> --episode N

    # 断点续传
    python generate_video.py <project_name> <script_file> --episode N --resume

    # 单场景模式
    python generate_video.py <project_name> <script_file> --scene SCENE_ID

    # 批量模式（独立生成每个场景）
    python generate_video.py <project_name> <script_file> --all

每个场景独立生成视频，使用分镜图作为起始帧，然后使用 ffmpeg 拼接。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path
from typing import Optional

from lib.generation_queue_client import (
    TaskFailedError,
    WorkerOfflineError,
    enqueue_and_wait,
    is_worker_online,
)
from lib.gemini_client import get_shared_rate_limiter
from lib.media_generator import MediaGenerator
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


def get_aspect_ratio(project_data: dict, asset_type: str) -> str:
    """
    根据项目配置获取画面比例（通过 API 参数传递，不写入 prompt）

    Args:
        project_data: project.json 数据
        asset_type: "design" | "grid" | "storyboard" | "video"

    Returns:
        画面比例字符串，如 "16:9" 或 "9:16"
    """
    content_mode = project_data.get('content_mode', 'narration') if project_data else 'narration'

    # 默认配置：说书模式使用竖屏，剧集动画模式使用横屏
    defaults = {
        "design": "16:9",
        "grid": "16:9",
        "storyboard": "9:16" if content_mode == 'narration' else "16:9",
        "video": "9:16" if content_mode == 'narration' else "16:9"
    }

    custom = project_data.get('aspect_ratio', {}) if project_data else {}
    return custom.get(asset_type, defaults[asset_type])


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


def get_default_max_workers() -> int:
    """读取默认视频并发数（来自环境变量 VIDEO_MAX_WORKERS，默认 2，最小 1）"""
    try:
        value = int(os.environ.get("VIDEO_MAX_WORKERS", "2"))
    except (TypeError, ValueError):
        value = 2
    return max(1, value)


def run_fail_fast_tasks(tasks: list, task_fn, max_workers: int):
    """
    有界并发执行任务（fail-fast）

    - 同时最多 in-flight = max_workers
    - 任意任务失败 → 停止提交新任务，尽量取消未开始任务，并抛出异常
    """
    if not tasks:
        return []

    max_workers = max(1, int(max_workers))
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {}
        tasks_iter = iter(tasks)

        for _ in range(min(max_workers, len(tasks))):
            task = next(tasks_iter)
            pending[executor.submit(task_fn, task)] = task

        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future, None)
                try:
                    results.append(future.result())
                except Exception:
                    # 尽量取消未开始的任务
                    for f in pending:
                        f.cancel()
                    raise

                try:
                    next_task = next(tasks_iter)
                except StopIteration:
                    continue
                pending[executor.submit(task_fn, next_task)] = next_task

    return results


def run_collect_tasks(tasks: list, task_fn, max_workers: int):
    """有界并发执行任务（收集全部结果，不 fail-fast）"""
    if not tasks:
        return [], []

    max_workers = max(1, int(max_workers))
    successes = []
    failures = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {}
        tasks_iter = iter(tasks)

        for _ in range(min(max_workers, len(tasks))):
            task = next(tasks_iter)
            pending[executor.submit(task_fn, task)] = task

        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                task = pending.pop(future, None)
                try:
                    successes.append(future.result())
                except Exception as e:
                    failures.append((task, str(e)))

                try:
                    next_task = next(tasks_iter)
                except StopIteration:
                    continue
                pending[executor.submit(task_fn, next_task)] = next_task

    return successes, failures


def _generate_video_direct(
    *,
    project_dir: Path,
    rate_limiter,
    prompt: str,
    resource_id: str,
    storyboard_path: Path,
    aspect_ratio: str,
    duration_seconds: str,
) -> Path:
    """回退直连生成视频。"""
    generator = MediaGenerator(project_dir, rate_limiter=rate_limiter)
    output_path, _, _, _ = generator.generate_video(
        prompt=prompt,
        resource_type="videos",
        resource_id=resource_id,
        start_image=storyboard_path,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
    )
    return output_path


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
# Episode 视频生成（每个场景独立生成）
# ============================================================================

def generate_episode_video(
    project_name: str,
    script_filename: str,
    episode: int,
    resume: bool = False,
    max_workers: int = 1
) -> Path:
    """
    为指定 episode 生成视频

    每个场景独立生成视频，使用分镜图作为起始帧，
    最后用 ffmpeg 拼接成完整视频。

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        episode: 集数编号
        resume: 是否从上次中断处继续

    Returns:
        最终视频路径
    """
    pm = ProjectManager()
    project_dir = pm.get_project_path(project_name)
    rate_limiter = get_shared_rate_limiter()
    queue_worker_online = is_worker_online()

    # 加载剧本和项目配置
    script = pm.load_script(project_name, script_filename)
    project_data = None
    if pm.project_exists(project_name):
        try:
            project_data = pm.load_project(project_name)
        except Exception:
            pass

    # 获取内容模式和画面比例
    content_mode = script.get('content_mode', 'narration')
    video_aspect_ratio = get_aspect_ratio(project_data, 'video')

    # 根据内容模式选择数据源
    all_items, id_field, _, _ = get_items_from_script(script)

    # 筛选指定 episode 的场景/片段
    episode_items = [
        s for s in all_items
        if s.get('episode', 1) == episode
    ]

    if not episode_items:
        raise ValueError(f"未找到第 {episode} 集的场景/片段")

    item_type = "片段" if content_mode == 'narration' else "场景"
    print(f"📋 第 {episode} 集共 {len(episode_items)} 个{item_type}")
    print(f"📐 视频画面比例: {video_aspect_ratio}")
    print("🧵 任务模式: 队列入队并等待" if queue_worker_online else "🧵 任务模式: 直连生成（worker 离线）")

    # 加载或初始化 checkpoint
    completed_scenes = []
    started_at = datetime.now().isoformat()

    if resume:
        checkpoint = load_checkpoint(project_dir, episode)
        if checkpoint:
            completed_scenes = checkpoint.get('completed_scenes', [])
            started_at = checkpoint.get('started_at', started_at)
            print(f"🔄 从 checkpoint 恢复，已完成 {len(completed_scenes)} 个场景")
        else:
            print("⚠️  未找到 checkpoint，从头开始")

    # 确保 videos 目录存在
    videos_dir = project_dir / 'videos'
    videos_dir.mkdir(parents=True, exist_ok=True)

    # 生成每个场景/片段的视频
    ordered_video_paths: list[Optional[Path]] = [None] * len(episode_items)
    tasks = []

    # 默认时长：说书模式 4 秒，剧集动画模式 8 秒
    default_duration = 4 if content_mode == 'narration' else 8

    script_update_lock = threading.Lock()
    checkpoint_lock = threading.Lock()

    for idx, item in enumerate(episode_items):
        item_id = item.get(id_field, item.get('scene_id', f'item_{idx}'))
        video_output = videos_dir / f"scene_{item_id}.mp4"

        # 检查是否已完成
        if item_id in completed_scenes:
            if video_output.exists():
                print(f"  [{idx + 1}/{len(episode_items)}] {item_type} {item_id} ✓ 已完成")
                ordered_video_paths[idx] = video_output
                continue
            else:
                # 标记为完成但文件不存在，需要重新生成
                completed_scenes.remove(item_id)

        print(f"  [{idx + 1}/{len(episode_items)}] {item_type} {item_id}")

        # 检查分镜图
        storyboard_image = item.get('generated_assets', {}).get('storyboard_image')
        if not storyboard_image:
            print(f"    ⚠️  {item_type} {item_id} 没有分镜图，跳过")
            continue

        storyboard_path = project_dir / storyboard_image
        if not storyboard_path.exists():
            print(f"    ⚠️  分镜图不存在: {storyboard_path}，跳过")
            continue

        # 直接使用 video_prompt 字段
        prompt = get_video_prompt(item)
        duration = item.get('duration_seconds', default_duration)
        duration_str = validate_duration(duration)

        tasks.append({
            "order_index": idx,
            "item_id": item_id,
            "storyboard_path": storyboard_path,
            "prompt": prompt,
            "duration_str": duration_str,
        })

    def generate_single_item(task: dict) -> tuple[int, Path]:
        item_id = task["item_id"]
        storyboard_path = task["storyboard_path"]
        prompt = task["prompt"]
        duration_str = task["duration_str"]

        print(f"    🎥 生成视频（{duration_str}秒）... {item_id}")

        if queue_worker_online:
            try:
                queued = enqueue_and_wait(
                    project_name=project_name,
                    task_type="video",
                    media_type="video",
                    resource_id=item_id,
                    payload={
                        "prompt": prompt,
                        "script_file": script_filename,
                        "duration_seconds": int(duration_str),
                    },
                    script_file=script_filename,
                    source="skill",
                )
                result = queued.get("result") or {}
                relative_path = result.get("file_path") or f"videos/scene_{item_id}.mp4"
                video_output = project_dir / relative_path
            except WorkerOfflineError:
                video_output = _generate_video_direct(
                    project_dir=project_dir,
                    rate_limiter=rate_limiter,
                    prompt=prompt,
                    resource_id=item_id,
                    storyboard_path=storyboard_path,
                    aspect_ratio=video_aspect_ratio,
                    duration_seconds=duration_str,
                )
                relative_path = f"videos/scene_{item_id}.mp4"
                with script_update_lock:
                    pm.update_scene_asset(
                        project_name, script_filename,
                        item_id, 'video_clip', relative_path
                    )
            except TaskFailedError as exc:
                raise RuntimeError(f"队列任务失败: {exc}") from exc
        else:
            video_output = _generate_video_direct(
                project_dir=project_dir,
                rate_limiter=rate_limiter,
                prompt=prompt,
                resource_id=item_id,
                storyboard_path=storyboard_path,
                aspect_ratio=video_aspect_ratio,
                duration_seconds=duration_str,
            )
            relative_path = f"videos/scene_{item_id}.mp4"
            with script_update_lock:
                pm.update_scene_asset(
                    project_name, script_filename,
                    item_id, 'video_clip', relative_path
                )

        # 保存 checkpoint（线程安全）
        with checkpoint_lock:
            completed_scenes.append(item_id)
            save_checkpoint(project_dir, episode, completed_scenes, started_at)

        print(f"    ✅ 完成: {video_output.name}")
        return task["order_index"], video_output

    results, failures = run_collect_tasks(tasks, generate_single_item, max_workers=max_workers)
    for order_index, output_path in results:
        ordered_video_paths[order_index] = output_path

    if failures:
        print(f"\n⚠️  {len(failures)} 个{item_type}生成失败:")
        for task, error in failures:
            task_id = task.get("item_id") if isinstance(task, dict) else str(task)
            print(f"   - {task_id}: {error}")
        print("    💡 使用 --resume 参数可从此处继续")
        raise RuntimeError(f"{len(failures)} 个{item_type}生成失败")

    scene_videos = [p for p in ordered_video_paths if p is not None]
    if not scene_videos:
        raise RuntimeError("没有生成任何视频片段")

    # 拼接所有场景视频
    final_output = project_dir / 'output' / f'episode_{episode:02d}.mp4'

    if len(scene_videos) > 1:
        print(f"\n🔧 拼接 {len(scene_videos)} 个场景视频...")
        concatenate_videos(scene_videos, final_output)
    else:
        import shutil
        final_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(scene_videos[0], final_output)
        print(f"✅ 视频已保存: {final_output}")

    # 清除 checkpoint
    clear_checkpoint(project_dir, episode)

    print(f"\n🎉 第 {episode} 集视频生成完成: {final_output}")
    return final_output


# ============================================================================
# 单场景生成
# ============================================================================

def generate_scene_video(
    project_name: str,
    script_filename: str,
    scene_id: str
) -> Path:
    """
    生成单个场景/片段的视频

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        scene_id: 场景/片段 ID

    Returns:
        生成的视频路径
    """
    pm = ProjectManager()
    project_dir = pm.get_project_path(project_name)

    # 加载剧本和项目配置
    script = pm.load_script(project_name, script_filename)
    project_data = None
    if pm.project_exists(project_name):
        try:
            project_data = pm.load_project(project_name)
        except Exception:
            pass

    # 获取内容模式和画面比例
    content_mode = script.get('content_mode', 'narration')
    video_aspect_ratio = get_aspect_ratio(project_data, 'video')
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

    queue_worker_online = is_worker_online()
    rate_limiter = get_shared_rate_limiter()

    print(f"🎬 正在生成视频: 场景/片段 {scene_id}")
    print(f"   画面比例: {video_aspect_ratio}")
    print("   预计等待时间: 1-6 分钟")
    print("   任务模式: 队列入队并等待" if queue_worker_online else "   任务模式: 直连生成（worker 离线）")

    if queue_worker_online:
        try:
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
        except WorkerOfflineError:
            output_path = _generate_video_direct(
                project_dir=project_dir,
                rate_limiter=rate_limiter,
                prompt=prompt,
                resource_id=scene_id,
                storyboard_path=storyboard_path,
                aspect_ratio=video_aspect_ratio,
                duration_seconds=duration_str,
            )
            relative_path = f"videos/scene_{scene_id}.mp4"
            pm.update_scene_asset(project_name, script_filename, scene_id, 'video_clip', relative_path)
        except TaskFailedError as exc:
            raise RuntimeError(f"队列任务失败: {exc}") from exc
    else:
        output_path = _generate_video_direct(
            project_dir=project_dir,
            rate_limiter=rate_limiter,
            prompt=prompt,
            resource_id=scene_id,
            storyboard_path=storyboard_path,
            aspect_ratio=video_aspect_ratio,
            duration_seconds=duration_str,
        )
        relative_path = f"videos/scene_{scene_id}.mp4"
        pm.update_scene_asset(project_name, script_filename, scene_id, 'video_clip', relative_path)

    print(f"✅ 视频已保存: {output_path}")

    if not queue_worker_online:
        print(f"✅ 剧本已更新")

    return output_path


def generate_all_videos(project_name: str, script_filename: str, max_workers: int = 1) -> list:
    """
    生成所有待处理场景的视频（独立模式）

    Returns:
        生成的视频路径列表
    """
    pm = ProjectManager()
    project_dir = pm.get_project_path(project_name)
    rate_limiter = get_shared_rate_limiter()
    queue_worker_online = is_worker_online()

    # 加载剧本和项目配置
    script = pm.load_script(project_name, script_filename)
    project_data = None
    if pm.project_exists(project_name):
        try:
            project_data = pm.load_project(project_name)
        except Exception:
            pass

    content_mode = script.get('content_mode', 'narration')
    video_aspect_ratio = get_aspect_ratio(project_data, 'video')
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
    print("🧵 任务模式: 队列入队并等待" if queue_worker_online else "🧵 任务模式: 直连生成（worker 离线）")

    # 默认时长：说书模式 4 秒，剧集动画模式 8 秒
    default_duration = 4 if content_mode == 'narration' else 8

    tasks = []
    for item in pending_items:
        item_id = item.get(id_field) or item.get('scene_id') or item.get('segment_id')
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

        tasks.append({
            "item_id": item_id,
            "storyboard_path": storyboard_path,
            "prompt": prompt,
            "duration_str": duration_str,
        })

    if not tasks:
        print("⚠️  没有任何可生成的视频任务（可能缺少分镜图或 prompt）")
        return []

    script_update_lock = threading.Lock()

    def generate_single_item(task: dict) -> Path:
        item_id = task["item_id"]
        storyboard_path = task["storyboard_path"]
        prompt = task["prompt"]
        duration_str = task["duration_str"]

        print(f"🎥 生成视频（{duration_str}秒）... {item_id}")
        if queue_worker_online:
            try:
                queued = enqueue_and_wait(
                    project_name=project_name,
                    task_type="video",
                    media_type="video",
                    resource_id=item_id,
                    payload={
                        "prompt": prompt,
                        "script_file": script_filename,
                        "duration_seconds": int(duration_str),
                    },
                    script_file=script_filename,
                    source="skill",
                )
                result = queued.get("result") or {}
                relative_path = result.get("file_path") or f"videos/scene_{item_id}.mp4"
                output_path = project_dir / relative_path
            except WorkerOfflineError:
                output_path = _generate_video_direct(
                    project_dir=project_dir,
                    rate_limiter=rate_limiter,
                    prompt=prompt,
                    resource_id=item_id,
                    storyboard_path=storyboard_path,
                    aspect_ratio=video_aspect_ratio,
                    duration_seconds=duration_str,
                )
                relative_path = f"videos/scene_{item_id}.mp4"
                with script_update_lock:
                    pm.update_scene_asset(project_name, script_filename, item_id, 'video_clip', relative_path)
            except TaskFailedError as exc:
                raise RuntimeError(f"队列任务失败: {exc}") from exc
        else:
            output_path = _generate_video_direct(
                project_dir=project_dir,
                rate_limiter=rate_limiter,
                prompt=prompt,
                resource_id=item_id,
                storyboard_path=storyboard_path,
                aspect_ratio=video_aspect_ratio,
                duration_seconds=duration_str,
            )
            relative_path = f"videos/scene_{item_id}.mp4"
            with script_update_lock:
                pm.update_scene_asset(project_name, script_filename, item_id, 'video_clip', relative_path)

        print(f"✅ 完成: {output_path.name}")
        return output_path

    successes, failures = run_collect_tasks(tasks, generate_single_item, max_workers=max_workers)

    if failures:
        print(f"\n⚠️  {len(failures)} 个{item_type}生成失败:")
        for task, error in failures:
            item_id = task.get("item_id") if isinstance(task, dict) else str(task)
            print(f"   - {item_id}: {error}")

    print(f"\n🎉 批量视频生成完成，共 {len(successes)} 个")
    return successes


def generate_selected_videos(
    project_name: str,
    script_filename: str,
    scene_ids: list,
    resume: bool = False,
    max_workers: int = 1
) -> list:
    """
    生成指定的多个场景视频

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        scene_ids: 场景 ID 列表
        resume: 是否从断点续传

    Returns:
        生成的视频路径列表
    """
    import hashlib

    pm = ProjectManager()
    project_dir = pm.get_project_path(project_name)
    rate_limiter = get_shared_rate_limiter()
    queue_worker_online = is_worker_online()

    # 加载剧本和项目配置
    script = pm.load_script(project_name, script_filename)
    project_data = None
    if pm.project_exists(project_name):
        try:
            project_data = pm.load_project(project_name)
        except Exception:
            pass

    # 获取内容模式和画面比例
    content_mode = script.get('content_mode', 'narration')
    video_aspect_ratio = get_aspect_ratio(project_data, 'video')
    all_items, id_field, _, _ = get_items_from_script(script)

    # 筛选指定的场景
    selected_items = []
    for scene_id in scene_ids:
        found = False
        for item in all_items:
            if item.get(id_field) == scene_id or item.get('scene_id') == scene_id:
                selected_items.append(item)
                found = True
                break
        if not found:
            print(f"⚠️  场景/片段 '{scene_id}' 不存在，跳过")

    if not selected_items:
        raise ValueError("没有找到任何有效的场景/片段")

    item_type = "片段" if content_mode == 'narration' else "场景"
    print(f"📋 共选择 {len(selected_items)} 个{item_type}")
    print(f"📐 视频画面比例: {video_aspect_ratio}")
    print("🧵 任务模式: 队列入队并等待" if queue_worker_online else "🧵 任务模式: 直连生成（worker 离线）")

    # Checkpoint 管理（使用场景列表的 hash 作为标识）
    scenes_hash = hashlib.md5(','.join(scene_ids).encode()).hexdigest()[:8]
    checkpoint_path = project_dir / 'videos' / f'.checkpoint_selected_{scenes_hash}.json'

    completed_scenes = []
    started_at = datetime.now().isoformat()

    if resume and checkpoint_path.exists():
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            checkpoint = json.load(f)
            completed_scenes = checkpoint.get('completed_scenes', [])
            started_at = checkpoint.get('started_at', started_at)
            print(f"🔄 从 checkpoint 恢复，已完成 {len(completed_scenes)} 个场景")

    # 确保 videos 目录存在
    videos_dir = project_dir / 'videos'
    videos_dir.mkdir(parents=True, exist_ok=True)

    # 默认时长
    default_duration = 4 if content_mode == 'narration' else 8

    ordered_results: list[Optional[Path]] = [None] * len(selected_items)
    tasks = []

    script_update_lock = threading.Lock()
    checkpoint_lock = threading.Lock()

    def save_selected_checkpoint():
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump({
                "scene_ids": scene_ids,
                "completed_scenes": completed_scenes,
                "started_at": started_at,
                "updated_at": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)

    for idx, item in enumerate(selected_items):
        item_id = item.get(id_field, item.get('scene_id', f'item_{idx}'))
        video_output = videos_dir / f"scene_{item_id}.mp4"

        # 检查是否已完成
        if item_id in completed_scenes:
            if video_output.exists():
                print(f"  [{idx + 1}/{len(selected_items)}] {item_type} {item_id} ✓ 已完成")
                ordered_results[idx] = video_output
                continue
            else:
                completed_scenes.remove(item_id)

        print(f"  [{idx + 1}/{len(selected_items)}] {item_type} {item_id}")

        # 检查分镜图
        storyboard_image = item.get('generated_assets', {}).get('storyboard_image')
        if not storyboard_image:
            print(f"    ⚠️  {item_type} {item_id} 没有分镜图，跳过")
            continue

        storyboard_path = project_dir / storyboard_image
        if not storyboard_path.exists():
            print(f"    ⚠️  分镜图不存在: {storyboard_path}，跳过")
            continue

        prompt = get_video_prompt(item)
        duration = item.get('duration_seconds', default_duration)
        duration_str = validate_duration(duration)

        tasks.append({
            "order_index": idx,
            "item_id": item_id,
            "storyboard_path": storyboard_path,
            "prompt": prompt,
            "duration_str": duration_str,
        })

    def generate_single_item(task: dict) -> tuple[int, Path]:
        item_id = task["item_id"]
        storyboard_path = task["storyboard_path"]
        prompt = task["prompt"]
        duration_str = task["duration_str"]

        print(f"    🎥 生成视频（{duration_str}秒）... {item_id}")
        if queue_worker_online:
            try:
                queued = enqueue_and_wait(
                    project_name=project_name,
                    task_type="video",
                    media_type="video",
                    resource_id=item_id,
                    payload={
                        "prompt": prompt,
                        "script_file": script_filename,
                        "duration_seconds": int(duration_str),
                    },
                    script_file=script_filename,
                    source="skill",
                )
                result = queued.get("result") or {}
                relative_path = result.get("file_path") or f"videos/scene_{item_id}.mp4"
                video_output = project_dir / relative_path
            except WorkerOfflineError:
                video_output = _generate_video_direct(
                    project_dir=project_dir,
                    rate_limiter=rate_limiter,
                    prompt=prompt,
                    resource_id=item_id,
                    storyboard_path=storyboard_path,
                    aspect_ratio=video_aspect_ratio,
                    duration_seconds=duration_str,
                )
                relative_path = f"videos/scene_{item_id}.mp4"
                with script_update_lock:
                    pm.update_scene_asset(
                        project_name, script_filename,
                        item_id, 'video_clip', relative_path
                    )
            except TaskFailedError as exc:
                raise RuntimeError(f"队列任务失败: {exc}") from exc
        else:
            video_output = _generate_video_direct(
                project_dir=project_dir,
                rate_limiter=rate_limiter,
                prompt=prompt,
                resource_id=item_id,
                storyboard_path=storyboard_path,
                aspect_ratio=video_aspect_ratio,
                duration_seconds=duration_str,
            )
            relative_path = f"videos/scene_{item_id}.mp4"
            with script_update_lock:
                pm.update_scene_asset(
                    project_name, script_filename,
                    item_id, 'video_clip', relative_path
                )

        with checkpoint_lock:
            completed_scenes.append(item_id)
            save_selected_checkpoint()

        print(f"    ✅ 完成: {video_output.name}")
        return task["order_index"], video_output

    results, failures = run_collect_tasks(tasks, generate_single_item, max_workers=max_workers)
    for order_index, output_path in results:
        ordered_results[order_index] = output_path

    final_results = [p for p in ordered_results if p is not None]

    if failures:
        print(f"\n⚠️  {len(failures)} 个{item_type}生成失败:")
        for task, error in failures:
            task_id = task.get("item_id") if isinstance(task, dict) else str(task)
            print(f"   - {task_id}: {error}")
        print("    💡 使用 --resume 参数可从此处继续")
        raise RuntimeError(f"{len(failures)} 个{item_type}生成失败")

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
  python generate_video.py my_novel script.json --episode 1

  # 断点续传
  python generate_video.py my_novel script.json --episode 1 --resume

  # 单场景模式
  python generate_video.py my_novel script.json --scene E1S1

  # 批量自选模式
  python generate_video.py my_novel script.json --scenes E1S01,E1S05,E1S10

  # 批量模式（独立生成）
  python generate_video.py my_novel script.json --all
        """
    )
    parser.add_argument('project', help='项目名称')
    parser.add_argument('script', help='剧本文件名')

    # 模式选择
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--scene', help='指定场景 ID（单场景模式）')
    mode_group.add_argument('--scenes', help='指定多个场景 ID（逗号分隔），如: E1S01,E1S05,E1S10')
    mode_group.add_argument('--all', action='store_true', help='生成所有待处理场景（独立模式）')
    mode_group.add_argument('--episode', type=int, help='按 episode 生成并拼接（推荐）')

    # 其他选项
    parser.add_argument('--resume', action='store_true', help='从上次中断处继续')
    parser.add_argument(
        '--max-workers',
        type=int,
        default=get_default_max_workers(),
        help='视频生成最大并发数（默认来自 VIDEO_MAX_WORKERS，最小 1）'
    )

    args = parser.parse_args()

    try:
        if args.scene:
            generate_scene_video(args.project, args.script, args.scene)
        elif args.scenes:
            scene_ids = parse_scene_ids(args.scenes)
            generate_selected_videos(
                args.project, args.script,
                scene_ids,
                resume=args.resume,
                max_workers=args.max_workers
            )
        elif args.all:
            generate_all_videos(args.project, args.script, max_workers=args.max_workers)
        elif args.episode:
            generate_episode_video(
                args.project, args.script,
                args.episode,
                resume=args.resume,
                max_workers=args.max_workers
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
