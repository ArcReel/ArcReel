"""Agent SessionStore — SDK transcript mirror to project DB."""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import project_key_for_directory

from lib.agent_session_store.models import AgentSessionEntry, AgentSessionSummary


def make_project_key(project_cwd: Path | str) -> str:
    """Derive the SessionStore project_key for a project cwd.

    Thin wrapper around SDK's public ``project_key_for_directory`` so adapter
    callers and SDK live-mirror writes agree on the key.
    """
    return project_key_for_directory(str(project_cwd))


__all__ = [
    "AgentSessionEntry",
    "AgentSessionSummary",
    "make_project_key",
]
