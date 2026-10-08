"""CLI 原始帧的读取、解析与控制请求：ArcReel 依赖的 claude-agent-sdk 私有入口集中在此。

``ClaudeSDKClient.receive_messages()`` 把每一帧交给 SDK 的消息解析器，解析器不认识的帧
（如 ``command_lifecycle``）被直接丢弃。actor 因此读取 Query 的原始帧流，自行认出
``command_lifecycle``，其余帧仍交 SDK 解析器还原为 SDK 消息。

这里用到的 SDK 入口都未公开：Query 的原始帧流（``client._query.receive_messages()``）、
消息解析器（``parse_message``）与控制请求入口（``Query._send_control_request``）。
SDK 升级后它们失效时，``tests/unit/server/agent_runtime/test_sdk_frames.py`` 的契约测试会失败。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from claude_agent_sdk import Message
from claude_agent_sdk._internal.message_parser import parse_message

logger = logging.getLogger(__name__)

CommandLifecycleState = Literal["queued", "started", "completed", "cancelled", "discarded", "refused"]
_LIFECYCLE_STATES = frozenset(get_args(CommandLifecycleState))


@dataclass(frozen=True)
class CommandLifecycle:
    """CLI 命令队列中一条命令的去向，``command_uuid`` 是送入时携带的用户消息 uuid。

    ``queued``：进入 CLI 命令队列；``started``：被并入一轮；之后恰有一个终态
    （``completed`` / ``cancelled`` / ``discarded`` / ``refused``）。不带 uuid 送入的消息没有这类帧。
    """

    command_uuid: str
    state: CommandLifecycleState


def raw_frames(client: Any) -> AsyncIterator[dict[str, Any]]:
    """已连接 client 的原始帧流：控制帧已被 Query 消化，其余帧按 CLI 输出顺序产出。

    流在 CLI 退出后结束；Query 收到错误帧时抛出对应异常。
    """
    return client._query.receive_messages()


def parse_frame(frame: dict[str, Any]) -> CommandLifecycle | Message | None:
    """把一条原始帧还原为 ``CommandLifecycle`` 或 SDK 消息；不认识的帧返回 None。

    与 SDK 解析器对未知帧的处理一致，``command_lifecycle`` 帧出现未知状态时同样丢弃，
    CLI 新增状态不会让会话退出。
    """
    if frame.get("type") == "command_lifecycle":
        command_uuid = frame.get("command_uuid")
        state = frame.get("state")
        if not isinstance(command_uuid, str) or state not in _LIFECYCLE_STATES:
            logger.debug("Skipping unrecognized command_lifecycle frame: %s", frame)
            return None
        return CommandLifecycle(command_uuid=command_uuid, state=cast(CommandLifecycleState, state))
    return parse_message(frame)


async def cancel_async_message(client: Any, message_uuid: str) -> bool:
    """从 CLI 命令队列撤回一条尚未被并入轮次的用户消息；已被取走或从未送入时返回 False。

    与其他 client 调用一样，只在该会话的 SessionActor 内调用。
    """
    response = await client._query._send_control_request(
        {"subtype": "cancel_async_message", "message_uuid": message_uuid}
    )
    return response.get("cancelled") is True
