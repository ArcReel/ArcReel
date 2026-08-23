import pytest

from server.agent_runtime.subagent_status import aggregate_subagent_usage, build_subagent_snapshot

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


def test_child_usage_is_accumulated_and_duplicate_message_rows_are_not_double_counted() -> None:
    usage = aggregate_subagent_usage(
        [
            {
                "type": "assistant",
                "message_id": "msg-1",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
            {
                "type": "assistant",
                "message_id": "msg-1",
                "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 30},
            },
            {
                "type": "assistant",
                "message_id": "msg-2",
                "usage": {"input_tokens": 50, "output_tokens": 10},
            },
        ]
    )

    assert usage == {"total_tokens": 210}


def test_live_child_usage_replaces_stale_zero_progress() -> None:
    main = [
        {
            "type": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "Agent", "input": {}}],
        },
        {
            "type": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "launched"}],
            "tool_use_result": {"agentId": "agent-1", "status": "async_launched"},
        },
        {
            "type": "system",
            "subtype": "task_progress",
            "task_id": "agent-1",
            "tool_use_id": "tu-1",
            "usage": {"total_tokens": 0, "duration_ms": 20_000},
        },
    ]
    child = [
        {
            "type": "assistant",
            "message_id": "msg-1",
            "content": [{"type": "text", "text": "working"}],
            "usage": {"input_tokens": 100, "output_tokens": 25},
        }
    ]

    snapshot = build_subagent_snapshot(main, {"tu-1": child}, runtime_alive=True)

    assert snapshot["tasks"][0]["usage"] == {"total_tokens": 125, "duration_ms": 20_000}


def test_watchdog_override_marks_only_target_task_stalled() -> None:
    main = [
        {
            "type": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu-1", "name": "Agent", "input": {}},
                {"type": "tool_use", "id": "tu-2", "name": "Agent", "input": {}},
            ],
        },
        {
            "type": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "launched"}],
            "tool_use_result": {"agentId": "agent-1", "status": "async_launched"},
        },
        {
            "type": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-2", "content": "launched"}],
            "tool_use_result": {"agentId": "agent-2", "status": "async_launched"},
        },
    ]

    snapshot = build_subagent_snapshot(
        main,
        {},
        runtime_alive=True,
        stalled_task_ids={"agent-1"},
        stall_timeout_seconds=300,
    )

    assert [task["status"] for task in snapshot["tasks"]] == ["stalled", "running"]
    assert snapshot["tasks"][0]["stall_timeout_seconds"] == 300
    assert snapshot["active"] is True


def test_killed_task_updated_is_durably_projected_as_stalled() -> None:
    main = [
        {
            "type": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "Agent", "input": {}}],
        },
        {
            "type": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "launched"}],
            "tool_use_result": {"agentId": "agent-1", "status": "async_launched"},
        },
        {
            "type": "system",
            "subtype": "task_updated",
            "task_id": "agent-1",
            "status": "killed",
        },
    ]

    snapshot = build_subagent_snapshot(main, {}, runtime_alive=False, stall_timeout_seconds=300)

    assert snapshot["tasks"][0]["status"] == "stalled"
    assert snapshot["active"] is False
