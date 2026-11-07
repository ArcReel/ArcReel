"""Pure functions serializing SDK messages into broadcastable dicts.

These cover the SDK-message → dict conversion and runtime-status message
construction. They hold no session state, so they can be unit-tested by
feeding message data directly.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from server.agent_runtime.models import SessionStatus

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
    "ConversationResetMessage": "conversation_reset",
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

    # Pydantic models: mode="json" produces JSON-safe structure once, avoiding another recursion
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


def is_main_turn_activity(message: Any) -> bool:
    """Main thread producing a turn, matching SDK in-progress criteria. Accept SDK messages or their dict form.

    Exclude subagent messages, task progress, and post-result system frames: they would make completed
    sessions appear in-flight without another result to close the turn.
    """
    if isinstance(message, dict):
        msg_type = message.get("type")
        parent_tool_use_id = message.get("parent_tool_use_id")
    else:
        msg_type = infer_message_type(message)
        parent_tool_use_id = getattr(message, "parent_tool_use_id", None)
    return msg_type in ("assistant", "stream_event") and parent_tool_use_id is None


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
