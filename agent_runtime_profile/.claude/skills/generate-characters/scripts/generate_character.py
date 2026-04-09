#!/usr/bin/env python3
"""
Character Generator - Generate character design sheets using the Gemini API

Usage:
    python generate_character.py --character "Zhang San"
    python generate_character.py --characters "Zhang San" "Li Si"
    python generate_character.py --all
    python generate_character.py --list

Note:
    Reference images are automatically read from the reference_image field in project.json
"""

import argparse
import sys
from pathlib import Path

from lib.generation_queue_client import (
    BatchTaskResult,
    BatchTaskSpec,
    batch_enqueue_and_wait_sync,
)
from lib.generation_queue_client import (
    enqueue_and_wait_sync as enqueue_and_wait,
)
from lib.project_manager import ProjectManager


def generate_character(
    character_name: str,
) -> Path:
    """
    Generate a single character design sheet

    Args:
        character_name: character name

    Returns:
        path to the generated image
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # get character information from project.json
    project = pm.load_project(project_name)

    description = ""
    if "characters" in project and character_name in project["characters"]:
        char_info = project["characters"][character_name]
        description = char_info.get("description", "")

    if not description:
        raise ValueError(f"Character '{character_name}' has no description; please add a description in project.json first")

    print(f"🎨 Generating character design sheet: {character_name}")
    print(f"   Description: {description[:50]}...")

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
    version_text = f" (version v{version})" if version is not None else ""
    print(f"✅ Character design sheet saved: {output_path}{version_text}")
    return output_path


def list_pending_characters() -> None:
    """List characters pending design sheet generation"""
    pm, project_name = ProjectManager.from_cwd()
    pending = pm.get_pending_characters(project_name)

    if not pending:
        print(f"✅ All characters in project '{project_name}' already have design sheets")
        return

    print(f"\n📋 Pending characters ({len(pending)}):\n")
    for char in pending:
        print(f"  🧑 {char['name']}")
        desc = char.get("description", "")
        print(f"     Description: {desc[:60]}..." if len(desc) > 60 else f"     Description: {desc}")
        print()


def generate_batch_characters(
    character_names: list[str] | None = None,
) -> tuple[int, int]:
    """
    Batch generate character design sheets (all queued; processed in parallel by the Worker)

    Args:
        character_names: list of specified character names. None means all pending characters.

    Returns:
        (success count, failure count)
    """
    pm, project_name = ProjectManager.from_cwd()
    project = pm.load_project(project_name)

    if character_names:
        chars = project.get("characters", {})
        names_to_process = []
        for name in character_names:
            if name not in chars:
                print(f"⚠️  Character '{name}' does not exist in project.json, skipping")
                continue
            if not chars[name].get("description"):
                print(f"⚠️  Character '{name}' is missing a description, skipping")
                continue
            names_to_process.append(name)
    else:
        pending = pm.get_pending_characters(project_name)
        names_to_process = [c["name"] for c in pending]

    if not names_to_process:
        print("✅ No characters need to be generated")
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
    print(f"\n🚀 Submitting {total} character design sheets to the generation queue...\n")

    def on_success(br: BatchTaskResult) -> None:
        version = (br.result or {}).get("version")
        version_text = f" (version v{version})" if version is not None else ""
        print(f"✅ Character design sheet: {br.resource_id} complete{version_text}")

    def on_failure(br: BatchTaskResult) -> None:
        print(f"❌ Character design sheet: {br.resource_id} failed - {br.error}")

    successes, failures = batch_enqueue_and_wait_sync(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )

    print(f"\n{'=' * 40}")
    print("Generation complete!")
    print(f"   ✅ Successful: {len(successes)}")
    print(f"   ❌ Failed: {len(failures)}")
    print(f"{'=' * 40}")

    return (len(successes), len(failures))


def main():
    parser = argparse.ArgumentParser(description="Generate character design sheets")
    parser.add_argument("--character", help="Specify a single character name")
    parser.add_argument("--characters", nargs="+", help="Specify multiple character names")
    parser.add_argument("--all", action="store_true", help="Generate all pending characters")
    parser.add_argument("--list", action="store_true", help="List pending characters")

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
            print(f"\n🖼️  Please view the generated image: {output_path}")
        else:
            parser.print_help()
            print("\n❌ Please specify --all, --characters, --character, or --list")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
