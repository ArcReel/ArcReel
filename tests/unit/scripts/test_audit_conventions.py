from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_conventions import audit

_STANDARD = """---
paths:
  - "lib/**"
  - "server/**"
---

# 后端
"""

_INDEX = """# Coding Standards

<!-- standards-index:start -->
<!-- standards-index:end -->
"""

_CODERABBIT = """knowledge_base:
  code_guidelines:
    filePatterns:
      # standards:start
      # standards:end
"""

_DEPENDABOT = """version: 2
updates:
  - package-ecosystem: "uv"
    directory: "/"
    groups:
      web:
        patterns:
          - "fastapi"
      dev-dependencies:
        patterns:
          - "pytest*"
      all-other:
        patterns:
          - "*"
  - package-ecosystem: "npm"
    directory: "/frontend"
    groups:
      react:
        patterns:
          - "react"
      dev-dependencies:
        dependency-type: "development"
        patterns:
          - "*"
      all-other:
        patterns:
          - "*"
  - package-ecosystem: "npm"
    directory: "/website"
    groups:
      all-other:
        patterns:
          - "*"
"""

_PYPROJECT = """[project]
name = "demo"
dependencies = [
    "fastapi>=0.1",
    "demo-core",
]

[dependency-groups]
dev = [
    "pytest>=9",
]

[tool.uv.sources]
demo-core = { workspace = true }
"""

_PRE_COMMIT = """repos:
  - repo: https://github.com/rhysd/actionlint
    rev: v1.7.12
    hooks:
      - id: actionlint
  - repo: https://github.com/zizmorcore/zizmor-pre-commit
    rev: v1.29.0
    hooks:
      - id: zizmor
"""

_WORKFLOW = """jobs:
  workflow-static:
    steps:
      - uses: docker://rhysd/actionlint@sha256:abc # v1.7.12
      - run: uvx zizmor@1.29.0 --offline .
"""


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """最小仓库：各项约定都满足，生成区为空待 `--fix` 填充。"""
    _write(tmp_path, "docs/standards/backend.md", _STANDARD)
    _write(tmp_path, "CODING_STANDARDS.md", _INDEX)
    _write(tmp_path, ".coderabbit.yaml", _CODERABBIT)
    _write(tmp_path, ".github/dependabot.yml", _DEPENDABOT)
    _write(tmp_path, "pyproject.toml", _PYPROJECT)
    _write(tmp_path, ".pre-commit-config.yaml", _PRE_COMMIT)
    _write(tmp_path, ".github/workflows/test.yml", _WORKFLOW)
    _write(
        tmp_path,
        "frontend/package.json",
        json.dumps({"dependencies": {"react": "^19"}, "devDependencies": {"vitest": "^3"}}),
    )
    _write(tmp_path, "website/package.json", json.dumps({}))
    audit(tmp_path, fix=True)
    return tmp_path


def _rules(root: Path) -> list[tuple[str, str]]:
    return [(v.rule, v.path.as_posix()) for v in audit(root)]


def test_fix_generates_index_and_coderabbit_mapping_from_frontmatter(repo: Path) -> None:
    assert audit(repo) == []
    index = (repo / "CODING_STANDARDS.md").read_text(encoding="utf-8")
    assert "| [`docs/standards/backend.md`](docs/standards/backend.md) | `lib/**`, `server/**` |" in index
    coderabbit = (repo / ".coderabbit.yaml").read_text(encoding="utf-8")
    assert '      - files: "docs/standards/backend.md"\n        applyTo: "lib/**,server/**"\n' in coderabbit


def test_frontmatter_change_without_regeneration_is_reported(repo: Path) -> None:
    _write(repo, "docs/standards/backend.md", _STANDARD.replace('  - "server/**"\n', ""))

    assert _rules(repo) == [("STANDARDS-INDEX", "CODING_STANDARDS.md"), ("STANDARDS-CODERABBIT", ".coderabbit.yaml")]


