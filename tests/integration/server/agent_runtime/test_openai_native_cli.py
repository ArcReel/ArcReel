"""The bundled CLI executes tools, resumes, and interrupts through the real bridge.

Only the loopback listener and native SDK subprocess are real I/O. OpenAI HTTP
is intercepted at the transport boundary, with synthetic credentials and data.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import claude_agent_sdk
import httpx
import pytest
import respx
import uvicorn
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)
from fastapi import Depends, FastAPI, Request

from lib.infra.httpx_shared import get_http_client
from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.openai_transport import (
    OPENAI_RESPONSES_URL,
    OpenAISessionRegistry,
    agent_client_session,
    get_openai_session_registry,
    router,
)
from server.agent_runtime.options_assembler import OptionsAssembler
from server.agent_runtime.session_manager import SessionManager
from server.agent_toolset.toolset import ARCREEL_MCP_TOOL_IDS

pytestmark = pytest.mark.local_port

_MODEL = "gpt-6.1-sol"
_KEY = "synthetic-director-key"
_IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDYsAAAAASUVORK5CYII="
_INITIAL_PROMPT = "Inspect shot one then give your conclusion."
_RESUME_PROMPT = "Continue using the approved voice."
_INTERRUPT_PROMPT = "Hold this turn open for interruption."
_RECOVERY_PROMPT = "Continue after the cancelled turn."
_ANSWER = "Frame reviewed. Keep the approved voice and identity."


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)


class _ListeningServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.listening = asyncio.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        self.listening.set()


class _InterruptedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.receiving = asyncio.Event()
        self.closed = asyncio.Event()
        self._never = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _sse({"type": "response.created", "response": {"id": "resp_interrupt"}})
        self.receiving.set()
        await self._never.wait()

    async def aclose(self) -> None:
        self.closed.set()


class _ResponseStream(httpx.AsyncByteStream):
    """Deliver separate network chunks so the CLI processes tools during streaming."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for event in self.events:
            yield _sse(event)


def _latest_user_text(payload: dict[str, Any]) -> str:
    for item in reversed(payload["input"]):
        if item.get("role") == "user":
            return "\n".join(part.get("text", "") for part in item["content"])
    return ""


class _OpenAIResponses:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.interrupted = _InterruptedStream()
        self.image_path: Path | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {_KEY}"
        payload = json.loads(request.content)
        self.requests.append(payload)
        prompt = _latest_user_text(payload)
        if _INTERRUPT_PROMPT in prompt and _RECOVERY_PROMPT not in prompt:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=self.interrupted)
        tool_outputs = [item for item in payload["input"] if item.get("type") == "function_call_output"]
        if _INITIAL_PROMPT in prompt and not tool_outputs:
            name = next(tool["name"] for tool in payload["tools"] if "inspect_frame" in tool["name"])
            output = [
                {
                    "type": "reasoning",
                    "id": "rs_review",
                    "encrypted_content": "synthetic-encrypted-history",
                    "summary": [],
                },
                {
                    "type": "message",
                    "id": "msg_commentary",
                    "role": "assistant",
                    "phase": "commentary",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "Inspecting the frame.", "annotations": []}],
                },
                {
                    "type": "function_call",
                    "id": "fc_review",
                    "call_id": "call_review",
                    "name": name,
                    "arguments": '{"shot":"one"}',
                    "status": "completed",
                },
                {
                    "type": "function_call",
                    "id": "fc_image",
                    "call_id": "call_image",
                    "name": "Read" if self.image_path else name,
                    "arguments": json.dumps(
                        {"file_path": str(self.image_path)} if self.image_path else {"shot": "two"}
                    ),
                    "status": "completed",
                },
            ]
        else:
            output = [
                {
                    "type": "message",
                    "id": f"msg_{len(self.requests)}",
                    "role": "assistant",
                    "phase": "final_answer",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": _ANSWER, "annotations": []}],
                }
            ]
        response = {
            "id": f"resp_{len(self.requests)}",
            "model": _MODEL,
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 120, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 40}},
        }
        if not payload.get("stream"):
            return httpx.Response(200, json=response)
        events = [{"type": "response.created", "response": {**response, "output": [], "usage": None}}]
        opened_tools: set[int] = set()
        for index, item in enumerate(output):
            added = {**item, "status": "in_progress"}
            if item["type"] == "function_call":
                for tool_index, tool_item in enumerate(output):
                    if tool_item["type"] == "function_call" and tool_index not in opened_tools:
                        events.append(
                            {
                                "type": "response.output_item.added",
                                "output_index": tool_index,
                                "item": {**tool_item, "status": "in_progress", "arguments": ""},
                            }
                        )
                        opened_tools.add(tool_index)
            elif item["type"] == "message":
                added["content"] = []
            if item["type"] != "function_call":
                events.append({"type": "response.output_item.added", "output_index": index, "item": added})
            if item["type"] == "function_call":
                events.append(
                    {
                        "type": "response.function_call_arguments.delta",
                        "output_index": index,
                        "delta": item["arguments"],
                    }
                )
            elif item["type"] == "message":
                for content_index, part in enumerate(item["content"]):
                    events.extend(
                        [
                            {
                                "type": "response.content_part.added",
                                "output_index": index,
                                "content_index": content_index,
                                "part": {**part, "text": ""},
                            },
                            {
                                "type": "response.output_text.delta",
                                "output_index": index,
                                "content_index": content_index,
                                "delta": part["text"],
                            },
                        ]
                    )
            events.append({"type": "response.output_item.done", "output_index": index, "item": item})
        events.append({"type": "response.completed", "response": response})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_ResponseStream(events),
        )


