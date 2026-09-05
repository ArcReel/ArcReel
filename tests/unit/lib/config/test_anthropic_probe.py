"""Anthropic probe 单元测试：respx 在 transport 层拦截，不打真实网络。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID, get_preset
from lib.config.anthropic_probe import (
    DiagnosisCode,
    ProbeResult,
    classify_probe_failure,
    probe_discovery,
    probe_messages,
    run_test,
)
from tests.http_capture import capture_http, only_request, request_json

_ANTHROPIC_OK = {"id": "msg_1", "type": "message", "content": [{"type": "text", "text": "ok"}]}


@pytest.fixture
async def probe_client() -> AsyncIterator[httpx.AsyncClient]:
    """真实 httpx 客户端；出站流量由 respx 在 transport 层接管。

    生产的共享单例要靠 lifespan 初始化，单测经 `http_client` seam 显式注入。
    """
    async with httpx.AsyncClient() as client:
        yield client


async def test_probe_messages_success(probe_client: httpx.AsyncClient) -> None:
    with capture_http() as router:
        route = router.post("https://api.example.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )
        result = await probe_messages(
            messages_root="https://api.example.com",
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
            messages_root="https://api.example.com",
            api_key="bad",
            model="claude-3-5-sonnet-20241022",
            http_client=probe_client,
        )

    assert result.success is False
    assert result.status_code == 401
    assert "authentication_error" in (result.error or "")


async def test_probe_messages_200_but_not_anthropic_marks_failure(probe_client: httpx.AsyncClient) -> None:
    """OpenAI 兼容协议响应：200 但缺 type=message 应判失败。"""
    with capture_http() as router:
        router.post("https://api.example.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"id": "chatcmpl-1", "object": "chat.completion", "choices": []})
        )
        result = await probe_messages(
            messages_root="https://api.example.com",
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
    """只有 ReadTimeout 证明服务可达；其余超时与网络不通同类。"""
    with capture_http() as router:
        router.post("https://api.example.com/v1/messages").mock(side_effect=exc)
        result = await probe_messages(
            messages_root="https://api.example.com",
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
            messages_root="https://api.example.com",
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


async def test_probe_discovery_none_root_returns_none() -> None:
    assert await probe_discovery(discovery_root=None, api_key="sk") is None


async def test_probe_discovery_success(probe_client: httpx.AsyncClient) -> None:
    with capture_http() as router:
        route = router.get("https://api.example.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "m"}]})
        )
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk", http_client=probe_client)

    assert result is not None
    assert result.success is True
    assert result.status_code == 200
    assert only_request(route).headers["x-api-key"] == "sk"


async def test_probe_discovery_non_2xx_marks_failure(probe_client: httpx.AsyncClient) -> None:
    with capture_http() as router:
        router.get("https://api.example.com/v1/models").mock(return_value=httpx.Response(404, text="not found"))
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk", http_client=probe_client)

    assert result is not None
    assert result.success is False
    assert result.status_code == 404
    assert "not found" in (result.error or "")


async def test_probe_discovery_network_error(probe_client: httpx.AsyncClient) -> None:
    with capture_http() as router:
        router.get("https://api.example.com/v1/models").mock(side_effect=httpx.ConnectError("dns fail"))
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk", http_client=probe_client)

    assert result is not None
    assert result.success is False
    assert result.status_code is None
    assert "dns fail" in (result.error or "").lower()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ReadTimeout("read timed out"), DiagnosisCode.TIMEOUT),
        (httpx.ConnectTimeout("connect timed out"), DiagnosisCode.NETWORK),
    ],
    ids=["read", "connect"],
)
async def test_probe_discovery_timeout_follows_messages_probe_rule(
    probe_client: httpx.AsyncClient, exc: httpx.TimeoutException, expected: DiagnosisCode
) -> None:
    with capture_http() as router:
        router.get("https://api.example.com/v1/models").mock(side_effect=exc)
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk", http_client=probe_client)

    assert result is not None
    assert result.success is False
    assert result.status_code is None
    assert classify_probe_failure(result) == expected


async def test_run_test_custom_mode_self_heals_with_anthropic_suffix(probe_client: httpx.AsyncClient) -> None:
    """用户填 https://api.deepseek.com，messages probe 失败 (404)；
    自动重试 https://api.deepseek.com/anthropic 成功 → suggestion 给出修复值。
    """
    with capture_http() as router:
        plain = router.post("https://api.deepseek.com/v1/messages").mock(
            return_value=httpx.Response(404, text="not found")
        )
        suffixed = router.post("https://api.deepseek.com/anthropic/v1/messages").mock(
            return_value=httpx.Response(200, json={"id": "msg_1", "type": "message", "content": []})
        )
        router.get("https://api.deepseek.com/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.deepseek.com",
            api_key="sk",
            model=None,
            http_client=probe_client,
        )

    assert resp.overall == "ok"
    assert resp.diagnosis == DiagnosisCode.MISSING_ANTHROPIC_SUFFIX
    assert resp.suggestion is not None
    assert resp.suggestion.kind == "replace_base_url"
    assert resp.suggestion.suggested_value == "https://api.deepseek.com/anthropic"
    assert plain.call_count == 1
    assert suffixed.call_count == 1


async def test_run_test_preset_skips_self_heal(probe_client: httpx.AsyncClient) -> None:
    """preset_id != __custom__ 时不做自愈尝试。"""
    preset = get_preset("anthropic-official")
    assert preset is not None
    root = preset.messages_url

    with capture_http() as router:
        router.post(f"{root}/v1/messages").mock(return_value=httpx.Response(404, text="not found"))
        suffixed = router.post(f"{root}/anthropic/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )
        router.get(f"{preset.discovery_url}/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

        resp = await run_test(
            preset_id="anthropic-official",
            base_url=None,
            api_key="sk",
            model="claude-3-5-sonnet-20241022",
            http_client=probe_client,
        )

    assert resp.overall == "fail"
    assert resp.suggestion is None
    assert suffixed.call_count == 0


async def test_run_test_self_heal_retry_also_fails_keeps_original_failure(probe_client: httpx.AsyncClient) -> None:
    """自愈重试也失败 (同 404) → suggestion=None，diagnosis=UNKNOWN。"""
    with capture_http() as router:
        plain = router.post("https://api.example.com/v1/messages").mock(
            return_value=httpx.Response(404, text="not found")
        )
        suffixed = router.post("https://api.example.com/anthropic/v1/messages").mock(
            return_value=httpx.Response(404, text="still not found")
        )
        router.get("https://api.example.com/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.example.com",
            api_key="sk",
            model=None,
            http_client=probe_client,
        )

    assert resp.overall == "fail"
    assert resp.suggestion is None
    assert resp.diagnosis == DiagnosisCode.UNKNOWN
    assert plain.call_count == 1
    assert suffixed.call_count == 1


async def test_run_test_self_heal_retry_promotes_specific_diagnosis(probe_client: httpx.AsyncClient) -> None:
    """重试失败但二次诊断更具体 (401) → 采纳 retry，让用户看到 AUTH_FAILED 而非 UNKNOWN。"""
    with capture_http() as router:
        router.post("https://api.example.com/v1/messages").mock(return_value=httpx.Response(404, text="not found"))
        router.post("https://api.example.com/anthropic/v1/messages").mock(
            return_value=httpx.Response(401, json={"error": {"type": "authentication_error"}})
        )
        router.get("https://api.example.com/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.example.com",
            api_key="bad",
            model=None,
            http_client=probe_client,
        )

    assert resp.overall == "fail"
    assert resp.diagnosis == DiagnosisCode.AUTH_FAILED
    assert resp.derived_messages_root.endswith("/anthropic")
    # retry 401 ≠ 缺后缀的诊断，不发 suggestion
    assert resp.suggestion is None


async def test_run_test_preset_with_base_url_override_derives_discovery(probe_client: httpx.AsyncClient) -> None:
    """preset 凭证覆盖 base_url → discovery 也从 base_url 派生，与运行时一致。"""
    with capture_http() as router:
        messages = router.post("https://corp-proxy.example.com/anthropic/v1/messages").mock(
            return_value=httpx.Response(200, json={"id": "msg_1", "type": "message", "content": []})
        )
        # discovery 也从 base_url 派生（剥掉 /anthropic）
        discovery = router.get("https://corp-proxy.example.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )

        resp = await run_test(
            preset_id="deepseek",
            base_url="https://corp-proxy.example.com/anthropic",
            api_key="sk",
            model=None,
            http_client=probe_client,
        )

    assert resp.overall == "ok"
    assert messages.call_count == 1
    assert discovery.call_count == 1
    assert resp.derived_discovery_root == "https://corp-proxy.example.com"


async def test_run_test_custom_mode_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url required"):
        await run_test(preset_id=None, base_url=None, api_key="sk", model=None)


async def test_run_test_unknown_preset_raises() -> None:
    with pytest.raises(ValueError, match="unknown preset"):
        await run_test(preset_id="bogus-preset", base_url=None, api_key="sk", model=None)


def test_classify_probe_failure_400_missing_model_is_model_required() -> None:
    """火山方舟缺 model 参数时返回 400 MissingParameter，不该落到 UNKNOWN。"""
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
    """缺参措辞与 model 之间夹着单词时同样归为 model_required。"""
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


async def test_probe_discovery_retries_with_bearer_after_401(probe_client: httpx.AsyncClient) -> None:
    """只认 Authorization 的网关（火山方舟）：x-api-key 拿 401 后换 Bearer 重试。"""
    with capture_http() as router:
        route = router.get("https://ark.example.com/v1/models").mock(
            side_effect=[
                httpx.Response(401, text="unauthorized"),
                httpx.Response(200, json={"data": [{"id": "m"}]}),
            ]
        )
        result = await probe_discovery(discovery_root="https://ark.example.com", api_key="sk", http_client=probe_client)

    assert result is not None
    assert result.success is True
    assert result.status_code == 200
    assert route.call_count == 2
    assert route.calls[0].request.headers["x-api-key"] == "sk"
    assert "x-api-key" not in route.calls[1].request.headers
    assert route.calls[1].request.headers["authorization"] == "Bearer sk"


async def test_probe_discovery_bearer_retry_still_401_marks_failure(probe_client: httpx.AsyncClient) -> None:
    with capture_http() as router:
        route = router.get("https://ark.example.com/v1/models").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        result = await probe_discovery(
            discovery_root="https://ark.example.com", api_key="bad", http_client=probe_client
        )

    assert result is not None
    assert result.success is False
    assert result.status_code == 401
    assert route.call_count == 2


async def test_run_test_preset_without_model_short_circuits(probe_client: httpx.AsyncClient) -> None:
    """预设默认模型为空且用户未填：不发 messages 请求，直接给 MODEL_REQUIRED。"""
    preset = get_preset("anthropic-official")
    assert preset is not None

    with capture_http() as router:
        messages = router.post(f"{preset.messages_url}/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )
        discovery = router.get(f"{preset.discovery_url}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
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
    assert resp.discovery_probe is None
    assert messages.call_count == 0
    assert discovery.call_count == 0


async def test_run_test_ark_preset_uses_default_model_and_skips_discovery(
    probe_client: httpx.AsyncClient,
) -> None:
    """火山方舟 Agent Plan：预设默认模型直接可用，discovery 关闭不拖成 warn。"""
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
    assert resp.discovery_probe is None
    assert request_json(only_request(messages))["model"] == preset.default_model


async def test_run_test_custom_mode_without_model_uses_fallback(probe_client: httpx.AsyncClient) -> None:
    """自定义模式不受空模型短路影响，仍用内置兜底模型。"""
    with capture_http() as router:
        messages = router.post("https://api.example.com/anthropic/v1/messages").mock(
            return_value=httpx.Response(200, json=_ANTHROPIC_OK)
        )
        router.get("https://api.example.com/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

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
    """缺参提示指向别的字段时不连坐到 model。"""
    p = ProbeResult(
        success=False,
        status_code=400,
        latency_ms=10,
        error='{"message":"missing `max_tokens`; requested model doubao-seed-evolving"}',
    )
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_NOT_FOUND
