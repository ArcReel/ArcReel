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
                                    "header": "镜头效果",
                                    "question": "请选择镜头效果",
                                    "multiSelect": True,
                                    "options": [
                                        {"label": "手持摄影感", "description": "增加紧张感"},
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
                        "header": "镜头效果",
                        "question": "请选择镜头效果",
                        "multiSelect": True,
                        "options": [
                            {"label": "手持摄影感", "description": "增加紧张感"},
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
                        "thinking": "正在整理提问内容",
                        "signature": "",
                    },
                    {
                        "type": "tool_use",
                        "id": "ask-1",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "测试提问",
                                    "question": "请选择一个模式",
                                    "multiSelect": False,
                                    "options": [
                                        {"label": "说书+画面模式", "description": "默认模式"},
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
            "thinking": "正在整理提问内容",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "tool_use",
            "id": "ask-1",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "测试提问",
                        "question": "请选择一个模式",
                        "multiSelect": False,
                        "options": [
                            {"label": "说书+画面模式", "description": "默认模式"},
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
                                    "header": "镜头效果",
                                    "question": "请选择镜头效果",
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
                        "header": "镜头效果",
                        "question": "请选择镜头效果",
                        "multiSelect": True,
                        "options": [],
                    },
                ],
            },
        }
        projector.draft._blocks_by_index[1] = {"type": "text", "text": "继续补充说明"}

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
                        "thinking": "第一次准备提问",
                        "signature": "",
                    },
                    {
                        "type": "tool_use",
                        "id": "ask-invalid",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "无效提问",
                                    "question": "无效",
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
                        "thinking": "修正参数后重新提问",
                        "signature": "",
                    },
                    {
                        "type": "tool_use",
                        "id": "ask-valid",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "视觉风格",
                                    "question": "请选择风格",
                                    "multiSelect": True,
                                    "options": [{"label": "赛博朋克", "description": "高对比"}],
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
            "thinking": "修正参数后重新提问",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "tool_use",
            "id": "ask-valid",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "视觉风格",
                        "question": "请选择风格",
                        "multiSelect": True,
                        "options": [{"label": "赛博朋克", "description": "高对比"}],
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
                        "thinking": "正在整理提问内容",
                        "signature": "",
                    },
                    {
                        "type": "tool_use",
                        "id": "ask-1",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "测试提问",
                                    "question": "请选择一个模式",
                                    "multiSelect": False,
                                    "options": [
                                        {"label": "说书+画面", "description": "默认模式"},
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
                        "thinking": "正在整理提问内容",
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
                            '{"questions":[{"header":"测试提问","question":"请选择一个模式",'
                            '"multiSelect":false,"options":[{"label":"说书+画面","description":"默认模式"}]}]}'
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
                    "content": "使用提问工具向我提问",
                    "uuid": "user-1",
                    "timestamp": "2026-02-28T12:33:25.418Z",
                },
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "正在组织提问",
                            "signature": "",
                        },
                        {
                            "type": "text",
                            "text": "我现在调用提问工具。",
                        },
                        {
                            "type": "tool_use",
                            "id": "ask-1",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": [
                                    {
                                        "header": "测试问题",
                                        "question": "接下来想测试什么？",
                                        "multiSelect": False,
                                        "options": [
                                            {"label": "仅测试界面", "description": "不继续其他任务"},
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
            "thinking": "正在组织提问",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "text",
            "text": "我现在调用提问工具。",
        }
        projector.draft._blocks_by_index[2] = {
            "type": "tool_use",
            "id": "ask-1",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "测试问题",
                        "question": "接下来想测试什么？",
                        "multiSelect": False,
                        "options": [
                            {"label": "仅测试界面", "description": "不继续其他任务"},
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
                        "content": 'User has answered: "接下来想测试什么？"="仅测试界面"',
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
                    "content": "使用提问工具向我提一个选项很多的问题",
                    "uuid": "user-1",
                    "timestamp": "2026-02-28T13:11:22.739Z",
                },
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "第一次尝试",
                            "signature": "",
                        },
                        {
                            "type": "tool_use",
                            "id": "ask-invalid",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": [
                                    {
                                        "header": "视觉风格",
                                        "question": "请选择风格",
                                        "multiSelect": True,
                                        "options": [{"label": "赛博朋克", "description": "高对比"}],
                                    },
                                ],
                                "reason": "invalid extra field",
                            },
                            "result": "<tool_use_error>InputValidationError</tool_use_error>",
                            "is_error": True,
                        },
                        {
                            "type": "thinking",
                            "thinking": "修正后重试",
                            "signature": "",
                        },
                        {
                            "type": "tool_use",
                            "id": "ask-valid",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": [
                                    {
                                        "header": "视觉风格",
                                        "question": "请选择风格",
                                        "multiSelect": True,
                                        "options": [{"label": "赛博朋克", "description": "高对比"}],
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
            "thinking": "修正后重试",
        }
        projector.draft._blocks_by_index[1] = {
            "type": "tool_use",
            "id": "ask-valid",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "视觉风格",
                        "question": "请选择风格",
                        "multiSelect": True,
                        "options": [{"label": "赛博朋克", "description": "高对比"}],
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
                        "content": 'User has answered: "请选择风格"="赛博朋克"',
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
