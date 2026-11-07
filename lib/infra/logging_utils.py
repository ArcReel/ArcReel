"""Safe log formatting.

``format_kwargs_for_log`` serializes dicts, Pydantic objects and lists into one
line, truncating/summarizing logger.info parameters so large prompts, reference
image base64 and bytes do not flood logs.
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
# Credentials in prose: upstream 4xx errors may echo keys in text ("Incorrect API key provided: <key>");
# key/value rules miss intervening words. After a label, allow at most two words before : / = and mask the following value.
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


#: Usage-count key shapes: token(s)-suffixed names such as prompt_tokens / total_tokens also match
#: sensitive key shapes but hold numbers.
_TOKEN_COUNT_KEY_RE = re.compile(r"(?i)(?:[A-Za-z0-9]+[_-])*tokens?")
#: Credential-qualified token names, such as access_token, are always credentials regardless of value.
_CREDENTIAL_TOKEN_QUALIFIERS = frozenset(
    {"access", "auth", "bearer", "refresh", "id", "api", "secret", "private", "signing", "session", "csrf"}
)


def _is_token_count(key: str | None, value: object) -> bool:
    """A token-count-shaped key with a numeric value represents usage, not a credential.

    Credentials are strings. Redacting counts such as ``prompt_tokens: 128`` silently
    removes usage/billing information from readers. Nonnumeric values and token keys
    explicitly qualified as credentials remain credentials.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if not key or not _TOKEN_COUNT_KEY_RE.fullmatch(key):
        return False
    segments = re.split(r"[_-]", key)
    return len(segments) < 2 or segments[-2].lower() not in _CREDENTIAL_TOKEN_QUALIFIERS


def redact_diagnostic_text(value: object) -> str:
    """Preserve full diagnostic text, masking only values usable directly for authentication or signing."""
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
    """Clean JSON-compatible diagnostics without truncating unknown fields; mask complete secret values.

    ``redact_text`` controls per-string redaction. The default masks recognized
    credentials only. Pass stricter rules, such as stripping URL queries, for data
    persisted or read back through APIs, rather than repeating recursion per caller.

    Preserve numeric token counts on sensitive-shaped keys; see :func:`_is_token_count`.
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


def redact_value(value: str) -> str:
    """Display a masked credential, retaining its first/last 4 characters for recognition; mask short values completely.

    Deliberately avoid secret/key in the name: this returns redacted data and should
    not be misclassified as a sensitive source by name-based static log analysis.
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
    # Mask an entire sensitive-key value: in nested {"api_key": {"value": "secret"}},
    # recursing into "value" would otherwise lose the sensitive key_hint and leak the secret.
    if _is_sensitive_key(key_hint):
        # Pass None through to distinguish unconfigured from configured-but-redacted; replace all other types,
        # including int/bool/float, with bullets so numeric secrets such as {"password": 123456} cannot leak.
        if obj is None:
            return None
        if isinstance(obj, str):
            return redact_value(obj)
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
    """Convert an arbitrary object to a safe, readable one-line log string.

    - Truncate long strings to 500 characters.
    - Replace bytes/bytearray with ``<bytes:N>``.
    - Recursively process nested dicts/lists.
    - Fully redact sensitive keys (api_key/secret/token/password/authorization).
    - Automatically convert Pydantic/dataclass objects to dicts.

    Formatting must not affect the main flow: swallow any helper exception and return
    at worst ``<unserializable>``, so generation cannot crash because of log formatting.
    """
    try:
        safe = _to_safe(payload)
        return json.dumps(safe, ensure_ascii=False, default=str)
    except Exception:
        # Never fall back to repr(payload): the original object is unredacted and could restore sensitive
        # values to logs, bypassing all prior redaction. A fixed placeholder is safer.
        return "<unserializable>"
