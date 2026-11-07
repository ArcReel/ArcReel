"""Shared Kling tools: JWT HS256 authentication manager, base URLs, and async task response parsing.

Shared by image/video backends and connection tests. Official Kling uses JWT HS256 (access_key as
``iss``, secret_key signature, approximately 30-minute expiry); images/videos both use async submission → task_id polling → URL retrieval.

- ``KLING_BASE_URL``: official base including ``/v1``.
- ``KlingJWTManager``: cached HS256 tokens, **checking expiry on every access, resigning with <60s until expiry**
  because rendering may outlive one token, so never sign only once per instance. Injectable clocks avoid real wall time in tests.
- ``kling_bearer_headers``: static api_key headers bypassing JWT in bearer mode.
- Async parsing: ``code`` envelopes, ``data.task_id``, ``data.task_status``
  (submitted/processing/succeed/failed), ``data.task_result.{videos,images}[].url``.
- New API (model names in paths, API Key only): ``KLING_MODEL_PATH_BASE_URL`` excludes ``/v1``; submission returns ``data.id``;
  ``GET /tasks?task_ids=`` returns ``status`` in ``data[]`` (submitted/processing/succeeded/failed),
  ``outputs[type=video].url``, and billing duration ``usage.output_duration``.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import jwt

from lib.config.url_utils import strip_base_url_version
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS

# Official base including /v1, matching ark_shared.ARK_BASE_URL conventions. Official domestic host moved from api.klingai.com
# to api-beijing.klingai.com; old host remains available without forced shutdown. Only new users without configured base_url
# are affected. registry.default_base_url shares this value; keep aligned as a single authority.
KLING_BASE_URL = "https://api-beijing.klingai.com/v1"
# New API hosts exclude versions; paths are directly /text-to-video/<模型名> and /tasks, per official Authentication docs.
KLING_MODEL_PATH_BASE_URL = "https://api-beijing.klingai.com"

# JWT lifetime/refresh policy.
_TOKEN_TTL_SECONDS = 1800  # Approximately 30-minute expiry, officially.
_TOKEN_NBF_SKEW_SECONDS = 5  # Slightly earlier nbf tolerates small client/server clock skew.
_TOKEN_REFRESH_MARGIN_SECONDS = 60  # Resign with <60s until expiry, avoiding expired tokens during long tasks.

# Async video terminals: succeed / failed; submitted / processing continue polling.
KLING_STATUS_SUCCEED = "succeed"
KLING_STATUS_FAILED = "failed"
_TERMINAL_STATES = frozenset({KLING_STATUS_SUCCEED, KLING_STATUS_FAILED})


class KlingJWTManager:
    """Kling JWT HS256 manager with on-demand resigning and injectable clocks.

    payload = ``{"iss": access_key, "exp": now+1800, "nbf": now-5}``, signed with secret_key.
    Cache tokens; ``token()`` checks every access, reusing only when >60s remain, otherwise resigning. HS256 encoding costs almost
    nothing; caching merely avoids repeated encoding. The essential feature is automatic renewal during >30-minute async renders.
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._clock = clock
        self._cached_token: str | None = None
        self._expires_at: float = 0.0

    def token(self) -> str:
        """Return valid tokens: reuse cached tokens with >60s remaining, otherwise resign on demand."""
        now = self._clock()
        if self._cached_token is not None and self._expires_at - now > _TOKEN_REFRESH_MARGIN_SECONDS:
            return self._cached_token
        return self._mint(now)

    def _mint(self, now: float) -> str:
        exp = int(now) + _TOKEN_TTL_SECONDS
        payload = {
            "iss": self._access_key,
            "exp": exp,
            "nbf": int(now) - _TOKEN_NBF_SKEW_SECONDS,
        }
        token = jwt.encode(
            payload,
            self._secret_key,
            algorithm="HS256",
            headers={"alg": "HS256", "typ": "JWT"},
        )
        self._cached_token = token
        self._expires_at = exp
        return token

    def auth_headers(self) -> dict[str, str]:
        """Authentication headers, checking expiry/resigning on every access."""
        return {
            "Authorization": f"Bearer {self.token()}",
            "Content-Type": "application/json",
        }


def kling_bearer_headers(api_key: str) -> dict[str, str]:
    """Bearer mode (custom endpoints): static api_key headers bypassing JWT management."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def resolve_kling_jwt_credentials(access_key: str | None, secret_key: str | None) -> tuple[str, str]:
    """Validate/normalize Kling JWT key pairs; raise if either missing, without environment fallback."""
    ak = (access_key or "").strip()
    sk = (secret_key or "").strip()
    if not ak or not sk:
        raise ValueError("请到系统配置页填写可灵 Kling 的 Access Key 与 Secret Key")
    return ak, sk


def resolve_kling_api_key(api_key: str | None) -> str:
    """Validate/normalize bearer static api_key; raise if missing.

    Shared by both bearer paths: custom endpoints (relay Bearer keys) and built-in provider single API Key mode,
    so error wording does not exclusively name either.
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("请填写可灵 Kling 的 API Key")
    return key


