"""Derive two-level Agent-memory paths: project-memory source of truth and user-ID validation.

Pure functions with no I/O derive paths even for absent directories. Session assembly (native
auto-memory redirection), user-memory storage, and both ``AgentAccessPolicy`` fences obtain
paths here. Independently assembling allowed/written directories risks typos causing Agent writes
outside fences and silent rejection.

- Project memory: ``<项目目录>/.arcreel/memory/``
- User memory: location comes from data-root layout (``DataRootLayout.user_memory_dir``)

Use ``.arcreel/``, not ``.claude/``: the latter is profile materialization, whose entire tree is
deleted on manifest mismatch or built-in-profile restoration.
"""

from __future__ import annotations

from pathlib import Path

#: ArcReel internal-state directory: validators skip dot directories and archives export visible entries only, so memory
#: is naturally excluded from archives and produces no unrecognized-directory diagnostics.
ARCREEL_DIRNAME = ".arcreel"

#: Memory directory name, shared by both levels so memory is readily identifiable in either location.
MEMORY_DIRNAME = "memory"


def project_memory_dir(project_dir: Path) -> Path:
    """Project-memory directory: ``<项目目录>/.arcreel/memory/``."""
    return project_dir / ARCREEL_DIRNAME / MEMORY_DIRNAME


def is_valid_memory_user_id(user_id: str) -> bool:
    """Whether ``user_id`` is safe as a single user-memory directory path segment.

    Apply the intersection of all platforms’ rules, never relaxing for the current platform:
    ``:`` is ordinary on POSIX, but combining Windows ``<数据根>/users`` with ``C:`` produces
    drive-relative paths (``Path`` discards the entire left side), placing memory in that drive’s
    current directory rather than below the data root. Future OIDC identities (``issuer:subject``)
    must normalize into single segments before passing here.
    """
    if not user_id or user_id in {".", ".."}:
        return False
    return not any(char in user_id for char in ("/", "\\", ":", "\x00"))
