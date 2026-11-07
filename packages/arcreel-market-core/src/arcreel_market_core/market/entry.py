"""Entry/definition consistency (rules ④⑤): definitions pass shared validation and index entries equal the projection of definition ``meta``."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from arcreel_market_core.endpoint_definition import definition_media_type, validate_definition

from .index import ENDPOINT_ENTRY_TYPE, MarketIndexEntry
from .issues import INDEX_FILENAME, ROOT_PATH, MarketIssue, MarketIssueCode, join_path

#: Fields projected from definition ``meta`` into index entries, in generator output order.
PROJECTED_META_FIELDS = ("name", "author", "version", "description", "homepage", "min_app_version")


def project_meta(definition: Mapping[str, Any]) -> dict[str, Any]:
    """A definition's projection into an index entry: fields present in ``meta``, plus its declared media type.

    Media type is not fixed: definitions declare image or video output; declarative definitions default to video when omitted. Reading shares
    one ``definition_media_type`` implementation with endpoint projections and mirror columns. Market-index ``media_type`` is the value written to the mirror column after
    the definition is stored; disagreement would display an image endpoint as video in the market.

    If ``kind`` is missing or unknown to this version, omit ``media_type`` from the projection: the definition itself is invalid,
    :func:`validate_definition` already reports it in the same check; guessing a value here would hide it.
    """
    meta: Mapping[str, Any] = definition.get("meta") or {}
    projection = {field: meta[field] for field in PROJECTED_META_FIELDS if field in meta}
    with suppress(KeyError, ValueError):
        projection["media_type"] = definition_media_type(definition)
    return projection


def check_entry_definition(
    entry: MarketIndexEntry,
    definition: object,
    *,
    definition_file: str,
    entry_path: str = ROOT_PATH,
) -> list[MarketIssue]:
    """Rules ④⑤. ``definition_file`` and ``entry_path`` serve only diagnostic locations."""
    issues = [
        MarketIssue(
            definition_file,
            issue.path,
            MarketIssueCode.DEFINITION_INVALID,
            {"code": issue.code.value, "detail": issue.message},
        )
        for issue in validate_definition(definition).errors
    ]
    meta = definition.get("meta") if isinstance(definition, Mapping) else None
    if isinstance(definition, Mapping) and isinstance(meta, Mapping):
        issues.extend(_projection_issues(entry, definition, entry_path))
    return issues


def _projection_issues(entry: MarketIndexEntry, definition: Mapping[str, Any], entry_path: str) -> list[MarketIssue]:
    expected = project_meta(definition)
    actual: dict[str, Any] = {
        "name": entry.name,
        "author": entry.author,
        "version": entry.version,
        "description": entry.description,
        "homepage": entry.homepage,
        "min_app_version": entry.min_app_version,
        "media_type": entry.media_type,
    }
    issues: list[MarketIssue] = []
    if entry.type != ENDPOINT_ENTRY_TYPE:
        issues.append(_mismatch(entry_path, "type", entry.type, ENDPOINT_ENTRY_TYPE))
    for field, index_value in actual.items():
        definition_value = expected.get(field)
        if index_value != definition_value:
            issues.append(_mismatch(entry_path, field, index_value, definition_value))
    return issues


def _mismatch(entry_path: str, field: str, index_value: object, definition_value: object) -> MarketIssue:
    return MarketIssue(
        INDEX_FILENAME,
        join_path(entry_path, field),
        MarketIssueCode.PROJECTION_MISMATCH,
        {"field": field, "index_value": _display(index_value), "definition_value": _display(definition_value)},
    )


def _display(value: object) -> str:
    return "—" if value is None else str(value)
