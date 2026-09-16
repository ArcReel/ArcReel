"""市场源索引的 schema 与读取口径（校验规则 ①）。

:func:`parse_index` 先按主版本判定能否读，再过 ``index_schema.json`` 整份判定，最后投影出当前
版本认识的条目。纯逻辑：输入是已解析的 JSON 值。
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

from lib.custom_provider.endpoint_definition import parse_semver

from .issues import INDEX_FILENAME, ROOT_PATH, MarketIssue, MarketIssueCode, join_path

INDEX_SCHEMA_PATH = Path(__file__).parent / "index_schema.json"

#: 本客户端实现的索引格式版本。主版本更高的索引整份拒读，同主版本的更高 minor 照常读。
INDEX_SCHEMA_VERSION = "1.0.0"

#: 本客户端识别的唯一条目类型；其余类型读索引时静默跳过。
ENDPOINT_ENTRY_TYPE = "endpoint"

#: 字段名 → 该字段格式不合规（pattern / 禁换行）时的专用码，其余格式问题报 ``invalid_value``。
_FORMAT_CODES = {
    "slug": MarketIssueCode.SLUG_INVALID,
    "path": MarketIssueCode.PATH_NOT_RELATIVE,
    "icon": MarketIssueCode.PATH_NOT_RELATIVE,
    "min_app_version": MarketIssueCode.MIN_APP_VERSION_INVALID,
}

#: 同一实例在 if/then 引用下会被多条规则各报一遍，这些关键字的报错按「路径 + 码」去重即可。
_FORMAT_KEYWORDS = frozenset({"pattern", "not"})


@dataclass(frozen=True)
class MarketIndexEntry:
    """一个调用端点条目。``path`` / ``icon`` 是相对索引所在目录的路径。"""

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
    """索引不可用。"""


class UnsupportedIndexSchemaError(MarketIndexError):
    """索引主版本高于本客户端，整份拒读。"""

    def __init__(self, version: str) -> None:
        super().__init__(f"unsupported market index schema_version {version}")
        self.version = version
        self.supported = INDEX_SCHEMA_VERSION


class InvalidIndexError(MarketIndexError):
    """索引未过 schema；任一条目不合规即整份无效。"""

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
    """按客户端兼容口径读一份索引。

    Raises:
        UnsupportedIndexSchemaError: 主版本高于本客户端（先于 schema 判定：新主版本的形状本就判不了）。
        InvalidIndexError: 未过索引 schema。
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
    """索引过 schema 的全部诊断，按出现位置排序、去重。"""
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
