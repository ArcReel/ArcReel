#!/usr/bin/env python3
"""
Storyboard Generator - 使用 Gemini API 生成分镜图

支持两种模式：
- narration 模式（说书+画面）：直接生成分镜图，无需多宫格
- drama 模式（剧集动画）：两步流程（多宫格→单独场景图）

Usage:
    # narration 模式：直接生成分镜图（默认）
    python generate_storyboard.py <project_name> <script_file>
    python generate_storyboard.py <project_name> <script_file> --segment-ids E1S01 E1S02

    # drama 模式：两步流程
    # 步骤 1：生成多宫格预览图
    python generate_storyboard.py <project_name> <script_file> --grids --all
    python generate_storyboard.py <project_name> <script_file> --grids --batch 1

    # 步骤 2：生成单独场景图（需要已生成 grids）
    python generate_storyboard.py <project_name> <script_file> --scenes
    python generate_storyboard.py <project_name> <script_file> --scenes --scene-ids E1S01 E1S02
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
        failure_type: str,  # "scene" or "grid"
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
        asset_type: "design" | "grid" | "storyboard" | "video"

    Returns:
        画面比例字符串，如 "16:9" 或 "9:16"
    """
    content_mode = project_data.get('content_mode', 'narration') if project_data else 'narration'

    # 默认配置：说书模式使用竖屏，剧集动画模式使用横屏
    defaults = {
        "design": "16:9",      # 人物/线索设计图始终横屏
        "grid": "16:9",        # 多宫格预览图始终横屏
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


def get_grid_layout(scene_count: int) -> tuple:
    """
    根据场景数量确定宫格布局

    Args:
        scene_count: 场景数量

    Returns:
        (rows, cols, layout_name) 元组
    """
    if scene_count <= 4:
        return (2, 2, "2x2 四宫格")
    else:
        return (2, 3, "2x3 六宫格")


def build_grid_prompt(scenes: List[dict], characters: dict, clues: dict = None, style: str = "", id_field: str = 'scene_id', char_field: str = 'characters_in_scene', clue_field: str = 'clues_in_scene') -> str:
    """
    构建多宫格分镜图生成 prompt

    支持结构化 prompt 格式：如果 image_prompt 是 dict，则转换为 YAML 格式。

    Args:
        scenes: 场景列表
        characters: 人物字典（保留参数以兼容调用）
        clues: 线索字典（保留参数以兼容调用）
        style: 项目整体风格（用于 YAML 转换）
        id_field: 场景ID字段名
        char_field: 人物字段名（保留参数以兼容调用）
        clue_field: 线索字段名（保留参数以兼容调用）

    Returns:
        完整的 prompt 字符串
    """
    scene_count = len(scenes)
    _, _, layout_name = get_grid_layout(scene_count)

    # 构建各宫格描述，支持结构化 image_prompt
    grid_descriptions = []
    for i, scene in enumerate(scenes, 1):
        image_prompt = scene.get('image_prompt', '')
        if not image_prompt:
            raise ValueError(f"场景 {scene[id_field]} 缺少 image_prompt 字段")

        # 检测是否为结构化格式
        if is_structured_image_prompt(image_prompt):
            prompt_content = image_prompt_to_yaml(image_prompt, style)
        else:
            prompt_content = image_prompt

        grid_descriptions.append(f"宫格{i}（{scene[id_field]}）：{prompt_content}")

    prompt = f"""一张 16:9 横屏的多宫格分镜图，包含 {scene_count} 个连续场景。
采用 {layout_name} 布局，每个格子展示一个场景的关键画面。宫格之间用细黑线分隔。

{chr(10).join(grid_descriptions)}

人物必须与提供的参考图完全一致。"""

    return prompt


def build_scene_prompt(scene: dict, characters: dict, grid_position: int, total_in_grid: int, clues: dict = None, style: str = "", id_field: str = 'scene_id', char_field: str = 'characters_in_scene', clue_field: str = 'clues_in_scene') -> str:
    """
    构建单独场景图生成 prompt（从多宫格参考图生成单独场景）

    支持结构化 prompt 格式：如果 image_prompt 是 dict，则转换为 YAML 格式。

    Args:
        scene: 场景字典
        characters: 人物字典（保留参数以兼容调用）
        grid_position: 该场景在多宫格中的位置（从 1 开始）
        total_in_grid: 该多宫格中的场景总数
        clues: 线索字典（保留参数以兼容调用）
        style: 项目整体风格（用于 YAML 转换）
        id_field: 场景ID字段名
        char_field: 人物字段名（保留参数以兼容调用）
        clue_field: 线索字段名（保留参数以兼容调用）

    Returns:
        完整的 prompt 字符串
    """
    image_prompt = scene.get('image_prompt', '')
    if not image_prompt:
        raise ValueError(f"场景 {scene[id_field]} 缺少 image_prompt 字段")

    # 检测是否为结构化格式
    if is_structured_image_prompt(image_prompt):
        # 转换为 YAML 格式
        prompt_content = image_prompt_to_yaml(image_prompt, style)
    else:
        prompt_content = image_prompt

    # 确定宫格位置描述
    _, cols, layout_name = get_grid_layout(total_in_grid)
    row_num = (grid_position - 1) // cols + 1
    col_num = (grid_position - 1) % cols + 1
    position_desc = f"第 {row_num} 行第 {col_num} 列（宫格 {grid_position}）"

    prompt = f"""根据提供的多宫格分镜参考图（{layout_name}），生成其中 {position_desc} 的单独高清场景图。

{prompt_content}

人物必须与提供的参考图完全一致。"""

    return prompt


def build_direct_scene_prompt(
    segment: dict,
    characters: dict = None,
    clues: dict = None,
    style: str = "",
    style_description: str = "",
    id_field: str = 'segment_id',
    char_field: str = 'characters_in_segment',
    clue_field: str = 'clues_in_segment'
) -> str:
    """
    构建直接生成场景图的 prompt（narration 模式，无多宫格参考）

    支持结构化 prompt 格式：如果 image_prompt 是 dict，则转换为 YAML 格式。

    Args:
        segment: 片段字典
        characters: 人物字典（保留参数以兼容调用）
        clues: 线索字典（保留参数以兼容调用）
        style: 项目风格（用于 YAML 转换）
        style_description: AI 分析的风格描述
        id_field: ID 字段名
        char_field: 人物字段名（保留参数以兼容调用）
        clue_field: 线索字段名（保留参数以兼容调用）

    Returns:
        image_prompt 字符串（可能是 YAML 格式或普通字符串）
    """
    image_prompt = segment.get('image_prompt', '')
    if not image_prompt:
        raise ValueError(f"片段 {segment[id_field]} 缺少 image_prompt 字段")

    # 构建风格前缀
    style_parts = []
    if style:
        style_parts.append(f"Style: {style}")
    if style_description:
        style_parts.append(f"Visual style: {style_description}")
    style_prefix = '\n'.join(style_parts) + '\n\n' if style_parts else ''

    # 检测是否为结构化格式
    if is_structured_image_prompt(image_prompt):
        # 转换为 YAML 格式
        yaml_prompt = image_prompt_to_yaml(image_prompt, style)
        return f"{style_prefix}{yaml_prompt}\n竖屏构图。"

    return f"{style_prefix}{image_prompt} 竖屏构图。"


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


def generate_individual_scenes(
    project_name: str,
    script_filename: str,
    scenes: List[dict],
    grid_image_path: Path,
    batch_id: int,
    script: dict,
    max_workers: int = 10,
    rate_limiter: Optional[Any] = None,
    project_data: Optional[dict] = None
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """
    以多宫格图作为参考，并行批量生成单独场景图

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        scenes: 要生成的场景列表
        grid_image_path: 多宫格参考图路径
        batch_id: 批次编号
        script: 完整剧本
        max_workers: 最大并发数
        rate_limiter: 可选的限流器实例
        project_data: 可选的项目元数据（用于获取线索信息）

    Returns:
        (成功路径列表, 失败列表) 元组
    """
    pm = ProjectManager()
    project_dir = pm.get_project_path(project_name)
    total_in_grid = len(scenes)
    queue_worker_online = is_worker_online()

    # 获取字段配置
    _, id_field, char_field, clue_field = get_items_from_script(script)

    # 获取人物和线索数据
    characters = project_data.get('characters', {}) if project_data else script.get('characters', {})
    clues = project_data.get('clues', {}) if project_data else {}

    # 获取项目风格
    style = project_data.get('style', '') if project_data else ''

    # 获取分镜图画面比例（根据内容模式动态决定）
    storyboard_aspect_ratio = get_aspect_ratio(project_data, 'storyboard')

    # 过滤需要生成的场景（跳过已存在的）
    scenes_to_generate = []
    existing_results = []

    for idx, scene in enumerate(scenes, 1):
        scene_id = scene[id_field]
        output_path = project_dir / 'storyboards' / f"scene_{scene_id}.png"
        if output_path.exists():
            print(f"⏭️  场景 {scene_id} 已存在，跳过")
            existing_results.append(output_path)
        else:
            scenes_to_generate.append((idx, scene))

    if not scenes_to_generate:
        return existing_results, []

    print(f"📷 并行生成 {len(scenes_to_generate)} 个场景图...")
    if queue_worker_online:
        print("🧵 任务模式: 队列入队并等待")
    else:
        print("🧵 任务模式: 直连生成（worker 离线）")

    # 使用锁保护剧本更新操作（线程安全）
    script_update_lock = threading.Lock()

    def generate_single_scene(task_data: Tuple[int, dict]) -> Path:
        idx, scene = task_data
        scene_id = scene[id_field]

        # 收集参考图：多宫格图 + 该场景的人物设计图 + 线索设计图
        reference_images = [grid_image_path]

        # 人物参考图
        for char_name in scene.get(char_field, []):
            if char_name in characters:
                char_sheet = characters[char_name].get('character_sheet', '')
                if char_sheet:
                    char_path = project_dir / char_sheet
                    if char_path.exists():
                        reference_images.append(char_path)

        # 线索参考图
        for clue_name in scene.get(clue_field, []):
            if clue_name in clues:
                clue_sheet = clues[clue_name].get('clue_sheet', '')
                if clue_sheet:
                    clue_path = project_dir / clue_sheet
                    if clue_path.exists():
                        reference_images.append(clue_path)

        # 构建 prompt（包含宫格位置信息、线索信息和项目风格）
        prompt = build_scene_prompt(scene, characters, idx, total_in_grid, clues, style, id_field, char_field, clue_field)

        if queue_worker_online:
            try:
                queued = enqueue_and_wait(
                    project_name=project_name,
                    task_type="storyboard",
                    media_type="image",
                    resource_id=str(scene_id),
                    payload={
                        "prompt": prompt,
                        "script_file": script_filename,
                        "extra_reference_images": [str(grid_image_path)],
                    },
                    script_file=script_filename,
                    source="skill",
                )
                result = queued.get("result") or {}
                relative_path = result.get("file_path") or f"storyboards/scene_{scene_id}.png"
                output_path = project_dir / relative_path
            except WorkerOfflineError:
                output_path = _generate_storyboard_direct_image(
                    project_dir=project_dir,
                    rate_limiter=rate_limiter,
                    prompt=prompt,
                    resource_id=str(scene_id),
                    reference_images=reference_images,
                    aspect_ratio=storyboard_aspect_ratio,
                )
                relative_path = f"storyboards/scene_{scene_id}.png"
                with script_update_lock:
                    pm.update_scene_asset(
                        project_name, script_filename,
                        scene_id, 'storyboard_image', relative_path
                    )
            except TaskFailedError as exc:
                raise RuntimeError(f"队列任务失败: {exc}") from exc
        else:
            output_path = _generate_storyboard_direct_image(
                project_dir=project_dir,
                rate_limiter=rate_limiter,
                prompt=prompt,
                resource_id=str(scene_id),
                reference_images=reference_images,
                aspect_ratio=storyboard_aspect_ratio,
            )
            relative_path = f"storyboards/scene_{scene_id}.png"
            with script_update_lock:
                pm.update_scene_asset(
                    project_name, script_filename,
                    scene_id, 'storyboard_image', relative_path
                )

        return output_path

    # 并行执行
    executor = ParallelExecutor(max_workers=max_workers)
    results, failures = executor.execute(
        scenes_to_generate,
        generate_single_scene,
        desc="场景图生成",
        task_id_fn=lambda x: x[1][id_field]
    )

    # 合并已存在的结果
    all_results = existing_results + results

    # 转换失败格式
    failed = [(task[1][id_field], error) for task, error in failures]

    # 汇总报告
    if failed:
        print(f"\n⚠️  {len(failed)} 个场景生成失败:")
        for scene_id, error in failed:
            print(f"   - {scene_id}: {error}")

    return all_results, failed


def generate_storyboard_grid(
    project_name: str,
    script_filename: str,
    scenes: List[dict],
    batch_id: int,
    script: dict,
    rate_limiter: Optional[Any] = None,
    project_data: Optional[dict] = None
) -> Tuple[Path, List[Path], List[Tuple[str, str]]]:
    """
    生成一批场景的多宫格分镜图

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        scenes: 要生成的场景列表
        batch_id: 批次编号
        script: 完整剧本
        rate_limiter: 可选的限流器实例
        project_data: 可选的项目元数据（用于获取线索信息）

    Returns:
        (grid_path, [], failed_scenes) 元组
        注意：现在不再生成单独场景图，返回的第二个元素为空列表
    """
    pm = ProjectManager()
    project_dir = pm.get_project_path(project_name)

    # 获取字段配置
    _, id_field, char_field, clue_field = get_items_from_script(script)

    # 获取人物和线索数据
    characters = project_data.get('characters', {}) if project_data else script.get('characters', {})
    clues = project_data.get('clues', {}) if project_data else {}

    # 收集所有场景中的人物和线索
    all_characters = set()
    all_clues = set()
    for scene in scenes:
        all_characters.update(scene.get(char_field, []))
        all_clues.update(scene.get(clue_field, []))

    reference_images = []

    # 收集人物参考图
    for char_name in all_characters:
        if char_name in characters:
            char_sheet = characters[char_name].get('character_sheet', '')
            if char_sheet:
                char_path = project_dir / char_sheet
                if char_path.exists():
                    reference_images.append(char_path)
                else:
                    print(f"⚠️  人物设计图不存在: {char_path}")
            else:
                print(f"⚠️  人物 '{char_name}' 没有设计图，可能影响一致性")

    # 收集线索参考图
    for clue_name in all_clues:
        if clue_name in clues:
            clue_sheet = clues[clue_name].get('clue_sheet', '')
            if clue_sheet:
                clue_path = project_dir / clue_sheet
                if clue_path.exists():
                    reference_images.append(clue_path)
                else:
                    print(f"⚠️  线索设计图不存在: {clue_path}")

    # 获取项目风格
    style = project_data.get('style', '') if project_data else ''

    # 构建 prompt（包含线索信息和项目风格）
    prompt = build_grid_prompt(scenes, characters, clues, style, id_field, char_field, clue_field)

    queue_worker_online = is_worker_online()
    output_path = project_dir / 'storyboards' / f"grid_{batch_id:03d}.png"

    scene_ids = [s[id_field] for s in scenes]
    print(f"🎬 正在生成多宫格分镜图: 批次 {batch_id}")
    print(f"   包含场景: {', '.join(scene_ids)}")
    print("   任务模式: 队列入队并等待" if queue_worker_online else "   任务模式: 直连生成（worker 离线）")
    if all_characters:
        print(f"   参考人物: {', '.join(all_characters)}")
    if all_clues:
        print(f"   参考线索: {', '.join(all_clues)}")
    print(f"\n📝 Prompt:\n{prompt}\n")

    if queue_worker_online:
        try:
            queued = enqueue_and_wait(
                project_name=project_name,
                task_type="storyboard_grid",
                media_type="image",
                resource_id=f"batch_{batch_id}",
                payload={
                    "script_file": script_filename,
                    "batch_id": int(batch_id),
                    "scene_ids": [str(scene_id) for scene_id in scene_ids],
                },
                script_file=script_filename,
                source="skill",
            )
            result = queued.get("result") or {}
            relative_path = result.get("file_path") or f"storyboards/grid_{batch_id:03d}.png"
            output_path = project_dir / relative_path
        except WorkerOfflineError:
            client = GeminiClient(rate_limiter=rate_limiter)
            client.generate_image(
                prompt=prompt,
                reference_images=reference_images if reference_images else None,
                aspect_ratio="16:9",  # 多宫格分镜图使用横屏
                output_path=output_path
            )
            relative_path = f"storyboards/grid_{batch_id:03d}.png"
            for scene in scenes:
                pm.update_scene_asset(
                    project_name, script_filename,
                    scene[id_field], 'storyboard_grid', relative_path
                )
            print("✅ 剧本已更新 (storyboard_grid)")
        except TaskFailedError as exc:
            raise RuntimeError(f"队列任务失败: {exc}") from exc
    else:
        client = GeminiClient(rate_limiter=rate_limiter)
        client.generate_image(
            prompt=prompt,
            reference_images=reference_images if reference_images else None,
            aspect_ratio="16:9",  # 多宫格分镜图使用横屏
            output_path=output_path
        )
        relative_path = f"storyboards/grid_{batch_id:03d}.png"
        for scene in scenes:
            pm.update_scene_asset(
                project_name, script_filename,
                scene[id_field], 'storyboard_grid', relative_path
            )
        print("✅ 剧本已更新 (storyboard_grid)")

    print(f"✅ 多宫格分镜图已保存: {output_path}")

    # 这一步现在不生成单独场景图
    return output_path, [], []


def generate_all_grids(
    project_name: str,
    script_filename: str,
    max_workers: int = 10,
    rate_limiter: Optional[Any] = None
) -> Tuple[List[Path], List[Path], List[Tuple[str, str]]]:
    """
    生成所有待处理场景的多宫格分镜图（并行处理）

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        max_workers: 最大并发数
        rate_limiter: 可选的限流器实例

    Returns:
        (grid_paths, [], failed_scenes) 元组
    """
    pm = ProjectManager()

    # 检查待处理场景：没有 storyboard_grid 的场景
    script = pm.load_script(project_name, script_filename)
    project_dir = pm.get_project_path(project_name)

    # 支持 segments（说书模式）和 scenes（剧集动画模式）
    all_items, _, _, _ = get_items_from_script(script)

    # 尝试加载项目级元数据（如果存在）
    project_data = None
    if pm.project_exists(project_name):
        try:
            project_data = pm.load_project(project_name)
            print("📁 已加载项目元数据 (project.json)")
        except Exception as e:
            print(f"⚠️  无法加载项目元数据: {e}")

    # 按批次处理（每批最多 6 个场景/片段）
    batch_size = 6
    batch_tasks = []

    # 遍历所有场景/片段批次，而不是仅遍历待处理的
    # 这样可以确保 batch_id 与全局索引对应（1-6 -> Batch 1, 7-12 -> Batch 2）
    for i in range(0, len(all_items), batch_size):
        full_batch = all_items[i:i + batch_size]
        batch_id = (i // batch_size) + 1

        # 检查该批次是否含有未生成的场景/片段
        pending_in_batch = [
            s for s in full_batch
            if not s.get('generated_assets', {}).get('storyboard_grid')
        ]

        if pending_in_batch:
            # 如果有任意场景/片段缺失 grid，则重新生成整个批次
            # 这样保证 grid 布局完整（2x3）且内容一致
            batch_tasks.append((batch_id, full_batch))

    if not batch_tasks:
        print("✨ 所有场景的多宫格分镜图都已生成")
        return [], [], []

    print(f"📋 共 {len(batch_tasks)} 个批次待生成，准备并行处理")

    # 创建失败记录器
    recorder = FailureRecorder(project_dir / 'storyboards')

    # 定义批次处理函数
    def process_batch(batch_data: Tuple[int, List[dict]]) -> Tuple[Path, List[Path], List[Tuple[str, str]]]:
        batch_id, batch_scenes = batch_data
        return generate_storyboard_grid(
            project_name, script_filename,
            batch_scenes, batch_id, script,
            rate_limiter=rate_limiter,
            project_data=project_data
        )

    # 并行执行所有批次
    executor = ParallelExecutor(max_workers=max_workers)

    results, failures = executor.execute(
        batch_tasks,
        process_batch,
        desc="多宫格生成",
        task_id_fn=lambda x: f"批次{x[0]}"
    )

    # 获取字段配置
    items, id_field, char_field, clue_field = get_items_from_script(script)

    # 记录失败
    for (batch_id, batch_scenes), error in failures:
        scene_ids = [s[id_field] for s in batch_scenes]
        recorder.record_failure(
            scene_id=f"batch_{batch_id}",
            failure_type="grid",
            error=error,
            attempts=3,
            scenes_in_batch=scene_ids
        )

    # 整理结果
    grid_results = []
    all_failed = []

    for result in results:
        grid_path, _, failed = result
        grid_results.append(grid_path)
        all_failed.extend(failed)

    # 保存失败记录
    recorder.save()

    return grid_results, [], all_failed


def generate_individual_only(
    project_name: str,
    script_filename: str,
    scene_ids: Optional[List[str]] = None,
    max_workers: int = 10,
    rate_limiter: Optional[Any] = None
) -> Tuple[List[Path], List[Path], List[Tuple[str, str]]]:
    """
    生成单独场景图（需要已有多宫格图）

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        scene_ids: 可选的场景 ID 列表，为空则处理所有有 storyboard_grid 但无 storyboard_image 的场景
        max_workers: 最大并发数
        rate_limiter: 可选的限流器实例

    Returns:
        ([], individual_paths, failed_scenes) 元组
    """
    pm = ProjectManager()
    script = pm.load_script(project_name, script_filename)
    project_dir = pm.get_project_path(project_name)

    # 获取字段配置
    items, id_field, _, _ = get_items_from_script(script)

    # 尝试加载项目级元数据（如果存在）
    project_data = None
    if pm.project_exists(project_name):
        try:
            project_data = pm.load_project(project_name)
            print("📁 已加载项目元数据 (project.json)")
        except Exception as e:
            print(f"⚠️  无法加载项目元数据: {e}")

    # 筛选需要处理的场景
    if scene_ids:
        # 处理指定的场景
        scenes_to_process = [
            scene for scene in items
            if scene[id_field] in scene_ids
        ]
        # 检查是否有 storyboard_grid
        for scene in scenes_to_process:
            if not scene['generated_assets'].get('storyboard_grid'):
                print(f"⚠️  场景 {scene[id_field]} 没有多宫格图，无法生成单独场景图")
                scenes_to_process = [s for s in scenes_to_process if s != scene]
    else:
        # 获取所有需要生成单独场景图的场景
        scenes_to_process = pm.get_scenes_needing_individual(project_name, script_filename)

    if not scenes_to_process:
        print("✨ 所有场景的单独分镜图都已生成")
        return [], [], []

    print(f"📷 共 {len(scenes_to_process)} 个场景需要并行生成单独场景图")

    # 按 grid 分组处理
    grid_groups: dict = {}
    for scene in scenes_to_process:
        grid_path = scene['generated_assets']['storyboard_grid']
        if grid_path not in grid_groups:
            grid_groups[grid_path] = []
        grid_groups[grid_path].append(scene)

    all_results = []
    all_failed = []

    # 创建失败记录器
    recorder = FailureRecorder(project_dir / 'storyboards')

    for grid_path, scenes in grid_groups.items():
        full_grid_path = project_dir / grid_path
        if not full_grid_path.exists():
            print(f"⚠️  多宫格图不存在: {grid_path}")
            for scene in scenes:
                all_failed.append((scene[id_field], f"多宫格图不存在: {grid_path}"))
                recorder.record_failure(
                    scene_id=scene[id_field],
                    failure_type="scene",
                    error=f"多宫格图不存在: {grid_path}",
                    attempts=0
                )
            continue

        # 需要确定每个场景在原始批次中的位置
        # 从 grid 文件名提取批次号
        try:
            batch_id = int(grid_path.split('_')[-1].replace('.png', ''))
        except ValueError:
            batch_id = 0  # 如果文件名格式不匹配，默认为 0

        results, failed = generate_individual_scenes(
            project_name, script_filename,
            scenes, full_grid_path, batch_id, script,
            max_workers=max_workers,
            rate_limiter=rate_limiter,
            project_data=project_data
        )
        all_results.extend(results)
        all_failed.extend(failed)

        # 记录失败
        for scene_id, error in failed:
            recorder.record_failure(
                scene_id=scene_id,
                failure_type="scene",
                error=error,
                attempts=3
            )

    # 保存失败记录
    recorder.save()

    return [], all_results, all_failed


def generate_storyboard_direct(
    project_name: str,
    script_filename: str,
    segment_ids: Optional[List[str]] = None,
    max_workers: int = 10,
    rate_limiter: Optional[Any] = None
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """
    直接生成分镜图（narration 模式专用，无需多宫格图）

    仅使用 character_sheet 和 clue_sheet 作为参考图。

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        segment_ids: 可选的片段 ID 列表
        max_workers: 最大并发数
        rate_limiter: 可选的限流器实例

    Returns:
        (成功路径列表, 失败列表) 元组
    """
    pm = ProjectManager()
    script = pm.load_script(project_name, script_filename)
    project_dir = pm.get_project_path(project_name)

    # 验证是 narration 模式
    content_mode = script.get('content_mode', 'narration')
    if content_mode != 'narration':
        raise ValueError(f"generate_storyboard_direct 仅适用于 narration 模式，当前模式: {content_mode}")

    # 加载项目元数据
    project_data = None
    if pm.project_exists(project_name):
        try:
            project_data = pm.load_project(project_name)
            print("📁 已加载项目元数据 (project.json)")
        except Exception as e:
            print(f"⚠️  无法加载项目元数据: {e}")

    # 获取字段配置
    _, id_field, char_field, clue_field = get_items_from_script(script)

    segments = script.get('segments', [])

    # 筛选需要生成的片段
    if segment_ids:
        segments_to_process = [s for s in segments if s[id_field] in segment_ids]
    else:
        # 获取所有没有 storyboard_image 的片段
        segments_to_process = [
            s for s in segments
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
            id_field, char_field, clue_field
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


def generate_single_batch(
    project_name: str,
    script_filename: str,
    batch_num: int,
    rate_limiter: Optional[Any] = None
) -> Tuple[Path, List[Path], List[Tuple[str, str]]]:
    """
    生成指定批次的分镜图（仅多宫格）

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        batch_num: 批次编号（从 1 开始）
        rate_limiter: 可选的限流器实例

    Returns:
        (grid_path, [], failed_scenes) 元组
    """
    pm = ProjectManager()
    script = pm.load_script(project_name, script_filename)

    # 获取所有场景
    all_scenes = script['scenes']

    # 按批次划分
    batch_size = 6
    start_idx = (batch_num - 1) * batch_size
    end_idx = start_idx + batch_size

    if start_idx >= len(all_scenes):
        raise ValueError(f"批次 {batch_num} 超出范围，共有 {len(all_scenes)} 个场景")

    batch_scenes = all_scenes[start_idx:end_idx]

    return generate_storyboard_grid(
        project_name, script_filename,
        batch_scenes, batch_num, script,
        rate_limiter=rate_limiter
    )


def main():
    from lib.gemini_client import RateLimiter

    parser = argparse.ArgumentParser(description='生成分镜图')
    parser.add_argument('project', help='项目名称')
    parser.add_argument('script', help='剧本文件名')

    # 操作模式参数（drama 模式必选，narration 模式可选）
    action_group = parser.add_mutually_exclusive_group(required=False)
    action_group.add_argument('--grids', action='store_true', help='[drama 模式] 步骤1：生成多宫格预览图')
    action_group.add_argument('--scenes', action='store_true', help='[drama 模式] 步骤2：生成单独场景图（需要已有多宫格图）')

    # 辅助参数
    parser.add_argument('--batch', type=int, help='指定批次编号（从 1 开始）')
    parser.add_argument('--all', action='store_true', help='处理所有待处理场景')
    parser.add_argument('--scene-ids', nargs='+', help='指定场景/片段 ID')
    parser.add_argument('--segment-ids', nargs='+', help='[narration 模式] 指定片段 ID')

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

        if content_mode == 'narration':
            # narration 模式：直接生成分镜图，无需多宫格
            if args.grids:
                print("⚠️  narration 模式不需要多宫格图，将直接生成分镜图")
            if args.scenes:
                print("⚠️  narration 模式不需要两步流程，将直接生成分镜图")

            print("🚀 narration 模式：直接生成分镜图（无多宫格）")

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
                print(f"⚠️  失败: {len(failed)} 个片段")

        else:
            # drama 模式：保持现有两步流程
            if not args.grids and not args.scenes:
                print("❌ drama 模式需要指定 --grids 或 --scenes 参数")
                sys.exit(1)

            if args.grids:
                # 步骤 1：生成多宫格图
                print("🚀 drama 模式步骤 1：生成多宫格分镜图")

                if args.batch:
                    # 生成指定批次
                    grid_path, _, failed = generate_single_batch(
                        args.project, args.script, args.batch,
                        rate_limiter=rate_limiter
                    )
                    print(f"\n📊 批次 {args.batch} 生成完成")
                    print(f"   多宫格图: {grid_path}")
                    if failed:
                        print(f"   失败: {len(failed)} 个场景")

                elif args.all:
                    # 生成所有缺失的 grids
                    grid_paths, _, failed = generate_all_grids(
                        args.project, args.script,
                        rate_limiter=rate_limiter,
                        max_workers=max_workers
                    )
                    print(f"\n📊 生成完成:")
                    print(f"   多宫格图: {len(grid_paths)} 个")
                    if failed:
                        print(f"   失败: {len(failed)} 个场景")
                else:
                    print("❌ 请指定 --batch 或 --all 参数")
                    sys.exit(1)

            elif args.scenes:
                # 步骤 2：生成单独场景图
                print("🚀 drama 模式步骤 2：生成单独场景图")

                _, individual_paths, failed = generate_individual_only(
                    args.project, args.script,
                    scene_ids=args.scene_ids,
                    rate_limiter=rate_limiter,
                    max_workers=max_workers
                )
                print(f"\n📊 生成完成: {len(individual_paths)} 个场景图")
                if failed:
                    print(f"⚠️  失败: {len(failed)} 个场景")

    except Exception as e:
        print(f"❌ 错误: {e}")
        # traceback.print_exc() # 可选：打印堆栈
        sys.exit(1)


if __name__ == '__main__':
    main()
