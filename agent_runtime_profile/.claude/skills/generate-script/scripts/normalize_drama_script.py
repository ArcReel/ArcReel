#!/usr/bin/env python3
"""
normalize_drama_script.py - Generate normalized scripts using Gemini Pro

Converts the novel source text in source/ into a Markdown-format normalized script
(step1_normalized_script.md) for consumption by generate_script.py.

Usage:
    python normalize_drama_script.py --episode <N>
    python normalize_drama_script.py --episode <N> --source <file>
    python normalize_drama_script.py --episode <N> --dry-run
"""

import argparse
import sys
from pathlib import Path

# allow running this script directly from any working directory in the repo
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/generate-script/scripts -> repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio

from lib.project_manager import ProjectManager
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_backends.factory import create_text_backend_for_task


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict,
    style: str,
    characters: dict,
    clues: dict,
) -> str:
    """Build the prompt for the normalized script"""

    char_list = "\n".join(f"- {name}" for name in characters.keys()) or "(none)"
    clue_list = "\n".join(f"- {name}" for name in clues.keys()) or "(none)"

    return f"""Your task is to adapt the novel source text into a structured storyboard scene table (Markdown format) for subsequent AI video generation.

## Project Information

<overview>
{project_overview.get("synopsis", "")}

Genre: {project_overview.get("genre", "")}
Core theme: {project_overview.get("theme", "")}
World setting: {project_overview.get("world_setting", "")}
</overview>

<style>
{style}
</style>

<characters>
{char_list}
</characters>

<clues>
{clue_list}
</clues>

## Novel Source Text

<novel>
{novel_text}
</novel>

## Output Requirements

Adapt the novel into a scene list using Markdown table format:

| Scene ID | Scene Description | Duration | Scene Type | segment_break |
|---------|---------|------|---------|---------------|
| E{{N}}S01 | Detailed scene description... | 8 | Drama | Yes |
| E{{N}}S02 | Detailed scene description... | 8 | Dialogue | No |

Rules:
- Scene ID format: E{{episode}}S{{two-digit sequence}} (e.g., E1S01, E1S02)
- Scene description: adapted script-style description including character actions, dialogue, and environment; suitable for visual presentation
- Duration: 4, 6, or 8 seconds (default 8 seconds; simple visuals can use 4 or 6 seconds)
- Scene type: Drama, Action, Dialogue, Transition, Establishing shot
- segment_break: mark "Yes" at scene transition points; mark "No" for the same continuous scene
- Each scene should be an independent visual image completable within the specified duration
- Avoid a single scene containing multiple different actions or visual transitions

Output only the Markdown table; do not include any other explanatory text.
"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate normalized scripts using Gemini Pro",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --episode 1
    %(prog)s --episode 1 --source source/chapter1.txt
    %(prog)s --episode 1 --dry-run
        """,
    )

    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number")
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        default=None,
        help="Specify novel source file path (default: read all files in the source/ directory)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show prompt only; do not actually call the API")

    args = parser.parse_args()

    # build project path
    pm, project_name = ProjectManager.from_cwd()
    project_path = pm.get_project_path(project_name)
    project = pm.load_project(project_name)

    # read novel source text
    if args.source:
        source_path = (project_path / args.source).resolve()
        if not source_path.is_relative_to(project_path.resolve()):
            print(f"❌ Path is outside the project directory: {source_path}")
            sys.exit(1)
        if not source_path.exists():
            print(f"❌ Source file not found: {source_path}")
            sys.exit(1)
        novel_text = source_path.read_text(encoding="utf-8")
    else:
        source_dir = project_path / "source"
        if not source_dir.exists() or not any(source_dir.iterdir()):
            print(f"❌ source/ directory is empty or does not exist: {source_dir}")
            sys.exit(1)
        # read all text files sorted by filename
        texts = []
        for f in sorted(source_dir.iterdir()):
            if f.suffix in (".txt", ".md", ".text"):
                texts.append(f.read_text(encoding="utf-8"))
        novel_text = "\n\n".join(texts)

    if not novel_text.strip():
        print("❌ Novel source text is empty")
        sys.exit(1)

    # build prompt
    prompt = build_normalize_prompt(
        novel_text=novel_text,
        project_overview=project.get("overview", {}),
        style=project.get("style", ""),
        characters=project.get("characters", {}),
        clues=project.get("clues", {}),
    )

    if args.dry_run:
        print("=" * 60)
        print("DRY RUN - The following is the prompt that would be sent to Gemini:")
        print("=" * 60)
        print(prompt)
        print("=" * 60)
        print(f"\nPrompt length: {len(prompt)} characters")
        return

    # call TextBackend
    async def _run():
        backend = await create_text_backend_for_task(TextTaskType.SCRIPT)
        print(f"Generating normalized script using {backend.model}...")
        result = await backend.generate(TextGenerationRequest(prompt=prompt))
        return result.text

    response = asyncio.run(_run())

    # save file
    drafts_dir = project_path / "drafts" / f"episode_{args.episode}"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    step1_path = drafts_dir / "step1_normalized_script.md"
    step1_path.write_text(response.strip(), encoding="utf-8")
    print(f"✅ Normalized script saved: {step1_path}")

    # brief statistics
    lines = [
        line
        for line in response.split("\n")
        if line.strip().startswith("|") and "Scene ID" not in line and "---" not in line
    ]
    scene_count = len(lines)
    print(f"\n📊 Generation statistics: {scene_count} scenes")


if __name__ == "__main__":
    main()
