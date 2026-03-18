"""Unit tests for shared turn grouper."""

from server.agent_runtime.turn_grouper import (
    _extract_task_notification,
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
        assert [turn["type"] for turn in turns] == ["user", "assistant"]
        assistant_turn = turns[1]
        assert len(assistant_turn["content"]) == 3
        assert assistant_turn["content"][0]["type"] == "text"
        assert assistant_turn["content"][1]["type"] == "tool_use"
        assert assistant_turn["content"][1]["result"] == "hello"
        assert assistant_turn["content"][2]["type"] == "text"

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

    def test_result_turn_is_eliminated(self):
        """Result messages flush current turn but don't create independent turn."""
        raw_messages = [
            {"type": "user", "content": "hello"},
            {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
            {"type": "result", "subtype": "success"},
        ]
        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant"]

    def test_result_between_rounds_flushes_correctly(self):
        """Result between two user messages flushes correctly."""
        raw_messages = [
            {"type": "user", "content": "first"},
            {"type": "assistant", "content": [{"type": "text", "text": "response 1"}]},
            {"type": "result", "subtype": "success"},
            {"type": "user", "content": "second"},
            {"type": "assistant", "content": [{"type": "text", "text": "response 2"}]},
        ]
        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant", "user", "assistant"]

    def test_task_progress_attached_to_assistant_turn(self):
        """Task notification updates existing task_started block in-place."""
        raw_messages = [
            {"type": "user", "content": "do something complex"},
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "agent-1", "name": "Agent", "input": {}}],
            },
            {
                "type": "system",
                "subtype": "task_started",
                "description": "Exploring codebase",
                "task_id": "task-abc",
                "tool_use_id": "agent-1",
            },
            {
                "type": "system",
                "subtype": "task_notification",
                "description": "Exploring codebase",
                "summary": "Found 3 relevant files",
                "status": "completed",
                "task_id": "task-abc",
            },
        ]
        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant"]
        assistant_content = turns[1]["content"]
        # tool_use + 1 updated task_progress block (notification merges into started)
        assert len(assistant_content) == 2
        assert assistant_content[1]["type"] == "task_progress"
        assert assistant_content[1]["status"] == "task_notification"
        assert assistant_content[1]["task_status"] == "completed"
        assert assistant_content[1]["summary"] == "Found 3 relevant files"

    def test_task_notification_without_prior_started_appends(self):
        """Task notification without a prior task_started still appends as new block."""
        raw_messages = [
            {"type": "user", "content": "do something"},
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "agent-1", "name": "Agent", "input": {}}],
            },
            {
                "type": "system",
                "subtype": "task_notification",
                "summary": "Done",
                "status": "completed",
                "task_id": "task-new",
            },
        ]
        turns = group_messages_into_turns(raw_messages)
        assistant_content = turns[1]["content"]
        assert len(assistant_content) == 2
        assert assistant_content[1]["type"] == "task_progress"
        assert assistant_content[1]["status"] == "task_notification"

    def test_stale_task_started_resolved_by_agent_result(self):
        """task_started block is auto-completed when Agent tool_use has result."""
        raw_messages = [
            {"type": "user", "content": "run subagent"},
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "agent-1", "name": "Agent", "input": {}}],
            },
            {
                "type": "system",
                "subtype": "task_started",
                "description": "Testing",
                "task_id": "task-123",
                "tool_use_id": "agent-1",
            },
            # tool_result arrives but no task_notification
            {
                "type": "user",
                "content": [{"type": "tool_result", "tool_use_id": "agent-1", "content": "done"}],
                "parent_tool_use_id": "agent-1",
            },
        ]
        turns = group_messages_into_turns(raw_messages)
        assistant_content = turns[1]["content"]
        task_block = next(b for b in assistant_content if b["type"] == "task_progress")
        assert task_block["status"] == "task_notification"
        assert task_block["task_status"] == "completed"

    def test_subagent_prompt_mentioning_skill_paths_not_treated_as_skill_content(self):
        """Subagent prompt that mentions .claude/skills/**/SKILL.md should be suppressed, not shown as skill_content."""
        raw_messages = [
            {"type": "user", "content": "explore project"},
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "agent-1",
                        "name": "Agent",
                        "input": {"subagent_type": "Explore", "prompt": "find skills"},
                    }
                ],
            },
            {
                "type": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请用 Glob 查找 .claude/skills/**/SKILL.md 下的所有 skill 文件",
                    }
                ],
                "parent_tool_use_id": "agent-1",
            },
        ]

        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant"]
        # The subagent prompt should be suppressed, not turned into skill_content
        assert len(turns[1]["content"]) == 1
        assert turns[1]["content"][0]["type"] == "tool_use"
        assert "skill_content" not in turns[1]["content"][0]

    def test_task_progress_without_assistant_creates_system_turn(self):
        """Task progress without a preceding assistant turn creates a system turn."""
        raw_messages = [
            {"type": "user", "content": "hello"},
            {
                "type": "system",
                "subtype": "task_started",
                "description": "Starting task",
                "task_id": "task-xyz",
            },
        ]
        turns = group_messages_into_turns(raw_messages)
        assert len(turns) == 2
        assert turns[0]["type"] == "user"
        assert turns[1]["type"] == "system"
        assert turns[1]["content"][0]["type"] == "task_progress"

    def test_task_notification_user_message_converted_to_task_progress(self):
        """SDK-injected <task-notification> user message becomes task_progress block."""
        xml_content = (
            '<task-notification>\n'
            '<task-id>bdgaof0ba</task-id>\n'
            '<tool-use-id>toolu_016arH6Ny81xuwipeci3ic5e</tool-use-id>\n'
            '<output-file>/tmp/claude-0/tasks/bdgaof0ba.output</output-file>\n'
            '<status>failed</status>\n'
            '<summary>Background command failed with exit code 2</summary>\n'
            '</task-notification>\n'
            'Read the output file to retrieve the result.'
        )
        raw_messages = [
            {"type": "user", "content": "run this in background"},
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "agent-1", "name": "Agent", "input": {}},
                ],
            },
            {
                "type": "system",
                "subtype": "task_started",
                "description": "Running command",
                "task_id": "bdgaof0ba",
                "tool_use_id": "agent-1",
            },
            # SDK transcript stores the notification as a user message
            {"type": "user", "content": xml_content},
        ]
        turns = group_messages_into_turns(raw_messages)
        assert [turn["type"] for turn in turns] == ["user", "assistant"]
        assistant_content = turns[1]["content"]
        task_blocks = [b for b in assistant_content if b.get("type") == "task_progress"]
        assert len(task_blocks) == 1
        assert task_blocks[0]["status"] == "task_notification"
        assert task_blocks[0]["task_status"] == "failed"
        assert task_blocks[0]["summary"] == "Background command failed with exit code 2"

    def test_task_notification_user_message_list_content(self):
        """Task notification in list-of-blocks content format is also detected."""
        xml_text = (
            '<task-notification>\n'
            '<task-id>abc123</task-id>\n'
            '<tool-use-id>toolu_xyz</tool-use-id>\n'
            '<output-file>/tmp/claude-0/tasks/abc123.output</output-file>\n'
            '<status>completed</status>\n'
            '<summary>Task finished successfully</summary>\n'
            '</task-notification>'
        )
        raw_messages = [
            {"type": "user", "content": "start"},
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "working on it"}],
            },
            # Content as list of text blocks (SDK format)
            {
                "type": "user",
                "content": [{"type": "text", "text": xml_text}],
            },
        ]
        turns = group_messages_into_turns(raw_messages)
        # Should NOT appear as a user turn
        user_turns = [t for t in turns if t["type"] == "user"]
        assert len(user_turns) == 1  # only the initial "start"
        # The task_progress block should be on the assistant turn
        assistant_content = turns[1]["content"]
        task_blocks = [b for b in assistant_content if b.get("type") == "task_progress"]
        assert len(task_blocks) == 1
        assert task_blocks[0]["task_status"] == "completed"

    def test_task_notification_without_prior_turn_creates_system_turn(self):
        """Task notification user message without assistant turn creates system turn."""
        xml_content = (
            '<task-notification>\n'
            '<task-id>solo-task</task-id>\n'
            '<tool-use-id>toolu_solo</tool-use-id>\n'
            '<output-file>/tmp/tasks/solo-task.output</output-file>\n'
            '<status>completed</status>\n'
            '<summary>Done</summary>\n'
            '</task-notification>'
        )
        raw_messages = [
            {"type": "user", "content": xml_content},
        ]
        turns = group_messages_into_turns(raw_messages)
        assert len(turns) == 1
        assert turns[0]["type"] == "system"
        assert turns[0]["content"][0]["type"] == "task_progress"


class TestExtractTaskNotification:
    """Tests for _extract_task_notification helper."""

    def test_extracts_all_fields(self):
        xml = (
            '<task-notification>\n'
            '<task-id>abc</task-id>\n'
            '<tool-use-id>toolu_1</tool-use-id>\n'
            '<output-file>/tmp/out.txt</output-file>\n'
            '<status>completed</status>\n'
            '<summary>All good</summary>\n'
            '</task-notification>'
        )
        result = _extract_task_notification(xml)
        assert result is not None
        assert result["task_id"] == "abc"
        assert result["tool_use_id"] == "toolu_1"
        assert result["output_file"] == "/tmp/out.txt"
        assert result["status"] == "completed"
        assert result["summary"] == "All good"

    def test_returns_none_for_normal_text(self):
        assert _extract_task_notification("hello world") is None

    def test_handles_list_content(self):
        blocks = [{"type": "text", "text": "<task-notification><task-id>x</task-id><status>ok</status></task-notification>"}]
        result = _extract_task_notification(blocks)
        assert result is not None
        assert result["task_id"] == "x"
