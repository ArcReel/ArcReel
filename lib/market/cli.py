"""``python -m lib.market check|generate <dir>``：市场仓 CI 与维护者本地使用的命令行入口。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from lib.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, _

from .generate import GenerateError, build_index, render_index, write_index
from .issues import MarketIssue
from .source import check_source


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lib.market", description="ArcReel market source tools")
    parser.add_argument("--locale", choices=SUPPORTED_LOCALES, default=DEFAULT_LOCALE)
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="validate a market source directory against all rules")
    check.add_argument("directory", type=Path)

    generate = commands.add_parser("generate", help="regenerate arcreel-market.json from endpoints/<slug>/")
    generate.add_argument("directory", type=Path)
    generate.add_argument("--dry-run", action="store_true", help="print the index instead of writing it")
    generate.add_argument("--name")
    generate.add_argument(
        "--default-name", help="name to use when neither --name nor a readable existing index provides one"
    )
    generate.add_argument("--description")
    generate.add_argument("--homepage")

    args = parser.parse_args(argv)
    translate = _translator(args.locale)
    if args.command == "check":
        return _check(args.directory, translate)
    return _generate(args, translate)


def _check(directory: Path, translate: Callable[..., str]) -> int:
    issues = check_source(directory)
    if issues:
        _report(issues, translate)
        print(translate("val_market_cli_check_failed", count=len(issues)), file=sys.stderr)
        return 1
    print(translate("val_market_cli_check_passed", directory=str(directory)))
    return 0


def _generate(args: argparse.Namespace, translate: Callable[..., str]) -> int:
    try:
        index = build_index(
            args.directory,
            name=args.name,
            description=args.description,
            homepage=args.homepage,
            default_name=args.default_name,
        )
    except GenerateError as exc:
        _report(exc.issues, translate)
        print(translate("val_market_cli_generate_failed", count=len(exc.issues)), file=sys.stderr)
        return 1
    if args.dry_run:
        sys.stdout.write(render_index(index))
        return 0
    path = write_index(args.directory, index)
    print(translate("val_market_cli_generate_written", path=str(path), count=len(index["entries"])))
    return 0


def _report(issues: Sequence[MarketIssue], translate: Callable[..., str]) -> None:
    for issue in issues:
        print(issue.render(translate), file=sys.stderr)


def _translator(locale: str) -> Callable[..., str]:
    def translate(key: str, **kwargs: Any) -> str:
        return _(key, locale=locale, **kwargs)

    return translate
