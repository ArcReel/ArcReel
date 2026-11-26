"""完整、局部或显式要求检查的 pytest 会话按实际触发情况结算豁免。"""

from pathlib import Path

import pytest

from tests.blocking_guard_probe import EXEMPTIONS, run_probe, write_probe

_SAMPLES = """
async def exempted_read(path):
    return path.read_text()
"""

_PROBE = """
import asyncio

from blocking_samples import exempted_read


async def test_exempted_read(tmp_path):
    path = tmp_path / "input"
    path.write_text("ok")
    assert await exempted_read(path) == "ok"


async def test_offloaded_read(tmp_path):
    path = tmp_path / "input"
    path.write_text("ok")
    assert await asyncio.to_thread(path.read_text) == "ok"
"""


def _make_session_dir(root: Path, exemptions: dict[str, str]) -> Path:
    write_probe(root, samples=_SAMPLES, tests=_PROBE, exemptions=exemptions)
    return root


def test_full_run_fails_on_untriggered_exemptions_but_partial_run_does_not(tmp_path: Path):
    stale = "lib/blocking_samples.py::never_called"
    root = _make_session_dir(tmp_path / "session", {stale: "样本：从未触发的豁免"})

    full = run_probe(root)
    partial = run_probe(root, "-k", "offloaded")

    assert full.returncode == pytest.ExitCode.TESTS_FAILED, full.stdout
    assert f"永久豁免未触发，请从 {EXEMPTIONS} 删除：{stale}" in full.stdout
    assert partial.returncode == 0, partial.stdout
    assert stale not in partial.stdout


@pytest.mark.parametrize(
    "selection",
    [("tests/test_probe.py::test_offloaded_read",), ("-k", "offloaded"), ("tests",)],
)
def test_expect_all_exemptions_option_reports_untriggered_exemptions_on_selected_runs(
    tmp_path: Path, selection: tuple[str, ...]
):
    stale = "lib/blocking_samples.py::never_called"
    root = _make_session_dir(tmp_path / "session", {stale: "样本：从未触发的豁免"})

    run = run_probe(root, "--blocking-guard-expect-all-exemptions", *selection)

    assert run.returncode == pytest.ExitCode.TESTS_FAILED, run.stdout
    assert f"永久豁免未触发，请从 {EXEMPTIONS} 删除：{stale}" in run.stdout


def test_expect_all_exemptions_option_passes_when_every_exemption_is_triggered(tmp_path: Path):
    root = _make_session_dir(
        tmp_path / "session",
        {"lib/blocking_samples.py::exempted_read": "样本：模拟启动路径上的一次性阻塞"},
    )

    run = run_probe(root, "--blocking-guard-expect-all-exemptions", "tests/test_probe.py::test_exempted_read")

    assert run.returncode == 0, run.stdout
    assert "永久豁免未触发" not in run.stdout


@pytest.mark.parametrize(
    "partial_args",
    [("--ignore=tests/test_probe.py",), ("--ignore-glob=*_probe.py",), ("--collect-only",), ("--setup-only",)],
)
def test_runs_that_skip_or_do_not_execute_tests_do_not_report_untriggered_exemptions(
    tmp_path: Path, partial_args: tuple[str, ...]
):
    stale = "lib/blocking_samples.py::never_called"
    root = _make_session_dir(tmp_path / "session", {stale: "样本：从未触发的豁免"})

    partial = run_probe(root, *partial_args)

    expected = pytest.ExitCode.NO_TESTS_COLLECTED if partial_args[0].startswith("--ignore") else pytest.ExitCode.OK
    assert partial.returncode == expected, partial.stdout + partial.stderr
    assert stale not in partial.stdout, partial.stdout
