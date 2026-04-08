#!/usr/bin/env python3
"""
Migrate existing projects to use .claude and CLAUDE.md symlinks.

Creates symlinks for projects that don't have them yet:
- .claude -> ../../agent_runtime_profile/.claude
- CLAUDE.md -> ../../agent_runtime_profile/CLAUDE.md

Usage:
    python scripts/migrate_claude_symlinks.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

SYMLINKS = {
    ".claude": "../../agent_runtime_profile/.claude",
    "CLAUDE.md": "../../agent_runtime_profile/CLAUDE.md",
}


def main():
    parser = argparse.ArgumentParser(description="Create symlinks for existing projects")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    projects_dir = project_root / "projects"
    profile_dir = project_root / "agent_runtime_profile"

    if not profile_dir.exists():
        print(f"ERROR: {profile_dir} does not exist")
        sys.exit(1)

    if not projects_dir.exists():
        print("No projects directory found")
        return

    created = 0
    skipped = 0
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        for name, rel_target in SYMLINKS.items():
            symlink_path = project_dir / name
            target_source = profile_dir / (".claude" if name == ".claude" else "CLAUDE.md")

            if not target_source.exists():
                continue

            if symlink_path.is_symlink() and not symlink_path.exists():
                # Broken symlink
                if args.dry_run:
                    print(f"  WOULD REPAIR {project_dir.name}/{name} (broken symlink)")
                else:
                    symlink_path.unlink()
                    symlink_path.symlink_to(Path(rel_target))
                    print(f"  REPAIRED {project_dir.name}/{name} -> {rel_target}")
                created += 1
            elif symlink_path.exists():
                print(f"  SKIP {project_dir.name}/{name} (already exists)")
                skipped += 1
            else:
                # Missing
                if args.dry_run:
                    print(f"  WOULD CREATE {project_dir.name}/{name} -> {rel_target}")
                else:
                    symlink_path.symlink_to(Path(rel_target))
                    print(f"  CREATED {project_dir.name}/{name} -> {rel_target}")
                created += 1

    action = "Would create" if args.dry_run else "Created"
    print(f"\n{action} {created} symlink(s), skipped {skipped}")


if __name__ == "__main__":
    main()
