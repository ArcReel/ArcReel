"""
Clean up redundant fields in existing projects.

This script migrates existing data by removing redundant fields
that have been changed to computed-on-read values.
Make sure to back up your data before running this script.

Usage:
    python scripts/migrate_clean_redundant_fields.py
    python scripts/migrate_clean_redundant_fields.py --dry-run  # preview only, no modifications
"""

import argparse
import json
from pathlib import Path


def migrate_project(project_dir: Path, dry_run: bool = False) -> dict:
    """
    Clean up redundant fields in a single project.

    Args:
        project_dir: Path to the project directory
        dry_run: If True, preview only without making changes

    Returns:
        Migration statistics
    """
    stats = {"project_cleaned": False, "scripts_cleaned": 0, "fields_removed": []}

    # Clean up project.json
    project_file = project_dir / "project.json"
    if project_file.exists():
        with open(project_file, encoding="utf-8") as f:
            project = json.load(f)

        original = json.dumps(project)

        # Remove status object (now computed on read)
        if "status" in project:
            stats["fields_removed"].append("project.json: status")
            if not dry_run:
                project.pop("status", None)

        # Remove computed fields from episodes
        for ep in project.get("episodes", []):
            if "scenes_count" in ep:
                stats["fields_removed"].append(f"project.json: episodes[{ep.get('episode')}].scenes_count")
                if not dry_run:
                    ep.pop("scenes_count", None)
            if "status" in ep:
                stats["fields_removed"].append(f"project.json: episodes[{ep.get('episode')}].status")
                if not dry_run:
                    ep.pop("status", None)

        if json.dumps(project) != original:
            stats["project_cleaned"] = True
            if not dry_run:
                with open(project_file, "w", encoding="utf-8") as f:
                    json.dump(project, f, ensure_ascii=False, indent=2)

    # Clean up scripts/*.json
    scripts_dir = project_dir / "scripts"
    if scripts_dir.exists():
        for script_file in scripts_dir.glob("*.json"):
            with open(script_file, encoding="utf-8") as f:
                script = json.load(f)

            original = json.dumps(script)
            script_name = script_file.name

            # Remove redundant fields
            if "characters_in_episode" in script:
                stats["fields_removed"].append(f"{script_name}: characters_in_episode")
                if not dry_run:
                    script.pop("characters_in_episode", None)

            if "clues_in_episode" in script:
                stats["fields_removed"].append(f"{script_name}: clues_in_episode")
                if not dry_run:
                    script.pop("clues_in_episode", None)

            if "duration_seconds" in script:
                stats["fields_removed"].append(f"{script_name}: duration_seconds")
                if not dry_run:
                    script.pop("duration_seconds", None)

            if "metadata" in script:
                if "total_scenes" in script["metadata"]:
                    stats["fields_removed"].append(f"{script_name}: metadata.total_scenes")
                    if not dry_run:
                        script["metadata"].pop("total_scenes", None)
                if "estimated_duration_seconds" in script["metadata"]:
                    stats["fields_removed"].append(f"{script_name}: metadata.estimated_duration_seconds")
                    if not dry_run:
                        script["metadata"].pop("estimated_duration_seconds", None)

            if json.dumps(script) != original:
                stats["scripts_cleaned"] += 1
                if not dry_run:
                    with open(script_file, "w", encoding="utf-8") as f:
                        json.dump(script, f, ensure_ascii=False, indent=2)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Clean up redundant fields in projects")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no modifications")
    parser.add_argument("--projects-root", default="projects", help="Projects root directory")
    args = parser.parse_args()

    projects_root = Path(args.projects_root)

    if not projects_root.exists():
        print(f"ERROR: Projects root directory does not exist: {projects_root}")
        return

    if args.dry_run:
        print("Preview mode - no files will be modified\n")

    total_stats = {"projects_processed": 0, "projects_cleaned": 0, "scripts_cleaned": 0, "fields_removed": []}

    for project_dir in projects_root.iterdir():
        if project_dir.is_dir() and not project_dir.name.startswith("."):
            print(f"Processing project: {project_dir.name}")
            stats = migrate_project(project_dir, args.dry_run)

            total_stats["projects_processed"] += 1
            if stats["project_cleaned"] or stats["scripts_cleaned"] > 0:
                total_stats["projects_cleaned"] += 1
            total_stats["scripts_cleaned"] += stats["scripts_cleaned"]
            total_stats["fields_removed"].extend(stats["fields_removed"])

            if stats["fields_removed"]:
                for field in stats["fields_removed"]:
                    print(f"  - Removed: {field}")
            else:
                print("  - Nothing to clean")

    print(f"\n{'Preview' if args.dry_run else 'Migration'} complete:")
    print(f"  - Projects processed: {total_stats['projects_processed']}")
    print(f"  - Projects cleaned: {total_stats['projects_cleaned']}")
    print(f"  - Scripts cleaned: {total_stats['scripts_cleaned']}")
    print(f"  - Fields removed: {len(total_stats['fields_removed'])}")

    if args.dry_run and total_stats["fields_removed"]:
        print("\nTo run the actual migration, remove the --dry-run flag and re-run")


if __name__ == "__main__":
    main()
