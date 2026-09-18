"""索引生成器：按目录约定 ``endpoints/<slug>/definition.json`` + 可选 ``icon.<png|webp|svg>`` 投影索引。

条目元数据的唯一手写来源是定义 ``meta``；目录名即 slug。生成器只负责投影与发现，合规与否由
:func:`lib.market.check_source` 判定——产物必须能过它。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.validation_messages import ValidationMessage

from .entry import project_meta
from .icon import ICON_FORMATS
from .index import ENDPOINT_ENTRY_TYPE, INDEX_SCHEMA_VERSION
from .issues import INDEX_FILENAME, ROOT_PATH, MarketIssue, MarketIssueCode
from .source import _check_source_document, read_index_document, read_json_file

ENDPOINTS_DIR = "endpoints"
DEFINITION_FILENAME = "definition.json"
ICON_STEM = "icon"

#: 沿用既有索引时保留的顶层可选字段。
_HEADER_FIELDS = ("name", "description", "homepage")


class GenerateError(Exception):
    """目录里有生成器无法投影的条目。"""

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
    """生成 ``root`` 的索引。

    顶层字段优先取参数，其次沿用既有索引；``name`` 两者都取不到时（无索引、索引不可读或其 ``name``
    不是字符串）用 ``default_name``，再退到目录名。
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
    if issues := _check_source_document(root, index):
        raise GenerateError(issues)
    return index


def render_index(index: dict[str, Any]) -> str:
    return json.dumps(index, ensure_ascii=False, indent=2) + "\n"


def write_index(root: Path, index: dict[str, Any]) -> Path:
    path = root / INDEX_FILENAME
    # 索引是生成物：被换成符号链接时替换链接本身，否则写入会落到链接目标，索引文件在 git 里不变。
    if path.is_symlink():
        path.unlink()
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
                {"detail": ValidationMessage("val_market_detail_meta_not_object")},
            )
        )
        return None

    entry: dict[str, Any] = {"type": ENDPOINT_ENTRY_TYPE, "slug": slug, "path": definition_path}
    entry.update(project_meta(definition))
    if len(icons) == 1:
        entry["icon"] = f"{relative_dir}/{icons[0]}"
    return entry


def _symlink_issue(relative: str) -> MarketIssue:
    """生成器从目录发现的条目与 ``check`` 同口径：市场源里被引用的路径不能是符号链接（见 :func:`_find_symlink`）。"""
    return MarketIssue(relative, ROOT_PATH, MarketIssueCode.SYMLINK_NOT_ALLOWED, {"value": relative})
