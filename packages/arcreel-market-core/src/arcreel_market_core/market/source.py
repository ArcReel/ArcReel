"""Validate a market source directory (all six rules).

Validation expands entries from the index; directory conventions apply only to the rule that slug equals the definition directory name. If structure is invalid (rule ①
fails), do not run per-file rules.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .entry import check_entry_definition
from .icon import inspect_icon
from .index import ENDPOINT_ENTRY_TYPE, InvalidIndexError, UnsupportedIndexSchemaError, parse_index
from .issues import INDEX_FILENAME, ROOT_PATH, MarketIssue, MarketIssueCode, join_path


def read_json_file(path: Path) -> Any:
    """Read a UTF-8 JSON file; on read or parse failure (including excessive nesting), raise ``ValueError`` with the reason as its message."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc


def read_index_document(root: Path) -> Any:
    """Read the index under ``root``. As a generated artifact it must be a regular file: do not follow symlinks, which could read arbitrary target files."""
    path = root / INDEX_FILENAME
    if path.is_symlink():
        raise ValueError(f"{INDEX_FILENAME} is a symbolic link")
    return read_json_file(path)


def check_source(root: Path) -> list[MarketIssue]:
    """Validate the market source under ``root`` and return all diagnostics; an empty list means compliance."""
    try:
        document = read_index_document(root)
    except ValueError as exc:
        return [MarketIssue(INDEX_FILENAME, ROOT_PATH, MarketIssueCode.INDEX_UNREADABLE, {"detail": str(exc)})]
    return check_source_document(root, document)


def check_source_document(root: Path, document: object) -> list[MarketIssue]:
    """Validate the already-read index and referenced files, sharing a round-trip gate between ``check`` and the generator."""
    try:
        index = parse_index(document)
    except UnsupportedIndexSchemaError as exc:
        return [
            MarketIssue(
                INDEX_FILENAME,
                "schema_version",
                MarketIssueCode.UNSUPPORTED_SCHEMA_VERSION,
                {"version": exc.version, "supported": exc.supported},
            )
        ]
    except InvalidIndexError as exc:
        return list(exc.issues)

    document = cast(dict[str, Any], document)
    root_resolved = root.resolve()
    positions = [i for i, raw in enumerate(document["entries"]) if raw["type"] == ENDPOINT_ENTRY_TYPE]
    issues: list[MarketIssue] = []
    seen_slugs: set[str] = set()
    for position, entry in zip(positions, index.entries, strict=True):
        entry_path = f"entries[{position}]"
        slug_path = join_path(entry_path, "slug")
        if entry.slug in seen_slugs:
            issues.append(_index_issue(slug_path, MarketIssueCode.SLUG_DUPLICATE, slug=entry.slug))
        seen_slugs.add(entry.slug)
        directory = PurePosixPath(entry.path).parent.name
        if directory != entry.slug:
            issues.append(
                _index_issue(slug_path, MarketIssueCode.SLUG_DIRECTORY_MISMATCH, slug=entry.slug, directory=directory)
            )

        if entry.icon is not None:
            icon_file = _referenced_file(root, root_resolved, entry.icon, join_path(entry_path, "icon"), issues)
            if icon_file is not None:
                issues.extend(inspect_icon(entry.icon, icon_file.read_bytes()))

        definition_file = _referenced_file(root, root_resolved, entry.path, join_path(entry_path, "path"), issues)
        if definition_file is None:
            continue
        try:
            definition = read_json_file(definition_file)
        except ValueError as exc:
            issues.append(
                MarketIssue(entry.path, ROOT_PATH, MarketIssueCode.DEFINITION_UNREADABLE, {"detail": str(exc)})
            )
            continue
        issues.extend(check_entry_definition(entry, definition, definition_file=entry.path, entry_path=entry_path))
    return issues


def _find_symlink(root: Path, relative: str) -> str | None:
    """Return the first symlink along ``relative`` (including itself) as a POSIX path relative to ``root``, or None if absent.

    Clients fetch files via GitHub raw, which returns a symlink target's path text rather than its content, so referenced
    files and their containing directories in market sources must be real entries.
    """
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return current.relative_to(root).as_posix()
    return None


def _referenced_file(
    root: Path, root_resolved: Path, relative: str, location: str, issues: list[MarketIssue]
) -> Path | None:
    """Parse an in-repository file referenced by the index; diagnose and return None on symlinks along the path, escape from the market source, absence, or parse failure."""
    if (link := _find_symlink(root, relative)) is not None:
        issues.append(_index_issue(location, MarketIssueCode.SYMLINK_NOT_ALLOWED, value=link))
        return None
    try:
        resolved = (root / relative).resolve()
    except (OSError, RuntimeError):
        issues.append(_index_issue(location, MarketIssueCode.FILE_MISSING, value=relative))
        return None
    if not resolved.is_relative_to(root_resolved):
        issues.append(_index_issue(location, MarketIssueCode.PATH_NOT_RELATIVE, value=relative))
        return None
    if not resolved.is_file():
        issues.append(_index_issue(location, MarketIssueCode.FILE_MISSING, value=relative))
        return None
    return resolved


def _index_issue(path: str, code: MarketIssueCode, **params: str) -> MarketIssue:
    return MarketIssue(INDEX_FILENAME, path, code, params)
