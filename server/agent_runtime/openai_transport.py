"""Direct OpenAI inference for the existing embedded director runtime.

The CLI receives a revocable, session-scoped capability, never the OpenAI key.
Only this process sends requests to the fixed official Responses endpoint.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, aclosing, asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from claude_agent_sdk import ClaudeAgentOptions
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from lib.agent.openai_messages import (
    ConversionError,
    ResponsesStreamTranslator,
    from_responses_response,
    to_responses_request,
)
from lib.config.url_utils import is_direct_openai_agent_base_url
from lib.infra.httpx_shared import get_http_client

BRIDGE_PREFIX = "/internal/openai-agent"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
_MAX_BODY_BYTES = 32 * 1024 * 1024
_MAX_EVENT_BYTES = 32 * 1024 * 1024
_TIMEOUT = httpx.Timeout(600, connect=30, pool=30)
_MODEL_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


@dataclass(frozen=True)
class OpenAISessionCredential:
    api_key: str = field(repr=False)
    models: frozenset[str]
    revoked: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)


class OpenAISessionRegistry:
    """Capabilities live exactly as long as their owning SDK connection."""

    def __init__(self) -> None:
        self._credentials: dict[str, OpenAISessionCredential] = {}

    def register(self, api_key: str, models: frozenset[str]) -> str:
        if not api_key or not models:
            raise ValueError("Configure an OpenAI director key and model before starting a session.")
        token = secrets.token_urlsafe(32)
        self._credentials[token] = OpenAISessionCredential(api_key, models)
        return token

    def resolve(self, token: str) -> OpenAISessionCredential | None:
        return self._credentials.get(token)

    def revoke(self, token: str) -> None:
        credential = self._credentials.pop(token, None)
        if credential is not None:
            credential.revoked.set()


_session_registry = OpenAISessionRegistry()


def get_openai_session_registry() -> OpenAISessionRegistry:
    return _session_registry


def _bridge_base_url(api_base: str) -> str:
    parsed = urlsplit(api_base)
    if (
        parsed.scheme not in ("http", "https")
        or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("The OpenAI director requires a loopback ARCREEL_API_BASE for its local connection.")
    return urlunsplit((parsed.scheme, parsed.netloc, BRIDGE_PREFIX, "", ""))


@asynccontextmanager
async def agent_client_session(
    options: ClaudeAgentOptions,
    *,
    client_factory: Callable[..., AbstractAsyncContextManager[Any]],
    registry: OpenAISessionRegistry | None = None,
) -> AsyncGenerator[Any]:
    """Keep SDK tools, permissions and transcripts; replace only model HTTP."""
    if not is_direct_openai_agent_base_url(options.env.get("ANTHROPIC_BASE_URL", "")):
        async with client_factory(options=options) as client:
            yield client
        return

    registry = registry if registry is not None else get_openai_session_registry()
    env = dict(options.env)
    base_url = _bridge_base_url(env.get("ARCREEL_API_BASE", "http://127.0.0.1:1241/api/v1"))
    models = frozenset(env[key] for key in _MODEL_KEYS if env.get(key))
    token = registry.register(env.get("ANTHROPIC_API_KEY", ""), models)
    env.update(
        ANTHROPIC_BASE_URL=base_url,
        ANTHROPIC_API_KEY=token,
        ANTHROPIC_AUTH_TOKEN="",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
    )
    try:
        # Auto mode depends on Anthropic's server-side tool-risk classifier.
        # Keep the SDK's normal permission checks and ArcReel's hooks instead.
        async with client_factory(options=replace(options, env=env, permission_mode="default")) as client:
            yield client
    finally:
        registry.revoke(token)


def _error(message: str, *, error_type: str = "invalid_request_error") -> dict[str, Any]:
    return {"type": "error", "error": {"type": error_type, "message": message}}


async def _upstream_error(response: httpx.Response, payload: dict[str, Any]) -> dict[str, Any]:
    status = response.status_code
    if status in (401, 403):
        return _error(
            "OpenAI rejected the director credential. Check its API access.", error_type="authentication_error"
        )
    if status == 429:
        return _error("OpenAI rate or credit limit reached. Check the account limits.", error_type="rate_limit_error")
    # Never relay the upstream message: it may echo credentials or prompt text.
    # These identifiers identify the failure without exposing request content.
    diagnostics = []
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > 64 * 1024:
            break
    try:
        body = json.loads(raw) if len(raw) <= 64 * 1024 else None
    except (ValueError, UnicodeDecodeError):
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        if code in (
            "context_length_exceeded",
            "invalid_encrypted_content",
            "invalid_image",
            "invalid_image_format",
            "invalid_value",
            "invalid_request",
            "invalid_request_error",
            "invalid_request_body",
            "invalid_json_schema",
            "missing_required_parameter",
            "unsupported_parameter",
            "unsupported_value",
            "model_not_found",
        ):
            diagnostics.append(f"code={code}")
        param = error.get("param")
        if isinstance(param, str) and re.fullmatch(
            r"(?:model|input|tools|instructions|reasoning|text|temperature|top_p|max_output_tokens)"
            r"(?:\[\d+\]|\.(?:content|type|id|call_id|name|arguments|output|image_url|detail|"
            r"encrypted_content|summary|format|schema|parameters|properties|effort))*",
            param,
        ):
            diagnostics.append(f"parameter={param}")
            location = re.fullmatch(r"input\[(\d+)\]\.content\[(\d+)\]", param)
            if location:
                item_index, part_index = map(int, location.groups())
                items = payload.get("input", [])
                if item_index < len(items):
                    item = items[item_index]
                    parts = item.get("content", [])
                    role = item.get("role")
                    if part_index < len(parts) and role in {"user", "assistant", "system", "developer"}:
                        kind = parts[part_index].get("type")
                        if kind in {"input_text", "output_text", "input_image", "refusal"}:
                            diagnostics.append(f"role={role}, content_type={kind}")
    request_id = response.headers.get("x-request-id", "")
    if re.fullmatch(r"req_[a-f0-9]{32}", request_id):
        diagnostics.append(f"request={request_id}")
    suffix = f" ({', '.join(diagnostics)})" if diagnostics else ""
    return _error(
        f"OpenAI rejected the director request (HTTP {status}){suffix}. Check the selected model and input.",
        error_type="api_error",
    )


async def _request_body(request: Request) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_BODY_BYTES:
            raise HTTPException(413, "Director request is too large.")
        chunks.append(chunk)
    try:
        body = await asyncio.to_thread(json.loads, b"".join(chunks))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Invalid director request JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "Director request must be an object.")
    return body


def _credential(request: Request, registry: OpenAISessionRegistry) -> OpenAISessionCredential:
    # A public/LAN app listener must not turn this internal transport into a relay.
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "The director transport is local only.")
    token = request.headers.get("x-api-key", "")
    credential = registry.resolve(token)
    if credential is None:
        raise HTTPException(401, "Director session is closed or unauthorized.")
    return credential


async def _disconnected(request: Request) -> None:
    # The body has already been consumed. Await the ASGI disconnect event,
    # rather than assuming a closed socket cancels a nonstream route handler.
    while (await request.receive())["type"] != "http.disconnect":
        pass


async def _while_open[T](
    operation: Callable[[], Awaitable[T]], credential: OpenAISessionCredential, request: Request | None = None
) -> T:
    if credential.revoked.is_set():
        raise HTTPException(401, "Director session is closed or unauthorized.")
    upstream = asyncio.ensure_future(operation())
    revoked = asyncio.create_task(credential.revoked.wait())
    disconnected = asyncio.create_task(_disconnected(request)) if request is not None else None
    tasks = [upstream, revoked, *([disconnected] if disconnected is not None else [])]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if credential.revoked.is_set():
            raise HTTPException(401, "Director session is closed or unauthorized.")
        if disconnected is not None and disconnected.done():
            await disconnected
            raise HTTPException(499, "Director client disconnected.")
        return await upstream
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _upstream_events(response: httpx.Response) -> AsyncGenerator[dict[str, Any]]:
    data: list[str] = []
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if data:
                raw = "\n".join(data)
                if raw != "[DONE]":
                    event = await asyncio.to_thread(json.loads, raw)
                    if not isinstance(event, dict):
                        raise ConversionError("Invalid OpenAI stream event.")
                    yield event
                data, size = [], 0
        elif line.startswith("data:"):
            part = line[5:].lstrip(" ")
            size += len(part.encode("utf-8"))
            if size > _MAX_EVENT_BYTES:
                raise ConversionError("OpenAI stream event is too large.")
            data.append(part)
    if data:
        raise ConversionError("OpenAI stream ended inside an event.")


def _sse(event: dict[str, Any]) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


async def _close_revoked_response(response: httpx.Response, credential: OpenAISessionCredential) -> None:
    await credential.revoked.wait()
    await response.aclose()


async def _openai_stream(
    client: httpx.AsyncClient, payload: dict[str, Any], credential: OpenAISessionCredential, names: dict[str, str]
) -> AsyncGenerator[bytes]:
    translator = ResponsesStreamTranslator(names, str(payload["model"]))
    try:
        async with client.stream(
            "POST",
            OPENAI_RESPONSES_URL,
            json=payload,
            headers={"Authorization": f"Bearer {credential.api_key}"},
            timeout=_TIMEOUT,
        ) as response:
            # Close even when the generator is suspended while sending a chunk
            # to a slow downstream client, with no upstream read to cancel yet.
            close_revoked = asyncio.create_task(_close_revoked_response(response, credential))
            try:
                if response.is_error:
                    yield _sse(await _upstream_error(response, payload))
                    return
                async with aclosing(_upstream_events(response)) as events:
                    async for event in events:
                        for translated in translator.feed(event):
                            yield _sse(translated)
                for translated in translator.finish():
                    yield _sse(translated)
            finally:
                close_revoked.cancel()
                await asyncio.gather(close_revoked, return_exceptions=True)
    except (httpx.HTTPError, ValueError):
        # Do not relay upstream bodies: they may echo prompts or credentials.
        yield _sse(
            _error("The OpenAI director stream failed. Resume the saved session to retry.", error_type="api_error")
        )


async def _stream(
    client: httpx.AsyncClient, payload: dict[str, Any], credential: OpenAISessionCredential, names: dict[str, str]
) -> AsyncIterator[bytes]:
    # StreamingResponse owns downstream disconnects. Race each upstream read
    # against session revocation, including the initial HTTP connection.
    async with aclosing(_openai_stream(client, payload, credential, names)) as stream:
        while True:
            try:
                yield await _while_open(lambda: anext(stream), credential)
            except StopAsyncIteration:
                return
            except HTTPException:
                yield _sse(_error("Director session is closed or unauthorized.", error_type="authentication_error"))
                return


router = APIRouter(prefix=BRIDGE_PREFIX, include_in_schema=False)


@router.post("/v1/messages", response_model=None)
async def messages(
    request: Request,
    registry: OpenAISessionRegistry = Depends(get_openai_session_registry),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> JSONResponse | StreamingResponse:
    credential = _credential(request, registry)
    body = await _request_body(request)
    if body.get("model") not in credential.models:
        return JSONResponse(_error("This model is not configured for the director session."), status_code=400)
    try:
        payload, names = await asyncio.to_thread(to_responses_request, body)
    except (ConversionError, TypeError, KeyError) as exc:
        return JSONResponse(_error(str(exc)), status_code=400)
    if body.get("stream"):
        payload["stream"] = True
        return StreamingResponse(_stream(client, payload, credential, names), media_type="text/event-stream")
    try:
        response = await _while_open(
            lambda: client.post(
                OPENAI_RESPONSES_URL,
                json=payload,
                headers={"Authorization": f"Bearer {credential.api_key}"},
                timeout=_TIMEOUT,
            ),
            credential,
            request,
        )
        if response.is_error:
            return JSONResponse(await _upstream_error(response, payload), status_code=response.status_code)
        result = await asyncio.to_thread(from_responses_response, response.json(), names, model=str(payload["model"]))
        return JSONResponse(result, status_code=502 if result.get("type") == "error" else 200)
    except (httpx.HTTPError, ValueError):
        return JSONResponse(_error("The OpenAI director request failed.", error_type="api_error"), status_code=502)


@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: Request,
    registry: OpenAISessionRegistry = Depends(get_openai_session_registry),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> JSONResponse:
    credential = _credential(request, registry)
    body = await _request_body(request)
    if body.get("model") not in credential.models:
        return JSONResponse(_error("This model is not configured for the director session."), status_code=400)
    try:
        payload, _ = await asyncio.to_thread(to_responses_request, {"max_tokens": 16, **body})
        # The counting endpoint shares input/tool shapes, but has no generation parameters.
        payload = {
            key: value
            for key, value in payload.items()
            if key in {"model", "input", "instructions", "tools", "tool_choice", "text", "reasoning"}
        }
        response = await _while_open(
            lambda: client.post(
                f"{OPENAI_RESPONSES_URL}/input_tokens",
                json=payload,
                headers={"Authorization": f"Bearer {credential.api_key}"},
                timeout=_TIMEOUT,
            ),
            credential,
            request,
        )
        if response.is_error:
            return JSONResponse(await _upstream_error(response, payload), status_code=response.status_code)
        count = response.json().get("input_tokens")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("Invalid token count.")
        return JSONResponse({"input_tokens": count})
    except (ConversionError, TypeError, KeyError):
        return JSONResponse(_error("Unsupported director token-count input."), status_code=400)
    except (httpx.HTTPError, ValueError):
        return JSONResponse(_error("OpenAI token counting failed.", error_type="api_error"), status_code=502)
