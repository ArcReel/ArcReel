"""Reuse the in-memory session_factory fixture from agent_session_store tests."""

from typing import Any

from tests.agent_session_store.conftest import session_factory  # noqa: F401


def make_transcript_entry(uuid: str, parent: str | None, entry_type: str, session_id: str, text: str) -> dict[str, Any]:
    """一条 SDK 形态的 transcript 条目，供前缀分叉相关的测试搭建原会话历史。"""
    return {
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "type": entry_type,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": entry_type, "content": text},
    }
