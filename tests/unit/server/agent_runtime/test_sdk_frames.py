"""sdk_frames 契约测试：真实 ClaudeSDKClient，只把传输层换成 CLI 替身。

sdk_frames 依赖的都是 SDK 未公开的入口（Query 的原始帧流、消息解析器、控制请求入口）。
升级 claude-agent-sdk 后这里失败，说明这些入口变了，需要先改 sdk_frames 再升级。
帧覆盖本地 stdio 模式下的用户消息回放、命令生命周期和撤回控制应答。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, Transport

from server.agent_runtime import sdk_frames
from server.agent_runtime.sdk_frames import CommandLifecycle
from tests.fakes import assistant_frame, command_lifecycle_frame

_EOF = object()


class _ScriptedCLITransport(Transport):
    """CLI 替身：应答 initialize 与 cancel_async_message，其余帧由测试脚本发出。"""

    def __init__(self, *, queued_uuids: set[str] | None = None):
        self._queued_uuids = set(queued_uuids or ())
        self._out: asyncio.Queue[Any] = asyncio.Queue()
        self.control_requests: list[dict[str, Any]] = []

    def emit(self, frame: dict[str, Any]) -> None:
        self._out.put_nowait(frame)

    def _answer(self, request_id: str, response: dict[str, Any]) -> None:
        self.emit(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": request_id, "response": response},
            }
        )

    async def connect(self) -> None:
        pass

    async def write(self, data: str) -> None:
        message = json.loads(data)
        if message.get("type") != "control_request":
            return
        request = message["request"]
        self.control_requests.append(request)
        if request["subtype"] == "initialize":
            self._answer(message["request_id"], {})
        elif request["subtype"] == "cancel_async_message":
            # 与 CLI 一致：只有仍在命令队列里的消息能撤回
            cancelled = request["message_uuid"] in self._queued_uuids
            self._queued_uuids.discard(request["message_uuid"])
            self._answer(message["request_id"], {"cancelled": cancelled})

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while (frame := await self._out.get()) is not _EOF:
            yield frame

    async def close(self) -> None:
        self._out.put_nowait(_EOF)

    def is_ready(self) -> bool:
        return True

    async def end_input(self) -> None:
        pass


async def _connected_client(transport: _ScriptedCLITransport) -> ClaudeSDKClient:
    client = ClaudeSDKClient(options=ClaudeAgentOptions(), transport=transport)
    await asyncio.wait_for(client.connect(), timeout=5)
    return client


async def test_command_lifecycle_frames_are_read_from_the_raw_frame_stream():
    transport = _ScriptedCLITransport()
    client = await _connected_client(transport)
    try:
        transport.emit(command_lifecycle_frame("u1", "started"))
        transport.emit(assistant_frame({"type": "text", "text": "好的"}, uuid="a1"))
        frames = sdk_frames.raw_frames(client)

        first = sdk_frames.parse_frame(await asyncio.wait_for(anext(frames), timeout=5))
        second = sdk_frames.parse_frame(await asyncio.wait_for(anext(frames), timeout=5))

        assert first == CommandLifecycle(command_uuid="u1", state="started")
        assert isinstance(second, AssistantMessage)
        assert second.uuid == "a1"
    finally:
        await client.disconnect()


async def test_cancel_async_message_gets_the_cli_answer():
    transport = _ScriptedCLITransport(queued_uuids={"queued-uuid"})
    client = await _connected_client(transport)
    try:
        withdrawn = await asyncio.wait_for(sdk_frames.cancel_async_message(client, "queued-uuid"), timeout=5)
        already_taken = await asyncio.wait_for(sdk_frames.cancel_async_message(client, "queued-uuid"), timeout=5)

        assert withdrawn is True
        assert already_taken is False
        assert transport.control_requests[-1] == {"subtype": "cancel_async_message", "message_uuid": "queued-uuid"}
    finally:
        await client.disconnect()
