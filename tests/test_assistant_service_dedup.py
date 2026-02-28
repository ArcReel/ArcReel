from server.agent_runtime.service import AssistantService

def test_content_key_extracts_thinking():
    # Test text
    msg1 = {"type": "assistant", "content": [{"text": "hello"}]}
    assert AssistantService._content_key(msg1) == "content:assistant:t:hello"
    
    # Test thinking
    msg2 = {"type": "assistant", "content": [{"thinking": "hmm... let me think about this"}]}
    assert AssistantService._content_key(msg2) == "content:assistant:th:hmm... let me think about this"
    
    # Test truncation
    msg3 = {"type": "assistant", "content": [{"thinking": "A" * 100}]}
    assert AssistantService._content_key(msg3) == "content:assistant:th:" + "A" * 50

def test_content_key_multiple_blocks():
    msg = {
        "type": "assistant", 
        "content": [
            {"thinking": "hmm"},
            {"text": "ok"},
            {"id": "t1"}
        ]
    }
    assert AssistantService._content_key(msg) == "content:assistant:th:hmm/t:ok/u:t1"

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

