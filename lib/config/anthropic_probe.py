"""Anthropic 兼容端点的真实连通性体检 + 诊断分类。

本模块只用 httpx 直调，不通过 Claude SDK，避免子进程副作用。
日志严格只打 URL 与 status，不打 body / headers / api_key。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

_ERR_TRUNCATE = 200


class DiagnosisCode(StrEnum):
    MISSING_ANTHROPIC_SUFFIX = "missing_anthropic_suffix"
    OPENAI_COMPAT_ONLY = "openai_compat_only"
    AUTH_FAILED = "auth_failed"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    success: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None  # 截断到 200 字符


async def _post(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
) -> httpx.Response:
    """间接层：测试时 patch 这一个。"""
    client = get_http_client()
    return await client.post(url, headers=headers, json=payload, timeout=timeout_s)


def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    return s if len(s) <= _ERR_TRUNCATE else s[:_ERR_TRUNCATE] + "…"


async def probe_messages(
    *,
    messages_root: str,
    api_key: str,
    model: str,
    timeout_s: float = 10.0,
) -> ProbeResult:
    """POST {messages_root}/v1/messages 发最小请求 (max_tokens=1)。

    判定:
    - 2xx 且响应 JSON 含 type=message → success
    - 2xx 但响应不像 anthropic JSON → 判失败 (OPENAI_COMPAT_ONLY)
    - 非 2xx → 失败
    - 网络异常/超时 → 失败 (status_code=None)
    """
    url = f"{messages_root.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    started = time.perf_counter()
    try:
        resp = await _post(url=url, headers=headers, payload=payload, timeout_s=timeout_s)
    except httpx.TimeoutException as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages timeout url=%s elapsed_ms=%d", url, elapsed)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=f"timeout: {exc!s}")
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages network err url=%s elapsed_ms=%d", url, elapsed)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=_truncate(str(exc)))

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("probe_messages url=%s status=%d elapsed_ms=%d", url, resp.status_code, elapsed)

    if resp.status_code >= 400:
        # 不打 body 全文，只截前 200 字符以便 UI 给用户看
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error=_truncate(resp.text),
        )

    # 2xx：检查是否真的是 anthropic JSON
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
    """把失败 ProbeResult 映射到 DiagnosisCode。"""
    if result.success:
        return DiagnosisCode.UNKNOWN  # caller misuse
    err = (result.error or "").lower()
    code = result.status_code
    if code in (401, 403):
        return DiagnosisCode.AUTH_FAILED
    if code == 429:
        return DiagnosisCode.RATE_LIMITED
    # 启发式：404 body 含 "model" 关键词即视为模型不存在；后端改措辞时会退化到 UNKNOWN
    if code == 404 and ("model" in err or "model_not_found" in err):
        return DiagnosisCode.MODEL_NOT_FOUND
    if code is not None and 200 <= code < 300:
        # 2xx 但 probe 判失败 = 协议不匹配
        return DiagnosisCode.OPENAI_COMPAT_ONLY
    if code is None:
        return DiagnosisCode.NETWORK
    return DiagnosisCode.UNKNOWN
