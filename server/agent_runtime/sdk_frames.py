"""Read/parse raw CLI frames and send control requests, centralizing ArcReel’s private claude-agent-sdk dependencies.

``ClaudeSDKClient.receive_messages()`` sends every frame through the SDK parser, which drops unknown frames
such as ``command_lifecycle``. Actors therefore read Query’s raw stream, recognize ``command_lifecycle`` themselves
and pass remaining frames to the SDK parser for SDK-message reconstruction.

All SDK entry points here are private: Query’s raw stream ``client._query.receive_messages()``, ``parse_message``
and ``Query._send_control_request``. If upgrades break them, contract tests in
``tests/unit/server/agent_runtime/test_sdk_frames.py`` fail."""

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
    """Lifecycle of one CLI-queued command, with ``command_uuid`` identifying its submitted user-message uuid.

    ``queued`` enters the CLI command queue; ``started`` joins a turn, followed by exactly one terminal state:
    ``completed`` / ``cancelled`` / ``discarded`` / ``refused``. Messages submitted without uuids have no such frames."""

    command_uuid: str
    state: CommandLifecycleState


def raw_frames(client: Any) -> AsyncIterator[dict[str, Any]]:
    """Raw frames from a connected client: Query consumes control frames, yielding others in CLI output order.

    The stream ends after CLI exit; Query raises the corresponding exception on error frames."""
    return client._query.receive_messages()


def parse_frame(frame: dict[str, Any]) -> CommandLifecycle | Message | None:
    """Reconstruct ``CommandLifecycle`` or SDK messages from raw frames; unknown frames return None.

    Like SDK unknown-frame handling, drop ``command_lifecycle`` frames with unknown states so new CLI states cannot terminate sessions."""
    if frame.get("type") == "command_lifecycle":
        command_uuid = frame.get("command_uuid")
        state = frame.get("state")
        if not isinstance(command_uuid, str) or state not in _LIFECYCLE_STATES:
            logger.debug("Skipping unrecognized command_lifecycle frame: %s", frame)
            return None
        return CommandLifecycle(command_uuid=command_uuid, state=cast(CommandLifecycleState, state))
    return parse_message(frame)


async def cancel_async_message(client: Any, message_uuid: str) -> bool:
    """Withdraw a user message not yet included in a turn from the CLI queue; return False if already taken or never submitted.

    Like other client operations, call only inside this session’s SessionActor."""
    response = await client._query._send_control_request(
        {"subtype": "cancel_async_message", "message_uuid": message_uuid}
    )
    return response.get("cancelled") is True
