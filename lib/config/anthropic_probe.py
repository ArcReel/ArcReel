"""Anthropic-compatible connectivity probes/diagnosis.

Probe actual Agent ``POST {base_url}/v1/messages`` only, matching runtime; model discovery remains
separate best effort, outside diagnostics.

Direct httpx, not Claude SDK subprocesses:
- SDK cold starts 6s+, timeout 30s, no stderr HTTP status, poor diagnostic precision.
- httpx exposes exact status/upstream bodies, faster and more reliable.

Log URLs/status only, never bodies/headers/api_key.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

import httpx

from lib.agent.agent_provider_catalog import CUSTOM_SENTINEL_ID, get_preset
from lib.config.url_utils import (
    ANTHROPIC_MESSAGES_PATH,
    anthropic_endpoint_url,
    validate_anthropic_base_url,
)
from lib.infra.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

_ERR_TRUNCATE = 200

# Timeouts/network failures share status_code=None; prefixed errors mean reachable but
# slow responses, distinguished from unreachable. Only ReadTimeout after connection/request delivery
# proves reachability; ConnectTimeout/WriteTimeout/PoolTimeout
# follow ordinary network-error classification.
_TIMEOUT_ERROR_PREFIX = "timeout: "

# Messages probe cap: reasoning doubao-seed Volcengine Agent Plan models
# often take 8–10s for first tokens, requiring margin to avoid false unreachable diagnoses.
_MESSAGES_PROBE_TIMEOUT_S = 30.0

# 400 missing-parameter wording near model means absent model, not unrecognized names.
# Permit intervening characters for missing parameter model, but bound proximity,
# preventing unrelated missing max_tokens errors from implicating model.
_MISSING_MODEL_RE = re.compile(r"(missing|required|empty).{0,16}model|model.{0,16}(missing|required|is empty)")


def anthropic_auth_headers(api_key: str, *, bearer: bool = False) -> dict[str, str]:
    """Anthropic headers; ``bearer=True`` for Authorization-only gateways such as Volcengine Ark."""
    auth = {"Authorization": f"Bearer {api_key}"} if bearer else {"x-api-key": api_key}
    return {**auth, "anthropic-version": "2023-06-01"}


class DiagnosisCode(StrEnum):
    OPENAI_COMPAT_ONLY = "openai_compat_only"
    AUTH_FAILED = "auth_failed"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_REQUIRED = "model_required"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    success: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None  # Truncate to 200 characters.


async def _post(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
    http_client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    """POST indirection, using shared clients without ``http_client``."""
    client = http_client or get_http_client()
    return await client.post(url, headers=headers, json=payload, timeout=timeout_s)


def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    return s if len(s) <= _ERR_TRUNCATE else s[:_ERR_TRUNCATE] + "…"


async def probe_messages(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_s: float = _MESSAGES_PROBE_TIMEOUT_S,
    http_client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    """Minimal POST {base_url}/v1/messages, max_tokens=1.

    Classification:
    - 2xx JSON type=message → success.
    - 2xx non-Anthropic JSON → failure, subsequently OPENAI_COMPAT_ONLY.
    - Non-2xx → failure, upstream bodies truncated to 200 characters in error.
    - Network/timeout → failure, status_code=None.
    """
    url = anthropic_endpoint_url(base_url, ANTHROPIC_MESSAGES_PATH)
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {**anthropic_auth_headers(api_key), "content-type": "application/json"}
    started = time.perf_counter()
    try:
        resp = await _post(url=url, headers=headers, payload=payload, timeout_s=timeout_s, http_client=http_client)
    except httpx.ReadTimeout as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages timeout url=%s elapsed_ms=%d", url, elapsed)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=f"{_TIMEOUT_ERROR_PREFIX}{exc!s}")
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages network err url=%s elapsed_ms=%d", url, elapsed)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=_truncate(str(exc)))

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("probe_messages url=%s status=%d elapsed_ms=%d", url, resp.status_code, elapsed)

    if resp.status_code >= 400:
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error=_truncate(resp.text),
        )

    # 2xx: verify Anthropic JSON, detecting OpenAI-compatible impersonation.
    try:
        data = resp.json()
    except ValueError:
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error="non-anthropic response: not JSON",
        )
    if not isinstance(data, dict) or data.get("type") != "message":
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error="non-anthropic JSON: missing type=message",
        )
    return ProbeResult(success=True, status_code=resp.status_code, latency_ms=elapsed, error=None)


def classify_probe_failure(result: ProbeResult) -> DiagnosisCode:
    """Map failed ProbeResult into DiagnosisCode."""
    if result.success:
        return DiagnosisCode.UNKNOWN  # caller misuse
    err = (result.error or "").lower()
    code = result.status_code
    if code in (401, 403):
        return DiagnosisCode.AUTH_FAILED
    if code == 429:
        return DiagnosisCode.RATE_LIMITED
    # Heuristic 404 with model in bodies means missing models; changed upstream wording falls to UNKNOWN.
    if code == 404 and ("model" in err or "model_not_found" in err):
        return DiagnosisCode.MODEL_NOT_FOUND
    # 400 mentioning model means parameter-validation rejection. MissingParameter, as in Ark,
    # and unrecognized names require different user actions, distinguished by body keywords.
    if code == 400 and "model" in err:
        if _MISSING_MODEL_RE.search(err):
            return DiagnosisCode.MODEL_REQUIRED
        return DiagnosisCode.MODEL_NOT_FOUND
    if code is not None and 200 <= code < 300:
        # Failed 2xx probes mean protocol mismatch, compatible OpenAI impersonating Anthropic.
        return DiagnosisCode.OPENAI_COMPAT_ONLY
    if code is None:
        if (result.error or "").startswith(_TIMEOUT_ERROR_PREFIX):
            return DiagnosisCode.TIMEOUT
        return DiagnosisCode.NETWORK
    return DiagnosisCode.UNKNOWN


_DEFAULT_TEST_MODEL = "claude-3-5-sonnet-20241022"


@dataclass(frozen=True)
class SuggestionAction:
    kind: Literal["check_api_key", "run_discovery", "see_docs"]
    suggested_value: str | None = None


@dataclass(frozen=True)
class TestConnectionResponse:
    overall: Literal["ok", "fail"]
    messages_probe: ProbeResult
    diagnosis: DiagnosisCode | None
    suggestion: SuggestionAction | None
    messages_url: str
    """Complete URL actually requested this time; preview and request use the same string."""


async def run_test(
    *,
    preset_id: str | None,
    base_url: str | None,
    api_key: str,
    model: str | None,
    http_client: httpx.AsyncClient | None = None,
) -> TestConnectionResponse:
    """Validate base_url → probe actual Agent messages endpoints → diagnose.

    Raises:
        ValueError: Unknown presets, custom modes without base_url, or invalid addresses,
            including subclass ``InvalidAnthropicBaseUrlError``.
    """
    if preset_id and preset_id != CUSTOM_SENTINEL_ID:
        preset = get_preset(preset_id)
        if preset is None:
            raise ValueError(f"unknown preset: {preset_id!r}")
        # Overridden preset.messages_url, such as internal proxies, follows custom paths; otherwise catalog values.
        effective_base = validate_anthropic_base_url(base_url) if base_url else preset.messages_url
        effective_model = model or preset.default_model
    else:
        if not base_url:
            raise ValueError("base_url required for __custom__ mode")
        effective_base = validate_anthropic_base_url(base_url)
        effective_model = model or _DEFAULT_TEST_MODEL

    messages_url = anthropic_endpoint_url(effective_base, ANTHROPIC_MESSAGES_PATH)

    # Empty preset models would inevitably fail missing-model checks; short-circuit
    # into default-model guidance instead of generic upstream errors.
    if not effective_model:
        return TestConnectionResponse(
            overall="fail",
            messages_probe=ProbeResult(success=False, status_code=None, latency_ms=None, error=None),
            diagnosis=DiagnosisCode.MODEL_REQUIRED,
            suggestion=SuggestionAction(kind="run_discovery"),
            messages_url=messages_url,
        )

    msg = await probe_messages(
        base_url=effective_base,
        api_key=api_key,
        model=effective_model,
        http_client=http_client,
    )
    return TestConnectionResponse(
        overall="ok" if msg.success else "fail",
        messages_probe=msg,
        diagnosis=None if msg.success else classify_probe_failure(msg),
        suggestion=None,
        messages_url=messages_url,
    )
