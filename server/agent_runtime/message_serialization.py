"""Pure functions serializing SDK messages into broadcastable dicts.

These cover the SDK-message → dict conversion, runtime-status message
construction, and replayed-user-echo detection (the write point marks SDK
replay copies so they are not logged twice). They hold no session state, so
they can be unit-tested by feeding message data directly.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from server.agent_runtime.message_utils import extract_plain_user_content
from server.agent_runtime.models import SessionStatus

# Sentinel used in pending_user_echoes for image-only messages (no text).
# The SDK parser drops image blocks, so the replayed UserMessage arrives
# with empty content; this sentinel lets is_duplicate_user_echo match it.
IMAGE_ONLY_SENTINEL = "__image_only__"

# SDK message class name to type mapping
MESSAGE_TYPE_MAP = {
    "UserMessage": "user",
    "AssistantMessage": "assistant",
    "ResultMessage": "result",
    "SystemMessage": "system",
    "StreamEvent": "stream_event",
    "TaskStartedMessage": "system",
    "TaskProgressMessage": "system",
    "TaskNotificationMessage": "system",
}

# Typed task message subtypes for precise classification
TASK_MESSAGE_SUBTYPES = {
    "TaskStartedMessage": "task_started",
    "TaskProgressMessage": "task_progress",
    "TaskNotificationMessage": "task_notification",
}


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def serialize_value(value: Any) -> Any:
    """Recursively serialize a value to JSON-safe types."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]

    # Pydantic models — mode="json" 一次产出 JSON 安全结构，避免再次递归
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    # Dataclasses or objects with __dict__
    if hasattr(value, "__dict__"):
        return {k: serialize_value(v) for k, v in value.__dict__.items() if not k.startswith("_")}

    # Fallback: convert to string
    return str(value)


def infer_message_type(message: Any) -> str | None:
    """Infer message type from SDK message class name."""
    class_name = type(message).__name__
    return MESSAGE_TYPE_MAP.get(class_name)


def message_to_dict(message: Any) -> dict[str, Any]:
    """Convert SDK message to dict for JSON serialization."""
    msg_dict = serialize_value(message)

    # Infer and add message type if not present
    if isinstance(msg_dict, dict) and "type" not in msg_dict:
        msg_type = infer_message_type(message)
        if msg_type:
            msg_dict["type"] = msg_type

    # Inject precise subtype for typed task messages
    if isinstance(msg_dict, dict):
        class_name = type(message).__name__
        subtype = TASK_MESSAGE_SUBTYPES.get(class_name)
        if subtype:
            msg_dict["subtype"] = subtype

    return msg_dict


def build_runtime_status_message(
    status: SessionStatus,
    session_id: str,
) -> dict[str, Any]:
    """Build runtime-only status message for SSE wake-up."""
    return {
        "type": "runtime_status",
        "status": status,
        "subtype": status,
        "stop_reason": None,
        "is_error": status == "error",
        "session_id": session_id,
        "uuid": f"runtime-status-{uuid4().hex}",
        "timestamp": utc_now_iso(),
    }


def is_duplicate_user_echo(
    pending_user_echoes: list[str],
    message: dict[str, Any],
) -> bool:
    """Skip SDK-replayed user message if it matches local echo queue."""
    if not pending_user_echoes:
        return False
    incoming = extract_plain_user_content(message)
    expected = pending_user_echoes[0].strip()

    # Image-only sentinel: the SDK parser drops image blocks, so the
    # replayed UserMessage arrives with empty content (incoming is None).
    if not incoming:
        if message.get("type") != "user" or expected != IMAGE_ONLY_SENTINEL:
            return False
        pending_user_echoes.pop(0)
        return True

    if incoming != expected:
        return False
    pending_user_echoes.pop(0)
    return True
