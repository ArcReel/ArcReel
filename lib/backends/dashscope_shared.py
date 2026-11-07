"""Shared Alibaba Bailian DashScope (Model Studio) utilities.

Reused by image_backends / video_backends / text_backends factories / custom_provider / config.
Includes:
- DASHSCOPE_BASE_URL: host without suffixes, Beijing starting region.
- resolve_dashscope_api_key: raise for missing keys without environment fallback.
- dashscope_text_base_url / dashscope_native_base_url: derive text /compatible-mode/v1 and native
  image/video /api/v1 bases from hosts, accepting configured suffixes or bare hosts.
- dashscope_headers: Bearer headers, asynchronous video adds X-DashScope-Async: enable.
- Async video helpers: states/failure reasons/task_id/video_url/billed durations.
- extract_image_url: synchronous multimodal-choices image URLs.
- safe_body_for_log: allowlisted logs excluding base64 images/long prompts.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from lib.backends.data_uri import image_to_data_uri as _image_to_data_uri
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS

logger = logging.getLogger(__name__)

# Host scheme://host without /api/v1 or /compatible-mode/v1; derive both bases from it.
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"

# Retry by response statuses, never exception strings: HTTPStatusError str() contains URLs/task_ids whose
# 503/timeout substrings could misclassify 4xx business failures. DashScope backends use
# lib.backends.backend_runtime status predicates: nonidempotent POST create/submit uses should_retry_submit,
# 4xx fail fast, 5xx/429 retry, ambiguous transport becomes nonretryable AmbiguousSubmitError through submit_post.
# Idempotent polling GET uses should_retry_poll, treating 404 as not ready; issued result URLs use should_retry_download,
# failing fast for every 4xx including 404. Explicit response.status_code gates preserve status semantics,
# letting central request handling recognize 413 for lower-tier fallback.

# Asynchronous image/video task states.
DASHSCOPE_STATUS_PENDING = "PENDING"
DASHSCOPE_STATUS_RUNNING = "RUNNING"
DASHSCOPE_STATUS_SUCCEEDED = "SUCCEEDED"
DASHSCOPE_STATUS_FAILED = "FAILED"
DASHSCOPE_STATUS_CANCELED = "CANCELED"
# Queries after task_id 24-hour expiry return UNKNOWN, identifying expired resume.
DASHSCOPE_STATUS_UNKNOWN = "UNKNOWN"

_TERMINAL_STATES = frozenset(
    {
        DASHSCOPE_STATUS_SUCCEEDED,
        DASHSCOPE_STATUS_FAILED,
        DASHSCOPE_STATUS_CANCELED,
        DASHSCOPE_STATUS_UNKNOWN,
    }
)
_FAILURE_STATES = frozenset({DASHSCOPE_STATUS_FAILED, DASHSCOPE_STATUS_CANCELED})

_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# Bailian recommends 15-second polling; task_id / video_url both expire after 24h.
DASHSCOPE_POLL_INTERVAL_SECONDS = 15.0

# Strip known suffixes to derive hosts from full bases, including region changes.
_KNOWN_SUFFIXES = ("/compatible-mode/v1", "/api/v1")


def resolve_dashscope_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 DashScope API Key")
    return api_key.strip()


def _dashscope_host(configured: str | None) -> str:
    """Extract hosts by stripping known suffixes from configured base_url, defaulting to Beijing."""
    # Strip before emptiness checks: whitespace-only strings are truthy and bypass or, so fall back after stripping,
    # otherwise derive invalid relative URLs such as "/api/v1" from empty bases.
    base = ((configured or "").strip() or DASHSCOPE_BASE_URL).rstrip("/")
    for suffix in _KNOWN_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def dashscope_text_base_url(configured: str | None = None) -> str:
    """OpenAI-compatible text base: {host}/compatible-mode/v1."""
    return f"{_dashscope_host(configured)}/compatible-mode/v1"


def dashscope_native_base_url(configured: str | None = None) -> str:
    """Native image/video base: {host}/api/v1."""
    return f"{_dashscope_host(configured)}/api/v1"


def dashscope_headers(api_key: str, *, async_mode: bool = False) -> dict[str, str]:
    """Bearer headers; video/asynchronous images require X-DashScope-Async: enable, synchronous images omit it."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if async_mode:
        headers["X-DashScope-Async"] = "enable"
    return headers


def image_to_data_uri(image_path: Path) -> str:
    """Local image → base64 data URI; Bailian media/image accepts URLs/data URIs."""
    return _image_to_data_uri(image_path, _IMAGE_MIME_TYPES)


# Asynchronous video task helpers.


def _as_dict(value: object) -> dict:
    """Normalize values to dicts; every nondict, including malformed upstream None/list/str, becomes empty.

    DashScope specifies object output/usage, but relays/errors may return truthy nondicts. Centralize
    fallback here to prevent .get AttributeError.
    """
    return value if isinstance(value, dict) else {}


def _task_status(payload: dict) -> str | None:
    return _as_dict(payload.get("output")).get("task_status")


def is_dashscope_succeeded(payload: dict) -> bool:
    return _task_status(payload) == DASHSCOPE_STATUS_SUCCEEDED


def is_dashscope_terminal(payload: dict) -> bool:
    """SUCCEEDED / FAILED / CANCELED / UNKNOWN are terminal and stop polling."""
    return _task_status(payload) in _TERMINAL_STATES


