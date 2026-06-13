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
    return translate(key, **params)
