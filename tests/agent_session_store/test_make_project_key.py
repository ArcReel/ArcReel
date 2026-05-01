"""make_project_key must agree with SDK live mirror's project_key derivation."""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import project_key_for_directory

from lib.agent_session_store import make_project_key


def test_matches_sdk_helper(tmp_path: Path):
    cwd = tmp_path / "projects" / "demo"
    cwd.mkdir(parents=True)
    assert make_project_key(cwd) == project_key_for_directory(str(cwd))


def test_accepts_string_path(tmp_path: Path):
    cwd = tmp_path / "projects" / "demo"
    cwd.mkdir(parents=True)
    assert make_project_key(str(cwd)) == project_key_for_directory(str(cwd))
