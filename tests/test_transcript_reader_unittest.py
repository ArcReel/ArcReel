"""Unit tests for TranscriptReader."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from webui.server.agent_runtime.transcript_reader import TranscriptReader


class TestTranscriptReader(unittest.TestCase):
    def test_read_jsonl_transcript_grouped(self):
        """Test reading SDK JSONL transcript with message grouping."""
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_root = tmppath / "project"
            project_root.mkdir()

            # Create mock SDK transcript location
            encoded_path = str(project_root).replace("/", "-")
            claude_dir = tmppath / ".claude" / "projects" / encoded_path
            claude_dir.mkdir(parents=True)

            sdk_session_id = "test-sdk-session-123"
            transcript_file = claude_dir / f"{sdk_session_id}.jsonl"

            # Write mock transcript entries
            entries = [
                {
                    "type": "queue-operation",
                    "operation": "dequeue",
                    "timestamp": "2026-02-09T08:00:00Z",
                },
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Hello, Claude!"},
                    "uuid": "user-123",
                    "timestamp": "2026-02-09T08:00:01Z",
                },
                {
                    "type": "progress",
                    "data": {"type": "hook_progress"},
                    "timestamp": "2026-02-09T08:00:02Z",
                },
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Hello! How can I help you?"}
                        ],
                    },
                    "uuid": "assistant-456",
                    "timestamp": "2026-02-09T08:00:03Z",
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "sessionId": sdk_session_id,
                    "uuid": "result-789",
                    "timestamp": "2026-02-09T08:00:04Z",
                },
            ]

            with open(transcript_file, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")

            # Create reader with custom claude projects dir
            reader = TranscriptReader(tmppath, project_root=project_root)
            reader._claude_projects_dir = tmppath / ".claude" / "projects"

            # Read messages (now returns grouped turns)
            turns = reader.read_messages("internal-id", sdk_session_id)

            self.assertEqual(len(turns), 3)  # user turn, assistant turn, result

            # Check user turn (content is now normalized to array)
            self.assertEqual(turns[0]["type"], "user")
            self.assertEqual(len(turns[0]["content"]), 1)
            self.assertEqual(turns[0]["content"][0]["type"], "text")
            self.assertEqual(turns[0]["content"][0]["text"], "Hello, Claude!")
            self.assertEqual(turns[0]["uuid"], "user-123")

            # Check assistant turn
            self.assertEqual(turns[1]["type"], "assistant")
            self.assertEqual(len(turns[1]["content"]), 1)
            self.assertEqual(turns[1]["content"][0]["type"], "text")
            self.assertEqual(turns[1]["content"][0]["text"], "Hello! How can I help you?")

            # Check result
            self.assertEqual(turns[2]["type"], "result")
            self.assertEqual(turns[2]["subtype"], "success")

    def test_tool_use_and_result_pairing(self):
        """Test that tool_use and tool_result are paired correctly."""
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_root = tmppath / "project"
            project_root.mkdir()

            encoded_path = str(project_root).replace("/", "-")
            claude_dir = tmppath / ".claude" / "projects" / encoded_path
            claude_dir.mkdir(parents=True)

            sdk_session_id = "tool-test-session"
            transcript_file = claude_dir / f"{sdk_session_id}.jsonl"

            entries = [
                {
                    "type": "user",
                    "message": {"content": "Read the file"},
                    "uuid": "user-1",
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Let me read that file."}
                        ],
                    },
                    "uuid": "assistant-1",
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-123",
                                "name": "Read",
                                "input": {"file_path": "/test.txt"},
                            }
                        ],
                    },
                    "uuid": "assistant-2",
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-123",
                                "content": "File contents here",
                            }
                        ],
                    },
                    "uuid": "tool-result-1",
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "The file contains: File contents here"}
                        ],
                    },
                    "uuid": "assistant-3",
                },
            ]

            with open(transcript_file, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")

            reader = TranscriptReader(tmppath, project_root=project_root)
            reader._claude_projects_dir = tmppath / ".claude" / "projects"

            turns = reader.read_messages("internal-id", sdk_session_id)

            # Should be 2 turns: user and assistant (tool_result attached to assistant)
            self.assertEqual(len(turns), 2)

            # Check user turn
            self.assertEqual(turns[0]["type"], "user")

            # Check assistant turn - should have all content merged
            self.assertEqual(turns[1]["type"], "assistant")
            content = turns[1]["content"]
            self.assertEqual(len(content), 3)  # text, tool_use, text

            # Check tool_use has result attached
            tool_use = content[1]
            self.assertEqual(tool_use["type"], "tool_use")
            self.assertEqual(tool_use["name"], "Read")
            self.assertEqual(tool_use["result"], "File contents here")

    def test_skill_content_attached(self):
        """Test that Skill content is attached to Skill tool_use."""
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_root = tmppath / "project"
            project_root.mkdir()

            encoded_path = str(project_root).replace("/", "-")
            claude_dir = tmppath / ".claude" / "projects" / encoded_path
            claude_dir.mkdir(parents=True)

            sdk_session_id = "skill-test-session"
            transcript_file = claude_dir / f"{sdk_session_id}.jsonl"

            entries = [
                {
                    "type": "user",
                    "message": {"content": "Use commit skill"},
                    "uuid": "user-1",
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "skill-123",
                                "name": "Skill",
                                "input": {"skill": "commit"},
                            }
                        ],
                    },
                    "uuid": "assistant-1",
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "skill-123",
                                "content": "Launching skill: commit",
                            }
                        ],
                    },
                    "uuid": "tool-result-1",
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Base directory for this skill: /test/.claude/skills/commit\n\n# Commit Skill\n\nThis skill helps you commit changes.",
                            }
                        ],
                    },
                    "uuid": "skill-content-1",
                },
            ]

            with open(transcript_file, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")

            reader = TranscriptReader(tmppath, project_root=project_root)
            reader._claude_projects_dir = tmppath / ".claude" / "projects"

            turns = reader.read_messages("internal-id", sdk_session_id)

            # Should be 2 turns: user and assistant
            self.assertEqual(len(turns), 2)

            # Check assistant turn
            assistant_turn = turns[1]
            self.assertEqual(assistant_turn["type"], "assistant")

            # Check Skill tool_use has both result and skill_content attached
            skill_block = assistant_turn["content"][0]
            self.assertEqual(skill_block["type"], "tool_use")
            self.assertEqual(skill_block["name"], "Skill")
            self.assertEqual(skill_block["result"], "Launching skill: commit")
            self.assertIn("skill_content", skill_block)
            self.assertIn("Base directory for this skill:", skill_block["skill_content"])

    def test_read_legacy_json_transcript(self):
        """Test reading legacy JSON transcript file."""
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            transcripts_dir = tmppath / "transcripts"
            transcripts_dir.mkdir()

            session_id = "legacy-session-123"
            transcript_file = transcripts_dir / f"{session_id}.json"

            # Write mock legacy transcript
            legacy_data = {
                "messages": [
                    {"type": "user", "content": "Hello"},
                    {"type": "assistant", "content": "Hi there!"},
                ]
            }
            with open(transcript_file, "w", encoding="utf-8") as f:
                json.dump(legacy_data, f)

            reader = TranscriptReader(tmppath)
            messages = reader.read_messages(session_id)

            # Legacy format is returned as-is (no grouping)
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["content"], "Hello")
            self.assertEqual(messages[1]["content"], "Hi there!")

    def test_read_empty_returns_empty_list(self):
        """Test that reading non-existent transcript returns empty list."""
        with TemporaryDirectory() as tmpdir:
            reader = TranscriptReader(Path(tmpdir))
            messages = reader.read_messages("nonexistent")
            self.assertEqual(messages, [])

    def test_exists_with_sdk_session(self):
        """Test exists() method with SDK session ID."""
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_root = tmppath / "project"
            project_root.mkdir()

            # Create mock SDK transcript
            encoded_path = str(project_root).replace("/", "-")
            claude_dir = tmppath / ".claude" / "projects" / encoded_path
            claude_dir.mkdir(parents=True)

            sdk_session_id = "sdk-123"
            transcript_file = claude_dir / f"{sdk_session_id}.jsonl"
            transcript_file.write_text("{}\n")

            reader = TranscriptReader(tmppath, project_root=project_root)
            reader._claude_projects_dir = tmppath / ".claude" / "projects"

            self.assertTrue(reader.exists("internal-id", sdk_session_id))
            self.assertFalse(reader.exists("internal-id", "nonexistent"))


if __name__ == "__main__":
    unittest.main()
