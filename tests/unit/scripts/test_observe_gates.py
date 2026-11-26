from scripts.observe_gates import render_summary


def summary(*paths: str) -> str:
    return render_summary(("\0".join(paths) + "\0").encode("utf-8"))


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


def test_reports_import_contract_configuration_change():
    assert "pyproject.toml" in summary("pyproject.toml")


def test_unrelated_change_has_empty_observation():
    assert "未涉及" in summary("lib/foo.py")
