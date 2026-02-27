"""Unit tests for shared turn grouper."""

from server.agent_runtime.turn_grouper import (
    build_turn_patch,
    group_messages_into_turns,
)


class TestTurnGrouper:
    def test_skill_tool_result_and_skill_content_attached(self):
        raw_messages = [
            {"type": "user", "content": "use skill"},
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "skill-1",
                        "name": "Skill",
                        "input": {"skill": "commit"},
                    }
                ],
            },
            {
                "type": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "skill-1", "content": "Launching skill: commit"}
                ],
            },
            {
                "type": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Base directory for this skill: /tmp/.claude/skills/commit/SKILL.md",
                    }
                ],
            },
        ]

        turns = group_messages_into_turns(raw_messages)
        assert len(turns) == 2
        assert turns[0]["type"] == "user"
        assert turns[1]["type"] == "assistant"

        skill_block = turns[1]["content"][0]
        assert skill_block["type"] == "tool_use"
        assert skill_block["name"] == "Skill"
        assert skill_block["result"] == "Launching skill: commit"
        assert "skill_content" in skill_block
        assert "Base directory for this skill:" in skill_block["skill_content"]

    def test_assistant_messages_merged_and_result_flushed(self):
        raw_messages = [
            {"type": "user", "content": "read file"},
            {"type": "assistant", "content": [{"type": "text", "text": "Reading..."}], "uuid": "a1"},
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "/tmp/a"}}],
                "uuid": "a2",
            },
            {
                "type": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "hello"}],
            },
            {"type": "assistant", "content": [{"type": "text", "text": "Done"}], "uuid": "a3"},
            {"type": "result", "subtype": "success", "uuid": "r1"},
        ]

        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant", "result"]
        assistant_turn = turns[1]
        assert len(assistant_turn["content"]) == 3
        assert assistant_turn["content"][0]["type"] == "text"
        assert assistant_turn["content"][1]["type"] == "tool_use"
        assert assistant_turn["content"][1]["result"] == "hello"
        assert assistant_turn["content"][2]["type"] == "text"
        assert turns[2]["subtype"] == "success"

    def test_tool_result_without_type_is_attached(self):
        raw_messages = [
            {"type": "user", "content": "run tool"},
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-plain-1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/plain.txt"},
                    }
                ],
            },
            {
                "type": "user",
                "content": [
                    {
                        "tool_use_id": "tool-plain-1",
                        "content": "plain tool result payload",
                        "is_error": False,
                    }
                ],
            },
        ]

        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant"]
        tool_block = turns[1]["content"][0]
        assert tool_block["type"] == "tool_use"
        assert tool_block["result"] == "plain tool result payload"
        assert tool_block["is_error"] == False

    def test_build_turn_patch_append_replace_reset(self):
        user_turn = {"type": "user", "content": [{"type": "text", "text": "hi"}]}
        assistant_turn_v1 = {"type": "assistant", "content": [{"type": "text", "text": "hello"}]}
        assistant_turn_v2 = {"type": "assistant", "content": [{"type": "text", "text": "hello again"}]}

        append_patch = build_turn_patch([user_turn], [user_turn, assistant_turn_v1])
        assert append_patch["op"] == "append"
        assert append_patch["turn"] == assistant_turn_v1

        replace_patch = build_turn_patch(
            [user_turn, assistant_turn_v1], [user_turn, assistant_turn_v2]
        )
        assert replace_patch["op"] == "replace_last"
        assert replace_patch["turn"] == assistant_turn_v2

        reset_patch = build_turn_patch([user_turn, assistant_turn_v1], [assistant_turn_v2])
        assert reset_patch["op"] == "reset"
        assert reset_patch["turns"] == [assistant_turn_v2]

    def test_incremental_patch_with_plain_tool_result_payload(self):
        raw_messages: list[dict] = []

        # Step 1: user turn appears
        raw_messages.append({"type": "user", "content": "run skill"})
        turns_v1 = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns_v1] == ["user"]

        # Step 2: assistant tool_use appears -> append assistant turn
        raw_messages.append(
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "skill-plain-1",
                        "name": "Skill",
                        "input": {"skill": "manga-workflow"},
                    }
                ],
            }
        )
        turns_v2 = group_messages_into_turns(raw_messages)
        patch_v2 = build_turn_patch(turns_v1, turns_v2)
        assert patch_v2["op"] == "append"
        assert [turn["type"] for turn in turns_v2] == ["user", "assistant"]

        # Step 3: tool_result payload without explicit type arrives as user content
        raw_messages.append(
            {
                "type": "user",
                "content": [
                    {
                        "tool_use_id": "skill-plain-1",
                        "content": "Launching skill: manga-workflow",
                        "is_error": False,
                    }
                ],
            }
        )
        turns_v3 = group_messages_into_turns(raw_messages)
        patch_v3 = build_turn_patch(turns_v2, turns_v3)

        # Key assertion: assistant turn is replaced/updated, not a new user turn appended.
        assert patch_v3["op"] == "replace_last"
        assert [turn["type"] for turn in turns_v3] == ["user", "assistant"]
        assert (
            turns_v3[1]["content"][0]["result"]
            == "Launching skill: manga-workflow"
        )

    def test_untyped_live_blocks_are_normalized_and_attached(self):
        raw_messages = [
            {"type": "user", "content": "使用 manga-workflow 开始项目"},
            {
                "type": "assistant",
                "content": [
                    {
                        "text": "我来启动 workflow",
                    }
                ],
            },
            {
                "type": "assistant",
                "content": [
                    {
                        "id": "tool-live-1",
                        "name": "Skill",
                        "input": {"skill": "manga-workflow", "args": "test"},
                    }
                ],
            },
            {
                "type": "user",
                "content": [
                    {
                        "tool_use_id": "tool-live-1",
                        "content": "Launching skill: manga-workflow",
                        "is_error": False,
                    }
                ],
            },
            {
                "type": "user",
                "content": [
                    {
                        "text": "Base directory for this skill: /tmp/.claude/skills/manga-workflow/SKILL.md\n\n# 视频工作流",
                    }
                ],
            },
        ]

        turns = group_messages_into_turns(raw_messages)
        assert len(turns) == 2
        assert turns[0]["type"] == "user"
        assert turns[1]["type"] == "assistant"

        assistant_blocks = turns[1]["content"]
        assert assistant_blocks[0]["type"] == "text"
        assert assistant_blocks[1]["type"] == "tool_use"
        assert assistant_blocks[1]["name"] == "Skill"
        assert assistant_blocks[1]["result"] == "Launching skill: manga-workflow"
        assert "skill_content" in assistant_blocks[1]

    def test_subagent_parent_user_text_is_filtered_from_assistant_turn(self):
        raw_messages = [
            {"type": "user", "content": "继续制作"},
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-1",
                        "name": "Task",
                        "input": {"subagent_type": "Explore", "description": "检查项目状态"},
                    }
                ],
            },
            {
                "type": "user",
                "content": [
                    {"type": "text", "text": "正在分析项目结构..."}
                ],
                "parent_tool_use_id": "task-1",
            },
        ]

        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant"]
        assert turns[1]["content"][0]["type"] == "tool_use"
        assert turns[1]["content"][0]["name"] == "Task"
        assert len(turns[1]["content"]) == 1

    def test_subagent_user_text_without_assistant_turn_is_dropped(self):
        raw_messages = [
            {"type": "user", "content": "请继续"},
            {
                "type": "user",
                "content": [{"type": "text", "text": "subagent telemetry"}],
                "parentToolUseID": "task-2",
            },
        ]

        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user"]

    def test_subagent_tool_result_still_attaches_to_task_tool_use(self):
        raw_messages = [
            {"type": "user", "content": "继续制作"},
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-attach-1",
                        "name": "Task",
                        "input": {"subagent_type": "Explore", "description": "检查项目状态"},
                    }
                ],
            },
            {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "task-attach-1",
                        "content": "subagent finished",
                    }
                ],
                "parent_tool_use_id": "task-attach-1",
            },
        ]

        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant"]
        task_block = turns[1]["content"][0]
        assert task_block["type"] == "tool_use"
        assert task_block["name"] == "Task"
        assert task_block["result"] == "subagent finished"
