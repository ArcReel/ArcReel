#!/usr/bin/env python3
"""
Migrate existing projects’ characters from scripts into project.json.

Usage:
    python scripts/migrate_to_project_json.py <项目名>
    python scripts/migrate_to_project_json.py --all  # Migrate every project
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add repository root to Python path.
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.infra.data_root_layout import PROJECT_NAME_PATTERN, DataRootLayout
from lib.project.project_manager import ProjectManager


def migrate_project(pm: ProjectManager, project_name: str, dry_run: bool = False) -> bool:
    """
    Migrate one project.

    Args:
        pm: ProjectManager instance.
        project_name: Project name.
        dry_run: Preview without executing.

    Returns:
        Whether migration succeeded.
    """
    print(f"\n{'=' * 50}")
    print(f"迁移项目: {project_name}")
    print("=" * 50)

    try:
        project_dir = pm.get_project_path(project_name)
    except FileNotFoundError:
        print(f"  ❌ 项目不存在: {project_name}")
        return False

    # Check whether project.json already exists.
    project_file = project_dir / "project.json"
    if project_file.exists():
        print("  ⚠️  project.json 已存在，跳过迁移")
        print(f"  如需重新迁移，请先删除 {project_file}")
        return True

    # Collect characters from all scripts.
    scripts_dir = project_dir / "scripts"
    all_characters = {}
    episodes = []
    script_files = list(scripts_dir.glob("*.json")) if scripts_dir.exists() else []

    if not script_files:
        print("  ⚠️  未找到剧本文件")

    for script_file in sorted(script_files):
        print(f"\n  📖 处理剧本: {script_file.name}")

        with open(script_file, encoding="utf-8") as f:
            script = json.load(f)

        # Extract characters.
        characters = script.get("characters", {})
        for name, char_data in characters.items():
            if name not in all_characters:
                all_characters[name] = char_data.copy()
                print(f"      👤 发现角色: {name}")
            else:
                # Merge data, preferring versions with asset images.
                if char_data.get("character_sheet") and not all_characters[name].get("character_sheet"):
                    all_characters[name] = char_data.copy()
                    print(f"      👤 更新角色: {name} (有资产图)")

        # Extract episode information.
        novel_info = script.get("novel", {})
        scenes_count = len(script.get("scenes", []))

        # Infer episode number from filename or content.
        episode_num = 1
        filename_lower = script_file.stem.lower()
        for i in range(1, 100):
            if f"episode_{i:02d}" in filename_lower or f"episode{i}" in filename_lower:
                episode_num = i
                break
            if f"chapter_{i:02d}" in filename_lower or f"chapter{i}" in filename_lower:
                episode_num = i
                break
            if f"_{i:02d}_" in filename_lower or f"_{i}_" in filename_lower:
                episode_num = i
                break

        # Add episode information without statistics, computed on read in the project summary.
        episodes.append(
            {
                "episode": episode_num,
                "title": novel_info.get("chapter", script_file.stem),
                "script_file": f"scripts/{script_file.name}",
            }
        )
        print(f"      📺 剧集 {episode_num}: {scenes_count} 个场景")

    # Deduplicate and sort episodes.
    seen_episodes = {}
    for ep in episodes:
        if ep["episode"] not in seen_episodes:
            seen_episodes[ep["episode"]] = ep
    episodes = sorted(seen_episodes.values(), key=lambda x: x["episode"])

    # Build project.json.
    project_title = project_name
    if script_files:
        with open(script_files[0], encoding="utf-8") as f:
            first_script = json.load(f)
            project_title = first_script.get("novel", {}).get("title", project_name)

    # Build project.json without status, computed on read in the project summary.
    project_data = {
        "title": project_title,
        "style": "",
        "episodes": episodes,
        "characters": all_characters,
        "clues": {},
        "metadata": {
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "migrated_from": "script_based_characters",
        },
    }

    # Count completed character asset images for logging only.
    completed_chars = 0
    for char_data in all_characters.values():
        sheet = char_data.get("character_sheet")
        if sheet:
            sheet_path = project_dir / sheet
            if sheet_path.exists():
                completed_chars += 1

    # Create clues directory.
    clues_dir = project_dir / "clues"
    if not clues_dir.exists():
        if not dry_run:
            clues_dir.mkdir(parents=True, exist_ok=True)
        print("\n  📁 创建目录: clues/")

    print("\n  📊 迁移摘要:")
    print(f"      - 角色: {len(all_characters)} 个 ({completed_chars} 个有资产图)")
    print(f"      - 剧集: {len(episodes)} 个")
    print("      - 线索: 0 个 (待添加)")

    if dry_run:
        print("\n  🔍 预览模式 - 不会实际写入文件")
        print("\n  将创建 project.json:")
        print(json.dumps(project_data, ensure_ascii=False, indent=2)[:500] + "...")
    else:
        # Write project.json.
        with open(project_file, "w", encoding="utf-8") as f:
            json.dump(project_data, f, ensure_ascii=False, indent=2)
        print("\n  ✅ 已创建 project.json")

        # Optionally remove script characters, keeping original backups.
        # Keep characters in scripts for backward compatibility.
        print("  ℹ️  保留剧本中的 characters 字段以保持向后兼容")

    return True


def main():
    parser = argparse.ArgumentParser(description="迁移项目数据到 project.json")
    parser.add_argument("project", nargs="?", help="项目名称，或使用 --all 迁移所有项目")
    parser.add_argument("--all", action="store_true", help="迁移所有项目")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    parser.add_argument(
        "--projects-root", "--data-root", dest="data_root", default=None, help="数据根（默认按当前配置解析）"
    )

    args = parser.parse_args()

    if not args.project and not args.all:
        parser.print_help()
        print("\n❌ 请指定项目名称或使用 --all")
        sys.exit(1)

    # Initialize ProjectManager.
    pm = ProjectManager(args.data_root or DataRootLayout.current().root)

    print("🚀 开始迁移...")
    print(f"   项目目录: {pm.projects_dir}")

    if args.dry_run:
        print("   📋 预览模式已启用")

    success_count = 0
    fail_count = 0

    if args.all:
        # Old projects lack project.json and are absent from list_projects(); identify by project-name rules and script files.
        projects = (
            sorted(
                entry.name
                for entry in pm.projects_dir.iterdir()
                if entry.is_dir()
                and PROJECT_NAME_PATTERN.fullmatch(entry.name)
                and any((entry / "scripts").glob("*.json"))
            )
            if pm.projects_dir.is_dir()
            else []
        )
        print(f"   发现 {len(projects)} 个项目")

        for project_name in projects:
            if migrate_project(pm, project_name, dry_run=args.dry_run):
                success_count += 1
            else:
                fail_count += 1
    else:
        if migrate_project(pm, args.project, dry_run=args.dry_run):
            success_count = 1
        else:
            fail_count = 1

    print("\n" + "=" * 50)
    print("迁移完成!")
    print(f"   ✅ 成功: {success_count}")
    print(f"   ❌ 失败: {fail_count}")
    print("=" * 50)

    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
