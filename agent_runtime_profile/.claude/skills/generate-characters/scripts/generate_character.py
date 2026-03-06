#!/usr/bin/env python3
"""
Character Generator - 使用 Gemini API 生成人物设计图

Usage:
    python generate_character.py <character_name>

Example:
    python generate_character.py 张三

Note:
    参考图会自动从 project.json 中的 reference_image 字段读取
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from lib.generation_queue_client import (
    TaskFailedError,
    WorkerOfflineError,
    enqueue_and_wait_sync as enqueue_and_wait,
    is_worker_online_sync as is_worker_online,
)
from lib.media_generator import MediaGenerator
from lib.project_manager import ProjectManager
from lib.prompt_builders import build_character_prompt


def generate_character(
    character_name: str,
) -> Path:
    """
    生成人物设计图

    Args:
        character_name: 人物名称

    Returns:
        生成的图片路径
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # 从 project.json 获取人物信息
    project = pm.load_project(project_name)

    description = ""
    style = project.get("style", "")
    style_description = project.get("style_description", "")
    reference_images = None

    if "characters" in project and character_name in project["characters"]:
        char_info = project["characters"][character_name]
        description = char_info.get("description", "")

        # 自动读取参考图
        ref_path = char_info.get("reference_image")
        if ref_path:
            ref_full_path = project_dir / ref_path
            if ref_full_path.exists():
                reference_images = [ref_full_path]
                print(f"📎 使用参考图: {ref_full_path}")

    if not description:
        raise ValueError(
            f"人物 '{character_name}' 的描述为空，请先在 project.json 中添加描述"
        )

    # 构建 prompt
    prompt = build_character_prompt(
        character_name, description, style, style_description
    )

    print(f"🎨 正在生成人物设计图: {character_name}")
    print(f"   描述: {description[:50]}...")

    # 优先走队列（worker 在线）
    if is_worker_online():
        try:
            queued = enqueue_and_wait(
                project_name=project_name,
                task_type="character",
                media_type="image",
                resource_id=character_name,
                payload={"prompt": description},
                source="skill",
            )
            result = queued.get("result") or {}
            relative_path = result.get("file_path") or f"characters/{character_name}.png"
            output_path = project_dir / relative_path
            version = result.get("version")
            version_text = f" (版本 v{version})" if version is not None else ""
            print(f"✅ 人物设计图已保存: {output_path}{version_text}")
            return output_path
        except WorkerOfflineError:
            print("ℹ️  未检测到队列 worker，回退直连生成")
        except TaskFailedError as exc:
            raise RuntimeError(f"队列任务执行失败: {exc}") from exc

    # 回退直连（保留原有重试与限流链路）
    generator = MediaGenerator(project_dir)
    output_path, version = generator.generate_image(
        prompt=prompt,
        resource_type="characters",
        resource_id=character_name,
        reference_images=reference_images,
        aspect_ratio="3:4",
    )

    print(f"✅ 人物设计图已保存: {output_path} (版本 v{version})")

    # 更新 project.json 中的 character_sheet 路径
    relative_path = f"characters/{character_name}.png"
    pm.update_project_character_sheet(project_name, character_name, relative_path)
    print("✅ project.json 已更新")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="生成人物设计图")
    parser.add_argument("character", help="人物名称")

    args = parser.parse_args()

    try:
        output_path = generate_character(
            args.character,
        )
        print(f"\n🖼️  请查看生成的图片: {output_path}")

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
