"""``python -m arcreel_market_core check|generate <dir>``：市场仓 CI 与维护者本地使用的命令行入口。

本包不携带翻译目录：默认诊断与汇总按 code + params 输出；应用边界可注入 translator 渲染自然语言。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from arcreel_market_core.validation_messages import Translator, code_translator

from .generate import GenerateError, build_index, render_index, write_index
from .issues import MarketIssue
from .source import check_source


def main(argv: Sequence[str] | None = None, *, translate: Translator = code_translator) -> int:
    parser = argparse.ArgumentParser(prog="python -m arcreel_market_core", description="ArcReel market source tools")
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
    if args.command == "check":
        return _check(args.directory, translate)
    return _generate(args, translate)


def _check(directory: Path, translate: Translator) -> int:
    issues = check_source(directory)
    if issues:
        _report(issues, translate)
        print(translate("val_market_cli_check_failed", count=len(issues)), file=sys.stderr)
        return 1
    print(translate("val_market_cli_check_passed", directory=str(directory)))
    return 0


def _generate(args: argparse.Namespace, translate: Translator) -> int:
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


def _report(issues: Sequence[MarketIssue], translate: Translator) -> None:
    for issue in issues:
        print(issue.render(translate), file=sys.stderr)