def kling_auth_mode(credentials: Mapping[str, str | None]) -> Literal["bearer", "jwt"]:
    """Select auth_mode from credentials: nonempty ``api_key`` prefers bearer, otherwise jwt (access_key + secret_key).

    Built-in construction (backend_assembly._build_kling) and connection tests (providers._test_kling) share
    this rule, preventing separately implemented api_key priority from drifting when only one site changes.

    Test emptiness after ``.strip()``: whitespace-only api_key, such as accidental paste, must not select bearer
    and silently swallow valid access_key/secret_key. resolve_kling_api_key validates whitespace later,
    so this prior decision must already align.
    """
    api_key = credentials.get("api_key")
    return "bearer" if api_key and api_key.strip() else "jwt"


def image_to_base64(image_path: Path) -> str:
    """Local images → bare base64 (Kling image/image_tail accept URLs or base64 without data URI prefixes)."""
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


# Async task response parsing: submit / poll / retrieve.


def _as_dict(value: object) -> dict:
    """Normalize values to dict; non-dicts (None/list/str and other anomalous upstream shapes) become empty dicts."""
    return value if isinstance(value, dict) else {}


def _as_str(value: object) -> str:
    """Normalize values to str; nonstrings, including explicit None, become empty strings, never literal 'None' from null."""
    return value if isinstance(value, str) else ""


def _code_error(scope: dict, message_key: str) -> str | None:
    """Build errors from ``scope`` ``code`` / ``scope[message_key]``; zero/missing ``code`` → None.

    Normalize ``code`` to int before comparison: bearer/relay endpoints may serialize strings (``"0"``)
    or floats. Direct ``code != 0`` would misclassify ``"0"``; unparseable codes remain errors exposing original values.
    """
    code = scope.get("code")
    if code is None:
        return None
    try:
        is_error = int(float(code)) != 0
    except (TypeError, ValueError, OverflowError):
        is_error = True
    if is_error:
        return f"Kling API code={code}: {_as_str(scope.get(message_key))}".strip()
    return None


def kling_response_error(payload: object) -> str | None:
    """``code != 0`` → error descriptions; zero/missing → None.

    All Kling responses contain top-level ``code`` / ``message``, zero meaning success. Surface submission/query API failures
    such as authentication/invalid parameters here (authentication may also use 4xx, handled by submit_post / raise_for_status).

    Some APIs, such as ``account/costs``, embed business ``code`` / ``msg`` in ``data``, separate from top-level
    envelopes and describing the business query itself (invalid parameters/missing packages). After top-level success,
    check this layer too; either nonzero means error. Submission/polling ``data`` (``task_id`` /
    ``task_status``, etc.) lacks ``code``, making this a no-op there.

    Normalize ``payload`` to dict: relays/anomalous gateways may return nonobject JSON arrays/strings,
    treated as empty dicts (no ``code`` means no error), avoiding ``.get()`` AttributeError.
    """
    payload = payload if isinstance(payload, dict) else {}
    return _code_error(payload, "message") or _code_error(_as_dict(payload.get("data")), "msg")


def extract_kling_task_id(submit_payload: dict) -> str:
    """Extract submission ``data.task_id``; if missing, raise the code error, or raise with the raw response."""
    task_id = _as_dict(submit_payload.get("data")).get("task_id")
    if not task_id:
        reason = kling_response_error(submit_payload)
        raise RuntimeError(reason or f"Kling 提交响应缺少 task_id: {submit_payload}")
    return str(task_id)


def kling_task_status(payload: dict) -> str | None:
    """Query response ``data.task_status`` (submitted/processing/succeed/failed)."""
    return _as_dict(payload.get("data")).get("task_status")


def is_kling_task_terminal(payload: dict) -> bool:
    """succeed / failed stop polling; everything else, including unknown/empty, continues."""
    return kling_task_status(payload) in _TERMINAL_STATES


def kling_task_failure_reason(payload: dict) -> str | None:
    """``task_status=failed`` or top-level ``code`` hard errors → descriptions; otherwise None.

    succeed/intermediate states return None; is_terminal keeps polling intermediates.
    """
    # Surface top-level code errors first, such as nonexistent task_id / authentication failure.
    err = kling_response_error(payload)
    if err is not None:
        return err
    if kling_task_status(payload) == KLING_STATUS_FAILED:
        data = _as_dict(payload.get("data"))
        return (f"Kling 任务失败 task_id={data.get('task_id')}: {_as_str(data.get('task_status_msg'))}").strip()
    return None


