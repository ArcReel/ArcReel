"""Unit tests for shared turn grouper."""

import unittest

from webui.server.agent_runtime.turn_grouper import (
    build_turn_patch,
    group_messages_into_turns,
)


class TestTurnGrouper(unittest.TestCase):
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
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["type"], "user")
        self.assertEqual(turns[1]["type"], "assistant")

        skill_block = turns[1]["content"][0]
        self.assertEqual(skill_block["type"], "tool_use")
        self.assertEqual(skill_block["name"], "Skill")
        self.assertEqual(skill_block["result"], "Launching skill: commit")
        self.assertIn("skill_content", skill_block)
        self.assertIn("Base directory for this skill:", skill_block["skill_content"])

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
        self.assertEqual([turn["type"] for turn in turns], ["user", "assistant", "result"])
        assistant_turn = turns[1]
        self.assertEqual(len(assistant_turn["content"]), 3)
        self.assertEqual(assistant_turn["content"][0]["type"], "text")
        self.assertEqual(assistant_turn["content"][1]["type"], "tool_use")
        self.assertEqual(assistant_turn["content"][1]["result"], "hello")
        self.assertEqual(assistant_turn["content"][2]["type"], "text")
        self.assertEqual(turns[2]["subtype"], "success")

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
        self.assertEqual([turn["type"] for turn in turns], ["user", "assistant"])
        tool_block = turns[1]["content"][0]
        self.assertEqual(tool_block["type"], "tool_use")
        self.assertEqual(tool_block["result"], "plain tool result payload")
        self.assertEqual(tool_block["is_error"], False)

    def test_build_turn_patch_append_replace_reset(self):
        user_turn = {"type": "user", "content": [{"type": "text", "text": "hi"}]}
        assistant_turn_v1 = {"type": "assistant", "content": [{"type": "text", "text": "hello"}]}
        assistant_turn_v2 = {"type": "assistant", "content": [{"type": "text", "text": "hello again"}]}

        append_patch = build_turn_patch([user_turn], [user_turn, assistant_turn_v1])
        self.assertEqual(append_patch["op"], "append")
        self.assertEqual(append_patch["turn"], assistant_turn_v1)

        replace_patch = build_turn_patch(
            [user_turn, assistant_turn_v1], [user_turn, assistant_turn_v2]
        )
        self.assertEqual(replace_patch["op"], "replace_last")
        self.assertEqual(replace_patch["turn"], assistant_turn_v2)

        reset_patch = build_turn_patch([user_turn, assistant_turn_v1], [assistant_turn_v2])
        self.assertEqual(reset_patch["op"], "reset")
        self.assertEqual(reset_patch["turns"], [assistant_turn_v2])

    def test_incremental_patch_with_plain_tool_result_payload(self):
        raw_messages: list[dict] = []

        # Step 1: user turn appears
        raw_messages.append({"type": "user", "content": "run skill"})
        turns_v1 = group_messages_into_turns(raw_messages)
        self.assertEqual([turn["type"] for turn in turns_v1], ["user"])

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
        self.assertEqual(patch_v2["op"], "append")
        self.assertEqual([turn["type"] for turn in turns_v2], ["user", "assistant"])

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
        self.assertEqual(patch_v3["op"], "replace_last")
        self.assertEqual([turn["type"] for turn in turns_v3], ["user", "assistant"])
        self.assertEqual(
            turns_v3[1]["content"][0]["result"],
            "Launching skill: manga-workflow",
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
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["type"], "user")
        self.assertEqual(turns[1]["type"], "assistant")

        assistant_blocks = turns[1]["content"]
        self.assertEqual(assistant_blocks[0]["type"], "text")
        self.assertEqual(assistant_blocks[1]["type"], "tool_use")
        self.assertEqual(assistant_blocks[1]["name"], "Skill")
        self.assertEqual(assistant_blocks[1]["result"], "Launching skill: manga-workflow")
        self.assertIn("skill_content", assistant_blocks[1])


if __name__ == "__main__":
    unittest.main()
