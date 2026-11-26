from __future__ import annotations

import contextlib
import hashlib
import sqlite3
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
        ("lib/foo.py", ["backend", "tests", "conventions", "e2e-freshness"]),
        ("docs/agents/testing.md", ["tests", "conventions"]),
        ("packages/arcreel-market-core/src/x.py", ["backend", "market-core", "tests", "conventions", "e2e-freshness"]),
        (
            "packages/arcreel-catalog-core/src/x.py",
            ["backend", "catalog-core", "tests", "conventions", "e2e-freshness"],
        ),
        ("frontend/src/i18n/zh/dashboard.ts", ["backend", "tests", "conventions", "frontend", "e2e-freshness"]),
        ("frontend/e2e/record-fixtures.ts", ["tests", "conventions", "frontend", "e2e-freshness", "e2e-record"]),
        ("frontend/e2e/live/backend.py", ["tests", "conventions", "frontend", "e2e-live"]),
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
        "e2e-freshness",
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
        assert any(fnmatchcase(_sample_path(pattern), trigger) for trigger in TRIGGERS[gate_domain]), pattern


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


def test_ci_recording_inputs_match_local_gate() -> None:
    assert set(_ci_filters()["recording"]) == set(TRIGGERS["e2e-record"])


@pytest.mark.parametrize(
    "path",
    [
        "server/routers/auth.py",
        "lib/db/session.py",
        "packages/arcreel-catalog-core/src/schema.py",
        "frontend/e2e/fixtures/recorded/auth-status.json",
        "unclassified/input.json",
    ],
)
def test_response_changes_only_require_single_recording(path):
    assert set(select_domains([path])) & {"e2e-record", "e2e-freshness"} == {"e2e-freshness"}


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


def _write_testmondata(db_path: Path, entries: list[tuple[str, str, int]]) -> None:
    with contextlib.closing(sqlite3.connect(db_path)) as con:
        con.executescript(
            """
            CREATE TABLE test_execution (id INTEGER PRIMARY KEY, failed INTEGER);
            CREATE TABLE file_fp (id INTEGER PRIMARY KEY, filename TEXT, fsha TEXT);
            CREATE TABLE test_execution_file_fp (test_execution_id INTEGER, fingerprint_id INTEGER);
            """
        )
        for idx, (filename, fsha, failed) in enumerate(entries, start=1):
            con.execute("INSERT INTO test_execution VALUES (?, ?)", (idx, failed))
            con.execute("INSERT INTO file_fp VALUES (?, ?, ?)", (idx, filename, fsha))
            con.execute("INSERT INTO test_execution_file_fp VALUES (?, ?)", (idx, idx))
        con.commit()


def test_repeat_testmon_zero_hits_with_verified_source_stays_quick_and_unlocked(tmp_path, monkeypatch):
    source = tmp_path / "server" / "ordinary.py"
    source.parent.mkdir()
    content = 'MSG = "中文状态"\n'
    source.write_text(content, encoding="utf-8")
    raw = source.read_bytes()
    git_blob = hashlib.sha1(b"blob %d\0" % len(raw) + raw, usedforsecurity=False).hexdigest()
    _write_testmondata(tmp_path / ".testmondata", [("server/ordinary.py", git_blob, 0)])

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        for arg in argv:
            if arg.startswith("--junitxml="):
                Path(arg.split("=", 1)[1]).write_text('<testsuite tests="0"/>', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    lock_file = tmp_path / "machine.lock"
    monkeypatch.setattr(gate, "LOCK_PATH", lock_file)
    monkeypatch.setattr(subprocess, "run", run)

    assert gate.run_domains(["backend"], ["server/ordinary.py"], set(), root=tmp_path) == 0
    assert gate.BACKEND_SUITE.argv not in calls
    assert not lock_file.exists()


@pytest.mark.parametrize(
    "recorded_entries",
    [
        [],
        [("server/ordinary.py", "stale-sha", 0)],
        [("server/ordinary.py", "current", 1)],
    ],
)
def test_testmon_zero_hits_with_unverified_source_upgrades_and_locks(tmp_path, monkeypatch, recorded_entries):
    source = tmp_path / "server" / "ordinary.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    raw = source.read_bytes()
    current_sha = hashlib.sha1(b"blob %d\0" % len(raw) + raw, usedforsecurity=False).hexdigest()
    entries = [(name, current_sha if sha == "current" else sha, failed) for name, sha, failed in recorded_entries]
    _write_testmondata(tmp_path / ".testmondata", entries)

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        for arg in argv:
            if arg.startswith("--junitxml="):
                Path(arg.split("=", 1)[1]).write_text('<testsuite tests="0"/>', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    lock_file = tmp_path / "machine.lock"
    monkeypatch.setattr(gate, "LOCK_PATH", lock_file)
    monkeypatch.setattr(subprocess, "run", run)

    assert gate.run_domains(["backend"], ["server/ordinary.py"], set(), root=tmp_path) == 0
    assert gate.BACKEND_SUITE.argv in calls
    assert lock_file.exists()


def test_quick_checks_skip_machine_lock_while_full_suites_acquire_it(tmp_path, monkeypatch, commands):
    lock_file = tmp_path / "machine.lock"
    monkeypatch.setattr(gate, "LOCK_PATH", lock_file)
    assert gate.run_domains(["frontend"], ["frontend/src/theme.ts"], set()) == 0
    assert not lock_file.exists()
    assert gate.run_domains(["frontend"], ["frontend/src/theme.ts"], {"frontend"}) == 0
    assert lock_file.exists()


@pytest.mark.parametrize("domains", [["e2e-live"], ["frontend", "e2e-live"]])
def test_live_gate_builds_once_then_uses_current_dist(domains, commands):
    assert gate.main(domains) == 0
    build = ("pnpm", "exec", "vite", "build")
    live = ("env", "E2E_PREBUILT=1", "pnpm", "e2e:live")
    assert commands.count(build) == commands.count(live) == 1
    assert commands.index(build) < commands.index(live)
