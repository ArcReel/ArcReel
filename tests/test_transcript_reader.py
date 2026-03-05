"""Unit tests for TranscriptReader."""

import json
from pathlib import Path

from server.agent_runtime.transcript_reader import TranscriptReader


class TestTranscriptReader:
    def test_read_jsonl_transcript_grouped(self, tmp_path):
        """Test reading SDK JSONL transcript with message grouping."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        # Create mock SDK transcript location
        encoded_path = str(project_root).replace("/", "-")
        claude_dir = tmp_path / ".claude" / "projects" / encoded_path
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
                "stop_reason": "end_turn",
                "is_error": False,
                "uuid": "result-789",
                "timestamp": "2026-02-09T08:00:04Z",
            },
        ]

        with open(transcript_file, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        # Create reader with custom claude projects dir
        reader = TranscriptReader(tmp_path, project_root=project_root)
        reader._claude_projects_dir = tmp_path / ".claude" / "projects"

        # Read messages (now returns grouped turns)
        turns = reader.read_messages("internal-id", sdk_session_id)

        assert len(turns) == 2  # user turn, assistant turn (result eliminated)

        # Check user turn (content is now normalized to array)
        assert turns[0]["type"] == "user"
        assert len(turns[0]["content"]) == 1
        assert turns[0]["content"][0]["type"] == "text"
        assert turns[0]["content"][0]["text"] == "Hello, Claude!"
        assert turns[0]["uuid"] == "user-123"

        # Check assistant turn
        assert turns[1]["type"] == "assistant"
        assert len(turns[1]["content"]) == 1
        assert turns[1]["content"][0]["type"] == "text"
        assert turns[1]["content"][0]["text"] == "Hello! How can I help you?"

    def test_tool_use_and_result_pairing(self, tmp_path):
        """Test that tool_use and tool_result are paired correctly."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        encoded_path = str(project_root).replace("/", "-")
        claude_dir = tmp_path / ".claude" / "projects" / encoded_path
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

        reader = TranscriptReader(tmp_path, project_root=project_root)
        reader._claude_projects_dir = tmp_path / ".claude" / "projects"

        turns = reader.read_messages("internal-id", sdk_session_id)

        # Should be 2 turns: user and assistant (tool_result attached to assistant)
        assert len(turns) == 2

        # Check user turn
        assert turns[0]["type"] == "user"

        # Check assistant turn - should have all content merged
        assert turns[1]["type"] == "assistant"
        content = turns[1]["content"]
        assert len(content) == 3  # text, tool_use, text

        # Check tool_use has result attached
        tool_use = content[1]
        assert tool_use["type"] == "tool_use"
        assert tool_use["name"] == "Read"
        assert tool_use["result"] == "File contents here"

    def test_tool_use_result_without_type_pairing(self, tmp_path):
        """Test tool_use_result payloads without explicit type are paired correctly."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        encoded_path = str(project_root).replace("/", "-")
        claude_dir = tmp_path / ".claude" / "projects" / encoded_path
        claude_dir.mkdir(parents=True)

        sdk_session_id = "tool-result-plain-session"
        transcript_file = claude_dir / f"{sdk_session_id}.jsonl"

        entries = [
            {
                "type": "user",
                "message": {"content": "Run Read tool"},
                "uuid": "user-plain-1",
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-plain-123",
                            "name": "Read",
                            "input": {"file_path": "/tmp/plain.txt"},
                        }
                    ],
                },
                "uuid": "assistant-plain-1",
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "tool_use_id": "tool-plain-123",
                            "content": "plain result text",
                            "is_error": False,
                        }
                    ],
                },
                "uuid": "tool-result-plain-1",
            },
        ]

        with open(transcript_file, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        reader = TranscriptReader(tmp_path, project_root=project_root)
        reader._claude_projects_dir = tmp_path / ".claude" / "projects"

        turns = reader.read_messages("internal-id", sdk_session_id)
        assert len(turns) == 2
        assert turns[1]["type"] == "assistant"
        tool_use = turns[1]["content"][0]
        assert tool_use["type"] == "tool_use"
        assert tool_use["result"] == "plain result text"

    def test_skill_content_attached(self, tmp_path):
        """Test that Skill content is attached to Skill tool_use."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        encoded_path = str(project_root).replace("/", "-")
        claude_dir = tmp_path / ".claude" / "projects" / encoded_path
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

        reader = TranscriptReader(tmp_path, project_root=project_root)
        reader._claude_projects_dir = tmp_path / ".claude" / "projects"

        turns = reader.read_messages("internal-id", sdk_session_id)

        # Should be 2 turns: user and assistant
        assert len(turns) == 2

        # Check assistant turn
        assistant_turn = turns[1]
        assert assistant_turn["type"] == "assistant"

        # Check Skill tool_use has both result and skill_content attached
        skill_block = assistant_turn["content"][0]
        assert skill_block["type"] == "tool_use"
        assert skill_block["name"] == "Skill"
        assert skill_block["result"] == "Launching skill: commit"
        assert "skill_content" in skill_block
        assert "Base directory for this skill:" in skill_block["skill_content"]

    def test_read_legacy_json_transcript_returns_empty(self, tmp_path):
        """Legacy JSON transcripts are no longer used for history rendering."""
        transcripts_dir = tmp_path / "transcripts"
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

        reader = TranscriptReader(tmp_path)
        messages = reader.read_messages(session_id)

        assert messages == []

    def test_subagent_user_metadata_is_preserved_and_filtered_in_history(self, tmp_path):
        """Subagent metadata from transcript must be preserved for turn filtering."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        encoded_path = str(project_root).replace("/", "-")
        claude_dir = tmp_path / ".claude" / "projects" / encoded_path
        claude_dir.mkdir(parents=True)

        sdk_session_id = "subagent-meta-session"
        transcript_file = claude_dir / f"{sdk_session_id}.jsonl"

        entries = [
            {
                "type": "user",
                "message": {"content": "继续任务"},
                "uuid": "user-root",
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "task-1",
                            "name": "Task",
                            "input": {"description": "检查项目状态"},
                        }
                    ],
                },
                "uuid": "assistant-task-1",
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "text", "text": "subagent telemetry that should be hidden"}
                    ]
                },
                "parent_tool_use_id": "task-1",
                "sourceToolAssistantUUID": "assistant-task-1",
                "isSidechain": True,
                "uuid": "user-subagent-telemetry-1",
            },
        ]

        with open(transcript_file, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        reader = TranscriptReader(tmp_path, project_root=project_root)
        reader._claude_projects_dir = tmp_path / ".claude" / "projects"

        raw_messages = reader.read_raw_messages("internal-id", sdk_session_id)
        assert len(raw_messages) == 3
        telemetry = raw_messages[2]
        assert telemetry["type"] == "user"
        assert telemetry.get("parent_tool_use_id") == "task-1"
        assert telemetry.get("sourceToolAssistantUUID") == "assistant-task-1"
        assert telemetry.get("isSidechain") == True

        turns = reader.read_messages("internal-id", sdk_session_id)
        assert [turn["type"] for turn in turns] == ["user", "assistant"]
        assert len(turns[1]["content"]) == 1
        assert turns[1]["content"][0]["type"] == "tool_use"

    def test_read_empty_returns_empty_list(self, tmp_path):
        """Test that reading non-existent transcript returns empty list."""
        reader = TranscriptReader(tmp_path)
        messages = reader.read_messages("nonexistent")
        assert messages == []

    def test_exists_with_sdk_session(self, tmp_path):
        """Test exists() method with SDK session ID."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        # Create mock SDK transcript
        encoded_path = str(project_root).replace("/", "-")
        claude_dir = tmp_path / ".claude" / "projects" / encoded_path
        claude_dir.mkdir(parents=True)

        sdk_session_id = "sdk-123"
        transcript_file = claude_dir / f"{sdk_session_id}.jsonl"
        transcript_file.write_text("{}\n")

        reader = TranscriptReader(tmp_path, project_root=project_root)
        reader._claude_projects_dir = tmp_path / ".claude" / "projects"

        assert reader.exists("internal-id", sdk_session_id)
        assert not reader.exists("internal-id", "nonexistent")
