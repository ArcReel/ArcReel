"""Exercise the local director bridge at its SDK and outbound HTTP boundaries."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from claude_agent_sdk import ClaudeAgentOptions
from fastapi import FastAPI

from lib.infra.httpx_shared import get_http_client
from server.agent_runtime.openai_transport import (
    BRIDGE_PREFIX,
    OPENAI_RESPONSES_URL,
    OpenAISessionRegistry,
    agent_client_session,
    get_openai_session_registry,
    router,
)
from tests.fakes import FakeSDKClient
from tests.http_capture import capture_http, only_request, request_json

_MODEL = "gpt-5.4"
_KEY = "sk-test-director-server-only"
_MESSAGES = f"{BRIDGE_PREFIX}/v1/messages"
_COUNT_TOKENS = f"{_MESSAGES}/count_tokens"


@pytest.fixture
def director_registry() -> OpenAISessionRegistry:
    return OpenAISessionRegistry()


@pytest.fixture
async def director_app(director_registry: OpenAISessionRegistry) -> AsyncIterator[FastAPI]:
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(trust_env=False) as upstream:
        app.dependency_overrides[get_openai_session_registry] = lambda: director_registry
        app.dependency_overrides[get_http_client] = lambda: upstream
        yield app


@pytest.fixture
async def director_client(director_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=director_app, client=("127.0.0.1", 54321))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        yield client


def _message(**extra: Any) -> dict[str, Any]:
    return {
        "model": _MODEL,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "Outline a scene."}],
        **extra,
    }


def _options(**env: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        env={
            "ANTHROPIC_BASE_URL": "https://api.openai.com",
            "ANTHROPIC_API_KEY": _KEY,
            "ANTHROPIC_AUTH_TOKEN": "old-auth-token",
            "ANTHROPIC_MODEL": _MODEL,
            "ARCREEL_API_BASE": "http://127.0.0.1:1241/api/v1",
            **env,
        }
    )


def _response(output: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": "resp_director",
        "model": _MODEL,
        "status": "completed",
        "output": output
        or [
            {
                "id": "msg_scene",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "A quiet opening.", "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 30, "input_tokens_details": {"cached_tokens": 10}, "output_tokens": 8},
    }


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)


def _events(response: httpx.Response) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


class _StalledOpenAIStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.receiving = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.closed = asyncio.Event()
        self._never = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            yield _sse({"type": "response.created", "response": {"id": "resp_director"}})
            self.receiving.set()
            await self._never.wait()
        finally:
            self.cancelled.set()

    async def aclose(self) -> None:
        self.closed.set()


async def _asgi_request(
    app: FastAPI,
    path: str,
    token: str,
    incoming: asyncio.Queue,
    sent: list[dict[str, Any]],
    blocked_send: tuple[asyncio.Event, asyncio.Event] | None = None,
) -> None:
    async def send(event: dict[str, Any]) -> None:
        sent.append(event)
        if blocked_send is not None and event.get("body"):
            blocked_send[0].set()
            await blocked_send[1].wait()

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"x-api-key", token.encode()), (b"content-type", b"application/json")],
            "server": ("127.0.0.1", 1241),
            "client": ("127.0.0.1", 54321),
        },
        incoming.get,
        send,
    )


@pytest.mark.parametrize("path", [_MESSAGES, _COUNT_TOKENS])
@pytest.mark.parametrize("access", ["missing", "unknown", "revoked", "lan"])
async def test_unauthorized_requests_never_reach_openai(director_app, director_registry, path, access):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    if access == "revoked":
        director_registry.revoke(token)
    headers = {} if access == "missing" else {"x-api-key": "unknown-token" if access == "unknown" else token}
    host = "192.168.1.20" if access == "lan" else "127.0.0.1"
    transport = httpx.ASGITransport(app=director_app, client=(host, 54321))
    with capture_http() as upstream:
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await client.post(path, headers=headers, json=_message())
        assert response.status_code == (403 if access == "lan" else 401)
        assert not upstream.calls


@pytest.mark.parametrize("path", [_MESSAGES, _COUNT_TOKENS])
async def test_session_cannot_choose_an_unconfigured_model(director_client, director_registry, path):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    with capture_http() as upstream:
        response = await director_client.post(path, headers={"x-api-key": token}, json=_message(model="other-model"))
        assert response.status_code == 400
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert not upstream.calls


async def test_session_snapshots_key_and_sends_only_capability_to_sdk(director_client, director_registry):
    options = _options()
    sdk = FakeSDKClient()
    received: list[ClaudeAgentOptions] = []

    def factory(*, options: ClaudeAgentOptions) -> FakeSDKClient:
        received.append(options)
        return sdk

    with capture_http(assert_all_called=True) as upstream:
        route = upstream.post(OPENAI_RESPONSES_URL).respond(json=_response())
        async with agent_client_session(options, client_factory=factory, registry=director_registry) as client:
            assert client is sdk
            cli_options = received[0]
            token = cli_options.env["ANTHROPIC_API_KEY"]
            assert token != _KEY
            assert _KEY not in cli_options.env.values()
            assert cli_options.env["ANTHROPIC_AUTH_TOKEN"] == ""
            assert cli_options.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
            assert cli_options.permission_mode == "default"
            assert cli_options.env["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:1241{BRIDGE_PREFIX}"
            assert options.env["ANTHROPIC_BASE_URL"] == "https://api.openai.com"
            options.env["ANTHROPIC_API_KEY"] = "sk-test-replaced-during-session"
            response = await director_client.post(_MESSAGES, headers={"x-api-key": token}, json=_message())
            assert response.status_code == 200
            assert response.json()["content"][0] == {"type": "text", "text": "A quiet opening."}
            assert response.json()["stop_reason"] == "end_turn"
            assert response.json()["usage"]["input_tokens"] == 20
            assert only_request(route).headers["authorization"] == f"Bearer {_KEY}"
            assert request_json(only_request(route))["store"] is False
    assert director_registry.resolve(token) is None
    assert sdk.disconnected


async def test_startup_failure_revokes_sdk_capability(director_registry):
    received: list[ClaudeAgentOptions] = []

    def factory(*, options: ClaudeAgentOptions) -> FakeSDKClient:
        received.append(options)
        return FakeSDKClient(connect_error=RuntimeError("CLI startup failed"))

    with pytest.raises(RuntimeError, match="CLI startup failed"):
        async with agent_client_session(_options(), client_factory=factory, registry=director_registry):
            pytest.fail("Failed startup must not enter the SDK context")
    assert director_registry.resolve(received[0].env["ANTHROPIC_API_KEY"]) is None


async def test_session_cancellation_disconnects_sdk_and_revokes_capability(director_registry):
    entered = asyncio.Event()
    never = asyncio.Event()
    received: list[ClaudeAgentOptions] = []
    sdk = FakeSDKClient()

    def factory(*, options: ClaudeAgentOptions) -> FakeSDKClient:
        received.append(options)
        return sdk

    async def run_session() -> None:
        async with agent_client_session(_options(), client_factory=factory, registry=director_registry):
            entered.set()
            await never.wait()

    task = asyncio.create_task(run_session())
    try:
        await entered.wait()
        token = received[0].env["ANTHROPIC_API_KEY"]
        assert director_registry.resolve(token) is not None
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert director_registry.resolve(token) is None
    assert sdk.disconnected


@pytest.mark.parametrize("base_url", ["https://api.anthropic.com", "https://router.example/anthropic"])
async def test_non_openai_sessions_preserve_existing_sdk_options(director_registry, base_url):
    original = _options(ANTHROPIC_BASE_URL=base_url)
    received: list[ClaudeAgentOptions] = []
    sdk = FakeSDKClient()

    def factory(*, options: ClaudeAgentOptions) -> FakeSDKClient:
        received.append(options)
        return sdk

    async with agent_client_session(original, client_factory=factory, registry=director_registry) as client:
        assert client is sdk
        assert received[0] is original
        assert received[0].env["ANTHROPIC_API_KEY"] == _KEY
        assert received[0].env["ANTHROPIC_BASE_URL"] == base_url
    assert sdk.disconnected


@pytest.mark.parametrize("api_base", ["http://192.168.1.20:1241/api/v1", "https://relay.example/api/v1"])
async def test_direct_session_refuses_non_loopback_bridge(api_base, director_registry):
    sdk = FakeSDKClient()
    with pytest.raises(ValueError, match="loopback"):
        async with agent_client_session(
            _options(ARCREEL_API_BASE=api_base), client_factory=lambda **_: sdk, registry=director_registry
        ):
            pytest.fail("A remote bridge must not receive a session capability")


async def test_streamed_tool_call_uses_messages_events(director_client, director_registry):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    tool = {"name": "Read", "description": "Read a project file", "input_schema": {"type": "object"}}
    item = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "Read",
        "arguments": '{"path":"scene.md"}',
        "status": "completed",
    }
    stream = _sse(
        {"type": "response.created", "response": {"id": "resp_director"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"path":'},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '"scene.md"}'},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": _response([item])},
    )
    with capture_http(assert_all_called=True) as upstream:
        route = upstream.post(OPENAI_RESPONSES_URL).respond(
            content=stream, headers={"content-type": "text/event-stream"}
        )
        response = await director_client.post(
            _MESSAGES, headers={"x-api-key": token}, json=_message(stream=True, tools=[tool])
        )
        payload = request_json(only_request(route))
        assert payload["stream"] is True
        assert payload["tools"][0]["type"] == "function"
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert events[0]["type"] == "message_start"
    tool_start = next(event for event in events if event.get("content_block", {}).get("type") == "tool_use")
    assert tool_start["content_block"] == {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}}
    arguments = "".join(
        event["delta"]["partial_json"] for event in events if event.get("delta", {}).get("type") == "input_json_delta"
    )
    assert json.loads(arguments) == {"path": "scene.md"}
    assert events[-2]["delta"]["stop_reason"] == "tool_use"
    assert events[-2]["usage"]["cache_read_input_tokens"] == 10
    assert events[-1] == {"type": "message_stop"}


@pytest.mark.parametrize("failure", ["unauthorized", "malformed", "truncated", "provider_failure"])
async def test_stream_failure_is_sanitized_and_never_looks_complete(director_client, director_registry, failure):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    secret = f"Upstream leaked {_KEY} and private prompt"
    status = 401 if failure == "unauthorized" else 200
    if failure == "malformed":
        content = b"data: {not valid JSON}\n\n"
    elif failure == "truncated":
        content = _sse({"type": "response.created", "response": {"id": "resp_director"}})
    else:
        content = _sse({"type": "response.failed", "response": {"error": {"message": secret}}})
    with capture_http(assert_all_called=True) as upstream:
        upstream.post(OPENAI_RESPONSES_URL).respond(status, content=content)
        response = await director_client.post(_MESSAGES, headers={"x-api-key": token}, json=_message(stream=True))
    events = _events(response)
    assert events[-1]["type"] == "error"
    assert all(event["type"] != "message_stop" for event in events)
    assert secret not in response.text
    assert _KEY not in response.text


async def test_downstream_cancellation_closes_inflight_openai_response(director_client, director_registry):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    stream = _StalledOpenAIStream()
    with capture_http(assert_all_called=True) as upstream:
        upstream.post(OPENAI_RESPONSES_URL).mock(return_value=httpx.Response(200, stream=stream))
        task = asyncio.create_task(
            director_client.post(_MESSAGES, headers={"x-api-key": token}, json=_message(stream=True))
        )
        try:
            await stream.receiving.wait()
            assert not stream.closed.is_set()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert stream.cancelled.is_set()
    assert stream.closed.is_set()


@pytest.mark.parametrize(("path", "streaming"), [(_MESSAGES, False), (_MESSAGES, True), (_COUNT_TOKENS, False)])
@pytest.mark.parametrize("closure", ["disconnect", "revoke"])
async def test_connection_closure_aborts_upstream_without_leaking_tasks(
    director_app, director_registry, path, streaming, closure
):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    stream = _StalledOpenAIStream()
    endpoint = OPENAI_RESPONSES_URL if path == _MESSAGES else f"{OPENAI_RESPONSES_URL}/input_tokens"
    incoming: asyncio.Queue = asyncio.Queue()
    incoming.put_nowait(
        {"type": "http.request", "body": json.dumps(_message(stream=streaming)).encode(), "more_body": False}
    )
    sent: list[dict[str, Any]] = []
    existing_tasks = asyncio.all_tasks()
    with capture_http(assert_all_called=True) as upstream:
        upstream.post(endpoint).mock(return_value=httpx.Response(200, stream=stream))
        task = asyncio.create_task(_asgi_request(director_app, path, token, incoming, sent))
        try:
            await asyncio.wait_for(stream.receiving.wait(), timeout=2)
            if closure == "disconnect":
                # Match a real ASGI server's socket-close notification. Do not
                # cancel the handler: nonstream handlers must observe it too.
                incoming.put_nowait({"type": "http.disconnect"})
            else:
                director_registry.revoke(token)
            await asyncio.wait_for(task, timeout=2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert stream.cancelled.is_set()
    assert stream.closed.is_set()
    assert not (asyncio.all_tasks() - existing_tasks)
    if not streaming:
        assert sent[0]["status"] == (499 if closure == "disconnect" else 401)
    elif closure == "revoke":
        body = b"".join(event.get("body", b"") for event in sent)
        events = _events(httpx.Response(200, content=body))
        assert events[-1]["type"] == "error"
        assert all(event["type"] != "message_stop" for event in events)


@pytest.mark.parametrize("streaming", [False, True])
async def test_revocation_cancels_connection_before_openai_sends_headers(director_client, director_registry, streaming):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    connecting = asyncio.Event()
    cancelled = asyncio.Event()

    async def connect(_request: httpx.Request) -> httpx.Response:
        connecting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("A revoked request must not finish connecting")

    existing_tasks = asyncio.all_tasks()
    # RESPX records a call only after its handler returns; this handler is
    # deliberately cancelled before headers, so connecting is our call proof.
    with capture_http() as upstream:
        upstream.post(OPENAI_RESPONSES_URL).mock(side_effect=connect)
        task = asyncio.create_task(
            director_client.post(_MESSAGES, headers={"x-api-key": token}, json=_message(stream=streaming))
        )
        try:
            await asyncio.wait_for(connecting.wait(), timeout=2)
            director_registry.revoke(token)
            response = await asyncio.wait_for(task, timeout=2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert cancelled.is_set()
    assert not (asyncio.all_tasks() - existing_tasks)
    assert response.status_code == (200 if streaming else 401)


async def test_revocation_closes_stream_while_downstream_send_is_blocked(director_app, director_registry):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    stream = _StalledOpenAIStream()
    incoming: asyncio.Queue = asyncio.Queue()
    incoming.put_nowait(
        {"type": "http.request", "body": json.dumps(_message(stream=True)).encode(), "more_body": False}
    )
    sending, release = asyncio.Event(), asyncio.Event()
    existing_tasks = asyncio.all_tasks()
    with capture_http(assert_all_called=True) as upstream:
        upstream.post(OPENAI_RESPONSES_URL).mock(return_value=httpx.Response(200, stream=stream))
        task = asyncio.create_task(_asgi_request(director_app, _MESSAGES, token, incoming, [], (sending, release)))
        try:
            await asyncio.wait_for(sending.wait(), timeout=2)
            director_registry.revoke(token)
            await asyncio.wait_for(stream.closed.wait(), timeout=2)
            release.set()
            await asyncio.wait_for(task, timeout=2)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    # HTTPX closes nested async iterators on subsequent loop turns. Require
    # them to finish naturally; an orphaned watcher would time out here.
    async with asyncio.timeout(1):
        while remaining := asyncio.all_tasks() - existing_tasks:
            await asyncio.gather(*remaining)
    assert not (asyncio.all_tasks() - existing_tasks)


async def test_token_count_sends_converted_input_without_generation_options(director_client, director_registry):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    body = _message(system="Use the project notes.", stream=True, temperature=0.5, thinking={"type": "adaptive"})
    with capture_http(assert_all_called=True) as upstream:
        route = upstream.post(f"{OPENAI_RESPONSES_URL}/input_tokens").respond(json={"input_tokens": 37})
        response = await director_client.post(_COUNT_TOKENS, headers={"x-api-key": token}, json=body)
        request = only_request(route)
        assert request.headers["authorization"] == f"Bearer {_KEY}"
        assert request_json(request) == {
            "model": _MODEL,
            "instructions": "Use the project notes.",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Outline a scene."}]}
            ],
            "reasoning": {"effort": "medium"},
        }
    assert response.status_code == 200
    assert response.json() == {"input_tokens": 37}


@pytest.mark.parametrize("path", [_MESSAGES, _COUNT_TOKENS])
async def test_upstream_authentication_error_never_echoes_credentials(director_client, director_registry, path):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    endpoint = OPENAI_RESPONSES_URL if path == _MESSAGES else f"{OPENAI_RESPONSES_URL}/input_tokens"
    with capture_http(assert_all_called=True) as upstream:
        upstream.post(endpoint).respond(401, json={"error": {"message": f"Incorrect API key {_KEY}: private prompt"}})
        response = await director_client.post(path, headers={"x-api-key": token}, json=_message())
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"
    assert _KEY not in response.text
    assert "private prompt" not in response.text


@pytest.mark.parametrize(("path", "stream"), [(_MESSAGES, False), (_MESSAGES, True), (_COUNT_TOKENS, False)])
@pytest.mark.parametrize("safe_identifiers", [True, False])
async def test_bad_request_reports_only_allowed_diagnostics(
    director_client, director_registry, path, stream, safe_identifiers
):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    endpoint = OPENAI_RESPONSES_URL if path == _MESSAGES else f"{OPENAI_RESPONSES_URL}/input_tokens"
    secret = f"{_KEY} private prompt"
    request_id = "req_" + "a" * 32
    with capture_http(assert_all_called=True) as upstream:
        upstream.post(endpoint).respond(
            400,
            headers={"x-request-id": request_id if safe_identifiers else secret},
            json={
                "error": {
                    "message": secret,
                    "code": "invalid_value" if safe_identifiers else secret,
                    "param": "input[0].content[0]" if safe_identifiers else secret,
                }
            },
        )
        response = await director_client.post(path, headers={"x-api-key": token}, json=_message(stream=stream))
    assert response.status_code == (200 if stream else 400)
    error = _events(response)[-1]["error"] if stream else response.json()["error"]
    assert error["type"] == "api_error"
    assert "HTTP 400" in error["message"]
    assert ("code=invalid_value" in error["message"]) is safe_identifiers
    assert ("parameter=input[0].content[0]" in error["message"]) is safe_identifiers
    assert ("role=user, content_type=input_text" in error["message"]) is safe_identifiers
    assert "Outline a scene" not in error["message"]
    assert (request_id in error["message"]) is safe_identifiers
    assert _KEY not in response.text
    assert "private prompt" not in response.text


@pytest.mark.parametrize("content", [b"not json", b"[]", b"x" * 65537])
async def test_unstructured_upstream_error_retains_safe_status(director_client, director_registry, content):
    token = director_registry.register(_KEY, frozenset({_MODEL}))
    with capture_http(assert_all_called=True) as upstream:
        upstream.post(OPENAI_RESPONSES_URL).respond(400, content=content)
        response = await director_client.post(_MESSAGES, headers={"x-api-key": token}, json=_message(stream=True))
    error = _events(response)[-1]["error"]
    assert error["type"] == "api_error"
    assert "HTTP 400" in error["message"]
