#!/usr/bin/env python3
"""
Storyboard Generator - 使用 Gemini API 生成分镜图

两种模式统一使用直接生成方式，无需多宫格中间步骤：
- narration 模式（说书+画面）：直接生成 9:16 竖屏分镜图
- drama 模式（剧集动画）：直接生成 16:9 横屏分镜图

Usage:
    # narration 模式：直接生成分镜图（默认）
    python generate_storyboard.py <project_name> <script_file>
    python generate_storyboard.py <project_name> <script_file> --segment-ids E1S01 E1S02

    # drama 模式：直接生成分镜图
    python generate_storyboard.py <project_name> <script_file>
    python generate_storyboard.py <project_name> <script_file> --scene-ids E1S01 E1S02
"""

import argparse
import sys
import os
import json
import threading
from pathlib import Path
from typing import List, Tuple, Optional, Callable, TypeVar, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.generation_queue_client import (
    TaskFailedError,
    WorkerOfflineError,
    enqueue_and_wait,
    is_worker_online,
)
from lib.gemini_client import GeminiClient, RateLimiter
from lib.media_generator import MediaGenerator
from lib.project_manager import ProjectManager
from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt
)


# ==================== 并行处理工具类 ====================

T = TypeVar('T')


class ParallelExecutor:
    """并行任务执行器"""

    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        self._lock = threading.Lock()

    def execute(
        self,
        tasks: List[Any],
        task_fn: Callable[[Any], T],
        desc: str = "处理中",
        task_id_fn: Optional[Callable[[Any], str]] = None
    ) -> Tuple[List[T], List[Tuple[Any, str]]]:
        """
        并行执行任务列表

        Args:
            tasks: 任务列表
            task_fn: 任务处理函数
            desc: 进度描述
            task_id_fn: 可选，从任务获取 ID 的函数（用于日志）

        Returns:
            (成功结果列表, 失败列表[(task, error)])
        """
        results = []
        failures = []
        completed = 0
        total = len(tasks)

        if total == 0:
            return results, failures

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {executor.submit(task_fn, task): task for task in tasks}

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                with self._lock:
                    completed += 1
                    task_id = task_id_fn(task) if task_id_fn else str(completed)

                try:
                    result = future.result()
                    results.append(result)
                    print(f"✅ [{completed}/{total}] {desc}: {task_id} 完成")
                except Exception as e:
                    failures.append((task, str(e)))
                    print(f"❌ [{completed}/{total}] {desc}: {task_id} 失败 - {e}")

        return results, failures


class FailureRecorder:
    """失败记录管理器（线程安全）"""

    def __init__(self, output_dir: Path):
        self.output_path = output_dir / "generation_failures.json"
        self.failures: List[dict] = []
        self._lock = threading.Lock()

    def record_failure(
        self,
        scene_id: str,
        failure_type: str,  # "scene"
        error: str,
        attempts: int = 3,
        **extra
    ):
        """记录一次失败"""
        with self._lock:
            self.failures.append({
                "scene_id": scene_id,
                "type": failure_type,
                "error": error,
                "attempts": attempts,
                "timestamp": datetime.now().isoformat(),
                **extra
            })

    def save(self):
        """保存失败记录到文件"""
        if not self.failures:
            return

        with self._lock:
            data = {
                "generated_at": datetime.now().isoformat(),
                "total_failures": len(self.failures),
                "failures": self.failures
            }

            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"\n⚠️  失败记录已保存: {self.output_path}")

    def get_failed_scene_ids(self) -> List[str]:
        """获取所有失败的场景 ID（用于重新生成）"""
        return [f["scene_id"] for f in self.failures if f["type"] == "scene"]


# ==================== 布局和 Prompt 构建函数 ====================


def get_image_prompt(item: dict) -> str:
    """
    获取分镜图生成 Prompt

    Args:
        item: 片段/场景字典

    Returns:
        image_prompt 字符串
    """
    prompt = item.get('image_prompt', '')
    if not prompt:
        raise ValueError(f"片段/场景缺少 image_prompt 字段: {item.get('segment_id') or item.get('scene_id')}")
    return prompt


def get_aspect_ratio(project_data: dict, asset_type: str) -> str:
    """
    根据项目配置获取画面比例（通过 API 参数传递，不写入 prompt）

    Args:
        project_data: project.json 数据
        asset_type: "design" | "storyboard" | "video"

    Returns:
        画面比例字符串，如 "16:9" 或 "9:16"
    """
    content_mode = project_data.get('content_mode', 'narration') if project_data else 'narration'

    # 默认配置：说书模式使用竖屏，剧集动画模式使用横屏
    defaults = {
        "design": "16:9",      # 人物/线索设计图始终横屏
        "storyboard": "9:16" if content_mode == 'narration' else "16:9",
        "video": "9:16" if content_mode == 'narration' else "16:9"
    }

    # 允许 project.json 中的 aspect_ratio 覆盖默认值
    custom = project_data.get('aspect_ratio', {}) if project_data else {}
    return custom.get(asset_type, defaults[asset_type])


