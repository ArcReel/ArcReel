"""ArcReel application version from root ``pyproject.toml``, maintained by release-please."""

from __future__ import annotations

import functools
import tomllib
from pathlib import Path

PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def load_app_version(pyproject_path: Path) -> str:
    """Read the version from the supplied pyproject without caching; raise if unavailable or empty."""
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    version = str(data["project"]["version"]).strip()
    if not version:
        raise RuntimeError("project.version is empty")
    return version


@functools.cache
def app_version() -> str:
    """Current application version, read only once per process."""
    return load_app_version(PYPROJECT_PATH)


def reset_for_tests() -> None:
    """Clear the process-wide :func:`app_version` cache for tests only."""
    app_version.cache_clear()