def test_standard_without_paths_is_reported_and_left_out_of_index(repo: Path) -> None:
    _write(repo, "docs/standards/general.md", "# 通用\n")

    violations = audit(repo, fix=True)

    assert [(v.rule, v.path.as_posix()) for v in violations] == [("STANDARDS-FRONTMATTER", "docs/standards/general.md")]
    assert "general.md" not in (repo / "CODING_STANDARDS.md").read_text(encoding="utf-8")


def test_missing_generation_markers_are_reported(repo: Path) -> None:
    _write(repo, ".coderabbit.yaml", "knowledge_base: {}\n")

    assert _rules(repo) == [("STANDARDS-CODERABBIT", ".coderabbit.yaml")]


def test_dependency_matching_only_the_catch_all_group_is_reported_at_its_declaration(repo: Path) -> None:
    _write(repo, "pyproject.toml", _PYPROJECT.replace('    "demo-core",\n', '    "demo-core",\n    "httpx>=0.27",\n'))
    _write(repo, "frontend/package.json", json.dumps({"dependencies": {"react": "^19", "zustand": "^5"}}, indent=2))

    violations = audit(repo)

    assert [(v.rule, v.path.as_posix(), v.line) for v in violations] == [
        ("DEPENDABOT-GROUP", "pyproject.toml", 6),
        ("DEPENDABOT-GROUP", "frontend/package.json", 4),
    ]
    assert "`httpx`" in violations[0].guidance
    assert "`zustand`" in violations[1].guidance


def test_dependency_excluded_from_its_named_group_is_reported(repo: Path) -> None:
    _write(
        repo,
        ".github/dependabot.yml",
        _DEPENDABOT.replace(
            '          - "pytest*"\n', '          - "pytest*"\n        exclude-patterns:\n          - "pytest-xdist"\n'
        ),
    )
    _write(
        repo, "pyproject.toml", _PYPROJECT.replace('    "demo-core",\n', '    "demo-core",\n    "pytest-xdist>=3",\n')
    )

    assert _rules(repo) == [("DEPENDABOT-GROUP", "pyproject.toml")]


def test_development_group_does_not_cover_production_dependencies(repo: Path) -> None:
    _write(repo, "frontend/package.json", json.dumps({"dependencies": {"vitest": "^3"}}))

    assert _rules(repo) == [("DEPENDABOT-GROUP", "frontend/package.json")]


def test_tool_version_drift_between_pre_commit_and_workflow_is_reported(repo: Path) -> None:
    _write(repo, ".github/workflows/test.yml", _WORKFLOW.replace("zizmor@1.29.0", "zizmor@1.30.0"))

    violations = audit(repo)

    assert [(v.rule, v.line) for v in violations] == [("TOOL-VERSION", 5)]
    assert "1.30.0" in violations[0].guidance
    assert "1.29.0" in violations[0].guidance


def test_unrecognised_tool_declaration_is_reported_instead_of_passing(repo: Path) -> None:
    _write(repo, ".github/workflows/test.yml", "jobs: {}\n")

    assert [v.rule for v in audit(repo)] == ["TOOL-VERSION", "TOOL-VERSION"]


def _suppression_lines(root: Path) -> list[tuple[str, int]]:
    return [(v.path.as_posix(), v.line) for v in audit(root) if v.rule == "SUPPRESSION-REASON"]


def test_python_suppression_without_same_line_reason_is_reported(repo: Path) -> None:
    _write(
        repo,
        "lib/demo.py",
        """import os  # noqa: F401
import sys  # noqa: F401 -- 理由
a: int = ""  # type: ignore[assignment]
b: int = ""  # type: ignore[assignment]  # 理由
c = os.nope  # pyright: ignore[reportAttributeAccessIssue]
d = os.nope  # pyright: ignore[reportAttributeAccessIssue]  # 理由
import foo  # deptry: ignore[DEP001]
import bar  # deptry: ignore[DEP001]  # 理由
# 理由写在上一行不算
e: int = ""  # type: ignore[assignment]
s = "# noqa: F401"
""",
    )

    assert _suppression_lines(repo) == [("lib/demo.py", n) for n in (1, 3, 5, 7, 10)]


