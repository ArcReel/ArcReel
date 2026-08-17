"""Refuse work on a project whose schema migration has not finished.

The verdict written by the migration runner blocks generation, not reading: the
project keeps opening, its scripts keep loading and the artifacts already on the
canvas stay visible. What is closed is every path that would create new work on
inputs the migration itself refused — the user should not pay for output derived
from a state the system cannot vouch for.
"""

from __future__ import annotations

from lib.api_errors import ConflictError
from lib.project_manager import ProjectManager, get_project_manager
from lib.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    MigrationFailureRecord,
    load_migration_failure,
)


def project_migration_failure(project_name: str, pm: ProjectManager | None = None) -> MigrationFailureRecord | None:
    """Return the blocking verdict for a project, or ``None`` when it is healthy.

    Callers bound to their own projects root (an agent session's ``ToolContext``,
    tests) pass their ``pm``: resolving through the global manager instead would
    read a different project directory under the same name.
    """

    pm = pm if pm is not None else get_project_manager()
    try:
        project_dir = pm.get_project_path(project_name)
    except (FileNotFoundError, ValueError):
        # A project that cannot be located is not blocked by migration; the caller's
        # own not-found handling owns that verdict.
        return None
    return load_migration_failure(project_dir)


def assert_project_migration_ok(project_name: str) -> None:
    """Raise the shared refusal when the project's migration verdict is a failure."""

    failure = project_migration_failure(project_name)
    if failure is not None:
        raise ConflictError(MIGRATION_FAILURE_CODE, name=project_name, reason=failure.reason)


__all__ = [
    "assert_project_migration_ok",
    "project_migration_failure",
]
