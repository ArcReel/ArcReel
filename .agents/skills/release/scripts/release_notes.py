#!/usr/bin/env python3
"""发版 skill 的确定性部分：按 Spec 归并 Changelog 条目，以及把确认稿写入 Changelog 与 GitHub Release。

    release_notes.py themes [版本号]              按 Spec 归并该版本的 Changelog 条目，默认取最新版本
    release_notes.py apply <版本号> <确认稿文件>   写入 CHANGELOG.md 与 GitHub Release，提交并推送 main
    release_notes.py apply ... --dry-run          只显示将要发生的改动

只依赖标准库、git 与 gh，在仓库内任意目录运行。
"""

from __future__ import annotations

import argparse
import difflib
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HEADING = "### 🌟 版本亮点"
VERSION_HEADER = re.compile(r"^## \[(\d+\.\d+\.\d+)\]")
ENTRY = re.compile(r"^\* (?:\*\*(?P<scope>[^*]+):\*\* )?(?P<body>.*)$")
COMMIT_LINK = re.compile(r" \(\[[0-9a-f]{7,}\]\([^)]*/commit/([0-9a-f]{40})\)\)")
PR_LINK = re.compile(r" \(\[#(\d+)\]\([^)]*\)\)")
ISSUE_LINK = re.compile(r"\[#(\d+)\]\([^)]*\)")
SPEC_SUFFIX = re.compile(r"\[Spec #(\d+)\]\s*$")
SPEC_BRANCH = re.compile(r"\bspec-(\d+)\b")
BATCH = 40


def execute(
    cmd: tuple[str, ...], cwd: Path | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = None
    if cmd[0] == "gh":  # gh 默认仓库可以与 origin 不同：读写都钉在 origin 指向的仓库上
        env = {**os.environ, "GH_REPO": origin_repo()}
    return subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, env=env)


@functools.cache
def origin_repo() -> str:
    url = run("git", "remote", "get-url", "origin").strip()
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not match:
        sys.exit(f"origin 不是 GitHub 仓库：{url}")
    return match.group(1)


