import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/observe_gates.py"


def summary(*paths: str) -> str:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="\0".join(paths) + "\0",
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_reports_only_changed_gate_paths_as_literal_text():
    paths = [
        "scripts/audit_tests.py",
        "tests/blocking_guard/exemptions.toml",
        "docs/standards/general.md",
        ".github/workflows/<check>.yml",
    ]
    report = summary(*paths, "lib/foo.py")
    assert all(path in report for path in paths[:3])
    assert "&lt;check&gt;" in report
    assert "lib/foo.py" not in report


def test_unrelated_change_has_empty_observation():
    assert "未涉及" in summary("lib/foo.py")