def test_registration_block_reason_covers_handlers_in_the_same_function_scope(repo: Path) -> None:
    _write(
        repo,
        "server/handlers.py",
        """def register(app, flag):
    # 以下处理器由装饰器就地注册，reportUnusedFunction 是工具误报。
    @app.get("/a")
    async def _a():  # pyright: ignore[reportUnusedFunction]
        return 1

    if flag:

        @app.get("/b")
        async def _b():  # pyright: ignore[reportUnusedFunction]
            return 2


def register_without_reason(app):
    @app.get("/c")
    async def _c():  # pyright: ignore[reportUnusedFunction]
        return 3


@app.get("/d")
async def _d():  # pyright: ignore[reportUnusedFunction]
    return 4


def register_with_empty_header(app):
    #
    @app.get("/e")
    async def _e():  # pyright: ignore[reportUnusedFunction]
        return 5
""",
    )

    assert _suppression_lines(repo) == [
        ("server/handlers.py", 16),
        ("server/handlers.py", 21),
        ("server/handlers.py", 28),
    ]


def test_frontend_and_workflow_suppressions_without_reason_are_reported(repo: Path) -> None:
    _write(
        repo,
        "frontend/src/demo.tsx",
        """// eslint-disable-next-line react-hooks/set-state-in-effect
// eslint-disable-next-line react-hooks/set-state-in-effect -- mount-only 初始化
/* eslint-disable react-hooks/refs */
/* eslint-disable react-hooks/refs -- 理由 */
{/* eslint-disable-next-line react-hooks/refs */}
{/* eslint-disable-next-line react-hooks/refs -- 理由 */}
/** @public */
/** @public 供外部 Agent skill 动态导入 */
/* eslint-enable react-hooks/refs */
""",
    )
    _write(
        repo,
        ".github/workflows/demo.yml",
        """on: # zizmor: ignore[dangerous-triggers]
  push:
jobs: # zizmor: ignore[excessive-permissions]  # 理由
""",
    )

    assert _suppression_lines(repo) == [
        (".github/workflows/demo.yml", 1),
        ("frontend/src/demo.tsx", 1),
        ("frontend/src/demo.tsx", 3),
        ("frontend/src/demo.tsx", 5),
        ("frontend/src/demo.tsx", 7),
    ]


def test_every_directive_comment_on_a_line_is_checked(repo: Path) -> None:
    _write(
        repo,
        "frontend/src/pair.ts",
        """/* eslint-disable-next-line no-alert -- 理由 */ /* eslint-disable-next-line no-console */
/* eslint-disable-next-line no-alert -- 理由 */ /* eslint-disable-next-line no-console -- 理由 */
""",
    )

    assert _suppression_lines(repo) == [("frontend/src/pair.ts", 1)]


def test_directive_text_inside_string_literals_is_not_a_suppression(repo: Path) -> None:
    _write(
        repo,
        "frontend/src/sample.ts",
        """const a = "// eslint-disable-next-line no-console";
const b = '/* eslint-disable no-console */';
const c = `/** @public */`;
/* 注释 */ const d = "// eslint-disable-next-line no-console";
foo("it's"); // eslint-disable-line no-console
const quote = /"/; console.log(quote); // eslint-disable-line no-console
""",
    )
    _write(
        repo,
        ".github/workflows/sample.yml",
        """sample: "# zizmor: ignore[dangerous-triggers]"
other: 'it''s # zizmor: ignore[dangerous-triggers]'
on: # zizmor: ignore[dangerous-triggers]
""",
    )

    assert _suppression_lines(repo) == [
        (".github/workflows/sample.yml", 3),
        ("frontend/src/sample.ts", 5),
        ("frontend/src/sample.ts", 6),
    ]


def test_dependency_and_build_directories_are_not_scanned(repo: Path) -> None:
    _write(repo, "frontend/node_modules/pkg/index.js", "// eslint-disable-next-line no-console\n")
    _write(repo, ".venv/lib/site.py", "import os  # noqa: F401\n")

    assert _suppression_lines(repo) == []
