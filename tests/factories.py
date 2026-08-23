"""Test data factories — reduce boilerplate when constructing common objects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from server.agent_runtime.models import SessionMeta


def make_session_meta(**overrides) -> SessionMeta:
    """Build a SessionMeta with sensible defaults.

    Any keyword argument overrides the corresponding default field.
    """
    defaults = dict(
        id="session-1",
        project_name="demo",
        title="demo",
        status="running",
        created_at=datetime(2026, 2, 9, 8, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 2, 9, 8, 0, 0, tzinfo=UTC),
    )
    defaults.update(overrides)
    return SessionMeta(**defaults)


def make_task_params(**overrides) -> dict:
    """Build a dict of parameters suitable for ``GenerationQueue.enqueue_task()``.

    Any keyword argument overrides the corresponding default.
    """
    defaults = dict(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={"prompt": "test"},
        script_file="episode_01.json",
        source="webui",
    )
    defaults.update(overrides)
    return defaults


def make_sdk_transcript_entry(
    uuid: str, parent: str | None, entry_type: str, session_id: str, text: str
) -> dict[str, Any]:
    """一条 SDK 形态的 transcript 条目，供前缀分叉相关的测试搭建原会话历史。"""
    return {
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "type": entry_type,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": entry_type, "content": text},
    }


def make_transcript_entry(
    msg_type: str = "assistant",
    text: str = "hello",
    *,
    uuid: str = "msg-1",
    tool_use_id: str | None = None,
    tool_name: str | None = None,
    **extra,
) -> dict:
    """Build a single transcript JSONL entry dict.

    ``msg_type`` is one of ``"user"``, ``"assistant"``, ``"result"``.
    """
    if msg_type == "user":
        content = text
    elif msg_type == "result":
        entry: dict = {
            "type": "result",
            "subtype": extra.get("subtype", "success"),
            "is_error": extra.get("is_error", False),
            "uuid": uuid,
        }
        entry.update(extra)
        return entry
    else:
        if tool_use_id:
            content = [{"type": "tool_use", "id": tool_use_id, "name": tool_name or "Tool", "input": {}}]
        else:
            content = [{"type": "text", "text": text}]

    entry = {"type": msg_type, "message": {"content": content}, "uuid": uuid}
    entry.update(extra)
    return entry
