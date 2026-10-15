"""ArcReel 的应用版本：取自仓库根的 ``pyproject.toml``（release-please 维护）。"""

from __future__ import annotations

import functools
import tomllib
from pathlib import Path

PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def load_app_version(pyproject_path: Path) -> str:
    """从给定 pyproject 读版本号；不缓存。读不到或为空时抛错。"""
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    version = str(data["project"]["version"]).strip()
    if not version:
        raise RuntimeError("project.version is empty")
    return version


@functools.cache
def app_version() -> str:
    """当前应用版本，进程内只读一次。"""
    return load_app_version(PYPROJECT_PATH)
