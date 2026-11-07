"""Anthropic probes use transport-level respx interception, without real network traffic."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from lib.agent.agent_provider_catalog import CUSTOM_SENTINEL_ID, get_preset
from lib.config.anthropic_probe import (
    DiagnosisCode,
    ProbeResult,
    classify_probe_failure,
    probe_messages,
    run_test,
)
from lib.config.url_utils import InvalidAnthropicBaseUrlError
from tests.http_capture import capture_http, only_request, request_json

_ANTHROPIC_OK = {"id": "msg_1", "type": "message", "content": [{"type": "text", "text": "ok"}]}


@pytest.fixture
async def probe_client() -> AsyncIterator[httpx.AsyncClient]:
    """Real httpx client with outbound traffic intercepted by respx transport.

    Production’s shared singleton requires lifespan initialization; unit tests explicitly inject
    through the `http_client` seam.
    """
    async with httpx.AsyncClient() as client:
        yield client


async def test_probe_messages_success(probe_client: httpx.AsyncClient) -> None:
    with capture_http() as router:
        route = router.post("https://api.example.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )
        result = await probe_messages(
            base_url="https://api.example.com",
            api_key="sk-test",
            model="claude-3-5-sonnet-20241022",
            http_client=probe_client,
        )

    assert result.success is True
    assert result.status_code == 200
    assert result.error is None
    request = only_request(route)
    assert request.headers["x-api-key"] == "sk-test"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request_json(request) == {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }


async def test_probe_messages_401_marks_failure(probe_client: httpx.AsyncClient) -> None:
    with capture_http() as router:
        router.post("https://api.example.com/v1/messages").mock(
            return_value=httpx.Response(401, json={"error": {"type": "authentication_error"}})
        )
        result = await probe_messages(
            base_url="https://api.example.com",
            api_key="bad",
            model="claude-3-5-sonnet-20241022",
            http_client=probe_client,
        )

    assert result.success is False
    assert result.status_code == 401
    assert "authentication_error" in (result.error or "")


async def test_probe_messages_200_but_not_anthropic_marks_failure(probe_client: httpx.AsyncClient) -> None:
    """OpenAI-compatible 200 responses missing type=message must fail."""
    with capture_http() as router:
        router.post("https://api.example.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"id": "chatcmpl-1", "object": "chat.completion", "choices": []})
        )
        result = await probe_messages(
            base_url="https://api.example.com",
            api_key="sk",
            model="x",
            http_client=probe_client,
        )

    assert result.success is False
    assert result.status_code == 200
    assert "non-anthropic" in (result.error or "").lower()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ReadTimeout("read timed out"), DiagnosisCode.TIMEOUT),
        (httpx.ConnectTimeout("connect timed out"), DiagnosisCode.NETWORK),
        (httpx.WriteTimeout("write timed out"), DiagnosisCode.NETWORK),
        (httpx.PoolTimeout("pool timed out"), DiagnosisCode.NETWORK),
    ],
    ids=["read", "connect", "write", "pool"],
)
async def test_probe_messages_timeout_only_read_timeout_means_upstream_slow(
    probe_client: httpx.AsyncClient, exc: httpx.TimeoutException, expected: DiagnosisCode
) -> None:
    """Only ReadTimeout establishes reachability; other timeouts count as network failures."""
    with capture_http() as router:
        router.post("https://api.example.com/v1/messages").mock(side_effect=exc)
        result = await probe_messages(
            base_url="https://api.example.com",
            api_key="sk",
            model="x",
            timeout_s=0.5,
            http_client=probe_client,
        )

    assert result.success is False
    assert result.status_code is None
    assert "timed out" in (result.error or "").lower()
    assert classify_probe_failure(result) == expected


async def test_probe_messages_network_error(probe_client: httpx.AsyncClient) -> None:
    with capture_http() as router:
        router.post("https://api.example.com/v1/messages").mock(side_effect=httpx.ConnectError("connection refused"))
        result = await probe_messages(
            base_url="https://api.example.com",
            api_key="sk",
            model="x",
            http_client=probe_client,
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
    p = ProbeResult(success=False, status_code=None, latency_ms=10, error="connection refused")
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


async def test_run_test_probes_only_messages_endpoint(probe_client: httpx.AsyncClient) -> None:
    """Connection tests hit only the Agent’s real messages endpoint, without /v1/models or /anthropic retries."""
    with capture_http() as router:
        messages = router.post("https://api.deepseek.com/v1/messages").mock(
            return_value=httpx.Response(404, text="not found")
        )
        models = router.get("https://api.deepseek.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        suffixed = router.post("https://api.deepseek.com/anthropic/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )

        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.deepseek.com",
            api_key="sk",
            model=None,
            http_client=probe_client,
        )

    assert messages.call_count == 1
    assert models.call_count == 0
    assert suffixed.call_count == 0
    assert resp.overall == "fail"
    assert resp.suggestion is None
    assert resp.messages_url == "https://api.deepseek.com/v1/messages"


async def test_run_test_preset_with_base_url_override_probes_stored_value(
    probe_client: httpx.AsyncClient,
) -> None:
    """Preset base_url overrides normalize complete endpoints to invoked values; probes use exactly those."""
    with capture_http() as router:
        messages = router.post("https://proxy.internal/anthropic/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )

        resp = await run_test(
            preset_id="deepseek",
            base_url="https://proxy.internal/anthropic/v1/messages",
            api_key="sk",
            model=None,
            http_client=probe_client,
        )

    assert resp.overall == "ok"
    assert messages.call_count == 1
    assert resp.messages_url == "https://proxy.internal/anthropic/v1/messages"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://relay.example.com/anthropic?api_key=sk-x",
        "https://x/a#frag",
        "https://u:p@x/a",
        "x.example.com",
    ],
)
async def test_run_test_rejects_unsupported_base_url(base_url: str) -> None:
    """Reject query/fragment/userinfo or missing-scheme URLs before probing; errors never echo input."""
    with pytest.raises(InvalidAnthropicBaseUrlError) as exc_info:
        await run_test(preset_id=CUSTOM_SENTINEL_ID, base_url=base_url, api_key="sk", model="m")
    assert "sk-x" not in str(exc_info.value)


async def test_run_test_custom_mode_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url required"):
        await run_test(preset_id=None, base_url=None, api_key="sk", model=None)


async def test_run_test_unknown_preset_raises() -> None:
    with pytest.raises(ValueError, match="unknown preset"):
        await run_test(preset_id="bogus-preset", base_url=None, api_key="sk", model=None)


def test_classify_probe_failure_400_missing_model_is_model_required() -> None:
    """Ark 400 MissingParameter for absent model must not become UNKNOWN."""
    p = ProbeResult(
        success=False,
        status_code=400,
        latency_ms=10,
        error='{"error":{"code":"MissingParameter","message":"missing `model` parameter"}}',
    )
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_REQUIRED


@pytest.mark.parametrize(
    "message",
    [
        "missing parameter model",
        "model is required",
        "the required field model was not provided",
    ],
)
def test_classify_probe_failure_400_missing_model_wording_variants(message: str) -> None:
    """Words between missing-parameter wording and model still classify as model_required."""
    p = ProbeResult(success=False, status_code=400, latency_ms=10, error=message)
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_REQUIRED


def test_classify_probe_failure_400_bad_model_is_model_not_found() -> None:
    p = ProbeResult(
        success=False,
        status_code=400,
        latency_ms=10,
        error='{"error":{"code":"InvalidParameter","message":"The model `nope` does not exist"}}',
    )
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_NOT_FOUND


def test_classify_probe_failure_400_without_model_keyword_stays_unknown() -> None:
    p = ProbeResult(success=False, status_code=400, latency_ms=10, error="malformed request body")
    assert classify_probe_failure(p) == DiagnosisCode.UNKNOWN


async def test_run_test_preset_without_model_short_circuits(probe_client: httpx.AsyncClient) -> None:
    """Empty preset default and user model sends no messages request, returning MODEL_REQUIRED directly."""
    preset = get_preset("anthropic-official")
    assert preset is not None

    with capture_http() as router:
        messages = router.post(f"{preset.messages_url}/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )

        resp = await run_test(
            preset_id="anthropic-official",
            base_url=None,
            api_key="sk",
            model=None,
            http_client=probe_client,
        )

    assert resp.overall == "fail"
    assert resp.diagnosis == DiagnosisCode.MODEL_REQUIRED
    assert resp.suggestion is not None
    assert resp.suggestion.kind == "run_discovery"
    assert resp.messages_probe.success is False
    assert resp.messages_probe.status_code is None
    assert resp.messages_url == f"{preset.messages_url}/v1/messages"
    assert messages.call_count == 0


async def test_run_test_ark_preset_uses_default_model(probe_client: httpx.AsyncClient) -> None:
    """Ark Agent Plan preset defaults work directly."""
    preset = get_preset("ark-agent-plan")
    assert preset is not None

    with capture_http() as router:
        messages = router.post(f"{preset.messages_url}/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )

        resp = await run_test(
            preset_id="ark-agent-plan",
            base_url=None,
            api_key="sk",
            model=None,
            http_client=probe_client,
        )

    assert resp.overall == "ok"
    assert request_json(only_request(messages))["model"] == preset.default_model


async def test_run_test_custom_mode_without_model_uses_fallback(probe_client: httpx.AsyncClient) -> None:
    """Custom mode bypasses empty-model short-circuiting and keeps its built-in fallback model."""
    with capture_http() as router:
        messages = router.post("https://api.example.com/anthropic/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )

        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.example.com/anthropic",
            api_key="sk",
            model=None,
            http_client=probe_client,
        )

    assert resp.overall == "ok"
    assert request_json(only_request(messages))["model"] == "claude-3-5-sonnet-20241022"


def test_classify_probe_failure_400_missing_other_param_is_not_model_required() -> None:
    """Missing-parameter errors for other fields must not implicate model."""
    p = ProbeResult(
        success=False,
        status_code=400,
        latency_ms=10,
        error='{"message":"missing `max_tokens`; requested model doubao-seed-evolving"}',
    )
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_NOT_FOUND
