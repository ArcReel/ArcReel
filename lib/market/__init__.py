"""市场源工具库：索引 schema、生成器与校验器；命令行入口是 ``python -m lib.market``。"""

from .entry import PROJECTED_META_FIELDS, check_entry_definition, project_meta
from .generate import GenerateError, build_index, render_index, write_index
from .icon import ICON_FORMATS, ICON_MAX_BYTES, inspect_icon
from .index import (
    ENDPOINT_ENTRY_TYPE,
    INDEX_SCHEMA_VERSION,
    InvalidIndexError,
    MarketIndex,
    MarketIndexEntry,
    MarketIndexError,
    UnsupportedIndexSchemaError,
    index_schema_issues,
    load_index_schema,
    parse_index,
)
from .issues import INDEX_FILENAME, MESSAGE_KEY_PREFIX, MarketIssue, MarketIssueCode, message_key
from .source import check_source

__all__ = [
    "ENDPOINT_ENTRY_TYPE",
    "ICON_FORMATS",
    "ICON_MAX_BYTES",
    "INDEX_FILENAME",
    "INDEX_SCHEMA_VERSION",
    "MESSAGE_KEY_PREFIX",
    "PROJECTED_META_FIELDS",
    "GenerateError",
    "InvalidIndexError",
    "MarketIndex",
    "MarketIndexEntry",
    "MarketIndexError",
    "MarketIssue",
    "MarketIssueCode",
    "UnsupportedIndexSchemaError",
    "build_index",
    "check_entry_definition",
    "check_source",
    "index_schema_issues",
    "inspect_icon",
    "load_index_schema",
    "message_key",
    "parse_index",
    "project_meta",
    "render_index",
    "write_index",
]
