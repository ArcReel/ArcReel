"""Anthropic probe 单元测试 (mock httpx，不打真实网络)。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lib.config.anthropic_probe import (
    DiagnosisCode,
    ProbeResult,
    classify_probe_failure,
    probe_messages,
)


@pytest.mark.asyncio
async def test_probe_messages_success() -> None:
    fake_response = httpx.Response(
        200,
        json={"id": "msg_1", "type": "message", "content": [{"type": "text", "text": "ok"}]},
    )
    with patch(
        "lib.config.anthropic_probe._post",
        AsyncMock(return_value=fake_response),
    ) as mocked:
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk-test",
            model="claude-3-5-sonnet-20241022",
        )
    assert result.success is True
    assert result.status_code == 200
    assert result.error is None
    mocked.assert_awaited_once()
    called_url = mocked.await_args.kwargs["url"]
    assert called_url == "https://api.example.com/v1/messages"


@pytest.mark.asyncio
async def test_probe_messages_401_marks_failure() -> None:
    fake = httpx.Response(401, json={"error": {"type": "authentication_error"}})
    with patch("lib.config.anthropic_probe._post", AsyncMock(return_value=fake)):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="bad",
            model="claude-3-5-sonnet-20241022",
        )
    assert result.success is False
    assert result.status_code == 401
    assert "authentication_error" in (result.error or "")


@pytest.mark.asyncio
async def test_probe_messages_200_but_not_anthropic_marks_failure() -> None:
    """OpenAI 兼容协议响应：200 但缺 type=message 应判失败。"""
    fake = httpx.Response(
        200,
        json={"id": "chatcmpl-1", "object": "chat.completion", "choices": []},
    )
    with patch("lib.config.anthropic_probe._post", AsyncMock(return_value=fake)):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk",
            model="x",
        )
    assert result.success is False
    assert result.status_code == 200
    assert "non-anthropic" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_probe_messages_timeout() -> None:
    with patch(
        "lib.config.anthropic_probe._post",
        AsyncMock(side_effect=httpx.TimeoutException("timeout")),
    ):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk",
            model="x",
            timeout_s=0.5,
        )
    assert result.success is False
    assert result.status_code is None
    assert "timeout" in (result.error or "").lower()


def test_classify_probe_failure_auth() -> None:
    p = ProbeResult(success=False, status_code=401, latency_ms=10, error="…")
    assert classify_probe_failure(p) == DiagnosisCode.AUTH_FAILED


def test_classify_probe_failure_404_with_model() -> None:
    p = ProbeResult(success=False, status_code=404, latency_ms=10, error="model_not_found")
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_NOT_FOUND


def test_classify_probe_failure_429() -> None:
    p = ProbeResult(success=False, status_code=429, latency_ms=10, error="rate")
    assert classify_probe_failure(p) == DiagnosisCode.RATE_LIMITED


def test_classify_probe_failure_network() -> None:
    p = ProbeResult(success=False, status_code=None, latency_ms=10, error="timeout")
    assert classify_probe_failure(p) == DiagnosisCode.NETWORK


def test_classify_probe_failure_openai_compat() -> None:
    p = ProbeResult(success=False, status_code=200, latency_ms=10, error="non-anthropic JSON")
    assert classify_probe_failure(p) == DiagnosisCode.OPENAI_COMPAT_ONLY
