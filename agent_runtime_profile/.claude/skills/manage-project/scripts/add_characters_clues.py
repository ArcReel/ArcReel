#!/usr/bin/env python3
"""
add_characters_clues.py - 批量添加角色/线索到 project.json

用法（需从项目目录内执行）:
    python .claude/skills/manage-project/scripts/add_characters_clues.py \
        --characters '{"角色名": {"description": "...", "voice_style": "..."}}' \
        --clues '{"线索名": {"type": "prop", "description": "...", "importance": "major"}}'

    # 通过 stdin 接收 JSON
    echo '{"characters": {...}, "clues": {...}}' | python .claude/skills/manage-project/scripts/add_characters_clues.py --stdin
"""

import argparse
import json
import sys
from pathlib import Path

# 允许从仓库任意工作目录直接运行该脚本
PROJECT_ROOT = (
    Path(__file__).resolve().parents[4]
)  # .claude/skills/manage-project/scripts -> repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.data_validator import validate_project
from lib.project_manager import ProjectManager


def main():
    parser = argparse.ArgumentParser(
        description="批量添加角色/线索到 project.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例（需从项目目录内执行）:
    %(prog)s --characters '{"李白": {"description": "白衣剑客", "voice_style": "豪放"}}'
    %(prog)s --clues '{"玉佩": {"type": "prop", "description": "温润白玉", "importance": "major"}}'
    echo '{"characters": {...}}' | %(prog)s --stdin
        """,
    )

    parser.add_argument(
        "--characters",
        type=str,
        default=None,
        help="JSON 格式的角色数据",
    )
    parser.add_argument(
        "--clues",
        type=str,
        default=None,
        help="JSON 格式的线索数据",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="从 stdin 读取 JSON（包含 characters 和/或 clues 字段）",
    )

    args = parser.parse_args()

    characters = {}
    clues = {}

    if args.stdin:
        stdin_data = json.loads(sys.stdin.read())
        characters = stdin_data.get("characters", {})
        clues = stdin_data.get("clues", {})
    else:
        if args.characters:
            characters = json.loads(args.characters)
        if args.clues:
            clues = json.loads(args.clues)

    if not characters and not clues:
        print("❌ 未提供角色或线索数据")
        sys.exit(1)

    pm, project_name = ProjectManager.from_cwd()

    # 添加角色
    chars_added = 0
    chars_skipped = 0
    if characters:
        project = pm.load_project(project_name)
        existing = project.get("characters", {})
        chars_skipped = sum(1 for name in characters if name in existing)
        chars_added = pm.add_characters_batch(project_name, characters)
        print(f"角色: 新增 {chars_added} 个，跳过 {chars_skipped} 个（已存在）")

    # 添加线索
    clues_added = 0
    clues_skipped = 0
    if clues:
        project = pm.load_project(project_name)
        existing = project.get("clues", {})
        clues_skipped = sum(1 for name in clues if name in existing)
        clues_added = pm.add_clues_batch(project_name, clues)
        print(f"线索: 新增 {clues_added} 个，跳过 {clues_skipped} 个（已存在）")

    # 数据验证
    result = validate_project(project_name, projects_root=str(pm.projects_root))
    if result.valid:
        print("✅ 数据验证通过")
    else:
        print("⚠️ 数据验证发现问题:")
        for error in result.errors:
            print(f"  错误: {error}")
        for warning in result.warnings:
            print(f"  警告: {warning}")
        sys.exit(1)

    # 汇总
    total_added = chars_added + clues_added
    if total_added > 0:
        print(f"\n✅ 完成: 共新增 {total_added} 条数据")
    else:
        print("\nℹ️ 所有数据已存在，无新增")


if __name__ == "__main__":
    main()