def get_items_from_script(script: dict) -> tuple:
    """
    根据内容模式获取场景/片段列表和 ID 字段名

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


def build_direct_scene_prompt(
    segment: dict,
    characters: dict = None,
    clues: dict = None,
    style: str = "",
    style_description: str = "",
    id_field: str = 'segment_id',
    char_field: str = 'characters_in_segment',
    clue_field: str = 'clues_in_segment',
    content_mode: str = 'narration',
) -> str:
    """
    构建直接生成场景图的 prompt（通用，适用于 narration 和 drama 模式）

    支持结构化 prompt 格式：如果 image_prompt 是 dict，则转换为 YAML 格式。

    Args:
        segment: 片段/场景字典
        characters: 人物字典（保留参数以兼容调用）
        clues: 线索字典（保留参数以兼容调用）
        style: 项目风格（用于 YAML 转换）
        style_description: AI 分析的风格描述
        id_field: ID 字段名
        char_field: 人物字段名（保留参数以兼容调用）
        clue_field: 线索字段名（保留参数以兼容调用）
        content_mode: 内容模式（'narration' 或 'drama'）

    Returns:
        image_prompt 字符串（可能是 YAML 格式或普通字符串）
    """
    image_prompt = segment.get('image_prompt', '')
    if not image_prompt:
        raise ValueError(f"片段/场景 {segment[id_field]} 缺少 image_prompt 字段")

    # 构建风格前缀
    style_parts = []
    if style:
        style_parts.append(f"Style: {style}")
    if style_description:
        style_parts.append(f"Visual style: {style_description}")
    style_prefix = '\n'.join(style_parts) + '\n\n' if style_parts else ''

    # narration 模式追加竖屏构图后缀，drama 模式通过 API aspect_ratio 参数控制
    composition_suffix = "\n竖屏构图。" if content_mode == 'narration' else ""

    # 检测是否为结构化格式
    if is_structured_image_prompt(image_prompt):
        # 转换为 YAML 格式
        yaml_prompt = image_prompt_to_yaml(image_prompt, style)
        return f"{style_prefix}{yaml_prompt}{composition_suffix}"

    return f"{style_prefix}{image_prompt}{' 竖屏构图。' if content_mode == 'narration' else ''}"


def _generate_storyboard_direct_image(
    *,
    project_dir: Path,
    rate_limiter: Optional[Any],
    prompt: str,
    resource_id: str,
    reference_images: Optional[List[Path]],
    aspect_ratio: str,
) -> Path:
    """回退直连生成分镜图。"""
    generator = MediaGenerator(project_dir, rate_limiter=rate_limiter)
    output_path, _ = generator.generate_image(
        prompt=prompt,
        resource_type="storyboards",
        resource_id=resource_id,
        reference_images=reference_images if reference_images else None,
        aspect_ratio=aspect_ratio
    )
    return output_path


def generate_storyboard_direct(
    project_name: str,
    script_filename: str,
    segment_ids: Optional[List[str]] = None,
    max_workers: int = 10,
    rate_limiter: Optional[Any] = None
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """
    直接生成分镜图（narration 和 drama 模式通用，无需多宫格图）

    仅使用 character_sheet 和 clue_sheet 作为参考图。

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        segment_ids: 可选的片段/场景 ID 列表
        max_workers: 最大并发数
        rate_limiter: 可选的限流器实例

    Returns:
        (成功路径列表, 失败列表) 元组
    """
    pm = ProjectManager()
    script = pm.load_script(project_name, script_filename)
    project_dir = pm.get_project_path(project_name)

    content_mode = script.get('content_mode', 'narration')

    # 加载项目元数据
    project_data = None
    if pm.project_exists(project_name):
        try:
            project_data = pm.load_project(project_name)
            print("📁 已加载项目元数据 (project.json)")
        except Exception as e:
            print(f"⚠️  无法加载项目元数据: {e}")

    # 获取字段配置
    items, id_field, char_field, clue_field = get_items_from_script(script)

    # 筛选需要生成的片段/场景
    if segment_ids:
        segments_to_process = [s for s in items if s[id_field] in segment_ids]
    else:
        # 获取所有没有 storyboard_image 的片段/场景
        segments_to_process = [
            s for s in items
            if not s.get('generated_assets', {}).get('storyboard_image')
        ]

    if not segments_to_process:
        print("✨ 所有片段的分镜图都已生成")
        return [], []

    # 获取人物和线索数据
    characters = project_data.get('characters', {}) if project_data else {}
    clues = project_data.get('clues', {}) if project_data else {}
    style = project_data.get('style', '') if project_data else ''
    style_description = project_data.get('style_description', '') if project_data else ''
    storyboard_aspect_ratio = get_aspect_ratio(project_data, 'storyboard')  # 9:16
    queue_worker_online = is_worker_online()

    print(f"📷 直接生成 {len(segments_to_process)} 个分镜图（无多宫格）...")
    print("🧵 任务模式: 队列入队并等待" if queue_worker_online else "🧵 任务模式: 直连生成（worker 离线）")

    # 使用锁保护剧本更新操作
    script_update_lock = threading.Lock()

    # 创建失败记录器
    recorder = FailureRecorder(project_dir / 'storyboards')

    def generate_single(segment: dict) -> Path:
        segment_id = segment[id_field]

        # 收集参考图：仅 character_sheet 和 clue_sheet
        reference_images = []

        for char_name in segment.get(char_field, []):
            if char_name in characters:
                char_sheet = characters[char_name].get('character_sheet', '')
                if char_sheet:
                    char_path = project_dir / char_sheet
                    if char_path.exists():
                        reference_images.append(char_path)

        for clue_name in segment.get(clue_field, []):
            if clue_name in clues:
                clue_sheet = clues[clue_name].get('clue_sheet', '')
                if clue_sheet:
                    clue_path = project_dir / clue_sheet
                    if clue_path.exists():
                        reference_images.append(clue_path)

        # 构建 prompt（直接生成，无需参考多宫格）
        prompt = build_direct_scene_prompt(
            segment, characters, clues, style, style_description,
            id_field, char_field, clue_field,
            content_mode=content_mode,
        )

        if queue_worker_online:
            try:
                queued = enqueue_and_wait(
                    project_name=project_name,
                    task_type="storyboard",
                    media_type="image",
                    resource_id=str(segment_id),
                    payload={
                        "prompt": prompt,
                        "script_file": script_filename,
                    },
                    script_file=script_filename,
                    source="skill",
                )
                result = queued.get("result") or {}
                relative_path = result.get("file_path") or f"storyboards/scene_{segment_id}.png"
                output_path = project_dir / relative_path
            except WorkerOfflineError:
                output_path = _generate_storyboard_direct_image(
                    project_dir=project_dir,
                    rate_limiter=rate_limiter,
                    prompt=prompt,
                    resource_id=str(segment_id),
                    reference_images=reference_images,
                    aspect_ratio=storyboard_aspect_ratio,
                )
                relative_path = f"storyboards/scene_{segment_id}.png"
                with script_update_lock:
                    pm.update_scene_asset(
                        project_name, script_filename,
                        segment_id, 'storyboard_image', relative_path
                    )
            except TaskFailedError as exc:
                raise RuntimeError(f"队列任务失败: {exc}") from exc
        else:
            output_path = _generate_storyboard_direct_image(
                project_dir=project_dir,
                rate_limiter=rate_limiter,
                prompt=prompt,
                resource_id=str(segment_id),
                reference_images=reference_images,
                aspect_ratio=storyboard_aspect_ratio,
            )
            relative_path = f"storyboards/scene_{segment_id}.png"
            with script_update_lock:
                pm.update_scene_asset(
                    project_name, script_filename,
                    segment_id, 'storyboard_image', relative_path
                )

        return output_path

    # 并行执行
    executor = ParallelExecutor(max_workers=max_workers)
    results, failures = executor.execute(
        segments_to_process,
        generate_single,
        desc="分镜图生成",
        task_id_fn=lambda x: x[id_field]
    )

    # 记录失败
    for segment, error in failures:
        recorder.record_failure(
            scene_id=segment[id_field],
            failure_type="scene",
            error=error,
            attempts=3
        )

    # 保存失败记录
    recorder.save()

    failed = [(seg[id_field], error) for seg, error in failures]

    return results, failed


def main():
    from lib.gemini_client import RateLimiter

    parser = argparse.ArgumentParser(description='生成分镜图')
    parser.add_argument('project', help='项目名称')
    parser.add_argument('script', help='剧本文件名')

    # 辅助参数
    parser.add_argument('--scene-ids', nargs='+', help='指定场景 ID')
    parser.add_argument('--segment-ids', nargs='+', help='指定片段 ID（narration 模式别名）')

    args = parser.parse_args()

    # 初始化限流器
    # 从环境变量读取配置，默认 Gemini 3 Pro Image 限制为 15 RPM
    image_rpm = int(os.environ.get('GEMINI_IMAGE_RPM', 15))
    rate_limiter = RateLimiter({
        "gemini-3-pro-image-preview": image_rpm
    })

    # 从环境变量读取最大并发数，默认 3
    max_workers = int(os.environ.get('STORYBOARD_MAX_WORKERS', 3))

    try:
        # 检测 content_mode
        pm = ProjectManager()
        script = pm.load_script(args.project, args.script)
        content_mode = script.get('content_mode', 'narration')

        print(f"🚀 {content_mode} 模式：直接生成分镜图")

        # 合并 --scene-ids 和 --segment-ids 参数
        segment_ids = args.segment_ids or args.scene_ids

        results, failed = generate_storyboard_direct(
            args.project, args.script,
            segment_ids=segment_ids,
            max_workers=max_workers,
            rate_limiter=rate_limiter
        )
        print(f"\n📊 生成完成: {len(results)} 个分镜图")
        if failed:
            print(f"⚠️  失败: {len(failed)} 个")

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
