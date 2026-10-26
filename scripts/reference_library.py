"""Archive PDF editions and publish one consolidated project reference context.

python -m scripts.reference_library --project PATH --id world-handoff archive FILE.pdf
python -m scripts.reference_library --project PATH --id world-handoff publish MAP.md --revision SHA256
python -m scripts.reference_library --project PATH context
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lib.project.reference_library import (
    current_reference_context,
    publish_reference_context,
    register_pdf_reference,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--id", default="world-handoff")
    commands = parser.add_subparsers(dest="command", required=True)
    archive = commands.add_parser("archive")
    archive.add_argument("pdf", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("map", type=Path)
    publish.add_argument("--revision", required=True)
    commands.add_parser("context")
    args = parser.parse_args()
    if not (args.project / "project.json").is_file():
        parser.error("--project must be an existing ArcReel project directory")
    if args.command == "archive":
        result = register_pdf_reference(args.project, args.id, args.pdf).model_dump(mode="json")
    elif args.command == "publish":
        result = publish_reference_context(
            args.project, args.id, expected_revision=args.revision, context=args.map.read_text(encoding="utf-8")
        ).model_dump(mode="json")
    else:
        result = current_reference_context(args.project)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
