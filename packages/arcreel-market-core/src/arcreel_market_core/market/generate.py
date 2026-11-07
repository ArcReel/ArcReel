"""Index generator: project an index from directory conventions ``endpoints/<slug>/definition.json`` + optional ``icon.<png|webp|svg>``.

Definition ``meta`` is the sole handwritten source of entry metadata; the directory name is slug. The generator only projects and discovers; compliance is decided by
:func:`arcreel_market_core.market.check_source`, which the output must pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arcreel_market_core.validation_messages import ValidationMessage

from .entry import project_meta
from .icon import ICON_FORMATS
from .index import ENDPOINT_ENTRY_TYPE, INDEX_SCHEMA_VERSION
from .issues import INDEX_FILENAME, ROOT_PATH, MarketIssue, MarketIssueCode
from .source import check_source_document, read_index_document, read_json_file

ENDPOINTS_DIR = "endpoints"
DEFINITION_FILENAME = "definition.json"
ICON_STEM = "icon"

#: Optional top-level fields retained when reusing an existing index.
_HEADER_FIELDS = ("name", "description", "homepage")

#: Detail message in ``definition_invalid`` diagnostics when an entry definition lacks a ``meta`` object.
META_NOT_OBJECT_KEY = "val_market_detail_meta_not_object"


class GenerateError(Exception):
    """The directory contains entries the generator cannot project."""

    def __init__(self, issues: list[MarketIssue]) -> None:
        super().__init__(f"cannot generate market index ({len(issues)} issues)")
        self.issues = tuple(issues)


def build_index(
    root: Path,
    *,
    name: str | None = None,
    description: str | None = None,
    homepage: str | None = None,
    default_name: str | None = None,
) -> dict[str, Any]:
    """Generate the index for ``root``.

    Top-level fields prefer arguments, then reuse the existing index; when neither supplies ``name`` (no index, unreadable index, or ``name``
    not a string), use ``default_name``, then fall back to the directory name.
    """
    header = _existing_header(root)
    for field, value in (("name", name), ("description", description), ("homepage", homepage)):
        if value is not None:
            header[field] = value
    header.setdefault("name", default_name if default_name is not None else root.resolve().name)

    entries: list[dict[str, Any]] = []
    issues: list[MarketIssue] = []
    endpoints = root / ENDPOINTS_DIR
    if endpoints.is_symlink():
        issues.append(_symlink_issue(ENDPOINTS_DIR))
    elif endpoints.is_dir():
        for directory in sorted(path for path in endpoints.iterdir() if path.is_dir() or path.is_symlink()):
            entry = _entry_of(directory, issues)
            if entry is not None:
                entries.append(entry)
    if issues:
        raise GenerateError(issues)

    index: dict[str, Any] = {"schema_version": INDEX_SCHEMA_VERSION}
    index.update({field: header[field] for field in _HEADER_FIELDS if field in header})
    index["entries"] = entries
    if issues := check_source_document(root, index):
        raise GenerateError(issues)
    return index


def render_index(index: dict[str, Any]) -> str:
    return json.dumps(index, ensure_ascii=False, indent=2) + "\n"


def write_index(root: Path, index: dict[str, Any]) -> Path:
    path = root / INDEX_FILENAME
    # The index is generated: replace a substituted symlink itself, or writes would hit its target while the Git index file stays unchanged.
    if path.is_symlink():
        path.unlink()  # hard-delete: derived -- Replace the symlink itself when substituted for the generated market index
    path.write_text(render_index(index), encoding="utf-8")
    return path


def _existing_header(root: Path) -> dict[str, Any]:
    try:
        document = read_index_document(root)
    except ValueError:
        return {}
    if not isinstance(document, dict):
        return {}
    return {field: document[field] for field in _HEADER_FIELDS if isinstance(document.get(field), str)}


def _entry_of(directory: Path, issues: list[MarketIssue]) -> dict[str, Any] | None:
    slug = directory.name
    relative_dir = f"{ENDPOINTS_DIR}/{slug}"
    definition_path = f"{relative_dir}/{DEFINITION_FILENAME}"
    if directory.is_symlink():
        issues.append(_symlink_issue(relative_dir))
        return None
    icons = [
        f"{ICON_STEM}{suffix}"
        for suffix in ICON_FORMATS
        if (icon := directory / f"{ICON_STEM}{suffix}").is_file() or icon.is_symlink()
    ]
    issues.extend(_symlink_issue(f"{relative_dir}/{icon}") for icon in icons if (directory / icon).is_symlink())
    if len(icons) > 1:
        issues.append(MarketIssue(relative_dir, ROOT_PATH, MarketIssueCode.ICON_AMBIGUOUS, {"icons": ", ".join(icons)}))

    if (directory / DEFINITION_FILENAME).is_symlink():
        issues.append(_symlink_issue(definition_path))
        return None
    if not (directory / DEFINITION_FILENAME).is_file():
        issues.append(MarketIssue(definition_path, ROOT_PATH, MarketIssueCode.FILE_MISSING, {"value": definition_path}))
        return None
    try:
        definition = read_json_file(directory / DEFINITION_FILENAME)
    except ValueError as exc:
        issues.append(
            MarketIssue(definition_path, ROOT_PATH, MarketIssueCode.DEFINITION_UNREADABLE, {"detail": str(exc)})
        )
        return None
    meta = definition.get("meta") if isinstance(definition, dict) else None
    if not isinstance(meta, dict):
        issues.append(
            MarketIssue(
                definition_path,
                ROOT_PATH,
                MarketIssueCode.DEFINITION_UNREADABLE,
                {"detail": ValidationMessage(META_NOT_OBJECT_KEY)},
            )
        )
        return None

    entry: dict[str, Any] = {"type": ENDPOINT_ENTRY_TYPE, "slug": slug, "path": definition_path}
    entry.update(project_meta(definition))
    if len(icons) == 1:
        entry["icon"] = f"{relative_dir}/{icons[0]}"
    return entry


def _symlink_issue(relative: str) -> MarketIssue:
    """Discovered directory entries follow ``check`` semantics: referenced market-source paths cannot be symlinks (see :func:`_find_symlink`)."""
    return MarketIssue(relative, ROOT_PATH, MarketIssueCode.SYMLINK_NOT_ALLOWED, {"value": relative})
