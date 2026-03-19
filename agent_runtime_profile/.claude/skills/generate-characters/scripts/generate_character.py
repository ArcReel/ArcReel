#!/usr/bin/env python3
"""
Character Generator - 使用 Gemini API 生成人物设计图

Usage:
    python generate_character.py --character "张三"
    python generate_character.py --all
    python generate_character.py --list

Note:
    参考图会自动从 project.json 中的 reference_image 字段读取
"""

import argparse
import sys
from pathlib import Path

from lib.generation_queue_client import enqueue_and_wait_sync as enqueue_and_wait
from lib.project_manager import ProjectManager


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
    if "characters" in project and character_name in project["characters"]:
        char_info = project["characters"][character_name]
        description = char_info.get("description", "")

    if not description:
        raise ValueError(
            f"人物 '{character_name}' 的描述为空，请先在 project.json 中添加描述"
        )

    print(f"🎨 正在生成人物设计图: {character_name}")
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
    print(f"✅ 人物设计图已保存: {output_path}{version_text}")
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


def generate_all_characters() -> tuple:
    """
    生成所有待处理的角色设计图

    Returns:
        (成功数, 失败数)
    """
    pm, project_name = ProjectManager.from_cwd()
    pending = pm.get_pending_characters(project_name)

    if not pending:
        print(f"✅ 项目 '{project_name}' 中所有角色都已有设计图")
        return (0, 0)

    print(f"\n🚀 开始生成 {len(pending)} 个人物设计图...\n")

    success_count = 0
    fail_count = 0

    for char in pending:
        try:
            generate_character(char["name"])
            success_count += 1
            print()
        except Exception as e:
            print(f"❌ 生成 '{char['name']}' 失败: {e}")
            fail_count += 1
            print()

    print(f"\n{'=' * 40}")
    print("生成完成!")
    print(f"   ✅ 成功: {success_count}")
    print(f"   ❌ 失败: {fail_count}")
    print(f"{'=' * 40}")

    return (success_count, fail_count)


def main():
    parser = argparse.ArgumentParser(description="生成人物设计图")
    parser.add_argument("--character", help="指定人物名称")
    parser.add_argument("--all", action="store_true", help="生成所有待处理的角色")
    parser.add_argument("--list", action="store_true", help="列出待生成的角色")

    args = parser.parse_args()

    try:
        if args.list:
            list_pending_characters()
        elif args.all:
            _, fail = generate_all_characters()
            sys.exit(0 if fail == 0 else 1)
        elif args.character:
            output_path = generate_character(args.character)
            print(f"\n🖼️  请查看生成的图片: {output_path}")
        else:
            parser.print_help()
            print("\n❌ 请指定 --all、--character 或 --list")
            sys.exit(1)

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
