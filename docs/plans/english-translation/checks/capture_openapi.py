"""Capture OpenAPI declarations without starting application lifespan or a server."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    with tempfile.TemporaryDirectory(prefix="arcreel-openapi-capture-") as data_root:
        os.environ["ARCREEL_DATA_DIR"] = data_root
        import server.app as module

        imported = Path(module.__file__).resolve()
        if not imported.is_relative_to(source):
            raise RuntimeError(f"Unexpected application import: {imported}")
        args.output.write_text(
            json.dumps(
                {"import_source": str(imported), "openapi": module.app.openapi()},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
