"""Structured task-failure encoding for the generation worker.

The worker (lib layer) stores a machine-stable reason in ``Task.error_message``
instead of locale-locked text: a ``[code]`` token optionally followed by a JSON
object of parameters. The tasks API serialization path renders that reason via
the request Translator on read, so the same failed task shows zh/en/vi text per
``Accept-Language``.

Anything that is not a recognised ``[code]`` form — raw provider exception text
(``str(exc)``), or legacy rows written before this format — passes through
verbatim, so no stored reason is ever lost or mis-parsed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

# Stable failure code -> i18n errors key. The code is agent-facing and persisted
# in the DB; the key resolves to zh/en/vi templates rendered at read time.
FAILURE_CODE_KEYS: dict[str, str] = {
    "provider_unsupported_media": "task_fail_provider_unsupported_media",
    "restart_lost_image": "task_fail_restart_lost_image",
    "restart_lost_audio": "task_fail_restart_lost_audio",
    "restart_lost_no_job_id": "task_fail_restart_lost_no_job_id",
    "restart_lost_resume_no_job_id": "task_fail_restart_lost_resume_no_job_id",
    "resume_unsupported_provider": "task_fail_resume_unsupported_provider",
    "resume_unsupported_capacity_zero": "task_fail_resume_unsupported_capacity_zero",
    "resume_unsupported_detail": "task_fail_resume_unsupported_detail",
    "resume_expired_detail": "task_fail_resume_expired_detail",
    # ScriptEditError.key 本身就是 errors.py 的 key（见 lib/script_editor.py），无需前缀间接层。
    "script_edit_error": "script_edit_error",
    "script_edit_items_not_list": "script_edit_items_not_list",
    "script_edit_unit_lists_invalid": "script_edit_unit_lists_invalid",
    "script_edit_generated_assets_invalid": "script_edit_generated_assets_invalid",
    # 级联失败（TaskRepository._cascade_failed_queued）：reason 嵌套存储被级联依赖任务自身
    # 的失败原因（可能又是一个结构化编码串），渲染时递归展开，见 render_failure。
    "cascade_blocked_dependency": "task_fail_cascade_blocked_dependency",
}

# A structured reason is ``[code]`` optionally followed by a single space and a
# JSON object of params. Anchored at both ends so legacy ``[restart_lost] 中文``
# (non-JSON tail) and arbitrary exception text never match. DOTALL keeps the JSON
# group matching even if a param value contains an escaped newline.
_STRUCTURED_RE = re.compile(r"^\[(\w+)\](?:[ ](\{.*\}))?$", re.DOTALL)


def encode_failure(code: str, /, **params: Any) -> str:
    """Encode a known failure code (+ params) into the stored machine string.

    ``[code]`` when there are no params, otherwise ``[code] {sorted-json}``.
    Raises ``KeyError`` for codes not declared in :data:`FAILURE_CODE_KEYS`, so a
    typo fails fast at the call site instead of silently storing an unrenderable
    reason.
    """
    if code not in FAILURE_CODE_KEYS:
        raise KeyError(f"unknown failure code: {code!r}")
    if params:
        return f"[{code}] {json.dumps(params, ensure_ascii=False, sort_keys=True)}"
    return f"[{code}]"


def bound_reason(reason: str, limit: int) -> str:
    """把 ``reason`` 裁剪到 ``limit`` 字符内，供级联失败编码前调用。

    直接按字符截断合法的 ``[code] {params}`` 结构化串会切断 JSON 尾部，使
    :func:`render_failure` 无法解析、原文未翻译地泄露给界面（如 ``resume_expired_detail``
    的 ``detail`` 参数源自远端错误响应，可能长达上千字符）。这里优先裁剪结构化串里最长的
    字符串参数，保持重新编码后仍是合法的 ``[code] {params}``；非结构化文本或裁剪后仍超限
    时退回按原始字符裁剪。
    """
    if len(reason) <= limit:
        return reason
    match = _STRUCTURED_RE.match(reason)
    if match is None:
        return reason[:limit]
    code = match.group(1)
    if code not in FAILURE_CODE_KEYS:
        return reason[:limit]
    raw_params = match.group(2)
    if not raw_params:
        return reason[:limit]
    try:
        parsed = json.loads(raw_params)
    except ValueError:
        return reason[:limit]
    if not isinstance(parsed, dict):
        return reason[:limit]
    params: dict[str, Any] = parsed
    string_keys = [k for k, v in params.items() if isinstance(v, str)]
    if not string_keys:
        return reason[:limit]

    encoded = encode_failure(code, **params)
    while len(encoded) > limit:
        longest_key = max(string_keys, key=lambda k: len(params[k]))
        deficit = len(encoded) - limit
        if len(params[longest_key]) <= deficit:
            return reason[:limit]
        params[longest_key] = params[longest_key][: len(params[longest_key]) - deficit]
        encoded = encode_failure(code, **params)
    return encoded


def render_failure(error_message: str | None, translate: Callable[..., str]) -> str | None:
    """Render a stored failure reason for display via the request Translator.

    Recognised ``[code]`` / ``[code] {params}`` strings render to localized text;
    everything else (raw exception text, legacy rows, malformed payloads) passes
    through unchanged.
    """
    if not error_message:
        return error_message
    match = _STRUCTURED_RE.match(error_message)
    if match is None:
        return error_message
    code = match.group(1)
    key = FAILURE_CODE_KEYS.get(code)
    if key is None:
        return error_message
    raw_params = match.group(2)
    params: dict[str, Any] = {}
    if raw_params:
        try:
            parsed = json.loads(raw_params)
        except ValueError:
            return error_message
        if not isinstance(parsed, dict):
            return error_message
        params = parsed
    if code == "cascade_blocked_dependency":
        nested_reason = params.get("reason")
        if isinstance(nested_reason, str):
            params = {**params, "reason": render_failure(nested_reason, translate)}
    return translate(key, **params)
