from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_conventions import Violation, check_suppression_reasons

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _git(comment_repo: Path, *args: str) -> None:
    subprocess.run(
        ("git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", *args),
        cwd=comment_repo,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def comment_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "example.py").write_text("first = 1  # Existing comment\nsecond = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "example.py")
    _git(tmp_path, "commit", "-qm", "baseline")
    _git(tmp_path, "tag", "baseline")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def _audit(comment_repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, "-m", "scripts.audit_comments", "--root", str(comment_repo), *args),
        cwd=_PROJECT_ROOT,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_reports_committed_worktree_and_new_file_additions(comment_repo: Path) -> None:
    source = comment_repo / "example.py"
    replaced = "first = 1  # Replaced comment\nsecond = 2\n"
    _git(comment_repo, "switch", "-qc", "upstream")
    source.write_text(replaced, encoding="utf-8")
    _git(comment_repo, "add", "example.py")
    _git(comment_repo, "commit", "-qm", "upstream replaces prose")
    _git(comment_repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(comment_repo, "switch", "-q", "main")
    source.write_text(replaced, encoding="utf-8")
    _git(comment_repo, "add", "example.py")
    _git(comment_repo, "commit", "-qm", "replace existing prose")
    source.write_text("first = 1  # Replaced comment\nsecond = 2  # Worktree comment\n", encoding="utf-8")
    (comment_repo / "新建.ts").write_text('const value = "// data"; // Untracked comment\n', encoding="utf-8")

    result = _audit(comment_repo)

    assert result.returncode == 1, result.stderr
    output = result.stdout + result.stderr
    assert "example.py:1" in output
    assert "example.py:2" in output
    assert "新建.ts:1" in output

    explicit = _audit(comment_repo, "--base", "origin/main")

    assert explicit.returncode == 1, explicit.stderr
    assert "example.py:1" not in explicit.stdout + explicit.stderr
    assert "example.py:2" in explicit.stdout + explicit.stderr


def test_reports_reintroduced_comment_against_explicit_base(comment_repo: Path) -> None:
    source = comment_repo / "example.py"
    source.write_text("first = 1\nsecond = 2  # Existing comment\n", encoding="utf-8")

    moved = _audit(comment_repo, "--base", "baseline")

    assert moved.returncode == 0, moved.stdout + moved.stderr

    source.write_text("first = 1\nsecond = 2\n", encoding="utf-8")

    removed = _audit(comment_repo, "--base", "baseline")

    assert removed.returncode == 0, removed.stdout + removed.stderr

    _git(comment_repo, "add", "example.py")
    _git(comment_repo, "commit", "-qm", "remove prose")
    _git(comment_repo, "tag", "comment-removed")
    source.write_text("first = 1  # Existing comment\nsecond = 2\n", encoding="utf-8")

    reintroduced = _audit(comment_repo, "--base", "comment-removed")

    assert reintroduced.returncode == 1, reintroduced.stderr
    assert "example.py:1" in reintroduced.stdout + reintroduced.stderr


def test_treats_renamed_source_as_a_new_comment_inventory(comment_repo: Path) -> None:
    _git(comment_repo, "mv", "example.py", "renamed.py")

    result = _audit(comment_repo, "--base", "baseline")

    assert result.returncode == 1, result.stderr
    assert "renamed.py:1" in result.stdout + result.stderr


def test_scopes_out_documentation_generated_locks_and_only_vendored_skills(comment_repo: Path) -> None:
    sources = {
        "docs/example.md": "<!-- Documentation note -->\n",
        "uv.lock": "# Generated metadata\n",
        "frontend/pnpm-lock.yaml": "# Generated metadata\n",
        "website/pnpm-lock.yaml": "# Generated metadata\n",
        "skills-lock.json": '{"version": 1, "skills": {"vendored": {}}}\n',
        ".agents/skills/vendored/scripts/demo.py": "# Upstream prose\n",
        ".agents/skills/local/scripts/demo.py": "# Local prose\n",
    }
    for path, content in sources.items():
        file = comment_repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")

    result = _audit(comment_repo, "--base", "baseline")

    assert result.returncode == 1, result.stderr
    diagnostic_paths = [line.split(":", 1)[0] for line in result.stdout.splitlines() if ": ARC001 " in line]
    assert diagnostic_paths == [".agents/skills/local/scripts/demo.py"]


def test_stats_count_comments_and_lines_with_only_removable_comments(comment_repo: Path) -> None:
    (comment_repo / "example.py").write_text(
        '#!/usr/bin/env python3\nfirst = "# data"  # Existing comment\n# Whole line comment\n', encoding="utf-8"
    )
    (comment_repo / "example.tsx").write_text(
        "const element = <div>\n"
        "  {/* JSX block */}\n"
        "</div>;\n"
        "/* First */ /* Second */\n"
        "/* Multiline\n"
        " * comment */ const item = 1;\n",
        encoding="utf-8",
    )

    result = _audit(comment_repo, "--stats")

    assert result.returncode == 0, result.stderr
    assert [line.split("\t") for line in result.stdout.splitlines()] == [
        ["language", "files", "comments", "directives", "comment_lines", "removable_lines"],
        ["python", "1", "3", "1", "3", "1"],
        ["tsx", "1", "4", "0", "4", "3"],
        ["total", "2", "7", "1", "7", "4"],
    ]


def test_allows_help_in_distributed_config_files(comment_repo: Path) -> None:
    sources = {
        ".env.example": "# Set this to your local data directory.\nARCREEL_DATA_DIR=./projects\n",
        "frontend/.env.example": "# Set when the API is hosted separately.\n",
        "deploy/docker-compose.yml": "# Choose a different host port if 1241 is in use.\nservices: {}\n",
        "deploy/production/docker-compose.yml": "# Keep the database volume between upgrades.\nservices: {}\n",
        "deploy/internal.yml": "# This is an internal config.\nservice: worker\n",
    }
    for path, content in sources.items():
        file = comment_repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")

    result = _audit(comment_repo)

    assert result.returncode == 1, result.stderr
    diagnostic_paths = [line.split(":", 1)[0] for line in result.stdout.splitlines() if ": ARC001 " in line]
    assert diagnostic_paths == ["deploy/internal.yml"]


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("frontend/src/plugin.ts", "/** @public Imported dynamically by plugins */\nexport const plugin = 1;\n"),
        (".github/helper.py", "from api import run  # noqa: F401 -- Imported by external callers\n"),
    ],
    ids=["knip-public", "github-python-suppression"],
)
def test_required_tool_reasons_pass_both_checks(comment_repo: Path, path: str, source: str) -> None:
    file = comment_repo / path
    file.parent.mkdir(parents=True)
    file.write_text(source, encoding="utf-8")
    violations: list[Violation] = []

    check_suppression_reasons(comment_repo, violations)
    result = _audit(comment_repo)

    assert violations == []
    assert result.returncode == 0, result.stdout + result.stderr


def test_includes_typescript_config_comments_in_ratchet(comment_repo: Path) -> None:
    file = comment_repo / "tsconfig.node.json"
    file.write_text('{\n  // New config prose\n  "compilerOptions": {}\n}\n', encoding="utf-8")

    result = _audit(comment_repo)

    assert result.returncode == 1, result.stderr
    assert "tsconfig.node.json:2:3: ARC001" in result.stdout
