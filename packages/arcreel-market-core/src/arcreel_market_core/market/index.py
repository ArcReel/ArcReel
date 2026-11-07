"""Market-source index schema and reading semantics (validation rule ①).

:func:`parse_index` first checks major-version readability, validates the entire index against ``index_schema.json``, then projects entries recognized by the current
version. Pure logic: input is an already-parsed JSON value.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from arcreel_market_core.endpoint_definition import parse_semver

from .issues import INDEX_FILENAME, ROOT_PATH, MarketIssue, MarketIssueCode, join_path

INDEX_SCHEMA_PATH = Path(__file__).parent / "index_schema.json"

#: Index format version implemented by this client. Reject higher-major indexes entirely; accept higher minor versions within the same major.
INDEX_SCHEMA_VERSION = "1.0.0"

#: The only entry type recognized by this client; silently skip other types when reading indexes.
ENDPOINT_ENTRY_TYPE = "endpoint"

#: Field name to dedicated code for its format violations (pattern / no newlines); other format issues use ``invalid_value``.
_FORMAT_CODES = {
    "slug": MarketIssueCode.SLUG_INVALID,
    "path": MarketIssueCode.PATH_NOT_RELATIVE,
    "icon": MarketIssueCode.PATH_NOT_RELATIVE,
    "min_app_version": MarketIssueCode.MIN_APP_VERSION_INVALID,
}

#: Multiple rules report the same instance through if/then references; deduplicate errors for these keywords by path + code.
_FORMAT_KEYWORDS = frozenset({"pattern", "not"})


@dataclass(frozen=True)
class MarketIndexEntry:
    """An invocation-endpoint entry. ``path`` / ``icon`` are relative to the index directory."""

    type: str
    slug: str
    path: str
    name: str
    author: str
    version: str
    media_type: str
    description: str | None = None
    homepage: str | None = None
    icon: str | None = None
    min_app_version: str | None = None


@dataclass(frozen=True)
class MarketIndex:
    schema_version: str
    name: str
    entries: tuple[MarketIndexEntry, ...]
    description: str | None = None
    homepage: str | None = None


class MarketIndexError(Exception):
    """Index unavailable."""


class UnsupportedIndexSchemaError(MarketIndexError):
    """Index major version exceeds this client's; reject the entire index."""

    def __init__(self, version: str) -> None:
        super().__init__(f"unsupported market index schema_version {version}")
        self.version = version
        self.supported = INDEX_SCHEMA_VERSION


class InvalidIndexError(MarketIndexError):
    """Index fails schema validation; any invalid entry invalidates the entire index."""

    def __init__(self, issues: Sequence[MarketIssue]) -> None:
        super().__init__(f"invalid market index ({len(issues)} issues)")
        self.issues = tuple(issues)


@cache
def load_index_schema() -> dict[str, Any]:
    return json.loads(INDEX_SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def _index_validator() -> Draft202012Validator:
    schema = load_index_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def parse_index(document: object) -> MarketIndex:
    """Read an index under client compatibility semantics.

    Raises:
        UnsupportedIndexSchemaError: Major version exceeds this client (checked before schema: a new major's shape cannot be validated).
        InvalidIndexError: Index schema validation failed.
    """
    if isinstance(document, dict):
        declared = parse_semver(document.get("schema_version"))
        supported = parse_semver(INDEX_SCHEMA_VERSION)
        if declared is not None and supported is not None and declared[0] > supported[0]:
            raise UnsupportedIndexSchemaError(str(document["schema_version"]))
    issues = index_schema_issues(document)
    if issues or not isinstance(document, dict):
        raise InvalidIndexError(issues)
    return MarketIndex(
        schema_version=document["schema_version"],
        name=document["name"],
        description=document.get("description"),
        homepage=document.get("homepage"),
        entries=tuple(_project_entry(entry) for entry in document["entries"] if entry["type"] == ENDPOINT_ENTRY_TYPE),
    )


def index_schema_issues(document: object) -> list[MarketIssue]:
    """All index schema-validation diagnostics, sorted by location and deduplicated."""
    issues: list[MarketIssue] = []
    seen: set[tuple[str, str, str]] = set()
    errors = sorted(
        _index_validator().iter_errors(cast(Any, document)), key=lambda error: _sort_key(error.absolute_path)
    )
    for error in errors:
        for issue in _translate(error):
            identity = (issue.path, issue.code.value, repr(sorted(issue.params.items())))
            if identity not in seen:
                seen.add(identity)
                issues.append(issue)
    return issues


def _project_entry(entry: dict[str, Any]) -> MarketIndexEntry:
    return MarketIndexEntry(
        type=entry["type"],
        slug=entry["slug"],
        path=entry["path"],
        name=entry["name"],
        author=entry["author"],
        version=entry["version"],
        media_type=entry["media_type"],
        description=entry.get("description"),
        homepage=entry.get("homepage"),
        icon=entry.get("icon"),
        min_app_version=entry.get("min_app_version"),
    )


def _sort_key(parts: Sequence[str | int]) -> tuple[tuple[int, str | int], ...]:
    return tuple((0, part) if isinstance(part, int) else (1, part) for part in parts)


def _translate(error: ValidationError) -> Iterator[MarketIssue]:
    path = _format_path(error.absolute_path)
    keyword = str(error.validator)
    if keyword == "required":
        instance = error.instance if isinstance(error.instance, dict) else {}
        required = error.validator_value if isinstance(error.validator_value, list) else []
        for name in required:
            if name not in instance:
                yield _issue(path, MarketIssueCode.MISSING_FIELD, field=str(name))
        return
    if keyword == "type":
        yield _issue(path, MarketIssueCode.INVALID_TYPE, expected=str(error.validator_value))
        return
    field_name = error.absolute_path[-1] if error.absolute_path else None
    format_code = _FORMAT_CODES.get(field_name) if isinstance(field_name, str) else None
    if format_code is not None and keyword in _FORMAT_KEYWORDS:
        yield _issue(path, format_code, value=str(error.instance))
        return
    yield _issue(path, MarketIssueCode.INVALID_VALUE, keyword=keyword, constraint=str(error.validator_value))


def _issue(path: str, code: MarketIssueCode, **params: str) -> MarketIssue:
    return MarketIssue(INDEX_FILENAME, path, code, params)


def _format_path(parts: Sequence[str | int]) -> str:
    path = ROOT_PATH
    for part in parts:
        path = join_path(path, part)
    return path
