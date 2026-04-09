#!/usr/bin/env python3
"""
add_characters_clues.py - Batch add characters/clues to project.json

Usage (must be executed from within the project directory; must be a single line):
    python .claude/skills/manage-project/scripts/add_characters_clues.py --characters '{"CharacterName": {"description": "...", "voice_style": "..."}}' --clues '{"ClueName": {"type": "prop", "description": "...", "importance": "major"}}'
"""

import argparse
import json
import sys
from pathlib import Path

# allow running this script directly from any working directory in the repo
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/manage-project/scripts -> repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.data_validator import validate_project
from lib.project_manager import ProjectManager


def main():
    parser = argparse.ArgumentParser(
        description="Batch add characters/clues to project.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (must be executed from within the project directory; must be a single line):
    %(prog)s --characters '{"Li Bai": {"description": "swordsman in white", "voice_style": "bold"}}'
    %(prog)s --clues '{"Jade Pendant": {"type": "prop", "description": "warm white jade", "importance": "major"}}'
        """,
    )

    parser.add_argument(
        "--characters",
        type=str,
        default=None,
        help="Character data in JSON format",
    )
    parser.add_argument(
        "--clues",
        type=str,
        default=None,
        help="Clue data in JSON format",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON from stdin (containing characters and/or clues fields)",
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
        print("❌ No character or clue data provided")
        sys.exit(1)

    pm, project_name = ProjectManager.from_cwd()

    # add characters
    chars_added = 0
    chars_skipped = 0
    if characters:
        project = pm.load_project(project_name)
        existing = project.get("characters", {})
        chars_skipped = sum(1 for name in characters if name in existing)
        chars_added = pm.add_characters_batch(project_name, characters)
        print(f"Characters: {chars_added} added, {chars_skipped} skipped (already exist)")

    # add clues
    clues_added = 0
    clues_skipped = 0
    if clues:
        project = pm.load_project(project_name)
        existing = project.get("clues", {})
        clues_skipped = sum(1 for name in clues if name in existing)
        clues_added = pm.add_clues_batch(project_name, clues)
        print(f"Clues: {clues_added} added, {clues_skipped} skipped (already exist)")

    # data validation
    result = validate_project(project_name, projects_root=str(pm.projects_root))
    if result.valid:
        print("✅ Data validation passed")
    else:
        print("⚠️ Data validation found issues:")
        for error in result.errors:
            print(f"  Error: {error}")
        for warning in result.warnings:
            print(f"  Warning: {warning}")
        sys.exit(1)

    # summary
    total_added = chars_added + clues_added
    if total_added > 0:
        print(f"\n✅ Done: {total_added} items added in total")
    else:
        print("\nℹ️ All data already exists; nothing added")


if __name__ == "__main__":
    main()
