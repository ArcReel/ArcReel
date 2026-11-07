"""Shared MiniMax (Hailuo) utilities.

Reused by text_backends / image_backends / video_backends / config / connection tests. MiniMax uses
OpenAI-compatible single `/v1` bases shared by text/native video, without DashScope-style dual-base derivation:
- MINIMAX_BASE_URL: default mainland base including `/v1`.
- MINIMAX_INTL_BASE_URL: international base for configuration overrides.
- resolve_minimax_api_key: raise for missing Bearer keys, without environment fallback.
- minimax_text_base_url / minimax_video_base_url: normalize {host}/v1, accepting hosts or `/v1` suffixes.
- minimax_video_v2_base_url: multimodal H3 {host}/v2.
- minimax_headers: Bearer headers.
- extract_image_url / extract_image_base64: first images from single-step image_generation.
- minimax_failure_reason: image base_resp.status_code nonzero errors.
- safe_body_for_log: allowlisted views stripping base64/URL/prompt.
- Two-step video URLs: submit→poll query status=Success for file_id→files/retrieve download_url.
- v2 single-step video URLs: submit→poll query task.status=succeeded for task.content.url directly.
"""

from __future__ import annotations

from pathlib import Path

from lib.backends.data_uri import image_to_data_uri as _image_to_data_uri

# Default mainland base including /v1; international configurations override base_url with MINIMAX_INTL_BASE_URL.
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_INTL_BASE_URL = "https://api.minimax.io/v1"

# Strip the single known suffix during host normalization to accept complete configured bases.
_V1_SUFFIX = "/v1"
# H3 v2 video uses the v1 host with different prefixes; see minimax_video_v2_base_url.
_V2_SUFFIX = "/v2"

# Async video terminal states Success/Fail; Preparing/Queueing/Processing keep polling.
MINIMAX_STATUS_SUCCESS = "Success"
MINIMAX_STATUS_FAIL = "Fail"
_TERMINAL_VIDEO_STATES = frozenset({MINIMAX_STATUS_SUCCESS, MINIMAX_STATUS_FAIL})

# v2 differs completely: lowercase statuses plus cancellation, direct success download URLs without file_id
# retrieval. Keep both state machines independent, never add branches inside v1 decisions.
MINIMAX_V2_STATUS_SUCCEEDED = "succeeded"
MINIMAX_V2_STATUS_FAILED = "failed"
MINIMAX_V2_STATUS_CANCELLED = "cancelled"
_TERMINAL_V2_VIDEO_STATES = frozenset(
    {MINIMAX_V2_STATUS_SUCCEEDED, MINIMAX_V2_STATUS_FAILED, MINIMAX_V2_STATUS_CANCELLED}
)

# Poll every 10s, balancing latency/request counts for Hailuo generations lasting tens of seconds to minutes.
MINIMAX_VIDEO_POLL_INTERVAL_SECONDS = 10.0

# Image extension → MIME for first_frame_image data URIs. Shared across video models, unioning accepted formats;
# heic/heif are H3-only (sources:
# https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md）。
_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


def resolve_minimax_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 MiniMax API Key")
    return api_key.strip()


def _minimax_host(configured: str | None) -> str:
    """Extract hosts from configured base_url by stripping `/v1`, defaulting to mainland hosts."""
    # Strip before emptiness checks: whitespace-only strings are truthy and bypass or, so fall back after stripping,
    # otherwise derive invalid relative URLs such as "/v1" from empty bases.
    base = ((configured or "").strip() or MINIMAX_BASE_URL).rstrip("/")
    if base.endswith(_V1_SUFFIX):
        return base[: -len(_V1_SUFFIX)]
    return base


def minimax_text_base_url(configured: str | None = None) -> str:
    """OpenAI-compatible text base: {host}/v1."""
    return f"{_minimax_host(configured)}{_V1_SUFFIX}"


def minimax_video_base_url(configured: str | None = None) -> str:
    """Native video base: {host}/v1, shared with text and named separately only by purpose."""
    return f"{_minimax_host(configured)}{_V1_SUFFIX}"


def minimax_video_v2_base_url(configured: str | None = None) -> str:
    """Multimodal video base: {host}/v2, same host as /v1, for H3 content[] endpoints."""
    return f"{_minimax_host(configured)}{_V2_SUFFIX}"


