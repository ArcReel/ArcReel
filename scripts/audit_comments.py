"""Reject additions to the source comment inventory relative to a Git base."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from scripts.source_comments import Comment, extract_comments, language_for

GENERATED_FILES = frozenset({"uv.lock", "frontend/pnpm-lock.yaml", "website/pnpm-lock.yaml"})
USER_DEPLOYMENT_FILES = frozenset({"deploy/docker-compose.yml", "deploy/production/docker-compose.yml"})


def upstream_skills(root: Path) -> frozenset[str]:
    lockfile = root / "skills-lock.json"
    if not lockfile.is_file():
        return frozenset()
    return frozenset(json.loads(lockfile.read_text(encoding="utf-8"))["skills"])


def in_scope(path: str, vendor_skills: frozenset[str]) -> bool:
    parts = Path(path).parts
    return (
        language_for(path) is not None
        and path not in GENERATED_FILES
        and parts[-1] != ".env.example"
        and path not in USER_DEPLOYMENT_FILES
        and not (len(parts) > 2 and parts[:2] == (".agents", "skills") and parts[2] in vendor_skills)
    )


def comment_key(comment: Comment) -> str:
    return comment.prose if comment.directive else comment.raw.strip()


def comment_additions(path: str, before: str, after: str) -> list[Comment]:
    existing = Counter(key for comment in extract_comments(path, before) if (key := comment_key(comment)))
    additions = []
    for comment in extract_comments(path, after):
        key = comment_key(comment)
        if not key:
            continue
        if existing[key]:
            existing[key] -= 1
        else:
            additions.append(comment)
    return additions


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args),  # noqa: S607 -- developer Git executable is resolved through PATH
        cwd=root,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.decode("utf-8", errors="surrogateescape")


def paths_from_git(root: Path, *args: str) -> set[str]:
    return set(git(root, *args).split("\0")) - {""}


def check(root: Path, base: str) -> int:
    base = git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").strip()
    old_paths = paths_from_git(root, "ls-tree", "-r", "--name-only", "-z", base)
    changed = paths_from_git(root, "diff", "--no-renames", "--name-only", "-z", base, "--")
    untracked = paths_from_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    failures = 0
    vendor_skills = upstream_skills(root)
    for path in sorted(changed | untracked):
        file = root / path
        if not in_scope(path, vendor_skills) or file.is_symlink() or not file.is_file():
            continue
        before = git(root, "show", f"{base}:{path}") if path in old_paths else ""
        after = file.read_text(encoding="utf-8")
        for entry in comment_additions(path, before.replace("\r\n", "\n"), after):
            first_line = entry.raw.split("\n", 1)[0]
            print(f"{path}:{entry.start_line}:{entry.start_col + 1}: ARC001 新增代码注释：{first_line}")
            failures += 1
    if failures:
        print(f"ARC001 发现 {failures} 处新增注释。请优先用代码表达意图，将长期决策写入相关 ADR。")
        return 1
    print("ARC001 通过")
    return 0


def report_stats(root: Path) -> None:
    stats: dict[str, Counter[str]] = {}
    vendor_skills = upstream_skills(root)
    all_paths = paths_from_git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for path in sorted(all_paths):
        file = root / path
        language = language_for(path)
        if language is None or not in_scope(path, vendor_skills) or file.is_symlink() or not file.is_file():
            continue
        source = file.read_text(encoding="utf-8")
        lines = source.split("\n")
        comments = extract_comments(path, source)
        counts = stats.setdefault(language, Counter())
        counts["files"] += 1
        counts["comments"] += len(comments)
        spliceable: set[int] = set()
        covered: set[int] = set()
        non_whitespace = [sum(not char.isspace() for char in line) for line in lines]
        for comment in comments:
            counts["directives"] += int(comment.directive)
            active = range(comment.start_line - 1, comment.end_line)
            covered.update(active)
            if comment.directive:
                continue
            for index in active:
                start = comment.start_col if index == comment.start_line - 1 else 0
                end = comment.end_col if index == comment.end_line - 1 else len(lines[index])
                if index == active.start == active.stop - 1:
                    before = lines[index][:start].strip()
                    after = lines[index][end:].strip()
                    if before == "{" and after == "}" and language == "tsx":
                        start, end = 0, len(lines[index])
                non_whitespace[index] -= sum(not char.isspace() for char in lines[index][start:end])
                spliceable.add(index)
        counts["comment_lines"] += len(covered)
        counts["removable_lines"] += sum(non_whitespace[index] == 0 for index in spliceable)
    header = ("files", "comments", "directives", "comment_lines", "removable_lines")
    print("language", *header, sep="\t")
    total: Counter[str] = Counter()
    for language, counts in sorted(stats.items()):
        print(language, *(counts[key] for key in header), sep="\t")
        total.update(counts)
    print("total", *(total[key] for key in header), sep="\t")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--base", help="Git commit to compare against. Default: merge base of HEAD and origin/main.")
    modes.add_argument("--stats", action="store_true", help="Count comments and estimate removable lines.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository working directory.")
    args = parser.parse_args()
    try:
        root = Path(git(args.root, "rev-parse", "--show-toplevel").rstrip("\n"))
        if args.stats:
            report_stats(root)
            return 0
        base = args.base or git(root, "merge-base", "HEAD", "origin/main").strip()
        return check(root, base)
    except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
        parser.exit(2, f"ARC001: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
