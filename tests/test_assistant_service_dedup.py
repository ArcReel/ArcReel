from server.agent_runtime.service import AssistantService
import hashlib

def test_content_key_extracts_thinking():
    # Test text
    msg1 = {"type": "assistant", "content": [{"text": "hello"}]}
    assert AssistantService._content_key(msg1) == "content:assistant:t:hello"

    # Test thinking
    thinking_text = "hmm... let me think about this"
    msg2 = {"type": "assistant", "content": [{"thinking": thinking_text}]}
    expected_hash2 = hashlib.md5(thinking_text.encode("utf-8")).hexdigest()
    assert AssistantService._content_key(msg2) == f"content:assistant:th:{expected_hash2}"

    # Test long thinking
    long_thinking = "A" * 100
    msg3 = {"type": "assistant", "content": [{"thinking": long_thinking}]}
    expected_hash3 = hashlib.md5(long_thinking.encode("utf-8")).hexdigest()
    assert AssistantService._content_key(msg3) == f"content:assistant:th:{expected_hash3}"

def test_content_key_multiple_blocks():
    msg = {
        "type": "assistant",
        "content": [
            {"thinking": "hmm"},
            {"text": "ok"},
            {"id": "t1"}
        ]
    }
    hmm_hash = hashlib.md5("hmm".encode("utf-8")).hexdigest()
    assert AssistantService._content_key(msg) == f"content:assistant:th:{hmm_hash}/t:ok/u:t1"

def test_content_key_ignores_empty_or_other_blocks():
    msg = {
        "type": "assistant",
        "content": [
            {}, # No text, id, or thinking
            {"foo": "bar"}, # Unrecognized content block
            {"text": "valid"}
        ]
    }
    assert AssistantService._content_key(msg) == "content:assistant:t:valid"