def minimax_headers(api_key: str) -> dict[str, str]:
    """Bearer authentication headers."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _as_dict(value: object) -> dict:
    """Normalize values to dicts; nondicts including malformed upstream None/list/str become empty dicts.

    Prevent .get AttributeError on truthy nondicts returned by relays/error responses.
    """
    return value if isinstance(value, dict) else {}


def image_to_data_uri(image_path: Path) -> str:
    """Local image → base64 data URI; first_frame_image accepts URLs/data URIs."""
    return _image_to_data_uri(image_path, _IMAGE_MIME_TYPES)


# Single-step image_generation response helpers.


def extract_image_url(payload: object) -> str | None:
    """First data.image_urls URL from image_generation response_format=url, valid 24h.

    None for unavailable URLs: nondict roots/data, missing/nonlist fields, or all-empty entries.
    Callers fall back to base64 or raise.
    """
    urls = _as_dict(_as_dict(payload).get("data")).get("image_urls")
    if isinstance(urls, list):
        for url in urls:
            if isinstance(url, str) and url:
                return url
    return None


def extract_image_base64(payload: object) -> str | None:
    """First data.image_base64 value from image_generation response_format=base64.

    None for unavailable data such as nondict roots/data or missing fields; callers raise.
    """
    items = _as_dict(_as_dict(payload).get("data")).get("image_base64")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str) and item:
                return item
    return None


def minimax_failure_reason(payload: object) -> str | None:
    """Error descriptions for nonzero base_resp.status_code; None for success 0 or missing base_resp.

    MiniMax business errors use HTTP 200 with nonzero base_resp.status_code. Authentication failures may
    instead use 4xx, handled by submit_post/raise_for_status. Check base_resp before extracting synchronous images.
    """
    base = _as_dict(_as_dict(payload).get("base_resp"))
    status = base.get("status_code")
    if status is not None and status != 0:
        msg = base.get("status_msg") or ""
        return f"MiniMax 图像生成失败 status_code={status}: {msg}".strip()
    return None


# Log redaction.

# Allowlisted scalars only; prompt lengths and subject_reference counts,
# never base64/URLs, matching CodeQL clear-text-logging restrictions.
_SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {"model", "aspect_ratio", "width", "height", "response_format", "n", "prompt_optimizer", "seed"}
)


def safe_body_for_log(body: dict) -> dict:
    """Safe log views: allowlisted scalars, prompt lengths, subject_reference counts only.

    subject_reference contains image base64/URLs and prompts contain long text; never expand either.
    """
    view: dict = {key: body[key] for key in _SAFE_LOG_KEYS if key in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt_len"] = len(prompt)
    refs = body.get("subject_reference")
    if isinstance(refs, list) and refs:
        view["subject_reference"] = f"<{len(refs)} ref>"
    return view


# Two-step video URL state-machine helpers.


def _base_resp_error(payload: dict) -> str | None:
    """Error descriptions for ``base_resp.status_code != 0``; None for zero/absence.

    All MiniMax responses carry ``base_resp`` (``{status_code, status_msg}``), zero indicating success.
    Submission/query/retrieval endpoint failures such as authentication or invalid task_id surface here.
    """
    base = _as_dict(payload.get("base_resp"))
    code = base.get("status_code")
    if code is not None and code != 0:
        return f"MiniMax base_resp status_code={code}: {base.get('status_msg', '')}".strip()
    return None


def extract_minimax_video_task_id(submit_payload: dict) -> str:
    """Extract top-level submission task_id; if absent, raise base_resp errors or unchanged failures."""
    task_id = submit_payload.get("task_id")
    if not task_id:
        reason = _base_resp_error(submit_payload)
        raise RuntimeError(reason or f"MiniMax 视频提交响应缺少 task_id: {submit_payload}")
    return str(task_id)


def minimax_video_status(payload: dict) -> str | None:
    """Query-response status: Preparing/Queueing/Processing/Success/Fail."""
    return payload.get("status")


def is_minimax_video_terminal(payload: dict) -> bool:
    """Success / Fail stop polling; others, including unknown/empty, continue."""
    return minimax_video_status(payload) in _TERMINAL_VIDEO_STATES


def minimax_video_failure_reason(payload: dict) -> str | None:
    """Error descriptions for status=Fail or hard top-level query base_resp errors, otherwise None.

    status=Success and intermediate states return None; is_terminal keeps polling unfinished intermediates.
    """
    if minimax_video_status(payload) == MINIMAX_STATUS_FAIL:
        base = _as_dict(payload.get("base_resp"))
        return (
            f"MiniMax 视频任务失败 task_id={payload.get('task_id')} "
            f"status_code={base.get('status_code')}: {base.get('status_msg', '')}"
        ).strip()
    # Hard top-level base_resp errors, such as missing task_id/authentication failure: nonzero always fails.
    return _base_resp_error(payload)


def extract_minimax_file_id(query_payload: dict) -> str:
    """Extract file_id from status=Success query responses."""
    file_id = query_payload.get("file_id")
    if not file_id:
        raise RuntimeError(f"MiniMax 视频任务完成但缺少 file_id: {query_payload}")
    return str(file_id)


def extract_minimax_download_url(retrieve_payload: dict) -> str:
    """Extract file.download_url from files/retrieve responses."""
    url = _as_dict(retrieve_payload.get("file")).get("download_url")
    if not url:
        reason = _base_resp_error(retrieve_payload)
        raise RuntimeError(reason or f"MiniMax files/retrieve 响应缺少 download_url: {retrieve_payload}")
    return url


# v2 video state-machine helpers, single-step URLs.


def minimax_v2_video_status(payload: dict) -> str | None:
    """Query task.status: queued/running/succeeded/failed/cancelled."""
    return _as_dict(payload.get("task")).get("status")


def is_minimax_v2_video_terminal(payload: dict) -> bool:
    """succeeded / failed / cancelled are terminal; queued / running and unknown states continue polling."""
    return minimax_v2_video_status(payload) in _TERMINAL_V2_VIDEO_STATES


def minimax_v2_video_failure_reason(payload: dict) -> str | None:
    """Error descriptions for failed/cancelled status or hard top-level base_resp errors, otherwise None.

    Treat cancellation as failure too: tasks produce no further video and polling would only time out.
    """
    status = minimax_v2_video_status(payload)
    if status in (MINIMAX_V2_STATUS_FAILED, MINIMAX_V2_STATUS_CANCELLED):
        task = _as_dict(payload.get("task"))
        detail = task.get("error") or task.get("status_msg") or ""
        return f"MiniMax 视频任务{status} task_id={task.get('id')}: {detail}".strip()
    return _base_resp_error(payload)


def extract_minimax_v2_download_url(query_payload: dict) -> str:
    """Extract temporary task.content.url downloads from status=succeeded query responses."""
    url = _as_dict(_as_dict(query_payload.get("task")).get("content")).get("url")
    if not url:
        raise RuntimeError(f"MiniMax 视频任务完成但缺少 content.url: {query_payload}")
    return str(url)
