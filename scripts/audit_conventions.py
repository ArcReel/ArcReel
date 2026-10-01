#!/usr/bin/env python3
"""仓库约定中可机械判定的部分：散文规则一旦能被脚本判定，就迁到这里并从文档删除。

- STANDARDS-*：`docs/standards/*.md` 的 frontmatter `paths` 是规范适用路径的唯一真相源；
  `CODING_STANDARDS.md` 的索引表与 `.coderabbit.yaml` 的 `filePatterns` 都由它生成。
- DEPENDABOT-GROUP：每个直接依赖都要落进 `.github/dependabot.yml` 中一个具名分组，
  只命中兜底分组（`patterns: ["*"]`）说明新增依赖时漏了归组。
- TOOL-VERSION：actionlint / zizmor 在 pre-commit 与 CI workflow 中的版本一致。

`--check` 以 `规则号 file:line 修复指引` 列出全部命中，非零即退出码 1；
`--fix` 重新生成索引表与 CodeRabbit 映射后再检查其余规则。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STANDARDS_DIR = Path("docs/standards")
STANDARDS_INDEX = Path("CODING_STANDARDS.md")
CODERABBIT_CONFIG = Path(".coderabbit.yaml")
DEPENDABOT_CONFIG = Path(".github/dependabot.yml")
PRE_COMMIT_CONFIG = Path(".pre-commit-config.yaml")
CI_WORKFLOW = Path(".github/workflows/test.yml")

INDEX_START, INDEX_END = "<!-- standards-index:start -->", "<!-- standards-index:end -->"
CODERABBIT_START, CODERABBIT_END = "# standards:start", "# standards:end"


@dataclass(frozen=True)
class Violation:
    rule: str
    path: Path
    line: int
    guidance: str

    def render(self) -> str:
        return f"{self.rule} {self.path.as_posix()}:{self.line} {self.guidance}"


# ---------------------------------------------------------------- 规范索引


@dataclass(frozen=True)
class Standard:
    path: Path
    paths: tuple[str, ...]

    @property
    def rel(self) -> str:
        return self.path.as_posix()


def _frontmatter(text: str) -> object:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return yaml.safe_load(text[4:end])


def load_standards(root: Path, out: list[Violation]) -> list[Standard]:
    standards: list[Standard] = []
    for abs_path in sorted((root / STANDARDS_DIR).glob("*.md")):
        path = abs_path.relative_to(root)
        meta = _frontmatter(abs_path.read_text(encoding="utf-8"))
        paths = meta.get("paths") if isinstance(meta, dict) else None
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
            out.append(
                Violation(
                    "STANDARDS-FRONTMATTER",
                    path,
                    1,
                    "文件开头补 YAML frontmatter，`paths` 列出本规范适用的源码 glob（至少一项）",
                )
            )
            continue
        standards.append(Standard(path, tuple(paths)))
    return standards


def render_index(standards: list[Standard]) -> str:
    rows = ["| 规范 | 适用路径 |", "|---|---|"]
    for s in standards:
        globs = ", ".join(f"`{p}`" for p in s.paths)
        rows.append(f"| [`{s.rel}`]({s.rel}) | {globs} |")
    return "\n".join(rows)


def render_coderabbit(standards: list[Standard], indent: str) -> str:
    lines: list[str] = []
    for s in standards:
        lines.append(f'{indent}- files: "{s.rel}"')
        lines.append(f'{indent}  applyTo: "{",".join(s.paths)}"')
    return "\n".join(lines)


def _replace_block(text: str, start: str, end: str, body: str) -> str | None:
    """把 start/end 标记行之间的内容换成 body；任一标记缺失时返回 None。"""
    lines = text.split("\n")
    try:
        i = next(n for n, line in enumerate(lines) if line.strip() == start)
        j = next(n for n, line in enumerate(lines) if line.strip() == end and n > i)
    except StopIteration:
        return None
    return "\n".join([*lines[: i + 1], *([body] if body else []), *lines[j:]])


def _marker_indent(text: str, marker: str) -> str:
    for line in text.split("\n"):
        if line.strip() == marker:
            return line[: len(line) - len(line.lstrip())]
    return ""


def check_generated_blocks(root: Path, standards: list[Standard], fix: bool, out: list[Violation]) -> None:
    targets = [
        (STANDARDS_INDEX, INDEX_START, INDEX_END, "STANDARDS-INDEX", lambda _text: render_index(standards)),
        (
            CODERABBIT_CONFIG,
            CODERABBIT_START,
            CODERABBIT_END,
            "STANDARDS-CODERABBIT",
            lambda text: render_coderabbit(standards, _marker_indent(text, CODERABBIT_START)),
        ),
    ]
    for path, start, end, rule, render in targets:
        text = (root / path).read_text(encoding="utf-8")
        expected = _replace_block(text, start, end, render(text))
        if expected is None:
            out.append(Violation(rule, path, 1, f"缺少生成区标记 `{start}` / `{end}`，补回这两行"))
        elif expected != text:
            if fix:
                (root / path).write_text(expected, encoding="utf-8")
            else:
                line = next(n for n, row in enumerate(text.split("\n"), 1) if row.strip() == start)
                out.append(
                    Violation(
                        rule,
                        path,
                        line,
                        "与 docs/standards/*.md 的 frontmatter 不一致，运行 `uv run python scripts/audit_conventions.py --fix`",
                    )
                )


# ---------------------------------------------------------------- Dependabot 分组


def _line_of(root: Path, path: Path, needle: str) -> int:
    for n, line in enumerate((root / path).read_text(encoding="utf-8").split("\n"), 1):
        if needle in line:
            return n
    return 1


def _requirement_name(spec: str) -> str:
    return re.split(r"[\s<>=!~;\[@]", spec, maxsplit=1)[0]


def _is_catch_all(group: dict[str, object]) -> bool:
    return group.get("patterns") == ["*"] and "dependency-type" not in group


def _matches(name: str, patterns: object) -> bool:
    return isinstance(patterns, list) and any(fnmatchcase(name.lower(), str(p).lower()) for p in patterns)


def _named_group_for(name: str, development: bool, groups: dict[str, dict[str, object]]) -> str | None:
    for group_name, group in groups.items():
        if _is_catch_all(group):
            continue
        dep_type = group.get("dependency-type")
        if dep_type == "development" and not development:
            continue
        if dep_type == "production" and development:
            continue
        if _matches(name, group.get("exclude-patterns")):
            continue
        if _matches(name, group.get("patterns")):
            return group_name
    return None


def _ecosystem_groups(config: dict[str, object], ecosystem: str, directory: str) -> dict[str, dict[str, object]]:
    updates = config.get("updates")
    assert isinstance(updates, list)
    for update in updates:
        if update.get("package-ecosystem") == ecosystem and update.get("directory") == directory:
            groups = update.get("groups") or {}
            assert isinstance(groups, dict)
            return groups
    return {}


def check_dependabot_groups(root: Path, out: list[Violation]) -> None:
    config = yaml.safe_load((root / DEPENDABOT_CONFIG).read_text(encoding="utf-8"))
    guidance = "只落进 `.github/dependabot.yml` 的兜底分组，把它加进语义对应的具名分组"

    pyproject_path = Path("pyproject.toml")
    pyproject = tomllib.loads((root / pyproject_path).read_text(encoding="utf-8"))
    workspace_members = {
        name
        for name, source in pyproject.get("tool", {}).get("uv", {}).get("sources", {}).items()
        if source.get("workspace")
    }
    python_deps = [(_requirement_name(s), False) for s in pyproject["project"].get("dependencies", [])]
    for specs in pyproject.get("dependency-groups", {}).values():
        python_deps += [(_requirement_name(s), True) for s in specs if isinstance(s, str)]
    groups = _ecosystem_groups(config, "uv", "/")
    for name, development in python_deps:
        if name in workspace_members or _named_group_for(name, development, groups):
            continue
        out.append(
            Violation(
                "DEPENDABOT-GROUP", pyproject_path, _line_of(root, pyproject_path, f'"{name}'), f"`{name}` {guidance}"
            )
        )

    for package_dir in ("frontend", "website"):
        manifest_path = Path(package_dir) / "package.json"
        manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
        groups = _ecosystem_groups(config, "npm", f"/{package_dir}")
        deps = [(n, False) for n in manifest.get("dependencies", {})]
        deps += [(n, True) for n in manifest.get("devDependencies", {})]
        for name, development in deps:
            if _named_group_for(name, development, groups):
                continue
            out.append(
                Violation(
                    "DEPENDABOT-GROUP",
                    manifest_path,
                    _line_of(root, manifest_path, f'"{name}"'),
                    f"`{name}` {guidance}",
                )
            )


# ---------------------------------------------------------------- 工具版本


def check_tool_versions(root: Path, out: list[Violation]) -> None:
    pre_commit = yaml.safe_load((root / PRE_COMMIT_CONFIG).read_text(encoding="utf-8"))
    revs = {
        str(repo.get("repo", "")).rstrip("/").rsplit("/", 1)[-1]: str(repo.get("rev", "")).removeprefix("v")
        for repo in pre_commit.get("repos", [])
    }
    workflow = (root / CI_WORKFLOW).read_text(encoding="utf-8")
    tools = [
        ("actionlint", revs.get("actionlint"), r"rhysd/actionlint@\S+\s+#\s*v?([\w.]+)"),
        ("zizmor", revs.get("zizmor-pre-commit"), r"zizmor@v?([\w.]+)"),
    ]
    for tool, pre_commit_version, pattern in tools:
        match = re.search(pattern, workflow)
        if pre_commit_version is None or match is None:
            out.append(
                Violation(
                    "TOOL-VERSION",
                    CI_WORKFLOW,
                    1,
                    f"找不到 {tool} 的版本声明，更新本脚本的匹配规则或补回 pre-commit / workflow 中的声明",
                )
            )
            continue
        if match.group(1) != pre_commit_version:
            line = workflow[: match.start()].count("\n") + 1
            out.append(
                Violation(
                    "TOOL-VERSION",
                    CI_WORKFLOW,
                    line,
                    f"{tool} 版本 {match.group(1)} 与 .pre-commit-config.yaml 的 {pre_commit_version} 不一致，两处同步升级",
                )
            )


# ---------------------------------------------------------------- 入口


def audit(root: Path = ROOT, *, fix: bool = False) -> list[Violation]:
    out: list[Violation] = []
    standards = load_standards(root, out)
    check_generated_blocks(root, standards, fix, out)
    check_dependabot_groups(root, out)
    check_tool_versions(root, out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="列出全部违规，有违规时退出码 1")
    mode.add_argument("--fix", action="store_true", help="重新生成规范索引与 CodeRabbit 映射，再检查其余规则")
    args = parser.parse_args(argv)
    violations = audit(ROOT, fix=args.fix)
    for violation in violations:
        print(violation.render())
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
