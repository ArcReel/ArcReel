"""Compare emitted CSS content independently of hashed output filenames."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def capture(directory: Path) -> list[dict[str, str | int]]:
    """Record all emitted stylesheet bytes, ignoring source-map files."""
    files = sorted(directory.rglob("*.css"))
    if not files:
        raise ValueError(f"No stylesheets found in {directory}")
    return [
        {
            "path": str(path.relative_to(directory)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in files
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    baseline, candidate = capture(args.baseline), capture(args.candidate)

    def signatures(rows: list[dict[str, str | int]]) -> Counter[tuple[str | int, str | int]]:
        return Counter((row["bytes"], row["sha256"]) for row in rows)

    identical = signatures(baseline) == signatures(candidate)
    result = {
        "baseline_css": baseline,
        "candidate_css": candidate,
        "stylesheet_content_multisets_identical": identical,
        "scope": "CSS bytes only, built with the same frozen dependencies and configuration. Does not certify runtime/model behavior.",
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Production CSS content identical: {identical}")
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
