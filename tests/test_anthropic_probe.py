"""Anthropic probe 单元测试。

messages probe 走 SDK query()：用 patch 替换 _sdk_query_stream，让它产出真实的
SDK AssistantMessage/ResultMessage 或抛 ProcessError/CLIConnectionError，验证
probe_messages 的成功/失败映射、自愈逻辑、诊断分类。

discovery probe 仍走 httpx，相关测试沿用 mock httpx 的方式。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    ProcessError,
    ResultMessage,
    TextBlock,
)

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID
from lib.config.anthropic_probe import (
    DiagnosisCode,
    ProbeResult,
    classify_probe_failure,
    probe_discovery,
    probe_messages,
    run_test,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _assistant(text: str = "ok") -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="claude-3-5-sonnet")


def _result(*, is_error: bool = False, result: str | None = None) -> ResultMessage:
    return ResultMessage(
        subtype="error_during_execution" if is_error else "success",
        duration_ms=42,
        duration_api_ms=20,
        is_error=is_error,
        num_turns=1,
        session_id="sess-test",
        result=result,
    )


def _stream(*msgs: Any) -> AsyncIterator[Any]:
    """构造一个产出指定消息序列的 async generator。"""

    async def gen() -> AsyncIterator[Any]:
        for m in msgs:
            yield m

    return gen()


def _stream_raises(exc: BaseException) -> AsyncIterator[Any]:
    """构造一个迭代时抛指定异常的 async generator。"""

    async def gen() -> AsyncIterator[Any]:
        raise exc
        yield  # pragma: no cover  # noqa: B901

    return gen()


# ---------------------------------------------------------------------------
# probe_messages: success / failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_messages_success() -> None:
    """收到 AssistantMessage + 非 error ResultMessage 视为成功。"""
    calls: list[dict[str, Any]] = []

    def fake_stream(**kw: Any) -> AsyncIterator[Any]:
        calls.append(kw)
        return _stream(_assistant("ping back"), _result())

    with patch("lib.config.anthropic_probe._sdk_query_stream", side_effect=fake_stream):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk-test",
            model="claude-3-5-sonnet-20241022",
        )

    assert result.success is True
    assert result.status_code == 200
    assert result.error is None
    # SDK 入参透传：base_url / api_key / model
    assert calls == [
        {
            "messages_root": "https://api.example.com",
            "api_key": "sk-test",
            "model": "claude-3-5-sonnet-20241022",
        }
    ]


@pytest.mark.asyncio
async def test_probe_messages_process_error_401_extracts_status() -> None:
    """ProcessError stderr 含 401 → status_code=401，触发 AUTH_FAILED 诊断。"""
    err = ProcessError("CLI failed", exit_code=1, stderr="API error 401: invalid x-api-key")
    with patch(
        "lib.config.anthropic_probe._sdk_query_stream",
        side_effect=lambda **_: _stream_raises(err),
    ):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="bad",
            model="claude-3-5-sonnet-20241022",
        )
    assert result.success is False
    assert result.status_code == 401
    assert "401" in (result.error or "")


@pytest.mark.asyncio
async def test_probe_messages_process_error_429() -> None:
    err = ProcessError("CLI failed", exit_code=1, stderr="rate_limit_error 429")
    with patch(
        "lib.config.anthropic_probe._sdk_query_stream",
        side_effect=lambda **_: _stream_raises(err),
    ):
        result = await probe_messages(messages_root="https://api.example.com", api_key="sk", model="x")
    assert result.success is False
    assert result.status_code == 429


@pytest.mark.asyncio
async def test_probe_messages_cli_connection_error() -> None:
    """CLIConnectionError → status_code=None，error 文本透传。"""
    with patch(
        "lib.config.anthropic_probe._sdk_query_stream",
        side_effect=lambda **_: _stream_raises(CLIConnectionError("connection refused")),
    ):
        result = await probe_messages(messages_root="https://api.example.com", api_key="sk", model="x")
    assert result.success is False
    assert result.status_code is None
    assert "connection refused" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_probe_messages_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.timeout 触发 → status_code=None，error 含 'timeout'。"""

    async def slow_gen() -> AsyncIterator[Any]:
        import asyncio

        await asyncio.sleep(10)
        yield _assistant()

    with patch("lib.config.anthropic_probe._sdk_query_stream", side_effect=lambda **_: slow_gen()):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk",
            model="x",
            timeout_s=0.05,
        )
    assert result.success is False
    assert result.status_code is None
    assert "timeout" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_probe_messages_result_is_error_extracts_status() -> None:
    """SDK 完成 turn 但 ResultMessage.is_error=True → 失败 + 抠 status。"""
    with patch(
        "lib.config.anthropic_probe._sdk_query_stream",
        side_effect=lambda **_: _stream(_result(is_error=True, result="model_not_found 404")),
    ):
        result = await probe_messages(messages_root="https://api.example.com", api_key="sk", model="bogus-model")
    assert result.success is False
    assert result.status_code == 404
    assert "model_not_found" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_probe_messages_no_assistant_message() -> None:
    """只收到 ResultMessage 没有 AssistantMessage → 视为失败（兼容协议响应畸形）。"""
    with patch(
        "lib.config.anthropic_probe._sdk_query_stream",
        side_effect=lambda **_: _stream(_result()),
    ):
        result = await probe_messages(messages_root="https://api.example.com", api_key="sk", model="x")
    assert result.success is False
    assert "no assistant message" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# classify_probe_failure
