"""产物清单与版本历史整份解析次数的观测：在 JSON 解码边界上按各自的顶层结构识别。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.project.resource_paths import RESOURCE_TYPES

_MANIFEST_TOP_LEVEL_FIELDS = frozenset({"entries", "hash_algorithm", "schema_version"})
_VERSIONS_TOP_LEVEL_FIELDS = frozenset(RESOURCE_TYPES)


def count_manifest_parses(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """记录被解码文本的顶层恰好是清单三字段对象的次数；产物身份键、项目文件等其余解码不计入。"""

    return _count_parses(monkeypatch, _MANIFEST_TOP_LEVEL_FIELDS)


def count_versions_parses(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """记录被解码文本的顶层恰好是版本历史各资源类型桶的次数；其余解码不计入。"""

    return _count_parses(monkeypatch, _VERSIONS_TOP_LEVEL_FIELDS)


def _count_parses(monkeypatch: pytest.MonkeyPatch, top_level_fields: frozenset[str]) -> dict[str, int]:
    counts = {"parses": 0}
    original_loads = json.loads

    def _counted_loads(text: str | bytes, *args: Any, **kwargs: Any) -> Any:
        payload = original_loads(text, *args, **kwargs)
        if isinstance(payload, dict) and payload.keys() == top_level_fields:
            counts["parses"] += 1
        return payload

    monkeypatch.setattr(json, "loads", _counted_loads)
    return counts
