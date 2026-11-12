"""在 JSON 字符预算内收窄错误参数，保持结构合法。"""

from __future__ import annotations

import json
from typing import Any


def shrink_json_strings(params: dict[str, Any], shrinkable: list[tuple[dict[str, Any], str]], limit: int) -> bool:
    """原位裁剪指定字符串，按实际 JSON 转义长度收敛；骨架放不下时返回 False。"""
    encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    while len(encoded) > limit:
        if not shrinkable:
            return False
        owner, longest_key = max(shrinkable, key=lambda item: len(item[0][item[1]]))
        current: str = owner[longest_key]
        if not current:
            return False
        # JSON 转义会放大长度；差值超过原串时逐次减半，保留可容纳的诊断内容。
        deficit = len(encoded) - limit
        drop = deficit if deficit < len(current) else max(1, len(current) // 2)
        owner[longest_key] = current[: len(current) - drop]
        encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    return True
