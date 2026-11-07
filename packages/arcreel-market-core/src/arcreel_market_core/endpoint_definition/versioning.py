"""``schema_version`` comparison and category classification, plus the ``meta.min_app_version`` threshold check.

Categories are user-facing signals, **not gates**: every definition must pass the current schema to save, regardless of category. Failure is
an ordinary validation error unrelated to version. Classification serves both import confirmation (file versus current version) and age ordering for shared lineage
(existing definition ``meta.version`` versus imported file).

Version convention: optional-field additions / relaxed constraints increment minor; removed fields / changed semantics increment major.
"""

from __future__ import annotations

import re
from enum import StrEnum

from packaging.version import InvalidVersion, Version

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class SchemaVersionLevel(StrEnum):
    """File ``schema_version`` category relative to the current version."""

    #: Same version: import directly.
    DIRECT = "direct"
    #: Older within the same major: allow with a warning (the current schema is only more permissive).
    WARNING = "warning"
    #: Newer file or lower major: require user confirmation. The former may contain unknown fields; the latter has changed semantics.
    #: Missing or non-semver versions also enter this category; do not silently allow indeterminate age ordering.
    CONFIRM = "confirm"


class VersionRelation(StrEnum):
    """Existing definition ``meta.version`` age relative to the imported file."""

    NEWER = "newer"
    SAME = "same"
    OLDER = "older"


def parse_semver(value: object) -> tuple[int, int, int] | None:
    """Parse ``"1.2.3"`` into a comparable triple; return None for non-semver strings or components beyond the integer-conversion digit limit."""
    if not isinstance(value, str):
        return None
    match = _SEMVER.fullmatch(value)
    if match is None:
        return None
    major, minor, patch = match.groups()
    try:
        return int(major), int(minor), int(patch)
    except ValueError:
        return None


def schema_version_level(file_version: object, current_version: str) -> SchemaVersionLevel:
    """Classify the file version relative to the current version."""
    file_parsed = parse_semver(file_version)
    current_parsed = parse_semver(current_version)
    if file_parsed is None or current_parsed is None:
        return SchemaVersionLevel.CONFIRM
    if file_parsed == current_parsed:
        return SchemaVersionLevel.DIRECT
    if file_parsed > current_parsed or file_parsed[0] < current_parsed[0]:
        return SchemaVersionLevel.CONFIRM
    return SchemaVersionLevel.WARNING


def version_relation(existing_version: object, file_version: object) -> VersionRelation:
    """Existing definition age relative to the imported file. If either side is unparseable, use ``same`` and report duplication without age ordering."""
    existing_parsed = parse_semver(existing_version)
    file_parsed = parse_semver(file_version)
    if existing_parsed is None or file_parsed is None or existing_parsed == file_parsed:
        return VersionRelation.SAME
    return VersionRelation.NEWER if existing_parsed > file_parsed else VersionRelation.OLDER


def meets_min_app_version(required: object, app_version: str) -> bool:
    """Whether the application version meets the definition's declared ``meta.min_app_version``.

    The threshold accepts strict semver only; parse application versions with PEP 440, with prereleases below same-number releases. If either side cannot be ordered,
    treat the threshold as unmet.
    """
    if parse_semver(required) is None:
        return False
    try:
        return Version(app_version) >= Version(str(required))
    except InvalidVersion:
        return False
