#!/usr/bin/env python3
"""
Data migration script: migrate characters from scripts to project.json for existing projects.

Usage:
    python scripts/migrate_to_project_json.py <project_name>
    python scripts/migrate_to_project_json.py --all  # migrate all projects
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add lib directory to Python path
lib_path = Path(__file__).parent.parent / "lib"
sys.path.insert(0, str(lib_path))

from project_manager import ProjectManager


def migrate_project(pm: ProjectManager, project_name: str, dry_run: bool = False) -> bool:
    """
    Migrate a single project.

    Args:
        pm: ProjectManager instance
        project_name: project name
        dry_run: if True, preview only without making changes

    Returns:
        True if successful
    """
    print(f"\n{'=' * 50}")
    print(f"Migrating project: {project_name}")
    print("=" * 50)

    try:
        project_dir = pm.get_project_path(project_name)
    except FileNotFoundError:
        print(f"  ERROR: Project does not exist: {project_name}")
        return False

    # Check if project.json already exists
    project_file = project_dir / "project.json"
    if project_file.exists():
        print("  WARNING: project.json already exists, skipping migration")
        print(f"  To re-migrate, delete {project_file} first")
        return True

    # Collect all characters from scripts
    scripts_dir = project_dir / "scripts"
    all_characters = {}
    episodes = []
    script_files = list(scripts_dir.glob("*.json")) if scripts_dir.exists() else []

    if not script_files:
        print("  WARNING: No script files found")

    for script_file in sorted(script_files):
        print(f"\n  Processing script: {script_file.name}")

        with open(script_file, encoding="utf-8") as f:
            script = json.load(f)

        # Extract characters
        characters = script.get("characters", {})
        for name, char_data in characters.items():
            if name not in all_characters:
                all_characters[name] = char_data.copy()
                print(f"      Found character: {name}")
            else:
                # Merge data (prefer version with character sheet)
                if char_data.get("character_sheet") and not all_characters[name].get("character_sheet"):
                    all_characters[name] = char_data.copy()
                    print(f"      Updated character: {name} (has character sheet)")

        # Extract episode info
        novel_info = script.get("novel", {})
        scenes_count = len(script.get("scenes", []))

        # Try to infer episode number from filename or content
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

        # Add episode info (excluding stat fields, computed on read by StatusCalculator)
        episodes.append(
            {
                "episode": episode_num,
                "title": novel_info.get("chapter", script_file.stem),
                "script_file": f"scripts/{script_file.name}",
            }
        )
        print(f"      Episode {episode_num}: {scenes_count} scenes")

    # Deduplicate and sort episodes
    seen_episodes = {}
    for ep in episodes:
        if ep["episode"] not in seen_episodes:
            seen_episodes[ep["episode"]] = ep
    episodes = sorted(seen_episodes.values(), key=lambda x: x["episode"])

    # Build project.json
    project_title = project_name
    if script_files:
        with open(script_files[0], encoding="utf-8") as f:
            first_script = json.load(f)
            project_title = first_script.get("novel", {}).get("title", project_name)

    # Build project.json (excluding status field, computed on read by StatusCalculator)
    project_data = {
        "title": project_title,
        "style": "",
        "episodes": episodes,
        "characters": all_characters,
        "clues": {},
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "migrated_from": "script_based_characters",
        },
    }

    # Count completed character sheets (for log output only)
    completed_chars = 0
    for name, char_data in all_characters.items():
        sheet = char_data.get("character_sheet")
        if sheet:
            sheet_path = project_dir / sheet
            if sheet_path.exists():
                completed_chars += 1

    # Create clues directory
    clues_dir = project_dir / "clues"
    if not clues_dir.exists():
        if not dry_run:
            clues_dir.mkdir(parents=True, exist_ok=True)
        print("\n  Created directory: clues/")

    print("\n  Migration summary:")
    print(f"      - Characters: {len(all_characters)} ({completed_chars} with character sheet)")
    print(f"      - Episodes: {len(episodes)}")
    print("      - Clues: 0 (to be added)")

    if dry_run:
        print("\n  Preview mode - no files will be written")
        print("\n  project.json to be created:")
        print(json.dumps(project_data, ensure_ascii=False, indent=2)[:500] + "...")
    else:
        # Write project.json
        with open(project_file, "w", encoding="utf-8") as f:
            json.dump(project_data, f, ensure_ascii=False, indent=2)
        print("\n  Created project.json")

        # Optional: remove characters field from scripts (keep original file as backup)
        # Retaining characters in scripts for backward compatibility
        print("  Retaining characters field in scripts for backward compatibility")

    return True


def main():
    parser = argparse.ArgumentParser(description="Migrate project data to project.json")
    parser.add_argument("project", nargs="?", help="Project name, or use --all to migrate all projects")
    parser.add_argument("--all", action="store_true", help="Migrate all projects")
    parser.add_argument("--dry-run", action="store_true", help="Preview mode, no actual changes")
    parser.add_argument("--projects-root", default=None, help="Projects root directory")

    args = parser.parse_args()

    if not args.project and not args.all:
        parser.print_help()
        print("\nERROR: Please specify a project name or use --all")
        sys.exit(1)

    # Initialize ProjectManager
    pm = ProjectManager(projects_root=args.projects_root)

    print("Starting migration...")
    print(f"   Projects root: {pm.projects_root}")

    if args.dry_run:
        print("   Preview mode enabled")

    success_count = 0
    fail_count = 0

    if args.all:
        projects = pm.list_projects()
        print(f"   Found {len(projects)} project(s)")

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
    print("Migration complete!")
    print(f"   Success: {success_count}")
    print(f"   Failed: {fail_count}")
    print("=" * 50)

    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
