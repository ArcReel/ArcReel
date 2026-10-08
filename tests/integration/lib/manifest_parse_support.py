"""产物清单整份解析次数的观测：在 JSON 解码边界上按清单的顶层结构识别。"""

from __future__ import annotations

import json
from typing import Any

import pytest

_MANIFEST_TOP_LEVEL_FIELDS = frozenset({"entries", "hash_algorithm", "schema_version"})


def count_manifest_parses(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """记录被解码文本的顶层恰好是清单三字段对象的次数；产物身份键、项目文件等其余解码不计入。"""

    counts = {"parses": 0}
    original_loads = json.loads

    def _counted_loads(text: str | bytes, *args: Any, **kwargs: Any) -> Any:
        payload = original_loads(text, *args, **kwargs)
        if isinstance(payload, dict) and payload.keys() == _MANIFEST_TOP_LEVEL_FIELDS:
            counts["parses"] += 1
        return payload

    monkeypatch.setattr(json, "loads", _counted_loads)
    return counts
