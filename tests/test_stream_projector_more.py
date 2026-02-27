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
