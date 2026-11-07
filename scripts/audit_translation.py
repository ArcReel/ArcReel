"""Check Markdown translation contracts against a fixed Git reference.

This checks literal preservation, not semantic equivalence. Fenced examples are
excluded from literal comparison because comments may translate. Single-line
JSON tool-call examples receive a syntax check; other fenced content needs review.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_LINK = re.compile(r"\]\(([^)]+)\)")
_TOOL_EXAMPLE = re.compile(r"^\s*(mcp__arcreel__\w+)\((.*)\)\s*$", re.MULTILINE)
_QUOTATION = re.compile(r'"((?:\\.|[^"\\])*)"|“([^”]+)”|‘([^’]+)’|「([^」]+)」|『([^』]+)』', re.DOTALL)
_TRANSLATABLE_METADATA = {"title", "description", "sidebar_label"}


def prose_lines(markdown: str) -> list[str]:
    """Return lines outside fenced code, retaining inline code and link targets."""
    result: list[str] = []
    fence_char = ""
    fence_size = 0
    for line in markdown.splitlines():
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char = ""
            continue
        if not fence_char:
            result.append(line)
    return result


def markdown_literals(markdown: str) -> tuple[Counter[str], Counter[str]]:
    """Collect inline-code spans and link destinations outside fenced examples."""
    lines = prose_lines(markdown)
    # Code spans may cross line endings. Parsing each line separately treats the
    # closing delimiter as a new opening delimiter and incorrectly captures prose.
    # Blank lines end paragraphs and cannot be part of a CommonMark code span.
    paragraphs = re.split(r"\n\s*\n", "\n".join(lines))
    code: Counter[str] = Counter()
    for paragraph in paragraphs:
        for _, value in _CODE.findall(paragraph):
            normalized = value.replace("\n", " ")
            if normalized.startswith(" ") and normalized.endswith(" ") and normalized.strip(" "):
                normalized = normalized[1:-1]
            code[normalized] += 1
    links = Counter(value for line in lines for value in _LINK.findall(line))
    return code, links


def metadata_contract(markdown: str) -> object:
    """Keep functional frontmatter, including slot identities but not slot prose."""
    if not markdown.startswith("---\n"):
        return None
    sections = markdown[4:].split("\n---\n", 1)
    if len(sections) != 2:
        raise ValueError("unclosed frontmatter")
    value = yaml.safe_load(sections[0])
    if not isinstance(value, dict):
        raise ValueError("frontmatter must be an object")
    contract = {key: item for key, item in value.items() if key not in _TRANSLATABLE_METADATA}
    if "slots" in contract:
        slots = contract["slots"]
        if not isinstance(slots, dict):
            raise ValueError("slots must be an object")
        contract["slots"] = sorted(slots)
    return contract


def english_quotations(markdown: str) -> Counter[str]:
    """Collect existing English sentence quotations outside fenced examples.

    Four-word minimum avoids treating short translated terminology as quotations.
    This is intentionally bounded: attribution and unquoted/block quotations still
    require independent source review.
    """
    quotes: Counter[str] = Counter()
    paragraphs = re.split(r"\n\s*\n", "\n".join(prose_lines(markdown)))
    # Inline literals already have their own preservation check. Exclude them
    # here so quotes in adjacent JSON fields cannot become a prose quotation.
    prose = "\n\n".join(_CODE.sub(" ", paragraph) for paragraph in paragraphs)
    for groups in _QUOTATION.findall(prose):
        value = next((part for part in groups if part), "")
        if re.search(r"[\u3400-\u9fff]", value) or len(re.findall(r"[A-Za-z]+", value)) < 4:
            continue
        quotes[re.sub(r"\s+", " ", value).strip()] += 1
    return quotes


def audit_markdown(source: str, target: str) -> list[str]:
    """Report lost literal spans, changed destinations, and changed functional metadata."""
    findings: list[str] = []
    source_code, source_links = markdown_literals(source)
    target_code, target_links = markdown_literals(target)
    for literal, count in (source_code - target_code).items():
        findings.append(f"missing inline code ({count}): {literal!r}")
    for quotation, count in (english_quotations(source) - english_quotations(target)).items():
        findings.append(f"changed or dequoted English quotation ({count}): {quotation!r}")
    if source_links != target_links:
        findings.append("link destinations changed; review updated anchors or translations individually")
    try:
        if metadata_contract(source) != metadata_contract(target):
            findings.append("functional frontmatter changed")
    except (ValueError, yaml.YAMLError) as error:
        findings.append(f"invalid frontmatter: {error}")

    # Single-line JSON tool examples are executable guidance. Compare counts so
    # baseline pseudocode remains allowed, while an added brace cannot silently
    # turn a previously valid example into invalid JSON. Multiline examples still
    # require separate review.
    def invalid_examples(markdown: str) -> Counter[str]:
        invalid: Counter[str] = Counter()
        for name, arguments in _TOOL_EXAMPLE.findall(markdown):
            try:
                json.loads(arguments)
            except ValueError:
                invalid[name] += 1
        return invalid

    for name, count in (invalid_examples(target) - invalid_examples(source)).items():
        findings.append(f"new invalid JSON tool example ({count}): {name}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Source commit or branch for translation comparison")
    parser.add_argument("paths", nargs="*", help="Optional tracked Markdown paths; default: changed Markdown files")
    args = parser.parse_args()
    for path in args.paths:
        if not (ROOT / path).is_file():
            parser.error(f"expected a Markdown file, not a directory or missing path: {path}")
    git = shutil.which("git")
    if git is None:
        parser.error("git is not available")
    subprocess.run(
        [git, "rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    paths = (
        args.paths
        or subprocess.check_output(
            [git, "diff", "--name-only", "--diff-filter=AM", args.base, "--", "*.md", "*.mdx"],
            cwd=ROOT,
            text=True,
        ).splitlines()
    )
    failed = False
    checked = 0
    for path in paths:
        target_path = ROOT / path
        if not target_path.is_file():
            continue
        original = subprocess.run(
            [git, "show", f"{args.base}:{path}"], cwd=ROOT, capture_output=True, text=True, check=False
        )
        if original.returncode:
            continue
        checked += 1
        for finding in audit_markdown(original.stdout, target_path.read_text(encoding="utf-8")):
            print(f"{path}: {finding}")
            failed = True
    print(f"Checked {checked} Markdown files; semantic review and fenced-code review remain separate.")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