def _extract_task_result_urls(payload: dict, collection_key: str) -> list[str]:
    """Extract succeed ``data.task_result.{collection_key}[].url`` lists; empty for invalid shapes."""
    result = _as_dict(_as_dict(payload.get("data")).get("task_result"))
    items = result.get(collection_key)
    urls: list[str] = []
    if isinstance(items, list):
        for item in items:
            url = _as_dict(item).get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def extract_kling_video_url(payload: dict) -> str:
    """Extract succeed query ``data.task_result.videos[0].url``."""
    for url in _extract_task_result_urls(payload, "videos"):
        return url
    reason = kling_response_error(payload)
    raise RuntimeError(reason or f"Kling 视频任务完成但缺少视频 URL: {payload}")


def extract_kling_image_urls(payload: dict) -> list[str]:
    """Extract succeed query ``data.task_result.images[].url`` lists in image order.

    Async image tasks may produce multiple images (``n`` / sets); return all valid URLs for backends to persist the first as needed.
    Without any valid URL, raise the code error, or raise with the raw response, matching video URL fail-loud behavior.
    """
    urls = _extract_task_result_urls(payload, "images")
    if urls:
        return urls
    reason = kling_response_error(payload)
    raise RuntimeError(reason or f"Kling 图像任务完成但缺少图片 URL: {payload}")


# New API response parsing, with model names in paths.

# New API states: submitted / processing / succeeded / failed.
KLING_MODEL_PATH_SUCCEEDED = "succeeded"
_MODEL_PATH_TERMINAL_STATES = frozenset({KLING_MODEL_PATH_SUCCEEDED, KLING_STATUS_FAILED})


def kling_model_path_base_url(base_url: str | None) -> str:
    """New-API base_url: add scheme, remove trailing slashes and final version segments such as built-in default ``/v1``.

    Use :data:`KLING_MODEL_PATH_BASE_URL` if unconfigured. Version stripping follows path-override ``strip_version`` rules.
    """
    stripped = (base_url or "").strip().rstrip("/")
    if not stripped:
        return KLING_MODEL_PATH_BASE_URL
    if "://" not in stripped:
        stripped = f"https://{stripped}"
    return strip_base_url_version(stripped)


def kling_model_path_task_id(submit_payload: dict) -> str:
    """Extract new-API submission ``data.id``; if missing, raise the code error, or raise with the raw response."""
    task_id = _as_dict(submit_payload.get("data")).get("id")
    if not task_id:
        reason = kling_response_error(submit_payload)
        raise RuntimeError(reason or f"Kling 提交响应缺少任务 id: {submit_payload}")
    return str(task_id)


def kling_model_path_task(payload: dict) -> dict:
    """Task queried in ``GET /tasks?task_ids=<id>`` responses (``data`` array, one entry when querying one id)."""
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                return item
    return {}


def kling_model_path_status(payload: dict) -> str | None:
    status = kling_model_path_task(payload).get("status")
    return status if isinstance(status, str) else None


def is_kling_model_path_terminal(payload: dict) -> bool:
    """succeeded / failed are terminal; all else, including unknown/empty queries, continues polling."""
    return kling_model_path_status(payload) in _MODEL_PATH_TERMINAL_STATES


def kling_model_path_failure_reason(payload: dict) -> str | None:
    """Top-level ``code`` errors or ``status=failed`` → reasons from task ``message``; otherwise None."""
    err = kling_response_error(payload)
    if err is not None:
        return err
    task = kling_model_path_task(payload)
    if task.get("status") == KLING_STATUS_FAILED:
        return f"Kling 任务失败 task_id={_as_str(task.get('id'))}: {_as_str(task.get('message'))}".strip()
    return None


def extract_kling_model_path_video_url(payload: dict) -> str:
    """Extract first ``type=video`` ``url`` from succeeded ``outputs[]``, excluding watermarked variants."""
    outputs = kling_model_path_task(payload).get("outputs")
    if isinstance(outputs, list):
        for item in outputs:
            output = _as_dict(item)
            url = output.get("url")
            if output.get("type") == "video" and isinstance(url, str) and url:
                return url
    reason = kling_response_error(payload)
    raise RuntimeError(reason or f"Kling 视频任务完成但缺少视频 URL: {payload}")


def kling_model_path_output_duration(payload: dict) -> int | None:
    """``usage.output_duration``: official billed output seconds as strings, returned only after completion with usage.

    Half-up rounding; None for absent/nonpositive/unparseable/above-``MAX_BILLED_DURATION_SECONDS`` values, letting callers use request durations.
    """
    raw = _as_dict(kling_model_path_task(payload).get("usage")).get("output_duration")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
        if not 0 < value <= MAX_BILLED_DURATION_SECONDS:
            return None
        seconds = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None