def run(*cmd: str, cwd: Path | None = None, check: bool = True, stdin: str | None = None) -> str:
    result = execute(cmd, cwd, stdin)
    if check and result.returncode != 0:
        sys.exit(f"命令失败：{' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def repo_root() -> Path:
    return Path(run("git", "rev-parse", "--show-toplevel").strip())


def main_changelog() -> str:
    run("git", "fetch", "--quiet", "origin", "main")
    return run("git", "show", "origin/main:CHANGELOG.md")


def versions(changelog: str) -> list[str]:
    return [m.group(1) for line in changelog.splitlines() if (m := VERSION_HEADER.match(line))]


def section_bounds(lines: list[str], version: str) -> tuple[int, int]:
    """返回该版本一节的 [标题行, 下一版本标题行) 行号区间。"""
    start = next(
        (i for i, line in enumerate(lines) if (m := VERSION_HEADER.match(line)) and m.group(1) == version),
        None,
    )
    if start is None:
        sys.exit(f"找不到版本 {version} 的标题行")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


# ---------------------------------------------------------------- themes


@dataclass
class Entry:
    category: str
    scope: str
    text: str
    refs: list[int] = field(default_factory=list)  # closes 的 issue；没有时退回 PR 号
    sha: str = ""


def parse_entries(section: list[str]) -> list[Entry]:
    entries: list[Entry] = []
    category = ""
    for line in section:
        if line.startswith("### "):
            category = line[4:].strip()
            continue
        match = ENTRY.match(line)
        if not match or category == HEADING[4:]:
            continue
        body = match.group("body")
        head, _, closes = body.partition(", closes ")
        refs = [int(n) for n in ISSUE_LINK.findall(closes)]
        if not refs:
            refs = [int(n) for n in PR_LINK.findall(head)]
        text = PR_LINK.sub("", COMMIT_LINK.sub("", head)).strip()
        sha = next(iter(COMMIT_LINK.findall(head)), "")
        entries.append(Entry(category, match.group("scope") or "", text, refs, sha))
    return entries


ISSUE_FIELDS = "number title parent { number title }"
PR_FIELDS = f"number title headRefName closingIssuesReferences(first: 5) {{ nodes {{ {ISSUE_FIELDS} }} }}"


def graphql(aliases: dict[str, str]) -> dict[str, dict]:
    """分批执行带别名的 repository 查询；查询失败即退出，避免把失败当成「没有归属」。"""
    owner, name = origin_repo().split("/")
    found: dict[str, dict] = {}
    keys = list(aliases)
    for offset in range(0, len(keys), BATCH):
        body = "\n".join(f"{key}: {aliases[key]}" for key in keys[offset : offset + BATCH])
        query = f'{{ repository(owner: "{owner}", name: "{name}") {{\n{body}\n}} }}'
        result = execute(("gh", "api", "graphql", "-f", f"query={query}"))
        try:
            response = json.loads(result.stdout)
            data = response["data"]["repository"]
            # 编号查不到是正常结果；其余错误（如限流）会让部分结果为空，不能当成「没有归属」
            failed = [e for e in response.get("errors", []) if e.get("type") != "NOT_FOUND"]
        except (ValueError, KeyError, TypeError):
            data, failed = None, []
        if data is None or failed:
            sys.exit(f"查询 GitHub 失败，无法归并主题：\n{result.stderr.strip() or result.stdout.strip()}")
        found.update({key: node for key, node in data.items() if node})
    return found


def fetch_refs(numbers: list[int]) -> dict[int, dict]:
    """批量查询 issue 或 PR：标题、父 issue；PR 则取它关闭的 issue 与分支名。"""
    nodes = graphql(
        {
            f"n{n}": f"issueOrPullRequest(number: {n}) {{ __typename "
            f"... on Issue {{ {ISSUE_FIELDS} }} ... on PullRequest {{ {PR_FIELDS} }} }}"
            for n in numbers
        }
    )
    return {int(key[1:]): node for key, node in nodes.items()}


def fetch_commit_prs(shas: list[str]) -> dict[str, dict]:
    """rebase 合并的提交在 Changelog 里只有 commit 链接：按 SHA 找回它所属的 PR。"""
    nodes = graphql(
        {
            f"c{sha}": f'object(oid: "{sha}") {{ ... on Commit {{ '
            f"associatedPullRequests(first: 1) {{ nodes {{ {PR_FIELDS} }} }} }} }}"
            for sha in shas
        }
    )
    return {
        key[1:]: prs[0]
        for key, node in nodes.items()
        if (prs := (node.get("associatedPullRequests") or {}).get("nodes"))
    }


def theme_of(issue: dict) -> tuple[int, str] | None:
    """一个 issue 所属的主题：父 issue，其次标题尾缀指向的 Spec，再次它自己是 Spec。"""
    if parent := issue.get("parent"):
        return parent["number"], parent["title"]
    if match := SPEC_SUFFIX.search(issue["title"]):
        return int(match.group(1)), ""
    if issue["title"].startswith("Spec:"):
        return issue["number"], issue["title"]
    return None


def cmd_themes(args: argparse.Namespace) -> None:
    changelog = main_changelog()
    known = versions(changelog)
    version = args.version or known[0]
    lines = changelog.splitlines()
    start, end = section_bounds(lines, version)
    entries = parse_entries(lines[start:end])
    nodes = fetch_refs(sorted({n for e in entries for n in e.refs}))
    commit_prs = fetch_commit_prs(sorted({e.sha for e in entries if not e.refs and e.sha}))

    themes: dict[int, str] = {}
    grouped: dict[int | None, list[Entry]] = {}
    for entry in entries:
        owner: int | None = None
        sources = [nodes[ref] for ref in entry.refs if ref in nodes]
        if not entry.refs and entry.sha in commit_prs:
            sources = [commit_prs[entry.sha]]
            entry.refs = [sources[0]["number"]]
        for node in sources:
            issues = node["closingIssuesReferences"]["nodes"] if "closingIssuesReferences" in node else [node]
            found = next((theme for issue in issues if (theme := theme_of(issue))), None)
            if found is None and (branch := SPEC_BRANCH.search(node.get("headRefName") or "")):
                found = (int(branch.group(1)), "")
            if found:
                owner = found[0]
                themes[owner] = themes.get(owner) or found[1]
                break
        grouped.setdefault(owner, []).append(entry)

    missing = [n for n, title in themes.items() if not title]
    for n, node in fetch_refs(missing).items():
        themes[n] = node["title"]

    def show(entry: Entry) -> str:
        scope = f"{entry.scope}: " if entry.scope else ""
        refs = " ".join(f"#{n}" for n in entry.refs)
        return f"- [{entry.category}] {scope}{entry.text} {refs}".rstrip()

    print(f"# {version}：{len(entries)} 条 Changelog 条目按 Spec 归并\n")
    ranked = sorted(k for k in grouped if k is not None)
    for number in ranked:
        items = grouped[number]
        mix = "，".join(f"{cat} {count}" for cat, count in Counter(e.category for e in items).most_common())
        print(f"## #{number} {themes[number]}（{len(items)} 条：{mix}）\n")
        print("\n".join(show(e) for e in items), end="\n\n")
    if loose := grouped.get(None):
        print(f"## 无 Spec 归属（{len(loose)} 条）\n")
        print("\n".join(show(e) for e in loose), end="\n\n")

    for previous in known[known.index(version) + 1 :]:
        p_start, p_end = section_bounds(lines, previous)
        if span := highlight_span(lines[p_start:p_end]):
            print(f"## 最近一版带亮点的版本（{previous}）\n")
            print("\n".join(lines[p_start:p_end][span[0] : span[1]]).strip())
            break


# ----------------------------------------------------------------- apply


def highlight_span(section: list[str]) -> tuple[int, int] | None:
    """节内亮点块的 [起, 止) 行号，止于下一个三级标题。"""
    start = next((i for i, line in enumerate(section) if line.strip() == HEADING), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(section)) if section[i].startswith("### ")), len(section))
    return start, end


