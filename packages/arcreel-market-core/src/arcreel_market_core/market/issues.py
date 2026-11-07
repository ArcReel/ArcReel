"""Market-source diagnostic carrier: stable code + file and JSON location + locale-neutral message.

Each code has a ``val_market_<code>`` message key; new codes must add messages in all three languages.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from arcreel_market_core.validation_messages import Translator, ValidationMessage

MESSAGE_KEY_PREFIX = "val_market_"

#: Root path within the JSON document.
ROOT_PATH = "$"

#: Index filename, also the diagnostic ``file`` when referring to the index.
INDEX_FILENAME = "arcreel-market.json"


class MarketIssueCode(StrEnum):
    """All diagnostic codes produced by market-source validation."""

    # ---- ① Index structure ----
    INDEX_UNREADABLE = "index_unreadable"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    MISSING_FIELD = "missing_field"
    INVALID_TYPE = "invalid_type"
    INVALID_VALUE = "invalid_value"

    # ---- ② slug ----
    SLUG_INVALID = "slug_invalid"
    SLUG_DUPLICATE = "slug_duplicate"
    SLUG_DIRECTORY_MISMATCH = "slug_directory_mismatch"

    # ---- ③ Sidecar files ----
    PATH_NOT_RELATIVE = "path_not_relative"
    FILE_MISSING = "file_missing"
    SYMLINK_NOT_ALLOWED = "symlink_not_allowed"
    ICON_FORMAT_INVALID = "icon_format_invalid"
    ICON_TOO_LARGE = "icon_too_large"
    ICON_NOT_SQUARE = "icon_not_square"
    ICON_AMBIGUOUS = "icon_ambiguous"

    # ---- ④ Definitions ----
    DEFINITION_UNREADABLE = "definition_unreadable"
    DEFINITION_INVALID = "definition_invalid"

    # ---- ⑤ Projection consistency ----
    PROJECTION_MISMATCH = "projection_mismatch"

    # ---- ⑥ Version-threshold format ----
    MIN_APP_VERSION_INVALID = "min_app_version_invalid"


def message_key(code: MarketIssueCode) -> str:
    return f"{MESSAGE_KEY_PREFIX}{code.value}"


@dataclass(frozen=True)
class MarketIssue:
    """One diagnostic. ``file`` is relative to the market-source root; ``path`` locates the value within that JSON file."""

    file: str
    path: str
    code: MarketIssueCode
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> ValidationMessage:
        return ValidationMessage(message_key(self.code), self.params)

    def render(self, translate: Translator) -> str:
        """Render one ``file:path: [code] message`` line for direct CLI use and failure-reason recording."""
        return f"{self.file}:{self.path}: [{self.code.value}] {self.message.render(translate)}"


def join_path(base: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{base}[{key}]"
    return key if base == ROOT_PATH else f"{base}.{key}"
