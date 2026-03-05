from server.agent_runtime.service import AssistantService

def test_fingerprint_extracts_thinking():
    # Test text
    msg1 = {"type": "assistant", "content": [{"text": "hello"}]}
    assert AssistantService._fingerprint(msg1) == "fp:assistant:t:hello"

    # Test thinking (truncated to 200 chars)
    thinking_text = "hmm... let me think about this"
    msg2 = {"type": "assistant", "content": [{"thinking": thinking_text}]}
    assert AssistantService._fingerprint(msg2) == f"fp:assistant:th:{thinking_text}"

    # Test long thinking (truncated to 200 chars)
    long_thinking = "A" * 300
    msg3 = {"type": "assistant", "content": [{"thinking": long_thinking}]}
    assert AssistantService._fingerprint(msg3) == f"fp:assistant:th:{long_thinking[:200]}"

def test_fingerprint_multiple_blocks():
    msg = {
        "type": "assistant",
        "content": [
            {"thinking": "hmm"},
            {"text": "ok"},
            {"id": "t1"}
        ]
    }
    assert AssistantService._fingerprint(msg) == "fp:assistant:th:hmm/t:ok/u:t1"

def test_fingerprint_ignores_empty_or_other_blocks():
    msg = {
        "type": "assistant",
        "content": [
            {}, # No text, id, or thinking
            {"foo": "bar"}, # Unrecognized content block
            {"text": "valid"}
        ]
    }
    assert AssistantService._fingerprint(msg) == "fp:assistant:t:valid"

def test_fingerprint_result():
    msg = {"type": "result", "subtype": "success", "is_error": False}
    assert AssistantService._fingerprint(msg) == "fp:result:success:False"

def test_fingerprint_returns_none_for_user():
    assert AssistantService._fingerprint({"type": "user", "content": "x"}) is None