# ---------------------------------------------------------------------------


def test_classify_auth_401() -> None:
    p = ProbeResult(success=False, status_code=401, latency_ms=10, error="…")
    assert classify_probe_failure(p) == DiagnosisCode.AUTH_FAILED


def test_classify_auth_403() -> None:
    p = ProbeResult(success=False, status_code=403, latency_ms=10, error="forbidden")
    assert classify_probe_failure(p) == DiagnosisCode.AUTH_FAILED


def test_classify_rate_limited() -> None:
    p = ProbeResult(success=False, status_code=429, latency_ms=10, error="rate")
    assert classify_probe_failure(p) == DiagnosisCode.RATE_LIMITED


def test_classify_model_not_found_via_status() -> None:
    p = ProbeResult(success=False, status_code=404, latency_ms=10, error="model_not_found")
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_NOT_FOUND


def test_classify_404_no_model_kw_is_unknown() -> None:
    p = ProbeResult(success=False, status_code=404, latency_ms=10, error="endpoint not found")
    assert classify_probe_failure(p) == DiagnosisCode.UNKNOWN


def test_classify_openai_compat_2xx() -> None:
    p = ProbeResult(success=False, status_code=200, latency_ms=10, error="non-anthropic JSON")
    assert classify_probe_failure(p) == DiagnosisCode.OPENAI_COMPAT_ONLY


def test_classify_network_timeout_no_status() -> None:
    p = ProbeResult(success=False, status_code=None, latency_ms=10, error="timeout after 30s")
    assert classify_probe_failure(p) == DiagnosisCode.NETWORK


def test_classify_keyword_fallback_auth_no_status() -> None:
    """status_code 抠不到时关键词兜底 → unauthorized 匹配 AUTH_FAILED。"""
    p = ProbeResult(success=False, status_code=None, latency_ms=10, error="unauthorized request")
    assert classify_probe_failure(p) == DiagnosisCode.AUTH_FAILED


def test_classify_keyword_fallback_model() -> None:
    p = ProbeResult(success=False, status_code=None, latency_ms=10, error="model is invalid or unknown")
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_NOT_FOUND


def test_classify_unknown_5xx() -> None:
    p = ProbeResult(success=False, status_code=500, latency_ms=10, error="internal error")
    assert classify_probe_failure(p) == DiagnosisCode.UNKNOWN


# ---------------------------------------------------------------------------
# probe_discovery (httpx) — unchanged behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_discovery_none_root_returns_none() -> None:
    assert await probe_discovery(discovery_root=None, api_key="sk") is None


@pytest.mark.asyncio
async def test_probe_discovery_success() -> None:
    fake = httpx.Response(200, json={"data": [{"id": "m"}]})
    with patch("lib.config.anthropic_probe._get", AsyncMock(return_value=fake)) as mocked:
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk")
    assert result is not None
    assert result.success is True
    assert result.status_code == 200
    assert mocked.await_args.kwargs["url"] == "https://api.example.com/v1/models"


