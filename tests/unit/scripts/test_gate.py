from __future__ import annotations

import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

from scripts import gate
from scripts.gate import DOMAINS, TRIGGERS, select_domains

_REPO = Path(__file__).resolve().parents[3]
_CI_FILTER = _REPO / ".github" / "actions" / "domain-filter" / "action.yml"


def _ci_filters() -> dict[str, list[str]]:
    action = yaml.safe_load(_CI_FILTER.read_text(encoding="utf-8"))
    filter_step = next(step for step in action["runs"]["steps"] if step.get("id") == "filter")
    return yaml.safe_load(filter_step["with"]["filters"])


def _sample_path(pattern: str) -> str:
    """把 dorny/paths-filter 的 glob 变成一条能命中它的具体路径。"""
    return pattern.replace("**", "sub/leaf.ext").replace("*", "x")


def test_triggers_only_name_known_domains() -> None:
    assert set(TRIGGERS) <= set(DOMAINS)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("lib/foo.py", ["backend", "tests", "conventions"]),
        ("docs/agents/testing.md", ["tests", "conventions"]),
        ("packages/arcreel-market-core/src/x.py", ["backend", "market-core", "tests", "conventions"]),
        ("packages/arcreel-catalog-core/src/x.py", ["backend", "catalog-core", "tests", "conventions"]),
        ("frontend/src/i18n/zh/dashboard.ts", ["backend", "tests", "conventions", "frontend"]),
        ("frontend/e2e/record-fixtures.ts", ["tests", "conventions", "frontend", "e2e-record"]),
        ("frontend/src/a.tsx", ["tests", "conventions", "frontend"]),
        (".github/workflows/test.yml", ["tests", "conventions", "workflows"]),
        ("CONTRIBUTING.md", ["tests", "conventions", "website"]),
    ],
)
def test_select_domains_follows_domain_order(path: str, expected: list[str]) -> None:
    assert select_domains([path]) == expected


def test_select_domains_merges_paths() -> None:
    assert select_domains(["frontend/src/a.tsx", "alembic.ini"]) == [
        "backend",
        "tests",
        "conventions",
        "frontend",
    ]


def test_shared_request_contract_triggers_backend_in_ci_and_locally() -> None:
    path = "frontend/src/test/contracts/reference-unit-add-requests.json"
    assert "backend" in select_domains([path])
    assert any(fnmatchcase(path, pattern) for pattern in _ci_filters()["backend"])


@pytest.mark.parametrize(
    ("ci_domain", "gate_domain"),
    [("backend", "backend"), ("frontend", "frontend"), ("website", "website"), ("workflow", "workflows")],
)
def test_every_ci_trigger_path_selects_the_matching_gate_domain(ci_domain: str, gate_domain: str) -> None:
    skipped = {".codecov.yml", ".gitignore"}
    for pattern in _ci_filters()[ci_domain]:
        if pattern in skipped:
            continue
        assert gate_domain in select_domains([_sample_path(pattern)]), pattern


