"""Anthropic probe 单元测试 (mock httpx，不打真实网络)。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID
from lib.config.anthropic_probe import (
    DiagnosisCode,
    ProbeResult,
    classify_probe_failure,
    probe_discovery,
    probe_messages,
    run_test,
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


@pytest.mark.asyncio
async def test_probe_messages_network_error() -> None:
    with patch(
        "lib.config.anthropic_probe._post",
        AsyncMock(side_effect=httpx.ConnectError("connection refused")),
    ):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk",
            model="x",
        )
    assert result.success is False
    assert result.status_code is None
    assert result.error is not None
    assert "connection refused" in (result.error or "").lower()


def test_classify_probe_failure_auth() -> None:
    p = ProbeResult(success=False, status_code=401, latency_ms=10, error="…")
    assert classify_probe_failure(p) == DiagnosisCode.AUTH_FAILED


def test_classify_probe_failure_403_also_auth() -> None:
    p = ProbeResult(success=False, status_code=403, latency_ms=10, error="forbidden")
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


def test_classify_probe_failure_unknown_500() -> None:
    p = ProbeResult(success=False, status_code=500, latency_ms=10, error="internal error")
    assert classify_probe_failure(p) == DiagnosisCode.UNKNOWN


def test_classify_probe_failure_unknown_404_no_model() -> None:
    p = ProbeResult(success=False, status_code=404, latency_ms=10, error="endpoint not found")
    assert classify_probe_failure(p) == DiagnosisCode.UNKNOWN


@pytest.mark.asyncio
async def test_probe_discovery_none_root_returns_none() -> None:
    assert await probe_discovery(discovery_root=None, api_key="sk") is None


@pytest.mark.asyncio
async def test_probe_discovery_success() -> None:
    fake = httpx.Response(200, json={"data": [{"id": "m"}]})
    with patch(
        "lib.config.anthropic_probe._get",
        AsyncMock(return_value=fake),
    ) as mocked:
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk")
    assert result is not None
    assert result.success is True
    assert result.status_code == 200
    called_url = mocked.await_args.kwargs["url"]
    assert called_url == "https://api.example.com/v1/models"


@pytest.mark.asyncio
async def test_probe_discovery_non_2xx_marks_failure() -> None:
    fake = httpx.Response(404, text="not found")
    with patch("lib.config.anthropic_probe._get", AsyncMock(return_value=fake)):
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk")
    assert result is not None
    assert result.success is False
    assert result.status_code == 404
    assert "not found" in (result.error or "")


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


@pytest.mark.asyncio
async def test_run_test_custom_mode_self_heals_with_anthropic_suffix() -> None:
    """用户填 https://api.deepseek.com，messages probe 失败 (404)；
    自动重试 https://api.deepseek.com/anthropic 成功 → suggestion 给出修复值。
    """
    post_seq = [
        httpx.Response(404, text="not found"),  # 原 URL
        httpx.Response(200, json={"id": "msg_1", "type": "message", "content": []}),  # +/anthropic
    ]
    call_log: list[str] = []

    async def fake_post(*, url, **_kw):
        call_log.append(url)
        return post_seq.pop(0)

    async def fake_get(*, url, **_kw):
        call_log.append(url)
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._post", AsyncMock(side_effect=fake_post)),
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
    posted = [u for u in call_log if "/v1/messages" in u]
    assert posted == [
        "https://api.deepseek.com/v1/messages",
        "https://api.deepseek.com/anthropic/v1/messages",
    ]


@pytest.mark.asyncio
async def test_run_test_preset_skips_self_heal() -> None:
    """preset_id != __custom__ 时不做自愈尝试。"""
    seq = [httpx.Response(404, text="not found")]

    async def fake_post(*, url, **_kw):
        return seq.pop(0)

    async def fake_get(**_kw):
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._post", AsyncMock(side_effect=fake_post)),
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


@pytest.mark.asyncio
async def test_run_test_self_heal_retry_also_fails_keeps_original_failure() -> None:
    """自愈重试也失败 (同 404) → suggestion=None，diagnosis=UNKNOWN。"""
    post_seq = [
        httpx.Response(404, text="not found"),
        httpx.Response(404, text="still not found"),
    ]

    async def fake_post(*, url, **_kw):
        return post_seq.pop(0)

    async def fake_get(**_kw):
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._post", AsyncMock(side_effect=fake_post)),
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
    assert resp.diagnosis == DiagnosisCode.UNKNOWN


@pytest.mark.asyncio
async def test_run_test_self_heal_retry_promotes_specific_diagnosis() -> None:
    """重试失败但二次诊断更具体 (401) → 采纳 retry，让用户看到 AUTH_FAILED 而非 UNKNOWN。"""
    post_seq = [
        httpx.Response(404, text="not found"),  # 首次：路径错
        httpx.Response(401, json={"error": {"type": "authentication_error"}}),  # +/anthropic 后：key 错
    ]

    async def fake_post(*, url, **_kw):
        return post_seq.pop(0)

    async def fake_get(**_kw):
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._post", AsyncMock(side_effect=fake_post)),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.example.com",
            api_key="bad",
            model=None,
        )

    assert resp.overall == "fail"
    assert resp.diagnosis == DiagnosisCode.AUTH_FAILED
    assert resp.derived_messages_root.endswith("/anthropic")
    # retry 401 ≠ 缺后缀的诊断，不发 suggestion
    assert resp.suggestion is None


@pytest.mark.asyncio
async def test_run_test_preset_with_base_url_override_derives_discovery() -> None:
    """preset 凭证覆盖 base_url → discovery 也从 base_url 派生，与运行时一致。"""
    captured: dict[str, str] = {}

    async def fake_post(*, url, **_kw):
        captured["messages_url"] = url
        return httpx.Response(200, json={"id": "msg_1", "type": "message", "content": []})

    async def fake_get(*, url, **_kw):
        captured["discovery_url"] = url
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._post", AsyncMock(side_effect=fake_post)),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id="deepseek",
            base_url="https://corp-proxy.example.com/anthropic",
            api_key="sk",
            model=None,
        )

    assert resp.overall == "ok"
    assert captured["messages_url"] == "https://corp-proxy.example.com/anthropic/v1/messages"
    # discovery 也从 base_url 派生（剥掉 /anthropic）
    assert captured["discovery_url"] == "https://corp-proxy.example.com/v1/models"
    assert resp.derived_discovery_root == "https://corp-proxy.example.com"


@pytest.mark.asyncio
async def test_run_test_custom_mode_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url required"):
        await run_test(preset_id=None, base_url=None, api_key="sk", model=None)


@pytest.mark.asyncio
async def test_run_test_unknown_preset_raises() -> None:
    with pytest.raises(ValueError, match="unknown preset"):
        await run_test(preset_id="bogus-preset", base_url=None, api_key="sk", model=None)
