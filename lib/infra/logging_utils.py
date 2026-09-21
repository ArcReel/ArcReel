"""日志安全格式化工具。

提供 ``format_kwargs_for_log``：把任意 dict / Pydantic 对象 / 列表
序列化为单行字符串，专门用于 logger.info 调用前对参数做"截断 + 摘要"，
避免长 prompt、参考图 base64、bytes 等大字段把日志撑爆。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

_STR_TRUNCATE_AT = 500
_STR_TRUNCATE_PREFIX = 200
_LIST_TRUNCATE_AT = 10
_LIST_HEAD = 5
_LIST_TAIL = 2

_SEPARATED_SENSITIVE_KEY_PATTERN = (
    r"(?:[A-Za-z][A-Za-z0-9]*[_-])*"
    r"(?:api[_-]?key|authorization|cookie|password|passwd|pwd|secret[_-]?access[_-]?keys?|secrets?|"
    r"access[_-]?tokens?|auth[_-]?tokens?|"
    r"bearer[_-]?tokens?|private[_-]?keys?|signing[_-]?keys?|tokens?)"
)
_CAMEL_SENSITIVE_KEY_PATTERN = r"(?:apiKey|privateKey|signingKey|[A-Za-z][A-Za-z0-9]*(?:ApiKey|PrivateKey|SigningKey|SecretAccessKey|Secret|Token))"
_SENSITIVE_KEY_RE = re.compile(_SEPARATED_SENSITIVE_KEY_PATTERN, re.IGNORECASE)
_CAMEL_SENSITIVE_KEY_RE = re.compile(_CAMEL_SENSITIVE_KEY_PATTERN)
_COOKIE_LINE_RE = re.compile(r"(?im)^(\s*(?:set-)?cookie\s*:\s*).*$")
_AUTH_LINE_RE = re.compile(r"(?im)^(\s*(?:proxy-)?authorization\s*:\s*).*$")
_INLINE_AUTH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:proxy-)?authorization\s*[=:]\s*)(?:(?:basic|bearer)\s+)?[^\s,;&]+"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_SENSITIVE_TEXT_KEY_PATTERN = rf"(?:(?i:{_SEPARATED_SENSITIVE_KEY_PATTERN})|{_CAMEL_SENSITIVE_KEY_PATTERN})"
_DOUBLE_QUOTED_SECRET_RE = re.compile(
    rf"((?<![A-Za-z0-9]){_SENSITIVE_TEXT_KEY_PATTERN}\s*[\'\"]?\s*[=:]\s*\")"
    r'((?:\\.|[^"\\])*)(\")'
)
_SINGLE_QUOTED_SECRET_RE = re.compile(
    rf"((?<![A-Za-z0-9]){_SENSITIVE_TEXT_KEY_PATTERN}\s*['\"]?\s*[=:]\s*')"
    r"((?:\\.|[^'\\])*)(')"
)
_INLINE_SECRET_RE = re.compile(rf"((?<![A-Za-z0-9]){_SENSITIVE_TEXT_KEY_PATTERN}\s*[=:]\s*)" r"(?!['\"])([^\s,;&]+)")
_SIGNED_QUERY_RE = re.compile(
    r"(?i)([?&](?:x-amz-signature|x-goog-signature|signature|sig|access_token|auth_token|token|api_key|key|password)=)([^&#\s]*)"
)
_URL_PASSWORD_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^/@\s:]*:)([^@/\s]+)(@)")
_API_KEY_VALUE_RE = re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-|proj-)?[A-Za-z0-9_-]{8,}")
# 散文里的凭证：上游 4xx 常把密钥连同一句话回显（"Incorrect API key provided: <key>"），
# 标签与值之间隔着普通词，键值型规则匹配不到。标签后最多跨两个词再遇到 : / = 即遮蔽其后的值。
_PROSE_CREDENTIAL_RE = re.compile(
    r"(?i)(\b(?:api|access|secret|auth|private|signing)[ _-]?(?:key|token)s?\b(?:[ \t]+[A-Za-z]+){0,2}[ \t]*[:=]\s*)"
    r"([\'\"]?)[^\s,;&\'\"]+\2"
)
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9]+ )*PRIVATE KEY)-----.*?-----END (?P=label)-----",
    re.DOTALL,
)
_MASKED = "••••"


def _is_sensitive_key(key: str | None) -> bool:
    return bool(key and (_SENSITIVE_KEY_RE.fullmatch(key) or _CAMEL_SENSITIVE_KEY_RE.fullmatch(key)))


#: 用量计数的键形：``prompt_tokens`` / ``total_tokens`` 之类以 token(s) 收尾的名字同时落在
#: 敏感键形里，而它们带的是数字。
_TOKEN_COUNT_KEY_RE = re.compile(r"(?i)(?:[A-Za-z0-9]+[_-])*tokens?")
#: 直接限定 token 的凭证词：``access_token`` 一类无论值是什么都按凭证处理。
_CREDENTIAL_TOKEN_QUALIFIERS = frozenset(
    {"access", "auth", "bearer", "refresh", "id", "api", "secret", "private", "signing", "session", "csrf"}
)


def _is_token_count(key: str | None, value: object) -> bool:
    """键形像 token 计数、值又是数字时，它是用量计数而不是凭证。

    凭证是字符串；``prompt_tokens: 128`` 这类计数被当成凭证遮蔽，会让读侧的用量与计费信息
    静默消失。数字之外、以及被凭证词直接限定的 token 一律仍按凭证处理。
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if not key or not _TOKEN_COUNT_KEY_RE.fullmatch(key):
        return False
    segments = re.split(r"[_-]", key)
    return len(segments) < 2 or segments[-2].lower() not in _CREDENTIAL_TOKEN_QUALIFIERS


