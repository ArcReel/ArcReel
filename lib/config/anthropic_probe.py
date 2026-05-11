"""Anthropic 兼容端点的连通性体检 + 诊断分类。

messages probe 走 claude_agent_sdk.query() 单次调用，与运行时调用链一致；
discovery probe 走 httpx 直调（SDK 无对应能力）。

日志严格只打 URL/exit_code/elapsed，不打 body / headers / api_key / stderr 全文。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

import httpx

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID, get_preset
from lib.config.anthropic_url import AnthropicEndpoints, derive_anthropic_endpoints
from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

_ERR_TRUNCATE = 200
# 从 stderr 文本里抠出疑似 HTTP status（"... 401 ..." / "status=429" 等）
_STATUS_RE = re.compile(r"\b([45]\d{2})\b")


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


def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    return s if len(s) <= _ERR_TRUNCATE else s[:_ERR_TRUNCATE] + "…"


def _extract_status_code(text: str | None) -> int | None:
    if not text:
        return None
    m = _STATUS_RE.search(text)
    return int(m.group(1)) if m else None


def _sdk_query_stream(
    *,
    messages_root: str,
    api_key: str,
    model: str,
) -> AsyncIterator[Any]:
    """间接层：测试时 patch 这一个，返回 SDK 消息的 async iterator。

    通过 ClaudeAgentOptions.env 注入 ANTHROPIC_BASE_URL / API_KEY，与 ConfigService
    在运行时写入进程环境的方式等价。setting_sources=[] / system_prompt="" / max_turns=1
    最小化副作用：不读 user/project settings、不带预设 system prompt、单回合即返回。
    """
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        model=model,
        max_turns=1,
        allowed_tools=[],
        disallowed_tools=[],
        system_prompt="",
        setting_sources=[],
        env={
            "ANTHROPIC_BASE_URL": messages_root,
            "ANTHROPIC_API_KEY": api_key,
        },
    )
    return query(prompt="ping", options=options)


async def probe_messages(
    *,
    messages_root: str,
    api_key: str,
    model: str,
    timeout_s: float = 30.0,
) -> ProbeResult:
    """SDK query() 单次调用：收到 AssistantMessage 且 ResultMessage 非 error 视为成功。

    失败映射：
    - ProcessError → 从 stderr 抠 status；exit_code 进日志
    - CLIConnectionError / ClaudeSDKError → status_code=None（网络/启动失败）
    - asyncio.TimeoutError → status_code=None（视作 network）
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeSDKError,
        ResultMessage,
    )

    started = time.perf_counter()
    got_assistant = False
    result_msg: Any = None

    try:
        async with asyncio.timeout(timeout_s):
            async for msg in _sdk_query_stream(messages_root=messages_root, api_key=api_key, model=model):
                if isinstance(msg, AssistantMessage):
                    got_assistant = True
                elif isinstance(msg, ResultMessage):
                    result_msg = msg
                    break
    except TimeoutError:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages sdk timeout base_url=%s elapsed_ms=%d", messages_root, elapsed)
        return ProbeResult(
            success=False,
            status_code=None,
            latency_ms=elapsed,
            error=f"timeout after {timeout_s}s",
        )
    except ClaudeSDKError as exc:
        # 涵盖 ProcessError / CLIConnectionError / CLIJSONDecodeError / CLINotFoundError
        elapsed = int((time.perf_counter() - started) * 1000)
        stderr = getattr(exc, "stderr", None) or str(exc)
        exit_code = getattr(exc, "exit_code", None)
        status = _extract_status_code(stderr)
        logger.info(
            "probe_messages sdk error base_url=%s exit_code=%s status=%s elapsed_ms=%d",
            messages_root,
            exit_code,
            status,
            elapsed,
        )
        return ProbeResult(
            success=False,
            status_code=status,
            latency_ms=elapsed,
            error=_truncate(stderr),
        )

    elapsed = int((time.perf_counter() - started) * 1000)

    if result_msg is not None and getattr(result_msg, "is_error", False):
        # SDK 完成 turn 但 CLI 报告错误（例如 model 不存在、API 返回错误后被吞回 result）
        err_text = getattr(result_msg, "result", None) or "sdk reported is_error=True"
        return ProbeResult(
            success=False,
            status_code=_extract_status_code(str(err_text)),
            latency_ms=elapsed,
            error=_truncate(str(err_text)),
        )

    if not got_assistant:
        return ProbeResult(
            success=False,
            status_code=None,
            latency_ms=elapsed,
            error="no assistant message received",
        )

    logger.info("probe_messages sdk ok base_url=%s elapsed_ms=%d", messages_root, elapsed)
    return ProbeResult(success=True, status_code=200, latency_ms=elapsed, error=None)


def classify_probe_failure(result: ProbeResult) -> DiagnosisCode:
    """把失败 ProbeResult 映射到 DiagnosisCode。

    SDK 模式 status_code 是从 stderr 抠出来的近似值，可能为 None；
    抠不到时退化到关键词匹配 error 文本。
    """
    if result.success:
        return DiagnosisCode.UNKNOWN  # caller misuse
    err = (result.error or "").lower()
    code = result.status_code
    if code in (401, 403):
        return DiagnosisCode.AUTH_FAILED
    if code == 429:
        return DiagnosisCode.RATE_LIMITED
    if code == 404 and ("model" in err or "model_not_found" in err):
        return DiagnosisCode.MODEL_NOT_FOUND
    if code is not None and 200 <= code < 300:
        return DiagnosisCode.OPENAI_COMPAT_ONLY
    # status 缺失：关键词兜底
    if any(kw in err for kw in ("timeout", "econnrefused", "enotfound", "connection")):
        return DiagnosisCode.NETWORK
    if any(kw in err for kw in ("unauthor", "api key", "api_key", "invalid_api_key")):
        return DiagnosisCode.AUTH_FAILED
    if "rate" in err or "throttl" in err:
        return DiagnosisCode.RATE_LIMITED
    if "model" in err and any(kw in err for kw in ("not found", "invalid", "unknown")):
        return DiagnosisCode.MODEL_NOT_FOUND
    if code is None:
        return DiagnosisCode.NETWORK
    return DiagnosisCode.UNKNOWN


