"""Partition backend test files and combine the exact set of raw coverage artifacts."""

import argparse
import subprocess
import sys
from pathlib import Path

PACKAGE_SUITES = [Path("packages/arcreel-market-core/tests"), Path("packages/arcreel-catalog-core/tests")]


def partition(total: int) -> list[list[Path]]:
    files = [
        p
        for root in ("tests/unit", "tests/integration")
        for p in Path(root).rglob("*.py")
        if p.name.startswith("test_") or p.stem.endswith("_test")
    ]
    root_shards = total - len(PACKAGE_SUITES)
    if not 1 <= root_shards <= len(files):
        raise ValueError(f"cannot partition {len(files)} files into {total} shards")
    groups: list[list[Path]] = [[] for _ in range(root_shards)]
    sizes = [0] * len(groups)
    for path in sorted(files, key=lambda p: (-p.stat().st_size, p)):
        index = sizes.index(min(sizes))
        groups[index].append(path)
        sizes[index] += path.stat().st_size
    return groups + [[root] for root in PACKAGE_SUITES]


def combine(directory: Path, total: int) -> None:
    from coverage import Coverage, CoverageData

    paths = [directory / f"coverage-{index}" / "raw.coverage" for index in range(1, total + 1)]
    unexpected = set(directory.glob("coverage-*/raw.coverage")) - set(paths)
    if unexpected:
        raise ValueError(f"unexpected coverage shards: {sorted(unexpected)}")
    for path in paths:
        if not path.is_file():
            raise ValueError(f"missing coverage shard: {path}")
        data = CoverageData(basename=str(path))
        data.read()
        if not data.measured_files():
            raise ValueError(f"empty coverage shard: {path}")
    cov = Coverage()
    cov.combine(data_paths=[str(path) for path in paths], strict=True, keep=True)
    cov.save()
    cov.xml_report(outfile="coverage-backend.xml")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", required=True, type=int)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--shard", type=int)
    mode.add_argument("--combine", type=Path)
    args = parser.parse_args()
    if args.combine:
        combine(args.combine, args.total)
        return
    if not 1 <= args.shard <= args.total:
        parser.error("shard must be between 1 and total")
    files = partition(args.total)[args.shard - 1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.hang_watchdog",
            "-n",
            "4",
            "--dist",
            "loadfile",
            "--max-worker-restart=0",
            "--cov",
            "--cov-config=pyproject.toml",
            "--cov-report=",
            *files,
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
