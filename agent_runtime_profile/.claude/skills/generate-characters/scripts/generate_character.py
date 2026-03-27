#!/usr/bin/env python3
"""
Character Generator - 使用 Gemini API 生成角色设计图

Usage:
    python generate_character.py --character "张三"
    python generate_character.py --characters "张三" "李四"
    python generate_character.py --all
    python generate_character.py --list

Note:
    参考图会自动从 project.json 中的 reference_image 字段读取
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from lib.generation_queue_client import (
    BatchTaskSpec,
    BatchTaskResult,
    batch_enqueue_and_wait_sync,
    enqueue_and_wait_sync as enqueue_and_wait,
)
from lib.project_manager import ProjectManager


def generate_character(
    character_name: str,
) -> Path:
    """
    生成单个角色设计图

    Args:
        character_name: 角色名称

    Returns:
        生成的图片路径
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # 从 project.json 获取角色信息
    project = pm.load_project(project_name)

    description = ""
    if "characters" in project and character_name in project["characters"]:
        char_info = project["characters"][character_name]
        description = char_info.get("description", "")

    if not description:
        raise ValueError(
            f"角色 '{character_name}' 的描述为空，请先在 project.json 中添加描述"
        )

    print(f"🎨 正在生成角色设计图: {character_name}")
    print(f"   描述: {description[:50]}...")

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
    print(f"✅ 角色设计图已保存: {output_path}{version_text}")
    return output_path


def list_pending_characters() -> None:
    """列出待生成设计图的角色"""
    pm, project_name = ProjectManager.from_cwd()
    pending = pm.get_pending_characters(project_name)

    if not pending:
        print(f"✅ 项目 '{project_name}' 中所有角色都已有设计图")
        return

    print(f"\n📋 待生成的角色 ({len(pending)} 个):\n")
    for char in pending:
        print(f"  🧑 {char['name']}")
        desc = char.get("description", "")
        print(f"     描述: {desc[:60]}..." if len(desc) > 60 else f"     描述: {desc}")
        print()


def generate_batch_characters(
    character_names: Optional[List[str]] = None,
) -> Tuple[int, int]:
    """
    批量生成角色设计图（全部入队，由 Worker 并行处理）

    Args:
        character_names: 指定的角色名称列表。None 表示所有待处理角色。

    Returns:
        (成功数, 失败数)
    """
    pm, project_name = ProjectManager.from_cwd()
    project = pm.load_project(project_name)

    if character_names:
        chars = project.get("characters", {})
        names_to_process = []
        for name in character_names:
            if name not in chars:
                print(f"⚠️  角色 '{name}' 不存在于 project.json 中，跳过")
                continue
            if not chars[name].get("description"):
                print(f"⚠️  角色 '{name}' 缺少描述，跳过")
                continue
            names_to_process.append(name)
    else:
        pending = pm.get_pending_characters(project_name)
        names_to_process = [c["name"] for c in pending]

    if not names_to_process:
        print("✅ 没有需要生成的角色")
        return (0, 0)

    specs = [
        BatchTaskSpec(
            task_type="character",
            media_type="image",
            resource_id=name,
            payload={"prompt": project["characters"][name]["description"]},
        )
        for name in names_to_process
    ]

    total = len(specs)
    print(f"\n🚀 批量提交 {total} 个角色设计图到生成队列...\n")

    def on_success(br: BatchTaskResult) -> None:
        version = (br.result or {}).get("version")
        version_text = f" (版本 v{version})" if version is not None else ""
        print(f"✅ 角色设计图: {br.resource_id} 完成{version_text}")

    def on_failure(br: BatchTaskResult) -> None:
        print(f"❌ 角色设计图: {br.resource_id} 失败 - {br.error}")

    successes, failures = batch_enqueue_and_wait_sync(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )

    print(f"\n{'=' * 40}")
    print("生成完成!")
    print(f"   ✅ 成功: {len(successes)}")
    print(f"   ❌ 失败: {len(failures)}")
    print(f"{'=' * 40}")

    return (len(successes), len(failures))


def main():
    parser = argparse.ArgumentParser(description="生成角色设计图")
    parser.add_argument("--character", help="指定单个角色名称")
    parser.add_argument("--characters", nargs="+", help="指定多个角色名称")
    parser.add_argument("--all", action="store_true", help="生成所有待处理的角色")
    parser.add_argument("--list", action="store_true", help="列出待生成的角色")

    args = parser.parse_args()

    try:
        if args.list:
            list_pending_characters()
        elif args.all:
            _, fail = generate_batch_characters()
            sys.exit(0 if fail == 0 else 1)
        elif args.characters:
            _, fail = generate_batch_characters(args.characters)
            sys.exit(0 if fail == 0 else 1)
        elif args.character:
            output_path = generate_character(args.character)
            print(f"\n🖼️  请查看生成的图片: {output_path}")
        else:
            parser.print_help()
            print("\n❌ 请指定 --all、--characters、--character 或 --list")
            sys.exit(1)

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