@pytest.fixture
def commands(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        for arg in argv:
            if arg.startswith(("--junitxml=", "--outputFile.junit=")):
                Path(arg.split("=", 1)[1]).write_text(
                    '<testsuite><testcase name="selected"/></testsuite>', encoding="utf-8"
                )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(gate, "LOCK_PATH", tmp_path / "machine.lock")
    return calls


@pytest.mark.parametrize(
    ("path", "domains"),
    [
        ("uv.lock", {"backend"}),
        ("pyproject.toml", {"backend"}),
        ("tests/conftest.py", {"backend"}),
        ("alembic/versions/migration.py", {"backend"}),
        ("lib/project/project_migrations/change.py", {"backend"}),
        ("lib/db/session.py", {"backend"}),
        ("server/app.py", {"backend"}),
        ("scripts/entry.py", {"backend"}),
        ("agent_runtime_profile/tools/entry.py", {"backend"}),
        ("lib/pkg/__init__.py", {"backend"}),
        ("frontend/src/types/workflow.ts", {"backend", "frontend"}),
        ("frontend/src/test/contracts/example.json", {"backend", "frontend"}),
        ("frontend/src/test/setup.ts", {"frontend"}),
        ("frontend/pnpm-lock.yaml", {"frontend"}),
        ("frontend/vitest.config.ts", {"frontend"}),
        ("frontend/src/branding.ts", {"frontend"}),
        ("frontend/src/new-format.xyz", {"frontend"}),
        ("unknown/entry.py", {"backend", "frontend"}),
    ],
)
def test_shared_and_unknown_inputs_run_complete_suites(path, domains, tmp_path):
    source = tmp_path / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.touch()
    selected = set(gate.select_domains([path])) & {"backend", "frontend"}
    assert selected == domains
    for name in selected:
        assert gate.select_tests(name, [path], tmp_path) == [gate.DOMAINS[name][-1]]


@pytest.mark.parametrize(
    "path",
    [
        "scripts/e2e_record_backend.py",
        "tests/fixtures/catalog.json",
        "frontend/e2e/support/recorded.ts",
        "frontend/e2e/fixtures/comfyui.ts",
        "frontend/package.json",
        "lib/catalog/lifecycle.py",
    ],
)
def test_recording_inputs_trigger_double_recording(path):
    assert "e2e-record" in select_domains([path])


@pytest.mark.parametrize(
    ("args", "full"),
    [
        (["--changed"], False),
        (["--changed", "--full"], True),
        (["backend"], True),
        (["backend", "--changed"], True),
        ([], False),
        (["--full"], True),
    ],
)
def test_cli_depth_for_changed_test(args, full, monkeypatch, commands):
    path = "tests/unit/scripts/test_gate.py"
    monkeypatch.setattr(gate, "changed_paths", lambda base: [path])
    assert gate.main(args) == 0
    assert (gate.BACKEND_SUITE.argv in commands) == full
    assert any(path in argv for argv in commands) != full
    assert gate.DOMAINS["backend"][0].argv in commands


@pytest.mark.parametrize(("code", "expected", "full"), [(0, 0, True), (5, 0, True), (1, 1, False)])
def test_zero_hits_upgrade_and_assertion_failures_stop(code, expected, full, tmp_path, monkeypatch):
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        for arg in argv:
            if arg.startswith("--junitxml="):
                Path(arg.split("=", 1)[1]).write_text('<testsuite tests="0"/>', encoding="utf-8")
                return subprocess.CompletedProcess(argv, code)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(gate, "LOCK_PATH", tmp_path / "machine.lock")
    monkeypatch.setattr(subprocess, "run", run)
    assert gate.run_domains(["backend"], ["tests/unit/scripts/test_gate.py"], set()) == expected
    assert (gate.BACKEND_SUITE.argv in commands) == full


def test_frontend_source_and_changed_test_both_run_at_limited_concurrency(commands):
    paths = ["frontend/src/theme.ts", "frontend/src/api.test.ts"]
    assert gate.run_domains(["frontend"], paths, set()) == 0
    assert ("pnpm", "check:static") in commands
    assert any(
        argv[:7] == ("pnpm", "exec", "vitest", "related", "--run", "--maxWorkers=1", "src/theme.ts")
        for argv in commands
    )
    assert any(argv[:6] == ("pnpm", "exec", "vitest", "run", "--maxWorkers=1", "src/api.test.ts") for argv in commands)


def test_cold_warm_and_deleted_python_source(tmp_path):
    source = tmp_path / "server" / "ordinary.py"
    source.parent.mkdir()
    source.touch()
    paths = ["server/ordinary.py"]
    (cold,) = gate.select_tests("backend", paths, tmp_path)
    assert cold.heavy
    assert "--testmon-noselect" in cold.argv
    (tmp_path / ".testmondata").touch()
    (warm,) = gate.select_tests("backend", paths, tmp_path)
    assert not warm.heavy
    assert "--testmon" in warm.argv
    source.unlink()
    assert gate.select_tests("backend", paths, tmp_path) == [gate.BACKEND_SUITE]
