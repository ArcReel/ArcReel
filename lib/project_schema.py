"""Strict parsing for the persisted project schema discriminator."""

from __future__ import annotations

from collections.abc import Mapping

CURRENT_PROJECT_SCHEMA_VERSION = 8


def parse_project_schema_version(project: Mapping[str, object]) -> int:
    """Return a canonical non-negative version without coercing corrupt data."""

    raw_version = project.get("schema_version")
    if raw_version is None:
        return 0
    if type(raw_version) is not int or raw_version < 0:
        raise ValueError(f"schema_version must be an integer, got {raw_version!r}")
    return raw_version


def project_schema_is_current(project: Mapping[str, object]) -> bool:
    """Return whether a strictly parsed project uses the current schema."""

    return parse_project_schema_version(project) == CURRENT_PROJECT_SCHEMA_VERSION


__all__ = [
    "CURRENT_PROJECT_SCHEMA_VERSION",
    "parse_project_schema_version",
    "project_schema_is_current",
]
