"""条目与其定义的一致性（规则 ④⑤）：定义过共享校验器，索引条目等于定义 ``meta`` 的投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lib.custom_provider.endpoint_definition import validate_definition

from .index import ENDPOINT_ENTRY_TYPE, MarketIndexEntry
from .issues import INDEX_FILENAME, ROOT_PATH, MarketIssue, MarketIssueCode, join_path

#: 调用端点条目的媒体类型固定为视频（定义格式本身只描述视频端点）。
ENDPOINT_MEDIA_TYPE = "video"

#: 从定义 ``meta`` 投影进索引条目的字段，顺序即生成器写出的顺序。
PROJECTED_META_FIELDS = ("name", "author", "version", "description", "homepage", "min_app_version")


def project_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """定义 ``meta`` 在索引条目里的投影：只含 meta 里出现的字段，外加固定的 ``media_type``。"""
    projection = {field: meta[field] for field in PROJECTED_META_FIELDS if field in meta}
    projection["media_type"] = ENDPOINT_MEDIA_TYPE
    return projection


def check_entry_definition(
    entry: MarketIndexEntry,
    definition: object,
    *,
    definition_file: str,
    entry_path: str = ROOT_PATH,
) -> list[MarketIssue]:
    """规则 ④⑤。``definition_file`` 与 ``entry_path`` 只用于诊断定位。"""
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
    if isinstance(meta, Mapping):
        issues.extend(_projection_issues(entry, meta, entry_path))
    return issues


def _projection_issues(entry: MarketIndexEntry, meta: Mapping[str, Any], entry_path: str) -> list[MarketIssue]:
    expected = project_meta(meta)
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
