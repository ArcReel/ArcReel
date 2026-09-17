"""按目录校验一个市场源（全部六条规则）。

校验以索引为准逐条展开，目录约定只体现在「slug 等于定义所在目录名」这一条上；结构不成立（规则 ①
不过）时不再跑逐文件规则。
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
    """读一份 UTF-8 JSON 文件；读不到或解析不了（含嵌套过深）时抛 ``ValueError``，消息即原因。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc


def read_index_document(root: Path) -> Any:
    """读 ``root`` 下的索引。索引是生成物、应为普通文件：符号链接不跟随，否则会读入链接指向的任意文件。"""
    path = root / INDEX_FILENAME
    if path.is_symlink():
        raise ValueError(f"{INDEX_FILENAME} is a symbolic link")
    return read_json_file(path)


def check_source(root: Path) -> list[MarketIssue]:
    """校验 ``root`` 下的市场源，返回全部诊断；空列表即合规。"""
    try:
        document = read_index_document(root)
    except ValueError as exc:
        return [MarketIssue(INDEX_FILENAME, ROOT_PATH, MarketIssueCode.INDEX_UNREADABLE, {"detail": str(exc)})]
    return _check_source_document(root, document)


def _check_source_document(root: Path, document: object) -> list[MarketIssue]:
    """校验已读取的索引及其引用文件，供 ``check`` 与生成器共用往返闸门。"""
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


def _referenced_file(
    root: Path, root_resolved: Path, relative: str, location: str, issues: list[MarketIssue]
) -> Path | None:
    """解析索引引用的仓内文件；越出市场源（含经符号链接）、不存在或解析不了（如符号链接成环）时记诊断并返回 None。"""
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
