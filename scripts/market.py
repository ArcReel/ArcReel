"""Market-workflow language boundary: subpackage validates/generates; application supplies diagnostics/summary copy.

Usage: ``python -m scripts.market [--locale zh|en|vi] check|generate <dir>``.
"""

from __future__ import annotations

import argparse
import runpy
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from arcreel_market_core.market.cli import main as market_main

CATALOG_ROOT = Path(__file__).resolve().parents[1] / "lib" / "i18n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument(
        "--locale", choices=sorted(path.parent.name for path in CATALOG_ROOT.glob("*/validation.py")), default="zh"
    )
    args, remaining = parser.parse_known_args(argv)
    # lib.__init__ loads project/database dependencies; workflows install only the subpackage, so read standalone messages directly.
    messages = runpy.run_path(str(CATALOG_ROOT / args.locale / "validation.py"))["MESSAGES"]

    def translate(key: str, **params: Any) -> str:
        return messages[key].format(**params)

    return market_main(remaining, translate=translate)


if __name__ == "__main__":
    raise SystemExit(main())
