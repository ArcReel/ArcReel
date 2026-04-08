from server.agent_runtime import stream_projector as projector_mod


class TestStreamProjectorMore:
    def test_helpers_and_non_groupable_paths(self):
        assert projector_mod._coerce_index(True) is None
        assert projector_mod._coerce_index(3) == 3
        assert projector_mod._coerce_index(" 4 ") == 4
        assert projector_mod._coerce_index("x") is None
        assert projector_mod._safe_json_parse('{"a":1}') == {"a": 1}
        assert projector_mod._safe_json_parse("{bad}") is None

        projector = projector_mod.AssistantStreamProjector()
        # non-dict message is ignored
        update = projector.apply_message("not-a-dict")  # type: ignore[arg-type]
        assert update == {"patch": None, "delta": None, "question": None}

        question = {"type": "ask_user_question", "question_id": "aq-1", "questions": []}
        update = projector.apply_message(question)
        assert update["question"]["question_id"] == "aq-1"

    def test_draft_projector_stream_event_delta_variants(self):
        draft = projector_mod.DraftAssistantProjector()

        # Invalid payload is ignored
        assert draft.apply_stream_event({"event": "bad"}) is None

        # start + block start fallback to default text block
        assert (
            draft.apply_stream_event(
                {
                    "session_id": "sdk-1",
                    "event": {"type": "message_start"},
                }
            )
            is None
        )
        assert (
            draft.apply_stream_event(
                {
                    "session_id": "sdk-1",
                    "event": {"type": "content_block_start", "index": "0", "content_block": None},
                }
            )
            is None
        )

        # empty text chunk ignored
        assert (
            draft.apply_stream_event(
                {
                    "session_id": "sdk-1",
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": ""},
                    },
                }
            )
            is None
        )

        # text delta
        text_delta = draft.apply_stream_event(
            {
                "session_id": "sdk-1",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Hello"},
                },
            }
        )
        assert text_delta["delta_type"] == "text_delta"
        assert text_delta["text"] == "Hello"

        # tool_use json delta: first incomplete then complete
        first_json = draft.apply_stream_event(
            {
                "session_id": "sdk-1",
                "event": {
                    "type": "content_block_delta",
                    "index": "1",
                    "delta": {"type": "input_json_delta", "partial_json": '{"a":'},
                },
            }
        )
        assert first_json["delta_type"] == "input_json_delta"
        second_json = draft.apply_stream_event(
            {
                "session_id": "sdk-1",
                "event": {
                    "type": "content_block_delta",
                    "index": "1",
                    "delta": {"type": "input_json_delta", "partial_json": "1}"},
                },
            }
        )
        assert second_json["delta_type"] == "input_json_delta"

        # thinking delta
        thinking_delta = draft.apply_stream_event(
            {
                "session_id": "sdk-1",
                "event": {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {"type": "thinking_delta", "thinking": "hmm"},
                },
            }
        )
        assert thinking_delta["delta_type"] == "thinking_delta"
        assert thinking_delta["thinking"] == "hmm"

        # unknown delta type -> ignored
        assert (
            draft.apply_stream_event(
                {
                    "session_id": "sdk-1",
                    "event": {
                        "type": "content_block_delta",
                        "index": 3,
                        "delta": {"type": "other"},
                    },
                }
            )
            is None
        )

        turn = draft.build_turn()
        assert turn is not None
        assert turn["uuid"] == "draft-sdk-1"
        assert len(turn["content"]) >= 2

    def test_draft_build_turn_visibility_rules(self):
        draft = projector_mod.DraftAssistantProjector()
        assert draft.build_turn() is None

        draft._blocks_by_index[0] = {"type": "text", "text": "   "}
        assert draft.build_turn() is None

        draft._blocks_by_index[0] = {"type": "thinking", "thinking": "  "}
        assert draft.build_turn() is None

        draft._blocks_by_index[1] = {"type": "tool_use", "input": {}}
        visible = draft.build_turn()
        assert visible is not None
        assert visible["type"] == "assistant"

    def test_build_snapshot_omits_redundant_ask_user_question_draft(self):
        projector = projector_mod.AssistantStreamProjector()
        projector.turns = [
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "ask-1",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "Camera Effects",
                                    "question": "Please select camera effects",
                                    "multiSelect": True,
                                    "options": [
                                        {"label": "Handheld Camera Feel", "description": "Increases tension"},
                                    ],
                                },
                            ],
                        },
                    },
                ],
                "uuid": "assistant-1",
            },
        ]
        projector.draft._session_id = "sdk-1"
        projector.draft._blocks_by_index[0] = {
            "type": "tool_use",
            "id": "ask-1",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "Camera Effects",
                        "question": "Please select camera effects",
                        "multiSelect": True,
                        "options": [
                            {"label": "Handheld Camera Feel", "description": "Increases tension"},
                        ],
                    },
                ],
            },
        }

        snapshot = projector.build_snapshot("session-1", "running")

        assert snapshot["turns"][0]["content"][0]["name"] == "AskUserQuestion"
        assert snapshot["draft_turn"] is None

    def test_build_snapshot_omits_identical_reconnect_draft_with_thinking(self):
        projector = projector_mod.AssistantStreamProjector()
        projector.turns = [
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Organizing question content",
                        "signature": "",
                    },
                    {
                        "type": "tool_use",
                        "id": "ask-1",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "Test Question",
                                    "question": "Please select a mode",
                                    "multiSelect": False,
                                    "options": [
                                        {"label": "Narration + Visual Mode", "description": "Default mode"},
                                    ],
                                },
                            ],
                        },
                    },
                ],
                "uuid": "assistant-1",
            },
        ]
        projector.draft._session_id = "sdk-1"
        projector.draft._blocks_by_index[0] = {
            "type": "thinking",
            "thinking": "Organizing question content",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "tool_use",
            "id": "ask-1",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "Test Question",
                        "question": "Please select a mode",
                        "multiSelect": False,
                        "options": [
                            {"label": "Narration + Visual Mode", "description": "Default mode"},
                        ],
                    },
                ],
            },
        }

        snapshot = projector.build_snapshot("session-1", "running")

        assert snapshot["draft_turn"] is None

    def test_build_snapshot_keeps_mixed_draft_content(self):
        projector = projector_mod.AssistantStreamProjector()
        projector.turns = [
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "ask-1",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "Camera Effects",
                                    "question": "Please select camera effects",
                                    "multiSelect": True,
                                    "options": [],
                                },
                            ],
                        },
                    },
                ],
                "uuid": "assistant-1",
            },
        ]
        projector.draft._session_id = "sdk-1"
        projector.draft._blocks_by_index[0] = {
            "type": "tool_use",
            "id": "ask-1",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "Camera Effects",
                        "question": "Please select camera effects",
                        "multiSelect": True,
                        "options": [],
                    },
                ],
            },
        }
        projector.draft._blocks_by_index[1] = {"type": "text", "text": "Continuing with additional notes"}

        snapshot = projector.build_snapshot("session-1", "running")

        assert snapshot["draft_turn"] is not None

    def test_build_snapshot_omits_suffix_reconnect_draft_after_failed_ask_user_question(self):
        projector = projector_mod.AssistantStreamProjector()
        projector.turns = [
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Preparing to ask the first question",
                        "signature": "",
                    },
                    {
                        "type": "tool_use",
                        "id": "ask-invalid",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "Invalid Question",
                                    "question": "Invalid",
                                    "multiSelect": False,
                                    "options": [{"label": "A", "description": "A"}],
                                },
                            ],
                            "reason": "invalid extra field",
                        },
                        "result": "<tool_use_error>InputValidationError</tool_use_error>",
                        "is_error": True,
                    },
                    {
                        "type": "thinking",
                        "thinking": "Corrected parameters and re-asking",
                        "signature": "",
                    },
                    {
                        "type": "tool_use",
                        "id": "ask-valid",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "Visual Style",
                                    "question": "Please select a style",
                                    "multiSelect": True,
                                    "options": [{"label": "Cyberpunk", "description": "High contrast"}],
                                },
                            ],
                        },
                    },
                ],
                "uuid": "assistant-1",
            },
        ]
        projector.draft._session_id = "sdk-1"
        projector.draft._blocks_by_index[0] = {
            "type": "thinking",
            "thinking": "Corrected parameters and re-asking",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "tool_use",
            "id": "ask-valid",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "Visual Style",
                        "question": "Please select a style",
                        "multiSelect": True,
                        "options": [{"label": "Cyberpunk", "description": "High contrast"}],
                    },
                ],
            },
        }

        snapshot = projector.build_snapshot("session-1", "running")

        assert snapshot["draft_turn"] is None

    def test_build_snapshot_omits_middle_slice_draft_with_trailing_task_progress(self):
        """Draft [text, Agent_tool_use] is a middle slice of committed turn
        [thinking, ToolSearch, text, Agent, task_progress].  Should be hidden."""
        projector = projector_mod.AssistantStreamProjector()
        projector.turns = [
            {
                "type": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "planning", "signature": ""},
                    {
                        "type": "tool_use",
                        "id": "tool-search-1",
                        "name": "ToolSearch",
                        "input": {"query": "select:Agent", "max_results": 1},
                        "result": [{"type": "tool_reference", "tool_name": "Agent"}],
                        "is_error": False,
                    },
                    {"type": "text", "text": "Let me call a subagent:"},
                    {
                        "type": "tool_use",
                        "id": "agent-1",
                        "name": "Agent",
                        "input": {"description": "test", "prompt": "hello"},
                    },
                    {
                        "type": "task_progress",
                        "task_id": "tp-1",
                        "status": "task_started",
                        "description": "test",
                    },
                ],
                "uuid": "assistant-1",
            },
        ]
        # Draft built from stream events — missing thinking/ToolSearch prefix
        # and missing task_progress suffix
        projector.draft._session_id = "sdk-1"
        projector.draft._blocks_by_index[0] = {
            "type": "text",
            "text": "Let me call a subagent:",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "tool_use",
            "id": "agent-1",
            "name": "Agent",
            "input": {"description": "test", "prompt": "hello"},
        }

        snapshot = projector.build_snapshot("session-1", "running")
        assert snapshot["draft_turn"] is None, "Draft that is a middle slice of the committed turn should be hidden"

    def test_stream_delta_hides_duplicate_resume_draft(self):
        projector = projector_mod.AssistantStreamProjector()
        projector.turns = [
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Organizing question content",
                        "signature": "",
                    },
                    {
                        "type": "tool_use",
                        "id": "ask-1",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "Test Question",
                                    "question": "Please select a mode",
                                    "multiSelect": False,
                                    "options": [
                                        {"label": "Narration + Visuals", "description": "Default mode"},
                                    ],
                                },
                            ],
                        },
                    },
                ],
                "uuid": "assistant-1",
            },
        ]

        projector.apply_message(
            {
                "type": "stream_event",
                "session_id": "sdk-1",
                "event": {"type": "message_start"},
            }
        )
        projector.apply_message(
            {
                "type": "stream_event",
                "session_id": "sdk-1",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "thinking",
                        "thinking": "Organizing question content",
                    },
                },
            }
        )
        projector.apply_message(
            {
                "type": "stream_event",
                "session_id": "sdk-1",
                "event": {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "ask-1",
                        "name": "AskUserQuestion",
                        "input": {},
                    },
                },
            }
        )

        update = projector.apply_message(
            {
                "type": "stream_event",
                "session_id": "sdk-1",
                "event": {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": (
                            '{"questions":[{"header":"Test Question","question":"Please select a mode",'
                            '"multiSelect":false,"options":[{"label":"Narration + Visuals","description":"Default mode"}]}]}'
                        ),
                    },
                },
            }
        )

        assert update["delta"] is not None
        assert update["delta"]["draft_turn"] is None

    def test_patch_hides_stale_draft_when_tool_result_updates_last_turn(self):
        projector = projector_mod.AssistantStreamProjector(
            initial_messages=[
                {
                    "type": "user",
                    "content": "Use the question tool to ask me a question",
                    "uuid": "user-1",
                    "timestamp": "2026-02-28T12:33:25.418Z",
                },
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Organizing question",
                            "signature": "",
                        },
                        {
                            "type": "text",
                            "text": "I will now call the question tool.",
                        },
                        {
                            "type": "tool_use",
                            "id": "ask-1",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": [
                                    {
                                        "header": "Test Question",
                                        "question": "What do you want to test next?",
                                        "multiSelect": False,
                                        "options": [
                                            {"label": "UI only", "description": "Do not continue other tasks"},
                                        ],
                                    },
                                ],
                            },
                        },
                    ],
                    "uuid": "assistant-1",
                    "timestamp": "2026-02-28T12:33:33.152Z",
                },
            ]
        )
        projector.draft._session_id = "sdk-1"
        projector.draft._blocks_by_index[0] = {
            "type": "thinking",
            "thinking": "Organizing question",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "text",
            "text": "I will now call the question tool.",
        }
        projector.draft._blocks_by_index[2] = {
            "type": "tool_use",
            "id": "ask-1",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "Test Question",
                        "question": "What do you want to test next?",
                        "multiSelect": False,
                        "options": [
                            {"label": "UI only", "description": "Do not continue other tasks"},
                        ],
                    },
                ],
            },
        }

        update = projector.apply_message(
            {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "ask-1",
                        "content": 'User has answered: "What do you want to test next?"="UI only"',
                        "is_error": False,
                    },
                ],
                "uuid": "user-tool-result-1",
                "timestamp": "2026-02-28T12:33:34.600Z",
                "parent_tool_use_id": "ask-1",
            }
        )

        assert update["patch"] is not None
        assert update["patch"]["patch"]["op"] == "replace_last"
        assert update["patch"]["draft_turn"] is None

    def test_patch_hides_suffix_draft_when_last_turn_contains_failed_and_retried_question(self):
        projector = projector_mod.AssistantStreamProjector(
            initial_messages=[
                {
                    "type": "user",
                    "content": "Use the question tool to ask me a question with many options",
                    "uuid": "user-1",
                    "timestamp": "2026-02-28T13:11:22.739Z",
                },
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "First attempt",
                            "signature": "",
                        },
                        {
                            "type": "tool_use",
                            "id": "ask-invalid",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": [
                                    {
                                        "header": "Visual Style",
                                        "question": "Please select a style",
                                        "multiSelect": True,
                                        "options": [{"label": "Cyberpunk", "description": "High contrast"}],
                                    },
                                ],
                                "reason": "invalid extra field",
                            },
                            "result": "<tool_use_error>InputValidationError</tool_use_error>",
                            "is_error": True,
                        },
                        {
                            "type": "thinking",
                            "thinking": "Retry after correction",
                            "signature": "",
                        },
                        {
                            "type": "tool_use",
                            "id": "ask-valid",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": [
                                    {
                                        "header": "Visual Style",
                                        "question": "Please select a style",
                                        "multiSelect": True,
                                        "options": [{"label": "Cyberpunk", "description": "High contrast"}],
                                    },
                                ],
                            },
                        },
                    ],
                    "uuid": "assistant-1",
                    "timestamp": "2026-02-28T13:11:40.171Z",
                },
            ]
        )
        projector.draft._session_id = "sdk-1"
        projector.draft._blocks_by_index[0] = {
            "type": "thinking",
            "thinking": "Retry after correction",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "tool_use",
            "id": "ask-valid",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "Visual Style",
                        "question": "Please select a style",
                        "multiSelect": True,
                        "options": [{"label": "Cyberpunk", "description": "High contrast"}],
                    },
                ],
            },
        }

        update = projector.apply_message(
            {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "ask-valid",
                        "content": 'User has answered: "Please select a style"="Cyberpunk"',
                        "is_error": False,
                    },
                ],
                "uuid": "user-tool-result-1",
                "timestamp": "2026-02-28T13:11:51.300Z",
                "parent_tool_use_id": "ask-valid",
            }
        )

        assert update["patch"] is not None
        assert update["patch"]["patch"]["op"] == "replace_last"
        assert update["patch"]["draft_turn"] is None
