#!/usr/bin/env python3
"""
peek_split_point.py - Split point detection script

Shows context near the target word count to help the agent and user decide on a natural break point.

Usage:
    python peek_split_point.py --source source/novel.txt --target 1000
    python peek_split_point.py --source source/novel.txt --target 1000 --context 300
"""

import argparse
import json
import sys
from pathlib import Path

# import shared utilities
sys.path.insert(0, str(Path(__file__).parent))
from _text_utils import count_chars, find_char_offset, find_natural_breakpoints


def main():
    parser = argparse.ArgumentParser(description="Detect context near the split point")
    parser.add_argument("--source", required=True, help="Source file path")
    parser.add_argument("--target", required=True, type=int, help="Target word count (effective characters)")
    parser.add_argument("--context", default=200, type=int, help="Context size in characters (default 200)")
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.is_relative_to(Path.cwd().resolve()):
        print(f"Error: source file path is outside the current project directory: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not source_path.exists():
        print(f"Error: source file does not exist: {source_path}", file=sys.stderr)
        sys.exit(1)

    text = source_path.read_text(encoding="utf-8")
    total_chars = count_chars(text)

    if args.target >= total_chars:
        print(f"Error: target word count ({args.target}) exceeds or equals total effective characters ({total_chars})", file=sys.stderr)
        sys.exit(1)

    # locate the original text offset corresponding to the target word count
    target_offset = find_char_offset(text, args.target)

    # find nearby natural break points
    breakpoints = find_natural_breakpoints(text, target_offset, window=args.context)

    # extract context
    ctx_start = max(0, target_offset - args.context)
    ctx_end = min(len(text), target_offset + args.context)
    before_context = text[ctx_start:target_offset]
    after_context = text[target_offset:ctx_end]

    # output results
    result = {
        "source": str(source_path),
        "total_chars": total_chars,
        "target_chars": args.target,
        "target_offset": target_offset,
        "context_before": before_context,
        "context_after": after_context,
        "nearby_breakpoints": breakpoints[:10],  # take only the 10 closest
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
