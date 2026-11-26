"""Render NUL-delimited PR diff paths in a GitHub Actions job summary."""

import html
import sys
from fnmatch import fnmatchcase

GATE_PATHS = (
    "pyproject.toml",
    "scripts/audit_*.py",
    "scripts/lint_*.py",
    "scripts/source_comments.py",
    "scripts/gate.py",
    "tests/blocking_guard/exemptions.toml",
    "CODING_STANDARDS.md",
    "docs/standards/*",
    ".github/workflows/*",
    ".github/actions/*",
    ".github/codeql/*",
    ".github/actionlint.yaml",
    ".pre-commit-config.yaml",
    ".coderabbit.yaml",
    "*/eslint.config.js",
    "*/eslint.config.mjs",
    "scripts/observe_gates.py",
)


def render_summary(raw_input: bytes) -> str:
    paths = raw_input.decode("utf-8", errors="backslashreplace").split("\0")
    matches = [p for p in paths if any(fnmatchcase(p, pattern) for pattern in GATE_PATHS)]
    lines = ["### 静态门禁变更（observe）\n\n"]
    if matches:
        lines.append("请核查以下文件是否放宽检查或新增豁免。\n\n")
        escaped = "\n".join(html.escape(p.replace("\n", r"\n").replace("\r", r"\r")) for p in matches)
        lines.append(f"<pre>{escaped}</pre>\n")
    else:
        lines.append("本次改动未涉及静态门禁文件。\n")
    return "".join(lines)


def main() -> None:
    sys.stdout.write(render_summary(sys.stdin.buffer.read()))


if __name__ == "__main__":
    main()
