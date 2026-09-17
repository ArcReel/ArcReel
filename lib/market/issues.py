"""市场源诊断载体：稳定码 + 文件与 JSON 内定位 + locale-neutral 消息。

每个码都有 ``val_market_<code>`` 消息键，新增码必须同步三种语言的消息。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lib.validation_messages import ValidationMessage

MESSAGE_KEY_PREFIX = "val_market_"

#: JSON 文档内的根路径。
ROOT_PATH = "$"

#: 索引文件名，也是诊断里指向索引时的 ``file``。
INDEX_FILENAME = "arcreel-market.json"


class MarketIssueCode(StrEnum):
    """市场源校验能产出的全部诊断码。"""

    # ---- ① 索引结构 ----
    INDEX_UNREADABLE = "index_unreadable"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    MISSING_FIELD = "missing_field"
    INVALID_TYPE = "invalid_type"
    INVALID_VALUE = "invalid_value"

    # ---- ② slug ----
    SLUG_INVALID = "slug_invalid"
    SLUG_DUPLICATE = "slug_duplicate"
    SLUG_DIRECTORY_MISMATCH = "slug_directory_mismatch"

    # ---- ③ 旁置文件 ----
    PATH_NOT_RELATIVE = "path_not_relative"
    FILE_MISSING = "file_missing"
    ICON_FORMAT_INVALID = "icon_format_invalid"
    ICON_TOO_LARGE = "icon_too_large"
    ICON_NOT_SQUARE = "icon_not_square"
    ICON_AMBIGUOUS = "icon_ambiguous"

    # ---- ④ 定义 ----
    DEFINITION_UNREADABLE = "definition_unreadable"
    DEFINITION_INVALID = "definition_invalid"

    # ---- ⑤ 投影一致性 ----
    PROJECTION_MISMATCH = "projection_mismatch"

    # ---- ⑥ 版本门槛格式 ----
    MIN_APP_VERSION_INVALID = "min_app_version_invalid"


def message_key(code: MarketIssueCode) -> str:
    return f"{MESSAGE_KEY_PREFIX}{code.value}"


@dataclass(frozen=True)
class MarketIssue:
    """一条诊断。``file`` 是相对市场源根目录的文件，``path`` 是该 JSON 文件内的定位串。"""

    file: str
    path: str
    code: MarketIssueCode
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> ValidationMessage:
        return ValidationMessage(message_key(self.code), self.params)

    def render(self, translate: Callable[..., str] | None = None) -> str:
        """渲染成 ``file:path: [code] message`` 一行，供 CLI 与失败原因记录直接使用。"""
        return f"{self.file}:{self.path}: [{self.code.value}] {self.message.render(translate)}"


def join_path(base: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{base}[{key}]"
    return key if base == ROOT_PATH else f"{base}.{key}"
