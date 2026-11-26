"""Run registered Nightly suites and reject empty or skipped explorations."""

import argparse
import os
import secrets
import shlex
import subprocess
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree


def registered_packages(root: Path) -> Iterator[Path]:
    workspace = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"]["workspace"]
    excluded = {p for pattern in workspace.get("exclude", []) for p in root.glob(pattern)}
    for package in sorted({p for pattern in workspace["members"] for p in root.glob(pattern)} - excluded):
        config = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        markers = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
        if any(marker.partition(":")[0].strip() == "invariant" for marker in markers):
            yield package.relative_to(root)


def run_checked(command: list[str], env: dict[str, str], report_args: list[str], *, label: str = "Replay") -> int:
    print(f"{label}:", shlex.join([*(f"{k}={v}" for k, v in env.items()), *command]), flush=True)
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "results.xml"
        args = [arg.format(report=report) for arg in report_args]
        result = subprocess.run([*command, *args], env=os.environ | env, check=False)
        if result.returncode:
            return result.returncode
        cases = ElementTree.parse(report).findall(".//testcase")
        skipped = [case.get("name") for case in cases if case.find("skipped") is not None]
        if not cases or skipped:
            print(f"Nightly requires every registered test to run: {len(cases)} collected, skipped={skipped}")
            return 1
    return 0


def main(suite: str) -> int:
    if suite == "frontend":
        files = sorted(Path("frontend/src").rglob("*.property.test.*"))
        if not files:
            raise ValueError("no registered frontend property files")
        results = [
            run_checked(
                ["pnpm", "--dir", "frontend", "exec", "vitest", "run", str(file.relative_to("frontend"))],
                {"FC_PROFILE": "nightly"},
                ["--reporter=default", "--reporter=junit", "--outputFile.junit={report}"],
                label="Run",
            )
            for file in files
        ]
    else:
        selections = [(Path("."), "released_archive")]
        if suite == "backend":
            selections = [(Path("."), "invariant and not released_archive")]
            selections.extend((package, "invariant") for package in registered_packages(Path(".")))
        seed = str(secrets.randbits(64))
        results = [
            run_checked(
                [
                    "uv",
                    "run",
                    "--directory",
                    str(package),
                    "python",
                    "-m",
                    "pytest",
                    "tests",
                    "-m",
                    marker,
                    "-v",
                    "--tb=long",
                    "--hypothesis-profile=nightly",
                    f"--hypothesis-seed={seed}",
                ],
                {"HYPOTHESIS_PROFILE": "nightly"},
                ["--junitxml={report}"],
            )
            for package, marker in selections
        ]
    return next((status for status in results if status), 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=["backend", "frontend", "archives"])
    raise SystemExit(main(parser.parse_args().suite))
