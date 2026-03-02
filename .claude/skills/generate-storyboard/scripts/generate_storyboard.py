#!/usr/bin/env python3
"""
Storyboard Generator - 通过生成队列生成分镜图

两种模式统一通过 generation worker 生成分镜图：
- narration 模式（说书+画面）：生成 9:16 竖屏分镜图
- drama 模式（剧集动画）：生成 16:9 横屏分镜图

Usage:
    # narration 模式：提交分镜图生成任务（默认）
    python generate_storyboard.py <project_name> <script_file>
    python generate_storyboard.py <project_name> <script_file> --scene E1S05
    python generate_storyboard.py <project_name> <script_file> --segment-ids E1S01 E1S02

    # drama 模式：提交分镜图生成任务
    python generate_storyboard.py <project_name> <script_file>
    python generate_storyboard.py <project_name> <script_file> --scene E1S05
    python generate_storyboard.py <project_name> <script_file> --scene-ids E1S01 E1S02
"""

import argparse
import sys
import os
import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.generation_queue_client import (
    TaskFailedError,
    enqueue_task_only,
    wait_for_task,
)
from lib.project_manager import ProjectManager
from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt
)
from lib.storyboard_sequence import (
    StoryboardTaskPlan,
    build_storyboard_dependency_plan,
    get_storyboard_items,
)


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


# ==================== Prompt 构建函数 ====================


def get_items_from_script(script: dict) -> tuple:
    """
    根据内容模式获取场景/片段列表和 ID 字段名

    Args:
        script: 剧本数据

    Returns:
        (items_list, id_field, char_field, clue_field) 元组
    """
    return get_storyboard_items(script)


def build_storyboard_prompt(
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
    构建分镜图任务 prompt（通用，适用于 narration 和 drama 模式）

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
    composition_suffix = ""
    if content_mode == 'narration':
        # 结构化 prompt 使用换行，普通字符串使用空格，以保证格式正确
        if is_structured_image_prompt(image_prompt):
            composition_suffix = "\n竖屏构图。"
        else:
            composition_suffix = " 竖屏构图。"

    # 检测是否为结构化格式
    if is_structured_image_prompt(image_prompt):
        # 转换为 YAML 格式
        yaml_prompt = image_prompt_to_yaml(image_prompt, style)
        return f"{style_prefix}{yaml_prompt}{composition_suffix}"

    return f"{style_prefix}{image_prompt}{composition_suffix}"


def _select_storyboard_items(
    items: List[dict],
    id_field: str,
    segment_ids: Optional[List[str]],
) -> List[dict]:
    if segment_ids:
        selected_set = {str(segment_id) for segment_id in segment_ids}
        return [item for item in items if str(item.get(id_field)) in selected_set]

    return [
        item for item in items
        if not item.get('generated_assets', {}).get('storyboard_image')
    ]


def _enqueue_storyboard_batch(
    *,
    project_name: str,
    script_filename: str,
    plans: List[StoryboardTaskPlan],
    items_by_id: Dict[str, dict],
    characters: Dict[str, dict],
    clues: Dict[str, dict],
    style: str,
    style_description: str,
    id_field: str,
    char_field: str,
    clue_field: str,
    content_mode: str,
) -> Dict[str, str]:
    task_ids_by_resource: Dict[str, str] = {}

    for plan in plans:
        item = items_by_id[plan.resource_id]
        prompt = build_storyboard_prompt(
            item,
            characters,
            clues,
            style,
            style_description,
            id_field,
            char_field,
            clue_field,
            content_mode=content_mode,
        )

        dependency_task_id = None
        if plan.dependency_resource_id:
            dependency_task_id = task_ids_by_resource.get(plan.dependency_resource_id)

        enqueue_result = enqueue_task_only(
            project_name=project_name,
            task_type="storyboard",
            media_type="image",
            resource_id=plan.resource_id,
            payload={
                "prompt": prompt,
                "script_file": script_filename,
            },
            script_file=script_filename,
            source="skill",
            dependency_task_id=dependency_task_id,
            dependency_group=plan.dependency_group,
            dependency_index=plan.dependency_index,
        )
        task_ids_by_resource[plan.resource_id] = enqueue_result["task_id"]

    return task_ids_by_resource


def _wait_for_storyboard_tasks(
    *,
    project_dir: Path,
    plans: List[StoryboardTaskPlan],
    task_ids_by_resource: Dict[str, str],
    max_workers: int,
) -> Tuple[Dict[str, Path], List[Tuple[str, str]]]:
    results: Dict[str, Path] = {}
    failures: List[Tuple[str, str]] = []
    total = len(plans)
    completed = 0
    lock = threading.Lock()

    if not plans:
        return results, failures

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total))) as executor:
        future_to_plan = {
            executor.submit(wait_for_task, task_ids_by_resource[plan.resource_id]): plan
            for plan in plans
        }

        for future in as_completed(future_to_plan):
            plan = future_to_plan[future]
            with lock:
                completed += 1
                index = completed
            try:
                task = future.result()
                if task.get("status") == "failed":
                    raise TaskFailedError(task.get("error_message") or "task failed")

                result = task.get("result") or {}
                relative_path = result.get("file_path") or f"storyboards/scene_{plan.resource_id}.png"
                output_path = project_dir / relative_path
                results[plan.resource_id] = output_path
                print(f"✅ [{index}/{total}] 分镜图生成: {plan.resource_id} 完成")
            except Exception as exc:
                failures.append((plan.resource_id, str(exc)))
                print(f"❌ [{index}/{total}] 分镜图生成: {plan.resource_id} 失败 - {exc}")

    return results, failures