def is_dashscope_expired(payload: dict) -> bool:
    """Expired 24-hour task_id queries return UNKNOWN, causing ResumeExpiredError on resume."""
    return _task_status(payload) == DASHSCOPE_STATUS_UNKNOWN


def dashscope_failure_reason(payload: dict) -> str | None:
    """Error descriptions for FAILED/CANCELED; UNKNOWN is expiration, handled separately rather than failure.

    Also handle submission top-level ``{code, message, request_id}`` errors without output.
    """
    output = _as_dict(payload.get("output"))
    status = output.get("task_status")
    if status in _FAILURE_STATES:
        code = output.get("code") or "unknown"
        message = output.get("message") or ""
        return f"DashScope 任务失败 status={status} code={code}: {message}".strip()
    # Submission top-level errors such as InvalidApiKey lack output.task_status.
    if status is None and payload.get("code"):
        return f"DashScope 提交失败 code={payload.get('code')}: {payload.get('message', '')}".strip()
    return None


def extract_task_id(submit_payload: dict) -> str:
    """Extract output.task_id from submission responses."""
    task_id = _as_dict(submit_payload.get("output")).get("task_id")
    if not task_id:
        reason = dashscope_failure_reason(submit_payload)
        raise RuntimeError(reason or f"DashScope 提交响应缺少 task_id: {submit_payload}")
    return task_id


def extract_video_url(payload: dict) -> str:
    """Extract output.video_url from SUCCEEDED polling responses."""
    url = _as_dict(payload.get("output")).get("video_url")
    if not url:
        raise RuntimeError(f"DashScope 任务完成但缺少 video_url: {payload}")
    return url


def extract_billing_duration(payload: dict) -> int | None:
    """Read actual usage.duration, including input-video duration for wan2.7-r2v.

    Accept int/float/numeric strings, rounding half-up (4.5→5), never truncating or bankers rounding,
    to avoid undercounting billed seconds. Nonpositive/unparseable values and values above 24h reasonable
    limits return None, preventing DB Integer overflow. Callers fall back to requested duration, never zero bills.
    """
    raw = _as_dict(payload.get("usage")).get("duration")
    if raw is None:
        return None
    try:
        decimal_value = Decimal(str(raw))
        # Check raw values before rounding: 86400.4 exceeds 24h and cannot be accepted by rounding back within limits.
        # NaN comparisons raise InvalidOperation, returning None like parse failures.
        if not 0 < decimal_value <= MAX_BILLED_DURATION_SECONDS:
            return None
        value = int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value > 0 else None


# Synchronous image-response helpers.


def extract_image_url(payload: dict) -> str:
    """First output.choices[0].message.content[*].image URL from synchronous image responses."""
    choices = _as_dict(payload.get("output")).get("choices")
    if not isinstance(choices, list) or not choices:
        reason = dashscope_failure_reason(payload)
        raise RuntimeError(reason or f"DashScope 图像响应缺少 choices: {payload}")
    # Normalize malformed choices[0]/message nondicts to empty, preventing .get AttributeError.
    content = _as_dict(_as_dict(choices[0]).get("message")).get("content")
    # Require lists explicitly: truthy nonlist content, such as int/bool/dict, bypasses `or []`,
    # and direct iteration raises TypeError; isinstance guards route to RuntimeError below.
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and (url := item.get("image")):
                return url
    raise RuntimeError(f"DashScope 图像响应 content 无 image 字段: {payload}")


# Synchronous audio-response helpers.


def extract_audio_url(payload: dict) -> str:
    """Synchronous synthesis output.audio.url, wav with 24-hour validity."""
    url = _as_dict(_as_dict(payload.get("output")).get("audio")).get("url")
    if not url:
        reason = dashscope_failure_reason(payload)
        raise RuntimeError(reason or f"DashScope 语音合成响应缺少 audio.url: {payload}")
    return url


# Log redaction.

# Allowlisted scalars only, never base64 images/media URLs.
_SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {"model", "size", "resolution", "ratio", "duration", "n", "watermark", "prompt_extend", "seed"}
)


def safe_body_for_log(body: dict) -> dict:
    """Safe log views: allowlisted scalars, truncated prompts, media/messages counts only.

    input.messages images and input.media video contain embedded base64/URLs. Never expand them into
    logs, aligning with CodeQL clear-text-logging restrictions.
    """
    # Use _as_dict rather than `or {}`: truthy nondict parameters/input lists/strings bypass `or {}`,
    # causing .get / in AttributeError/TypeError and masking original failures in supposedly fail-safe logs.
    params = _as_dict(body.get("parameters"))
    view: dict = {"model": body.get("model")}
    for key in _SAFE_LOG_KEYS:
        if key in params:
            view[key] = params[key]

    inp = _as_dict(body.get("input"))
    prompt = inp.get("prompt")
    if isinstance(prompt, str):
        view["prompt"] = prompt[:120] + ("…" if len(prompt) > 120 else "")

    media = inp.get("media")
    if isinstance(media, list) and media:
        view["media"] = f"<{len(media)} item>"

    messages = inp.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        content = messages[0].get("content")
        if isinstance(content, list):
            images = sum(1 for c in content if isinstance(c, dict) and "image" in c)
            texts = sum(1 for c in content if isinstance(c, dict) and "text" in c)
            view["content"] = f"<{images} image, {texts} text>"
    return view
