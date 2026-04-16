#!/usr/bin/env python3
"""
add_assets.py - 批量添加角色/场景/道具到 project.json

用法（需从项目目录内执行，必须单行）:
    python .claude/skills/manage-project/scripts/add_assets.py \
        --characters '{"角色名": {"description": "...", "voice_style": "..."}}' \
        --scenes '{"场景名": {"description": "..."}}' \
        --props '{"道具名": {"description": "..."}}'
"""

import argparse
import json
import sys
from pathlib import Path

# 允许从仓库任意工作目录直接运行该脚本
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/manage-project/scripts -> repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.data_validator import validate_project
from lib.project_manager import ProjectManager

_LEGACY_FIELDS = {"type", "importance"}


def _strip_legacy_fields(data: dict[str, dict], asset_type: str) -> dict[str, dict]:
    """去除旧式 type/importance 字段，有则打印警告。"""
    cleaned = {}
    for name, attrs in data.items():
        found = _LEGACY_FIELDS & attrs.keys()
        if found:
            print(f"⚠️  {asset_type} '{name}': 忽略旧式字段 {sorted(found)}，仅保留 description 等")
            attrs = {k: v for k, v in attrs.items() if k not in _LEGACY_FIELDS}
        cleaned[name] = attrs
    return cleaned


def main():
    parser = argparse.ArgumentParser(
        description="批量添加角色/场景/道具到 project.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例（需从项目目录内执行，必须单行）:
    %(prog)s --characters '{"李白": {"description": "白衣剑客", "voice_style": "豪放"}}'
    %(prog)s --scenes '{"庙宇": {"description": "古朴石庙"}}'
    %(prog)s --props '{"玉佩": {"description": "温润白玉"}}'
        """,
    )

    parser.add_argument(
        "--characters",
        type=str,
        default=None,
        help="JSON 格式的角色数据",
    )
    parser.add_argument(
        "--scenes",
        type=str,
        default=None,
        help="JSON 格式的场景数据",
    )
    parser.add_argument(
        "--props",
        type=str,
        default=None,
        help="JSON 格式的道具数据",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="从 stdin 读取 JSON（包含 characters、scenes 和/或 props 字段）",
    )

    args = parser.parse_args()

    characters = {}
    scenes = {}
    props = {}

    if args.stdin:
        stdin_data = json.loads(sys.stdin.read())
        characters = stdin_data.get("characters", {})
        scenes = stdin_data.get("scenes", {})
        props = stdin_data.get("props", {})
    else:
        if args.characters:
            characters = json.loads(args.characters)
        if args.scenes:
            scenes = json.loads(args.scenes)
        if args.props:
            props = json.loads(args.props)

    if not characters and not scenes and not props:
        print("❌ 未提供角色、场景或道具数据")
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

    # 添加场景
    scenes_added = 0
    scenes_skipped = 0
    if scenes:
        scenes = _strip_legacy_fields(scenes, "场景")
        project = pm.load_project(project_name)
        existing = project.get("scenes", {})
        scenes_skipped = sum(1 for name in scenes if name in existing)
        scenes_added = pm.add_scenes_batch(project_name, scenes)
        print(f"场景: 新增 {scenes_added} 个，跳过 {scenes_skipped} 个（已存在）")

    # 添加道具
    props_added = 0
    props_skipped = 0
    if props:
        props = _strip_legacy_fields(props, "道具")
        project = pm.load_project(project_name)
        existing = project.get("props", {})
        props_skipped = sum(1 for name in props if name in existing)
        props_added = pm.add_props_batch(project_name, props)
        print(f"道具: 新增 {props_added} 个，跳过 {props_skipped} 个（已存在）")

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
    total_added = chars_added + scenes_added + props_added
    if total_added > 0:
        print(f"\n✅ 完成: 共新增 {total_added} 条数据")
    else:
        print("\nℹ️ 所有数据已存在，无新增")


if __name__ == "__main__":
    main()
