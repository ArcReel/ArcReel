"""新会话在流里只有 system 消息（init / api_retry）时也要拿到 sdk_session_id。"""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk.types import SystemMessage

from server.agent_runtime.message_serialization import message_to_dict
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore
from tests.fakes import FakeSDKClient

SDK_ID = "sdk-init-only-1"


def _init_message() -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "data": {"type": "system", "subtype": "init", "cwd": "/tmp/demo", "session_id": SDK_ID, "tools": []},
    }


def _api_retry_message(attempt: int) -> dict:
    return {
        "type": "system",
        "subtype": "api_retry",
        "data": {
            "type": "system",
            "subtype": "api_retry",
            "attempt": attempt,
            "max_retries": 10,
            "retry_delay_ms": 500,
            "error_status": 401,
            "error": "authentication_failed",
            "session_id": SDK_ID,
            "uuid": f"retry-{attempt}",
        },
    }


def test_system_message_serializes_session_id_under_data() -> None:
    """真实 SDK 的 SystemMessage 序列化后 session_id 只在 data 里，顶层没有。"""
    msg = message_to_dict(SystemMessage(subtype="init", data={"subtype": "init", "session_id": SDK_ID}))
    assert "session_id" not in msg
    assert msg["data"]["session_id"] == SDK_ID


@pytest.mark.asyncio
async def test_send_new_session_resolves_id_from_init_when_only_system_messages_arrive(
    tmp_path: Path, meta_store: SessionMetaStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_dir = tmp_path / "projects" / "demo"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")
    manager = SessionManager(project_root=tmp_path, meta_store=meta_store, sdk_id_timeout=2.0)

    client = FakeSDKClient(
        messages=[_init_message(), _api_retry_message(1), _api_retry_message(2)],
        block_forever=True,
    )

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)
    monkeypatch.setattr("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client)

    sdk_id = await manager.send_new_session("demo", "你好")
    try:
        assert sdk_id == SDK_ID
        assert manager.sessions[SDK_ID].status == "running"
        assert (await manager.meta_store.get(SDK_ID)).status == "running"
    finally:
        await manager.close_session(sdk_id)
