"""Shared Vidu (Shengshu) utilities.

Reused by image_backends / video_backends / providers routers. Includes:
- VIDU_BASE_URL: Vidu API base URL.
- resolve_vidu_api_key: API-key resolution, raising when absent without environment fallback.
- create_vidu_client: destination-validated httpx.AsyncClient factory.
- image_to_data_uri: local images → base64 data URIs, within 20MB body limits.
- fetch_vidu_task / is_vidu_done / vidu_failure_reason / extract_vidu_url: polling helpers.
- VIDU_RETRYABLE_ERRORS: transient HTTPX NetworkError / TimeoutException only.
- VIDU_MAX_BODY_BYTES / assert_vidu_body_size: 20MB request-body validation.
- safe_body_for_log: allowlisted log views excluding base64 images/unexpected fields.
- test_vidu_connection: fork-only connection tests extracted from providers routers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.data_uri import image_to_data_uri as _image_to_data_uri
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.transport_layer import TransportLayer
from lib.infra.retry import BASE_RETRYABLE_ERRORS

logger = logging.getLogger(__name__)

VIDU_BASE_URL = "https://api.vidu.cn/ent/v2"

# Retry transient networking only: HTTPError includes HTTPStatusError and would keep retrying 401/404 business
# failures until timeout, wasting API quota. Use NetworkError + TimeoutException only.
VIDU_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    *BASE_RETRYABLE_ERRORS,
    httpx.NetworkError,
    httpx.TimeoutException,
)

# Vidu caps bodies at 20MB; base64 adds approximately 33%, so reserve 2MB for non-image fields.
VIDU_MAX_BODY_BYTES = 18 * 1024 * 1024

_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def resolve_vidu_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Vidu API Key")
    return api_key.strip()


def create_vidu_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
    transport_layer: TransportLayer | None = None,
) -> httpx.AsyncClient:
    """Create destination-validated ``httpx.AsyncClient`` with Authorization headers."""
    token = resolve_vidu_api_key(api_key)
    return artifact_http_client(
        base_url=base_url or VIDU_BASE_URL,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
        transport_layer=transport_layer,
    )


def image_to_data_uri(image_path: Path) -> str:
    """Local image → base64 data URI; Vidu accepts URLs/data URIs, the latter avoiding file services."""
    return _image_to_data_uri(image_path, _IMAGE_MIME_TYPES)


# Allowlisted log fields only, excluding potential sensitive unexpected body fields flagged by CodeQL.
_SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {"model", "duration", "resolution", "aspect_ratio", "seed", "audio", "off_peak"}
)


def safe_body_for_log(body: dict) -> dict:
    """Safe log views: allowlisted fields, truncated prompts, images/sounds counts only."""
    view: dict = {k: body[k] for k in _SAFE_LOG_KEYS if k in body}
    if "prompt" in body:
        prompt = body["prompt"] or ""
        view["prompt"] = prompt[:120] + ("…" if len(prompt) > 120 else "")
    for key in ("images", "sounds"):
        items = body.get(key) or []
        if items:
            view[key] = f"<{len(items)} data uri>"
    return view


def assert_vidu_body_size(body: dict) -> None:
    """Check Vidu 20MB hard request limits after serialization, refusing early with 2MB headroom."""
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(encoded) > VIDU_MAX_BODY_BYTES:
        raise ValueError(
            f"Vidu 请求体大小 {len(encoded) / 1024 / 1024:.1f}MB 超过上限 "
            f"{VIDU_MAX_BODY_BYTES / 1024 / 1024:.0f}MB（base64 编码自带 ~33% 膨胀，"
            f"请减少参考图数量或压缩图片后重试）。"
        )


# Terminal-state enum.
_DONE_STATE = "success"
_FAILED_STATE = "failed"


async def fetch_vidu_task(client: httpx.AsyncClient, task_id: str) -> dict:
    """Query Vidu task states with GET /tasks/{id}/creations."""
    resp = await client.get(f"/tasks/{task_id}/creations")
    raise_for_status_redacted(resp)
    return resp.json()


def is_vidu_done(payload: dict) -> bool:
    return payload.get("state") == _DONE_STATE


def vidu_failure_reason(payload: dict) -> str | None:
    if payload.get("state") == _FAILED_STATE:
        err = payload.get("err_code") or "unknown"
        return f"Vidu 任务失败: err_code={err}"
    return None


def extract_vidu_url(payload: dict) -> str:
    """Extract generated URLs from success responses."""
    creations = payload.get("creations") or []
    if not creations:
        raise RuntimeError(f"Vidu 任务无 creations: task_id={payload.get('id')}")
    url = creations[0].get("url")
    if not url:
        raise RuntimeError(f"Vidu creations[0].url 为空: task_id={payload.get('id')}")
    return url


# ---------------------------------------------------------------------------
# Fork-only connection test, formerly server/routers/providers.py, extracted to reduce router coupling.
# ---------------------------------------------------------------------------
def test_vidu_connection(config: dict[str, str]) -> None:
    """Validate Vidu keys by querying nonexistent task ids.

    Vidu has no list-models endpoint, so use ``GET /tasks/<bogus>/creations`` for authentication.
    Servers parse task ids as integers: use numeric strings. Nonnumeric ids produce 400 CODEC parse
    errors before authentication, making connection tests always fail.

    Allowlist outcomes: valid credentials return 404 missing-task; 401/403 mean invalid credentials.
    All other statuses, including 5xx, are indeterminate and raise, never falsely passing.
    """
    api_key = resolve_vidu_api_key(config.get("api_key"))
    base_url = (config.get("base_url") or VIDU_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Token {api_key}"}
    # Use a syntactically valid numeric id extremely unlikely to exist, avoiding 400 CODEC integer parse errors.
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{base_url}/tasks/0/creations", headers=headers)
    if resp.status_code == 404:
        return
    if resp.status_code in (401, 403):
        raise RuntimeError(f"HTTP {resp.status_code}: 凭证无效")
    raise RuntimeError(f"HTTP {resp.status_code}: 连接测试无法判定（{resp.text[:200]}）")
