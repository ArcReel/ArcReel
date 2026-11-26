import sys
from pathlib import Path

import pytest

from scripts.nightly_invariants import main, run_checked


def test_backend_runs_new_domain_with_its_own_strict_markers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", sys.prefix)
    monkeypatch.setenv("UV_NO_SYNC", "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[3]))
    root_config = (
        '[project]\nname="sample"\nversion="0.1.0"\n'
        '[tool.uv.workspace]\nmembers=["packages/*"]\nexclude=["packages/old"]\n'
        '[tool.pytest.ini_options]\nmarkers=["invariant", "released_archive"]\naddopts="--strict-markers -p tests.invariant_profiles"'
    )
    (tmp_path / "pyproject.toml").write_text(root_config)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_root.py").write_text("import pytest\n@pytest.mark.invariant\ndef test_root(): pass")
    for name, markers in [("future", ["invariant: generated cases"]), ("ordinary", []), ("old", ["invariant"])]:
        package = tmp_path / "packages" / name
        package.mkdir(parents=True)
        (package / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\nversion="0.1.0"\n'
            f'[tool.pytest.ini_options]\nmarkers={markers!r}\naddopts="--strict-markers -p tests.invariant_profiles"'
        )
    future = tmp_path / "packages/future/tests"
    future.mkdir()
    (future / "test_new_domain.py").write_text(
        "import pytest\nfrom hypothesis import settings\nfrom pathlib import Path\n@pytest.mark.invariant\n"
        "def test_domain(): Path('profile').write_text(settings.get_current_profile_name())"
    )
    assert main("backend") == 0
    assert (future.parent / "profile").read_text() == "nightly"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ("def test_pass(): pass", 0),
        ("def test_fail(): assert False", 1),
        ("def test_skip(): pytest.skip()", 1),
        ("def test_skip(): pytest.skip()\ndef test_pass(): pass", 1),
        ("", 5),
    ],
)
def test_requires_each_registered_case_to_execute(tmp_path: Path, body: str, status: int) -> None:
    test_file = tmp_path / "test_future_domain.py"
    test_file.write_text(f"import pytest\n{body}\n")
    command = [sys.executable, "-m", "pytest", "-c", str(tmp_path / "pytest.ini"), str(test_file)]
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    assert run_checked(command, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}, ["--junitxml={report}"]) == status
