"""日志安全格式化工具。

提供 ``format_kwargs_for_log``：把任意 dict / Pydantic 对象 / 列表
序列化为单行字符串，专门用于 logger.info 调用前对参数做"截断 + 摘要"，
避免长 prompt、参考图 base64、bytes 等大字段把日志撑爆。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

_STR_TRUNCATE_AT = 500
_STR_TRUNCATE_PREFIX = 200
_LIST_TRUNCATE_AT = 10
_LIST_HEAD = 5
_LIST_TAIL = 2

_SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|secret|token|password|authorization)", re.IGNORECASE)


def _mask_secret(value: str) -> str:
    raw = value.strip()
    if len(raw) <= 8:
        return "••••"
    return f"{raw[:4]}…{raw[-4:]}"


def _truncate_str(value: str) -> str:
    if len(value) <= _STR_TRUNCATE_AT:
        return value
    return f"{value[:_STR_TRUNCATE_PREFIX]}... <truncated, total {len(value)} chars>"


def _summarize_image_like(obj: Any) -> str | None:
    mime = getattr(obj, "mime_type", None)
    if mime is not None:
        data = getattr(obj, "data", None)
        size = len(data) if isinstance(data, bytes | bytearray) else None
        return f"<image:mime={mime},bytes={size}>" if size is not None else f"<image:mime={mime}>"
    if hasattr(obj, "size") and hasattr(obj, "mode") and hasattr(obj, "format"):
        return f"<image:format={obj.format},size={obj.size},mode={obj.mode}>"
    if hasattr(obj, "read") and callable(obj.read):
        return f"<file-like:{type(obj).__name__}>"
    return None


def _to_safe(obj: Any, key_hint: str | None = None) -> Any:
    # 敏感 key 命中时整体脱敏：避免 {"api_key": {"value": "secret"}} 这类嵌套结构
    # 在递归到 "value" 子键时，因为 key_hint 不再敏感而泄漏 secret。
    if key_hint and _SENSITIVE_KEY_RE.search(key_hint):
        # None 透传以便区分"未配置"和"已配置但脱敏"；其他任何类型（含 int/bool/float）
        # 都整体替换为 ••••，避免 {"password": 123456} 这类用数字当 secret 的场景泄漏。
        if obj is None:
            return None
        if isinstance(obj, str):
            return _mask_secret(obj)
        return "••••"

    if obj is None or isinstance(obj, bool | int | float):
        return obj

    if isinstance(obj, str):
        return _truncate_str(obj)

    if isinstance(obj, bytes | bytearray):
        return f"<bytes:{len(obj)}>"

    image_summary = _summarize_image_like(obj)
    if image_summary is not None:
        return image_summary

    if isinstance(obj, Mapping):
        return {str(k): _to_safe(v, key_hint=str(k)) for k, v in obj.items()}

    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump) and not isinstance(obj, type):
        try:
            return _to_safe(model_dump())
        except Exception:
            pass

    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return _to_safe(asdict(obj))
        except Exception:
            pass

    if isinstance(obj, Sequence) and not isinstance(obj, str | bytes | bytearray):
        items = list(obj)
        if len(items) > _LIST_TRUNCATE_AT:
            head = [_to_safe(x) for x in items[:_LIST_HEAD]]
            tail = [_to_safe(x) for x in items[-_LIST_TAIL:]]
            omitted = len(items) - _LIST_HEAD - _LIST_TAIL
            return [*head, f"<omitted:{omitted}>", *tail]
        return [_to_safe(x) for x in items]

    return _truncate_str(repr(obj))


def format_kwargs_for_log(payload: Any) -> str:
    """把任意对象转成单行、安全可读的字符串，供 logger 输出。

    - 长字符串截断到 500 字
    - bytes/bytearray 替换为 ``<bytes:N>``
    - 嵌套 dict/list 递归处理
    - 敏感 key（api_key/secret/token/password/authorization）整体脱敏
    - Pydantic / dataclass 自动转 dict

    "失败不影响主流程"：日志辅助逻辑任何异常都吞掉，最差也只返回 ``<unserializable>``，
    保证生成调用链不会因为日志格式化崩溃。
    """
    try:
        safe = _to_safe(payload)
        return json.dumps(safe, ensure_ascii=False, default=str)
    except Exception:
        try:
            return _truncate_str(repr(payload))
        except Exception:
            return "<unserializable>"
