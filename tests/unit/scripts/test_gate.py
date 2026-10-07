from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.gate import DOMAINS, TRIGGERS, changed_paths, select_domains

_REPO = Path(__file__).resolve().parents[3]
_CI_FILTER = _REPO / ".github" / "actions" / "domain-filter" / "action.yml"


def _ci_filters() -> dict[str, list[str]]:
    action = yaml.safe_load(_CI_FILTER.read_text(encoding="utf-8"))
    filter_step = next(step for step in action["runs"]["steps"] if step.get("id") == "filter")
    return yaml.safe_load(filter_step["with"]["filters"])


def _sample_path(pattern: str) -> str:
    """把 dorny/paths-filter 的 glob 变成一条能命中它的具体路径。"""
    return pattern.replace("**", "sub/leaf.ext").replace("*", "x")


def _vcs(root: Path, *args: str) -> None:
    identity = ("-c", "user.name=test", "-c", "user.email=test@example.com")
    subprocess.run(("git", *identity, *args), cwd=root, check=True, capture_output=True)


def test_triggers_only_name_known_domains() -> None:
    assert set(TRIGGERS) <= set(DOMAINS)


def test_changed_paths_lists_moves_untracked_files_and_non_ascii_names_verbatim(tmp_path: Path) -> None:
    _vcs(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "说明.md").write_text("旧\n", encoding="utf-8")
    _vcs(tmp_path, "add", "server/a.py", "frontend/说明.md")
    _vcs(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "docs").mkdir()
    _vcs(tmp_path, "mv", "server/a.py", "docs/a.py")
    (tmp_path / "frontend" / "说明.md").write_text("新\n", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "新建.py").write_text("y = 2\n", encoding="utf-8")

    assert changed_paths("main", root=tmp_path) == ["docs/a.py", "frontend/说明.md", "lib/新建.py", "server/a.py"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("lib/foo.py", ["backend", "tests", "conventions"]),
        ("docs/agents/testing.md", ["tests", "conventions"]),
        ("packages/arcreel-market-core/src/x.py", ["backend", "market-core", "tests", "conventions"]),
        ("frontend/src/i18n/zh/dashboard.ts", ["backend", "tests", "conventions", "frontend"]),
        (".github/workflows/test.yml", ["tests", "conventions", "workflows"]),
        ("CONTRIBUTING.md", ["tests", "conventions", "website"]),
    ],
)
def test_select_domains_follows_domain_order(path: str, expected: list[str]) -> None:
    assert select_domains([path]) == expected


def test_select_domains_merges_paths() -> None:
    assert select_domains(["frontend/src/a.tsx", "alembic.ini"]) == ["backend", "tests", "conventions", "frontend"]


@pytest.mark.parametrize(
    ("ci_domain", "gate_domain"),
    [("backend", "backend"), ("frontend", "frontend"), ("website", "website"), ("workflow", "workflows")],
)
def test_every_ci_trigger_path_selects_the_matching_gate_domain(ci_domain: str, gate_domain: str) -> None:
    # CI 的 workflow 域里 .codecov.yml 只影响覆盖率上报，本地没有对应闸门；.gitignore 归 backend（ruff 文件发现）。
    skipped = {".codecov.yml", ".gitignore"}
    for pattern in _ci_filters()[ci_domain]:
        if pattern in skipped:
            continue
        assert gate_domain in select_domains([_sample_path(pattern)]), pattern