def read_block(path: Path) -> list[str]:
    block = path.read_text(encoding="utf-8").strip().splitlines()
    if not block or block[0].strip() != HEADING:
        sys.exit(f"确认稿第一行必须是「{HEADING}」")
    if any(line.startswith("#") for line in block[1:]):
        sys.exit("确认稿只能有亮点标题这一个标题")
    if not any(line.startswith("* ") for line in block):
        sys.exit("确认稿没有任何条目")
    return block


def with_block(text: str, version: str, block: list[str]) -> str:
    """在版本标题与分类明细之间放入亮点块；已有时原位替换。"""
    lines = text.splitlines()
    start, end = section_bounds(lines, version)
    section = lines[start:end]
    if span := highlight_span(section):
        rest = section[span[1] :]
    else:
        first = next((i for i, line in enumerate(section) if line.startswith("### ")), len(section))
        rest = section[first:]
    section = [section[0], "", "", *block, "", *rest]
    return "\n".join([*lines[:start], *section, *lines[end:]]) + ("\n" if text.endswith("\n") else "")


def verify(text: str, version: str, block: list[str], where: str) -> None:
    lines = text.splitlines()
    start, end = section_bounds(lines, version)
    section = lines[start:end]
    span = highlight_span(section)
    count = sum(1 for line in section if line.strip() == HEADING)
    if count != 1 or span is None or "\n".join(section[span[0] : span[1]]).strip() != "\n".join(block):
        sys.exit(f"校验失败：{where} 中 {version} 的亮点与确认稿不一致（标题出现 {count} 次）")
    print(f"✓ {where}：{version} 有且只有一个亮点块，与确认稿逐字一致")


def show_diff(before: str, after: str, name: str) -> None:
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), name, name, lineterm="", n=1)
    print("\n".join(diff) or f"（{name} 无变化）")


def push_main(worktree: Path) -> None:
    push = ("push", "origin", "HEAD:main")
    first = execute(("git", *push), worktree)
    if first.returncode == 0:
        return
    # 钥匙串不可用时（例如无人值守会话），改用 gh 的凭据
    helper = ("-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential")
    second = execute(("git", *helper, *push), worktree)
    if second.returncode != 0:
        sys.exit(f"推送 main 失败：\n{first.stderr.strip()}\n{second.stderr.strip()}")


def cmd_apply(args: argparse.Namespace) -> None:
    version, tag = args.version, f"v{args.version}"
    block = read_block(Path(args.block))
    root = repo_root()
    old_changelog = main_changelog()
    new_changelog = with_block(old_changelog, version, block)
    old_body = run("gh", "release", "view", tag, "--json", "body", "-q", ".body")
    new_body = with_block(old_body, version, block)

    if args.dry_run:
        show_diff(old_changelog, new_changelog, "CHANGELOG.md")
        show_diff(old_body, new_body, f"GitHub Release {tag}")
        return

    if new_changelog != old_changelog:
        with tempfile.TemporaryDirectory(prefix="arcreel-release-") as tmp:
            worktree = Path(tmp) / "main"
            run("git", "worktree", "add", "--quiet", "--detach", str(worktree), "origin/main", cwd=root)
            try:
                (worktree / "CHANGELOG.md").write_text(new_changelog, encoding="utf-8")
                run(
                    "git",
                    "commit",
                    "--quiet",
                    "-m",
                    f"chore(changelog): add {version} highlights",
                    "CHANGELOG.md",
                    cwd=worktree,
                )
                push_main(worktree)
            finally:
                run("git", "worktree", "remove", "--force", str(worktree), cwd=root, check=False)
    if new_body != old_body:
        run("gh", "release", "edit", tag, "--notes-file", "-", stdin=new_body)

    verify(main_changelog(), version, block, "origin/main 的 CHANGELOG.md")
    verify(run("gh", "release", "view", tag, "--json", "body", "-q", ".body"), version, block, f"GitHub Release {tag}")
    print(f"提交：{run('git', 'log', '-1', '--format=%h %s', 'origin/main').strip()}")
    print(f"Release：{run('gh', 'release', 'view', tag, '--json', 'url', '-q', '.url').strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    themes = sub.add_parser("themes", help="按 Spec 归并某个版本的 Changelog 条目")
    themes.add_argument("version", nargs="?")
    themes.set_defaults(func=cmd_themes)
    apply = sub.add_parser("apply", help="把确认稿写入 Changelog 与 GitHub Release 并推送 main")
    apply.add_argument("version")
    apply.add_argument("block", help="确认稿文件，第一行是亮点标题")
    apply.add_argument("--dry-run", action="store_true")
    apply.set_defaults(func=cmd_apply)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