@pytest.mark.asyncio
async def test_probe_discovery_non_2xx_marks_failure() -> None:
    fake = httpx.Response(404, text="not found")
    with patch("lib.config.anthropic_probe._get", AsyncMock(return_value=fake)):
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk")
    assert result is not None
    assert result.success is False
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_probe_discovery_network_error() -> None:
    with patch(
        "lib.config.anthropic_probe._get",
        AsyncMock(side_effect=httpx.ConnectError("dns fail")),
    ):
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk")
    assert result is not None
    assert result.success is False
    assert result.status_code is None
    assert "dns fail" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# run_test: end-to-end orchestration with self-heal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_test_custom_mode_self_heals_with_anthropic_suffix() -> None:
    """custom 模式 base_url 缺 /anthropic：第一次 SDK 失败 (404)；
    自动补 /anthropic 重试成功 → suggestion 给出修复值。
    """
    seq: list[AsyncIterator[Any]] = [
        # 第一次 base_url 失败
        _stream_raises(ProcessError("CLI failed", exit_code=1, stderr="HTTP 404 not found")),
        # 第二次 base_url + /anthropic 成功
        _stream(_assistant(), _result()),
    ]
    call_roots: list[str] = []

    def fake_stream(**kw: Any) -> AsyncIterator[Any]:
        call_roots.append(kw["messages_root"])
        return seq.pop(0)

    async def fake_get(**_: Any) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._sdk_query_stream", side_effect=fake_stream),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.deepseek.com",
            api_key="sk",
            model=None,
        )

    assert resp.overall == "ok"
    assert resp.diagnosis == DiagnosisCode.MISSING_ANTHROPIC_SUFFIX
    assert resp.suggestion is not None
    assert resp.suggestion.kind == "replace_base_url"
    assert resp.suggestion.suggested_value == "https://api.deepseek.com/anthropic"
    assert call_roots == [
        "https://api.deepseek.com",
        "https://api.deepseek.com/anthropic",
    ]


@pytest.mark.asyncio
async def test_run_test_preset_skips_self_heal() -> None:
    """preset_id != __custom__ 时不做自愈尝试。"""
    seq: list[AsyncIterator[Any]] = [_stream_raises(ProcessError("CLI failed", exit_code=1, stderr="HTTP 404"))]

    def fake_stream(**_: Any) -> AsyncIterator[Any]:
        return seq.pop(0)

    async def fake_get(**_: Any) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._sdk_query_stream", side_effect=fake_stream),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id="anthropic-official",
            base_url=None,
            api_key="sk",
            model=None,
        )
    assert resp.overall == "fail"
    assert resp.suggestion is None
    assert len(seq) == 0  # 只调用一次，没自愈重试


@pytest.mark.asyncio
async def test_run_test_self_heal_skipped_on_auth_failure() -> None:
    """custom 模式 auth 失败：不该自愈（重试也救不回来）。"""
    seq: list[AsyncIterator[Any]] = [
        _stream_raises(ProcessError("CLI failed", exit_code=1, stderr="API error 401: invalid x-api-key"))
    ]

    def fake_stream(**_: Any) -> AsyncIterator[Any]:
        return seq.pop(0)

    async def fake_get(**_: Any) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._sdk_query_stream", side_effect=fake_stream),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.deepseek.com",
            api_key="bad",
            model=None,
        )
    assert resp.overall == "fail"
    assert resp.diagnosis == DiagnosisCode.AUTH_FAILED
    assert resp.suggestion is None
    assert len(seq) == 0  # 没自愈重试


@pytest.mark.asyncio
async def test_run_test_self_heal_retry_also_fails_keeps_original_failure() -> None:
    """自愈重试也失败 → suggestion=None，diagnosis=UNKNOWN。"""
    seq: list[AsyncIterator[Any]] = [
        _stream_raises(ProcessError("CLI failed", exit_code=1, stderr="HTTP 404 not found")),
        _stream_raises(ProcessError("CLI failed", exit_code=1, stderr="HTTP 404 still not found")),
    ]

    def fake_stream(**_: Any) -> AsyncIterator[Any]:
        return seq.pop(0)

    async def fake_get(**_: Any) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._sdk_query_stream", side_effect=fake_stream),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.example.com",
            api_key="sk",
            model=None,
        )

    assert resp.overall == "fail"
    assert resp.suggestion is None
    # 404 + body 不含 "model" → UNKNOWN
    assert resp.diagnosis == DiagnosisCode.UNKNOWN


@pytest.mark.asyncio
async def test_run_test_custom_mode_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url required"):
        await run_test(preset_id=None, base_url=None, api_key="sk", model=None)


@pytest.mark.asyncio
async def test_run_test_unknown_preset_raises() -> None:
    with pytest.raises(ValueError, match="unknown preset"):
        await run_test(preset_id="bogus-preset", base_url=None, api_key="sk", model=None)


@pytest.mark.asyncio
async def test_run_test_messages_ok_discovery_fail_marks_warn() -> None:
    """messages 成功 + discovery 失败 → overall=warn（discovery 仅 warn 级）。"""

    def fake_stream(**_: Any) -> AsyncIterator[Any]:
        return _stream(_assistant(), _result())

    async def fake_get(**_: Any) -> httpx.Response:
        return httpx.Response(404, text="discovery not supported")

    with (
        patch("lib.config.anthropic_probe._sdk_query_stream", side_effect=fake_stream),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id="anthropic-official",
            base_url=None,
            api_key="sk",
            model=None,
        )
    assert resp.overall == "warn"
    assert resp.messages_probe.success is True
    assert resp.discovery_probe is not None
    assert resp.discovery_probe.success is False