async def _get(*, url: str, headers: dict[str, str], timeout_s: float) -> httpx.Response:
    """间接层：测试时 patch 这一个。"""
    client = get_http_client()
    return await client.get(url, headers=headers, timeout=timeout_s)


async def probe_discovery(
    *,
    discovery_root: str | None,
    api_key: str,
    timeout_s: float = 5.0,
) -> ProbeResult | None:
    """GET {discovery_root}/v1/models 体检模型发现端点 (warn 级，仅供参考)。"""
    if not discovery_root:
        return None
    url = f"{discovery_root.rstrip('/')}/v1/models"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    started = time.perf_counter()
    try:
        resp = await _get(url=url, headers=headers, timeout_s=timeout_s)
    except httpx.TimeoutException as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=f"timeout: {exc!s}")
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=_truncate(str(exc)))

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("probe_discovery url=%s status=%d", url, resp.status_code)
    success = 200 <= resp.status_code < 300
    return ProbeResult(
        success=success,
        status_code=resp.status_code,
        latency_ms=elapsed,
        error=None if success else _truncate(resp.text),
    )


_DEFAULT_TEST_MODEL = "claude-3-5-sonnet-20241022"


@dataclass(frozen=True)
class SuggestionAction:
    kind: Literal["replace_base_url", "check_api_key", "run_discovery", "see_docs"]
    suggested_value: str | None = None


@dataclass(frozen=True)
class TestConnectionResponse:
    overall: Literal["ok", "warn", "fail"]
    messages_probe: ProbeResult
    discovery_probe: ProbeResult | None
    diagnosis: DiagnosisCode | None
    suggestion: SuggestionAction | None
    derived_messages_root: str
    derived_discovery_root: str


# 自愈不该接管的诊断：用户问题，重试也救不回来
_NON_SELF_HEALABLE = (
    DiagnosisCode.AUTH_FAILED,
    DiagnosisCode.RATE_LIMITED,
    DiagnosisCode.MODEL_NOT_FOUND,
)


async def run_test(
    *,
    preset_id: str | None,
    base_url: str | None,
    api_key: str,
    model: str | None,
) -> TestConnectionResponse:
    """完整端到端测试：派生 → SDK query 试调 → 自定义模式自愈 → discovery → 诊断。"""
    # 1. 派生 endpoints
    if preset_id and preset_id != CUSTOM_SENTINEL_ID:
        preset = get_preset(preset_id)
        if preset is None:
            raise ValueError(f"unknown preset: {preset_id!r}")
        # 凭证允许覆盖 preset.messages_url（如内部代理）；测试必须用与运行时一致的 URL。
        # has_explicit_suffix=True 抑制自愈分支：preset 凭证视为权威，不补 /anthropic
        ep = AnthropicEndpoints(
            messages_root=base_url or preset.messages_url,
            discovery_root=preset.discovery_url or "",
            has_explicit_suffix=True,
        )
        effective_model = model or preset.default_model
    else:
        if not base_url:
            raise ValueError("base_url required for __custom__ mode")
        ep = derive_anthropic_endpoints(base_url)
        effective_model = model or _DEFAULT_TEST_MODEL

    # 2. messages (SDK) + discovery (httpx) 并发：discovery 是独立 warn 级信号
    msg, disc = await asyncio.gather(
        probe_messages(messages_root=ep.messages_root, api_key=api_key, model=effective_model),
        probe_discovery(discovery_root=ep.discovery_root or None, api_key=api_key),
    )

    # 3. 自定义模式 + 没显式 anthropic 后缀 + 失败且可自愈 → 补 /anthropic 串行重试
    #    SDK 拿不到精确 status code，用诊断码代替 status_code in (404,405,502) 闸门
    suggestion: SuggestionAction | None = None
    diagnosis: DiagnosisCode | None = None
    final_messages_root = ep.messages_root
    if (
        not msg.success
        and (preset_id is None or preset_id == CUSTOM_SENTINEL_ID)
        and not ep.has_explicit_suffix
        and classify_probe_failure(msg) not in _NON_SELF_HEALABLE
    ):
        retry_root = ep.messages_root.rstrip("/") + "/anthropic"
        retry = await probe_messages(messages_root=retry_root, api_key=api_key, model=effective_model)
        if retry.success:
            msg = retry
            final_messages_root = retry_root
            suggestion = SuggestionAction(kind="replace_base_url", suggested_value=retry_root)
            diagnosis = DiagnosisCode.MISSING_ANTHROPIC_SUFFIX

    # 4. 诊断 + 总评
    if msg.success:
        overall = "ok" if (disc is None or disc.success) else "warn"
    else:
        overall = "fail"
        diagnosis = classify_probe_failure(msg)

    return TestConnectionResponse(
        overall=overall,
        messages_probe=msg,
        discovery_probe=disc,
        diagnosis=diagnosis,
        suggestion=suggestion,
        derived_messages_root=final_messages_root,
        derived_discovery_root=ep.discovery_root,
    )
