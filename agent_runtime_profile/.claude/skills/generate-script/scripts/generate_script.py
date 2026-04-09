#!/usr/bin/env python3
"""
generate_script.py - Generate JSON scripts using Gemini

Usage:
    python generate_script.py --episode <N>
    python generate_script.py --episode <N> --output <path>
    python generate_script.py --episode <N> --dry-run

Examples:
    python generate_script.py --episode 1
    python generate_script.py --episode 1 --output scripts/ep1.json
"""

import argparse
import sys
from pathlib import Path

# allow running this script directly from any working directory in the repo
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/generate-script/scripts -> repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.project_manager import ProjectManager
from lib.script_generator import ScriptGenerator


def main():
    parser = argparse.ArgumentParser(
        description="Generate JSON scripts using Gemini",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --episode 1
    %(prog)s --episode 1 --output scripts/ep1.json
    %(prog)s --episode 1 --dry-run
        """,
    )

    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number")

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output file path (default: scripts/episode_N.json)",
    )

    parser.add_argument("--dry-run", action="store_true", help="Show prompt only; do not actually call the API")

    args = parser.parse_args()

    # build project path
    pm, project_name = ProjectManager.from_cwd()
    project_path = pm.get_project_path(project_name)

    # check whether intermediate file exists (determine filename based on content_mode)
    import json as _json

    project_json_path = project_path / "project.json"
    content_mode = "narration"
    if project_json_path.exists():
        try:
            content_mode = _json.loads(project_json_path.read_text(encoding="utf-8")).get("content_mode", "narration")
        except Exception:
            pass  # fall back to default "narration" if reading or parsing fails

    drafts_path = project_path / "drafts" / f"episode_{args.episode}"
    if content_mode == "drama":
        step1_path = drafts_path / "step1_normalized_script.md"
        step1_hint = "normalize_drama_script.py"
    else:
        step1_path = drafts_path / "step1_segments.md"
        step1_hint = "segment splitting (Step 1)"

    if not step1_path.exists():
        print(f"❌ Step 1 file not found: {step1_path}")
        print(f"   Please complete {step1_hint} first")
        sys.exit(1)

    try:
        if args.dry_run:
            # dry-run does not need a client
            generator = ScriptGenerator(project_path)
            print("=" * 60)
            print("DRY RUN - The following is the prompt that would be sent to Gemini:")
            print("=" * 60)
            prompt = generator.build_prompt(args.episode)
            print(prompt)
            print("=" * 60)
            return

        # actual generation (async)
        import asyncio

        async def _run():
            generator = await ScriptGenerator.create(project_path)
            output_path = Path(args.output) if args.output else None
            return await generator.generate(
                episode=args.episode,
                output_path=output_path,
            )

        result_path = asyncio.run(_run())

        print(f"\n✅ Script generation complete: {result_path}")

    except FileNotFoundError as e:
        print(f"❌ File error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Generation failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