def redact_diagnostic_text(value: object) -> str:
    """完整保留诊断文本，只遮蔽可直接用于认证或签名的值。"""
    try:
        rendered = str(value)
    except Exception:
        rendered = f"<unprintable {type(value).__module__}.{type(value).__name__}>"
    rendered = _PEM_PRIVATE_KEY_RE.sub(_MASKED, rendered)
    rendered = _COOKIE_LINE_RE.sub(lambda match: f"{match.group(1)}{_MASKED}", rendered)
    rendered = _AUTH_LINE_RE.sub(lambda match: f"{match.group(1)}{_MASKED}", rendered)
    rendered = _INLINE_AUTH_RE.sub(lambda match: f"{match.group(1)}{_MASKED}", rendered)
    rendered = _BEARER_RE.sub(lambda match: f"{match.group(1)}{_MASKED}", rendered)
    rendered = _DOUBLE_QUOTED_SECRET_RE.sub(lambda match: f"{match.group(1)}{_MASKED}{match.group(3)}", rendered)
    rendered = _SINGLE_QUOTED_SECRET_RE.sub(lambda match: f"{match.group(1)}{_MASKED}{match.group(3)}", rendered)
    rendered = _INLINE_SECRET_RE.sub(lambda match: f"{match.group(1)}{_MASKED}", rendered)
    rendered = _PROSE_CREDENTIAL_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_MASKED}{match.group(2)}", rendered
    )
    rendered = _SIGNED_QUERY_RE.sub(lambda match: f"{match.group(1)}{_MASKED}", rendered)
    rendered = _URL_PASSWORD_RE.sub(lambda match: f"{match.group(1)}{_MASKED}{match.group(3)}", rendered)
    return _API_KEY_VALUE_RE.sub(_MASKED, rendered)


def sanitize_diagnostic_payload(
    value: Any, *, redact_text: Callable[[object], str] = redact_diagnostic_text, _key: str | None = None
) -> Any:
    """清洗 JSON 兼容诊断载荷：不截断未知字段，只完整遮蔽秘密值。

    ``redact_text`` 是逐个字符串的脱敏口径。默认只遮蔽认得出的凭证；载荷会落库或经 API
    回读时传更严的口径（如连 URL 查询串一起剥掉），不必各调用点自己重走一遍递归。

    敏感键形上的数字型 token 计数照原样保留，见 :func:`_is_token_count`。
    """
    if _is_sensitive_key(_key) and not _is_token_count(_key, value):
        return None if value is None else _MASKED
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_diagnostic_payload(item, redact_text=redact_text, _key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [sanitize_diagnostic_payload(item, redact_text=redact_text) for item in value]
    return redact_text(value)


def _redact_value(value: str) -> str:
    """返回凭证的遮蔽显示值：仅保留首尾各 4 字符供人工识别，短值整体替换。

    命名刻意不含 secret/key 等敏感词：返回值是脱敏产物，避免日志静态分析
    按名字启发式把它误判为敏感数据源。
    """
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
    if _is_sensitive_key(key_hint):
        # None 透传以便区分"未配置"和"已配置但脱敏"；其他任何类型（含 int/bool/float）
        # 都整体替换为 ••••，避免 {"password": 123456} 这类用数字当 secret 的场景泄漏。
        if obj is None:
            return None
        if isinstance(obj, str):
            return _redact_value(obj)
        return "••••"

    if obj is None or isinstance(obj, bool | int | float):
        return obj

    if isinstance(obj, str):
        data_uri = re.match(r"^data:([^;,]+);base64,(.*)$", obj, re.DOTALL)
        if data_uri:
            return f"<data-uri:mime={data_uri.group(1)},chars={len(data_uri.group(2))}>"
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
        # 不能回退到 repr(payload)：原始对象未经脱敏，可能把敏感字段
        # 字面量重新带回日志，绕过前面所有的脱敏逻辑。固定占位符更安全。
        return "<unserializable>"
