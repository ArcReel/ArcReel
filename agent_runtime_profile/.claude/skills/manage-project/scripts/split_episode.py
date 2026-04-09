#!/usr/bin/env python3
"""
split_episode.py - Execute episode splitting

Uses target word count + anchor text together to locate the split position,
splitting the novel into episode_N.txt and _remaining.txt.
The target word count narrows the search window; the anchor text provides precise positioning.

Usage:
    # Dry run (preview only)
    python split_episode.py --source source/novel.txt --episode 1 --target 1000 --anchor "He turned and left." --dry-run

    # Actual execution
    python split_episode.py --source source/novel.txt --episode 1 --target 1000 --anchor "He turned and left."
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _text_utils import find_char_offset


def find_anchor_near_target(text: str, anchor: str, target_offset: int, window: int = 500) -> list[int]:
    """Find anchor text within a window near the target offset; returns a list of match end offsets (sorted by distance)."""
    search_start = max(0, target_offset - window)
    search_end = min(len(text), target_offset + window)
    search_region = text[search_start:search_end]

    positions = []
    start = 0
    while True:
        idx = search_region.find(anchor, start)
        if idx == -1:
            break
        abs_pos = search_start + idx + len(anchor)  # absolute offset of the anchor end
        positions.append(abs_pos)
        start = idx + 1

    # sort by distance from target_offset
    positions.sort(key=lambda p: abs(p - target_offset))
    return positions


def main():
    parser = argparse.ArgumentParser(description="Execute episode splitting")
    parser.add_argument("--source", required=True, help="Source file path")
    parser.add_argument("--episode", required=True, type=int, help="Episode number")
    parser.add_argument("--target", required=True, type=int, help="Target word count (consistent with peek's --target)")
    parser.add_argument("--anchor", required=True, help="Text fragment before the split point (10-20 characters)")
    parser.add_argument("--context", default=500, type=int, help="Search window size (default 500 characters)")
    parser.add_argument("--dry-run", action="store_true", help="Show split preview only; do not write files")
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.is_relative_to(Path.cwd().resolve()):
        print(f"Error: source file path is outside the current project directory: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not source_path.exists():
        print(f"Error: source file does not exist: {source_path}", file=sys.stderr)
        sys.exit(1)

    text = source_path.read_text(encoding="utf-8")

    # use target word count to calculate approximate offset position
    target_offset = find_char_offset(text, args.target)

    # search for anchor near the target offset
    positions = find_anchor_near_target(text, args.anchor, target_offset, window=args.context)

    if len(positions) == 0:
        print(
            f'Error: anchor text not found near target word count {args.target} (±{args.context} character window): "{args.anchor}"',
            file=sys.stderr,
        )
        sys.exit(1)

    if len(positions) > 1:
        print(
            f"Warning: anchor text matched {len(positions)} times within the window; using the match closest to the target.",
            file=sys.stderr,
        )
        for i, pos in enumerate(positions):
            ctx_start = max(0, pos - len(args.anchor) - 10)
            ctx_end = min(len(text), pos + 10)
            distance = abs(pos - target_offset)
            marker = " <- selected" if i == 0 else ""
            print(f"  Match {i + 1} (distance {distance}): ...{text[ctx_start:ctx_end]}...{marker}", file=sys.stderr)

    split_pos = positions[0]
    part_before = text[:split_pos]
    part_after = text[split_pos:]

    # show split preview
    preview_len = 50
    before_preview = part_before[-preview_len:] if len(part_before) > preview_len else part_before
    after_preview = part_after[:preview_len] if len(part_after) > preview_len else part_after

    print(f"Target word count: {args.target}, target offset: {target_offset}")
    print(f"Split position: character {split_pos}")
    print(f"End of first part: ...{before_preview}")
    print(f"Start of second part: {after_preview}...")
    print(f"First half: {len(part_before)} characters")
    print(f"Second half: {len(part_after)} characters")

    if args.dry_run:
        print("\n[Dry Run] File not written. Remove --dry-run to execute after confirming.")
        return

    # actually write files
    output_dir = source_path.parent
    episode_file = output_dir / f"episode_{args.episode}.txt"
    remaining_file = output_dir / "_remaining.txt"

    episode_file.write_text(part_before, encoding="utf-8")
    remaining_file.write_text(part_after, encoding="utf-8")

    print("\nGenerated:")
    print(f"  {episode_file} ({len(part_before)} characters)")
    print(f"  {remaining_file} ({len(part_after)} characters)")
    print(f"  Original file unchanged: {source_path}")


if __name__ == "__main__":
    main()
