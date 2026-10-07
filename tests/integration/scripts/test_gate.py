from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.gate import changed_paths


def _vcs(root: Path, *args: str) -> None:
    identity = ("-c", "user.name=test", "-c", "user.email=test@example.com")
    subprocess.run(("git", *identity, *args), cwd=root, check=True, capture_output=True)


def test_changed_paths_lists_moves_untracked_files_and_non_ascii_names_verbatim(tmp_path: Path) -> None:
    _vcs(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "说明.md").write_text("旧\n", encoding="utf-8")
    _vcs(tmp_path, "add", "server/a.py", "frontend/说明.md")
    _vcs(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "docs").mkdir()
    _vcs(tmp_path, "mv", "server/a.py", "docs/a.py")
    (tmp_path / "frontend" / "说明.md").write_text("新\n", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "新建.py").write_text("y = 2\n", encoding="utf-8")

    assert changed_paths("main", root=tmp_path) == ["docs/a.py", "frontend/说明.md", "lib/新建.py", "server/a.py"]