def generate_storyboard_direct(
    project_name: str,
    script_filename: str,
    segment_ids: Optional[List[str]] = None,
    max_workers: int = 10,
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """
    通过生成队列提交分镜图任务（narration 和 drama 模式通用）。

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名
        segment_ids: 可选的片段/场景 ID 列表
        max_workers: 最大并发数

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
    segments_to_process = _select_storyboard_items(items, id_field, segment_ids)

    if not segments_to_process:
        print("✨ 所有片段的分镜图都已生成")
        return [], []

    # 获取人物和线索数据
    characters = project_data.get('characters', {}) if project_data else {}
    clues = project_data.get('clues', {}) if project_data else {}
    style = project_data.get('style', '') if project_data else ''
    style_description = project_data.get('style_description', '') if project_data else ''
    items_by_id = {
        str(item[id_field]): item
        for item in items
        if item.get(id_field)
    }
    dependency_plans = build_storyboard_dependency_plan(
        items,
        id_field,
        [str(item[id_field]) for item in segments_to_process],
        script_filename,
    )

    print(f"📷 提交 {len(segments_to_process)} 个分镜图到生成队列...")
    print("🧵 任务模式: 队列入队并等待")

    # 创建失败记录器
    recorder = FailureRecorder(project_dir / 'storyboards')
    task_ids_by_resource = _enqueue_storyboard_batch(
        project_name=project_name,
        script_filename=script_filename,
        plans=dependency_plans,
        items_by_id=items_by_id,
        characters=characters,
        clues=clues,
        style=style,
        style_description=style_description,
        id_field=id_field,
        char_field=char_field,
        clue_field=clue_field,
        content_mode=content_mode,
    )
    result_map, failures = _wait_for_storyboard_tasks(
        project_dir=project_dir,
        plans=dependency_plans,
        task_ids_by_resource=task_ids_by_resource,
        max_workers=max_workers,
    )

    # 记录失败
    for segment_id, error in failures:
        recorder.record_failure(
            scene_id=segment_id,
            failure_type="scene",
            error=error,
            attempts=3
        )

    # 保存失败记录
    recorder.save()

    ordered_results = [
        result_map[plan.resource_id]
        for plan in dependency_plans
        if plan.resource_id in result_map
    ]
    return ordered_results, failures


def main():
    parser = argparse.ArgumentParser(description='生成分镜图')
    parser.add_argument('project', help='项目名称')
    parser.add_argument('script', help='剧本文件名')

    # 辅助参数
    parser.add_argument('--scene', help='指定单个场景 ID（单场景模式）')
    parser.add_argument('--scene-ids', nargs='+', help='指定场景 ID')
    parser.add_argument('--segment-ids', nargs='+', help='指定片段 ID（narration 模式别名）')

    args = parser.parse_args()

    # 从环境变量读取最大并发数，默认 3
    max_workers = int(os.environ.get('STORYBOARD_MAX_WORKERS', 3))

    try:
        # 检测 content_mode
        pm = ProjectManager()
        script = pm.load_script(args.project, args.script)
        content_mode = script.get('content_mode', 'narration')

        print(f"🚀 {content_mode} 模式：通过队列生成分镜图")

        # 合并 --scene-ids 和 --segment-ids 参数
        if args.scene:
            segment_ids = [args.scene]
        else:
            segment_ids = args.segment_ids or args.scene_ids

        results, failed = generate_storyboard_direct(
            args.project, args.script,
            segment_ids=segment_ids,
            max_workers=max_workers,
        )
        print(f"\n📊 生成完成: {len(results)} 个分镜图")
        if failed:
            print(f"⚠️  失败: {len(failed)} 个")

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
