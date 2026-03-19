#!/usr/bin/env python3
"""
Clue Generator - 使用 Gemini API 生成线索设计图

Usage:
    python generate_clue.py --all
    python generate_clue.py --clue "玉佩"
    python generate_clue.py --list

Example:
    python generate_clue.py --all
    python generate_clue.py --clue "老槐树"
"""

import argparse
import sys
from pathlib import Path

from lib.generation_queue_client import enqueue_and_wait_sync as enqueue_and_wait
from lib.project_manager import ProjectManager


def generate_clue(
    clue_name: str
) -> Path:
    """
    生成线索设计图

    Args:
        clue_name: 线索名称

    Returns:
        生成的图片路径
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # 获取线索信息
    clue = pm.get_clue(project_name, clue_name)
    clue_type = clue.get('type', 'prop')
    description = clue.get('description', '')

    if not description:
        raise ValueError(f"线索 '{clue_name}' 的描述为空，请先添加描述")

    print(f"🎨 正在生成线索设计图: {clue_name}")
    print(f"   类型: {clue_type}")
    print(f"   描述: {description[:50]}..." if len(description) > 50 else f"   描述: {description}")

    queued = enqueue_and_wait(
        project_name=project_name,
        task_type="clue",
        media_type="image",
        resource_id=clue_name,
        payload={"prompt": description},
        source="skill",
    )
    result = queued.get("result") or {}
    relative_path = result.get("file_path") or f"clues/{clue_name}.png"
    output_path = project_dir / relative_path
    version = result.get("version")
    version_text = f" (版本 v{version})" if version is not None else ""
    print(f"✅ 线索设计图已保存: {output_path}{version_text}")
    return output_path


def list_pending_clues() -> None:
    """
    列出待生成的线索
    """
    pm, project_name = ProjectManager.from_cwd()
    pending = pm.get_pending_clues(project_name)

    if not pending:
        print(f"✅ 项目 '{project_name}' 中所有重要线索都已有设计图")
        return

    print(f"\n📋 待生成的线索 ({len(pending)} 个):\n")
    for clue in pending:
        clue_type = clue.get('type', 'prop')
        type_emoji = "📦" if clue_type == 'prop' else "🏠"
        print(f"  {type_emoji} {clue['name']}")
        print(f"     类型: {clue_type}")
        print(f"     描述: {clue.get('description', '')[:60]}...")
        print()


def generate_all_clues() -> tuple:
    """
    生成所有待处理的线索

    Returns:
        (成功数, 失败数)
    """
    pm, project_name = ProjectManager.from_cwd()
    pending = pm.get_pending_clues(project_name)

    if not pending:
        print(f"✅ 项目 '{project_name}' 中所有重要线索都已有设计图")
        return (0, 0)

    print(f"\n🚀 开始生成 {len(pending)} 个线索设计图...\n")

    success_count = 0
    fail_count = 0

    for clue in pending:
        try:
            generate_clue(clue['name'])
            success_count += 1
            print()
        except Exception as e:
            print(f"❌ 生成 '{clue['name']}' 失败: {e}")
            fail_count += 1
            print()

    print(f"\n{'=' * 40}")
    print(f"生成完成!")
    print(f"   ✅ 成功: {success_count}")
    print(f"   ❌ 失败: {fail_count}")
    print(f"{'=' * 40}")

    return (success_count, fail_count)


def main():
    parser = argparse.ArgumentParser(description='生成线索设计图')
    parser.add_argument('--all', action='store_true', help='生成所有待处理的线索')
    parser.add_argument('--clue', help='指定线索名称')
    parser.add_argument('--list', action='store_true', help='列出待生成的线索')

    args = parser.parse_args()

    try:
        if args.list:
            list_pending_clues()
        elif args.all:
            success, fail = generate_all_clues()
            sys.exit(0 if fail == 0 else 1)
        elif args.clue:
            output_path = generate_clue(args.clue)
            print(f"\n🖼️  请查看生成的图片: {output_path}")
        else:
            parser.print_help()
            print("\n❌ 请指定 --all、--clue 或 --list")
            sys.exit(1)

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