async def _result(client: ClaudeSDKClient, *, texts: list[str] | None = None) -> ResultMessage:
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage) and texts is not None:
            texts.extend(block.text for block in message.content if isinstance(block, TextBlock))
        if isinstance(message, ResultMessage):
            return message
    raise AssertionError("The native CLI ended without a terminal result")


@pytest.mark.timeout(90)
@pytest.mark.parametrize("tool_catalog", ["minimal", "production"])
async def test_native_cli_tool_image_resume_and_interrupt(tmp_path: Path, monkeypatch, tool_catalog: str):
    binary_name = "claude.exe" if sys.platform == "win32" else "claude"
    cli_path = Path(claude_agent_sdk.__file__).parent / "_bundled" / binary_name
    if not cli_path.is_file():
        pytest.skip("This SDK installation does not contain its bundled Claude CLI")

    tool_calls: list[dict[str, Any]] = []

    @tool("inspect_frame", "Inspect a synthetic frame.", {"shot": str})
    async def inspect_frame(args: dict[str, Any]) -> dict[str, Any]:
        tool_calls.append(args)
        return {
            "content": [
                {"type": "text", "text": "The character is the same; use the same voice."},
                {"type": "image", "mimeType": "image/png", "data": _IMAGE},
            ]
        }

    request_shapes: list[dict[str, Any]] = []

    async def record_request_shape(request: Request) -> None:
        body = await request.json()
        declarations = body.get("tools", [])
        request_shapes.append(
            {
                "tool_names": [declaration.get("name") for declaration in declarations],
                "tool_fields": sorted({field for declaration in declarations for field in declaration}),
                "tool_types": sorted({str(declaration.get("type")) for declaration in declarations}),
            }
        )

    registry = OpenAISessionRegistry()
    app = FastAPI(dependencies=[Depends(record_request_shape)])
    app.include_router(router)
    replies = _OpenAIResponses()
    async with httpx.AsyncClient(trust_env=False) as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        app.dependency_overrides[get_openai_session_registry] = lambda: registry
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = _ListeningServer(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=port,
                    log_level="error",
                    access_log=False,
                    lifespan="off",
                    timeout_graceful_shutdown=2,
                )
            )
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(10):
                    await server.listening.wait()
                env = {
                    "ANTHROPIC_BASE_URL": "https://api.openai.com",
                    "ANTHROPIC_API_KEY": _KEY,
                    "ANTHROPIC_MODEL": _MODEL,
                    "ARCREEL_API_BASE": f"http://127.0.0.1:{port}/api/v1",
                    "CLAUDE_CONFIG_DIR": str(tmp_path),
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    "CLAUDE_CODE_OAUTH_TOKEN": "",
                }
                for key in (
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL",
                    "CLAUDE_CODE_SUBAGENT_MODEL",
                ):
                    env[key] = _MODEL
                synthetic_mcp = {"test": create_sdk_mcp_server(name="test", tools=[inspect_frame])}
                options = ClaudeAgentOptions(
                    cwd=tmp_path,
                    cli_path=cli_path,
                    env=env,
                    setting_sources=[],
                    tools=[],
                    max_turns=4,
                    system_prompt="You are a video director. Inspect the frame before answering.",
                    mcp_servers=synthetic_mcp,
                    allowed_tools=["mcp__test__inspect_frame"],
                )
                if tool_catalog == "production":
                    data_root = tmp_path / "data"
                    project_dir = data_root / "projects" / "demo"
                    project_dir.mkdir(parents=True)
                    replies.image_path = project_dir / "reference.png"
                    replies.image_path.write_bytes(base64.b64decode(_IMAGE))
                    policy = AgentAccessPolicy(
                        project_root=tmp_path / "repository",
                        data_root=data_root,
                        agent_profile_root=tmp_path / "profile",
                        claude_projects_dir=tmp_path / "projects",
                    )

                    async def synthetic_provider_env() -> dict[str, str]:
                        return dict(env)

                    monkeypatch.setenv("AUTH_ENABLED", "false")
                    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", "off")
                    monkeypatch.setenv("ARCREEL_API_BASE", env["ARCREEL_API_BASE"])
                    assembler = OptionsAssembler(
                        data_root=data_root,
                        allowed_tools=SessionManager.DEFAULT_ALLOWED_TOOLS,
                        setting_sources=SessionManager.DEFAULT_SETTING_SOURCES,
                        access_policy_provider=lambda: policy,
                        max_turns_provider=lambda: 4,
                        resolve_project_cwd=lambda _: project_dir,
                        provider_env_loader=synthetic_provider_env,
                    )
                    assembled = await assembler.build("demo", locale="en")
                    options = replace(
                        assembled,
                        cli_path=cli_path,
                        mcp_servers={**assembled.mcp_servers, **synthetic_mcp},
                        allowed_tools=[*assembled.allowed_tools, "mcp__test__inspect_frame"],
                    )
                with respx.mock(assert_all_mocked=True, assert_all_called=False) as http:
                    http.route(host="127.0.0.1").pass_through()
                    http.post(OPENAI_RESPONSES_URL).mock(side_effect=replies)
                    http.post(f"{OPENAI_RESPONSES_URL}/input_tokens").respond(json={"input_tokens": 120})
                    async with asyncio.timeout(60):
                        async with agent_client_session(
                            options, client_factory=ClaudeSDKClient, registry=registry
                        ) as sdk:
                            await sdk.query(_INITIAL_PROMPT)
                            texts: list[str] = []
                            first = await _result(sdk, texts=texts)
                            assert not first.is_error, {
                                "result": first.result,
                                "errors": first.errors,
                                "request_shapes": request_shapes,
                            }
                            assert _ANSWER in texts
                            assert tool_calls == (
                                [{"shot": "one"}]
                                if tool_catalog == "production"
                                else [{"shot": "one"}, {"shot": "two"}]
                            )
                            if tool_catalog == "production":
                                names = set(request_shapes[0]["tool_names"])
                                assert {"Read", "Bash"}.issubset(names)
                                assert {f"mcp__arcreel__{name}" for name in ARCREEL_MCP_TOOL_IDS}.issubset(names)

                        tool_turn = next(
                            body
                            for body in replies.requests
                            if any(item.get("type") == "function_call_output" for item in body["input"])
                        )
                        tool_output = next(
                            item for item in tool_turn["input"] if item.get("type") == "function_call_output"
                        )
                        assert tool_output["call_id"] == "call_review"
                        assert any(
                            part.get("type") == "input_image" and part["image_url"].startswith("data:image/png;base64,")
                            for part in tool_output["output"]
                        )
                        second_output = next(
                            item
                            for item in tool_turn["input"]
                            if item.get("call_id") == "call_image" and item.get("type") == "function_call_output"
                        )
                        assert any(part.get("type") == "input_image" for part in second_output["output"])
                        assert any(
                            item.get("id") == "msg_commentary" and item.get("phase") == "commentary"
                            for item in tool_turn["input"]
                        )
                        assert any(
                            item.get("type") == "reasoning"
                            and item.get("encrypted_content") == "synthetic-encrypted-history"
                            for item in tool_turn["input"]
                        )

                        async with agent_client_session(
                            replace(options, resume=first.session_id), client_factory=ClaudeSDKClient, registry=registry
                        ) as sdk:
                            await sdk.query(_RESUME_PROMPT)
                            resumed = await _result(sdk)
                            assert not resumed.is_error
                            assert resumed.session_id == first.session_id
                            resumed_input = next(
                                body["input"] for body in replies.requests if _RESUME_PROMPT in _latest_user_text(body)
                            )
                            assert any(
                                item.get("id") == "rs_review"
                                and item.get("encrypted_content") == "synthetic-encrypted-history"
                                for item in resumed_input
                            )
                            assert any(
                                item.get("role") == "assistant"
                                and item.get("phase") == "final_answer"
                                and any(part.get("text") == _ANSWER for part in item["content"])
                                for item in resumed_input
                            )

                            await sdk.query(_INTERRUPT_PROMPT)
                            interrupted_result = asyncio.create_task(_result(sdk))
                            try:
                                async with asyncio.timeout(15):
                                    await replies.interrupted.receiving.wait()
                                    await sdk.interrupt()
                                    interrupted = await interrupted_result
                                    await replies.interrupted.closed.wait()
                                assert interrupted.terminal_reason == "aborted_streaming"
                            finally:
                                interrupted_result.cancel()
                                await asyncio.gather(interrupted_result, return_exceptions=True)

                            await sdk.query(_RECOVERY_PROMPT)
                            recovered_texts: list[str] = []
                            recovered = await _result(sdk, texts=recovered_texts)
                            assert not recovered.is_error
                            assert _ANSWER in recovered_texts
                            assert recovered.session_id == first.session_id
            finally:
                server.should_exit = True
                await serving
