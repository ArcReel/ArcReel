import pytest

from server.agent_runtime.subagent_status import build_subagent_snapshot

pytestmark = pytest.mark.unit


def test_async_agent_result_remains_running_until_task_notification() -> None:
    main = [
        {
            "type": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu-1",
                    "name": "Agent",
                    "input": {"description": "提取资产", "subagent_type": "analyze-assets"},
                }
            ],
        },
        {
            "type": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "launched"}],
            "tool_use_result": {"agentId": "agent-1", "status": "async_launched"},
        },
    ]
    snapshot = build_subagent_snapshot(
        main,
        {"tu-1": [{"type": "assistant", "content": [{"type": "text", "text": "正在读取剧本"}]}]},
    )
    assert snapshot["active"] is True
    assert snapshot["tasks"][0]["status"] == "running"
    assert snapshot["tasks"][0]["task_id"] == "agent-1"
    assert snapshot["tasks"][0]["entries"][0]["content"][0]["text"] == "正在读取剧本"


def test_completion_notification_sets_terminal_summary_and_usage() -> None:
    main = [
        {
            "type": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "Agent", "input": {"description": "提取资产"}}],
        },
        {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "agent-1",
            "tool_use_id": "tu-1",
            "status": "completed",
            "summary": "提取完成",
            "usage": {"total_tokens": 321, "duration_ms": 4000},
        },
    ]
    snapshot = build_subagent_snapshot(main, {})
    assert snapshot["active"] is False
    assert snapshot["tasks"][0]["status"] == "completed"
    assert snapshot["tasks"][0]["summary"] == "提取完成"
    assert snapshot["tasks"][0]["usage"] == {"total_tokens": 321, "duration_ms": 4000}


def test_unresolved_task_becomes_interrupted_when_owning_runtime_is_gone() -> None:
    main = [
        {
            "type": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "Agent", "input": {"description": "拆分单元"}}],
        },
        {
            "type": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "launched"}],
            "tool_use_result": {"agentId": "agent-1", "status": "async_launched"},
        },
    ]

    snapshot = build_subagent_snapshot(main, {}, runtime_alive=False)

    assert snapshot["active"] is False
    assert snapshot["tasks"][0]["status"] == "interrupted"


def test_unresolved_task_stays_running_while_owning_runtime_is_alive() -> None:
    main = [
        {
            "type": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "Task", "input": {"description": "拆分单元"}}],
        }
    ]

    snapshot = build_subagent_snapshot(main, {}, runtime_alive=True)

    assert snapshot["active"] is True
    assert snapshot["tasks"][0]["status"] == "running"
