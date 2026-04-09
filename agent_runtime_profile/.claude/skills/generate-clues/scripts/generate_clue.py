#!/usr/bin/env python3
"""
Clue Generator - Generate clue design sheets using the Gemini API

Usage:
    python generate_clue.py --all
    python generate_clue.py --clue "jade pendant"
    python generate_clue.py --clues "jade pendant" "old scholar tree"
    python generate_clue.py --list

Example:
    python generate_clue.py --all
    python generate_clue.py --clue "old scholar tree"
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


def generate_clue(clue_name: str) -> Path:
    """
    Generate a single clue design sheet

    Args:
        clue_name: clue name

    Returns:
        path to the generated image
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # get clue information
    clue = pm.get_clue(project_name, clue_name)
    clue_type = clue.get("type", "prop")
    description = clue.get("description", "")

    if not description:
        raise ValueError(f"Clue '{clue_name}' has no description; please add a description first")

    print(f"🎨 Generating clue design sheet: {clue_name}")
    print(f"   Type: {clue_type}")
    print(f"   Description: {description[:50]}..." if len(description) > 50 else f"   Description: {description}")

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
    version_text = f" (version v{version})" if version is not None else ""
    print(f"✅ Clue design sheet saved: {output_path}{version_text}")
    return output_path


def list_pending_clues() -> None:
    """
    List pending clues for design sheet generation
    """
    pm, project_name = ProjectManager.from_cwd()
    pending = pm.get_pending_clues(project_name)

    if not pending:
        print(f"✅ All major clues in project '{project_name}' already have design sheets")
        return

    print(f"\n📋 Pending clues ({len(pending)}):\n")
    for clue in pending:
        clue_type = clue.get("type", "prop")
        type_emoji = "📦" if clue_type == "prop" else "🏠"
        print(f"  {type_emoji} {clue['name']}")
        print(f"     Type: {clue_type}")
        print(f"     Description: {clue.get('description', '')[:60]}...")
        print()


def generate_batch_clues(
    clue_names: list[str] | None = None,
) -> tuple[int, int]:
    """
    Batch generate clue design sheets (all queued; processed in parallel by the Worker)

    Args:
        clue_names: list of specified clue names. None means all pending clues.

    Returns:
        (success count, failure count)
    """
    pm, project_name = ProjectManager.from_cwd()
    project = pm.load_project(project_name)
    clues_dict = project.get("clues", {})

    if clue_names:
        names_to_process = []
        for name in clue_names:
            if name not in clues_dict:
                print(f"⚠️  Clue '{name}' does not exist in project.json, skipping")
                continue
            if not clues_dict[name].get("description"):
                print(f"⚠️  Clue '{name}' is missing a description, skipping")
                continue
            names_to_process.append(name)
    else:
        pending = pm.get_pending_clues(project_name)
        names_to_process = [c["name"] for c in pending]

    if not names_to_process:
        print("✅ No clues need to be generated")
        return (0, 0)
    specs = [
        BatchTaskSpec(
            task_type="clue",
            media_type="image",
            resource_id=name,
            payload={"prompt": clues_dict[name]["description"]},
        )
        for name in names_to_process
    ]

    total = len(specs)
    print(f"\n🚀 Submitting {total} clue design sheets to the generation queue...\n")

    def on_success(br: BatchTaskResult) -> None:
        version = (br.result or {}).get("version")
        version_text = f" (version v{version})" if version is not None else ""
        print(f"✅ Clue design sheet: {br.resource_id} complete{version_text}")

    def on_failure(br: BatchTaskResult) -> None:
        print(f"❌ Clue design sheet: {br.resource_id} failed - {br.error}")

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
    parser = argparse.ArgumentParser(description="Generate clue design sheets")
    parser.add_argument("--all", action="store_true", help="Generate all pending clues")
    parser.add_argument("--clue", help="Specify a single clue name")
    parser.add_argument("--clues", nargs="+", help="Specify multiple clue names")
    parser.add_argument("--list", action="store_true", help="List pending clues")

    args = parser.parse_args()

    try:
        if args.list:
            list_pending_clues()
        elif args.all:
            _, fail = generate_batch_clues()
            sys.exit(0 if fail == 0 else 1)
        elif args.clues:
            _, fail = generate_batch_clues(args.clues)
            sys.exit(0 if fail == 0 else 1)
        elif args.clue:
            output_path = generate_clue(args.clue)
            print(f"\n🖼️  Please view the generated image: {output_path}")
        else:
            parser.print_help()
            print("\n❌ Please specify --all, --clues, --clue, or --list")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
