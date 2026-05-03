import json

from pydantic import BaseModel

from lib.logging_utils import format_kwargs_for_log


def test_short_string_passthrough():
    out = format_kwargs_for_log({"prompt": "hello"})
    assert json.loads(out) == {"prompt": "hello"}


def test_long_string_truncated():
    long_text = "a" * 1500
    out = format_kwargs_for_log({"prompt": long_text})
    payload = json.loads(out)
    assert payload["prompt"].startswith("a" * 200)
    assert "truncated, total 1500 chars" in payload["prompt"]


def test_bytes_summarized():
    out = format_kwargs_for_log({"image": b"\x00\x01\x02\x03"})
    assert json.loads(out) == {"image": "<bytes:4>"}


def test_nested_dict_recursion():
    payload = {"outer": {"inner": {"prompt": "x" * 1000}}}
    out = format_kwargs_for_log(payload)
    parsed = json.loads(out)
    assert "truncated" in parsed["outer"]["inner"]["prompt"]


def test_pydantic_model_dump():
    class Req(BaseModel):
        prompt: str
        size: str

    out = format_kwargs_for_log(Req(prompt="hi", size="1024x1024"))
    assert json.loads(out) == {"prompt": "hi", "size": "1024x1024"}


def test_sensitive_key_masked():
    out = format_kwargs_for_log({"api_key": "sk-1234567890abcdef", "model": "gpt-4o"})
    parsed = json.loads(out)
    assert parsed["api_key"] == "sk-1…cdef"
    assert parsed["model"] == "gpt-4o"


def test_short_secret_redacted_to_dots():
    out = format_kwargs_for_log({"token": "short"})
    assert json.loads(out)["token"] == "••••"


def test_long_list_truncated():
    out = format_kwargs_for_log({"messages": list(range(20))})
    parsed = json.loads(out)
    msgs = parsed["messages"]
    assert msgs[:5] == [0, 1, 2, 3, 4]
    assert isinstance(msgs[5], str)
    assert msgs[5] == "<omitted:13>"
    assert msgs[-2:] == [18, 19]


def test_short_list_kept_as_is():
    out = format_kwargs_for_log({"messages": [{"role": "user", "content": "hi"}]})
    parsed = json.loads(out)
    assert parsed["messages"] == [{"role": "user", "content": "hi"}]


def test_image_part_with_mime_type():
    class FakePart:
        mime_type = "image/png"
        data = b"\x89PNG" + b"\x00" * 100

    out = format_kwargs_for_log({"image": FakePart()})
    assert json.loads(out)["image"] == "<image:mime=image/png,bytes=104>"


def test_authorization_header_masked():
    out = format_kwargs_for_log({"headers": {"Authorization": "Bearer eyJabcdefgh"}})
    parsed = json.loads(out)
    assert parsed["headers"]["Authorization"] == "Bear…efgh"


def test_dataclass_serialized():
    from dataclasses import dataclass

    @dataclass
    class Cfg:
        model: str
        temperature: float

    out = format_kwargs_for_log(Cfg(model="gpt-4o", temperature=0.7))
    assert json.loads(out) == {"model": "gpt-4o", "temperature": 0.7}


def test_unserializable_object_falls_back_to_repr():
    class Weird:
        def __repr__(self) -> str:
            return "Weird()"

    out = format_kwargs_for_log({"x": Weird()})
    assert json.loads(out) == {"x": "Weird()"}


def test_sensitive_key_with_nested_dict_value_fully_masked():
    out = format_kwargs_for_log({"api_key": {"value": "supersecret123", "scope": "all"}})
    assert json.loads(out) == {"api_key": "••••"}


def test_sensitive_key_with_list_value_fully_masked():
    out = format_kwargs_for_log({"tokens": ["a-token-1", "a-token-2"]})
    assert json.loads(out) == {"tokens": "••••"}


def test_sensitive_key_with_none_passthrough():
    out = format_kwargs_for_log({"api_key": None})
    assert json.loads(out) == {"api_key": None}


def test_formatter_swallows_internal_errors():
    class Boom:
        def model_dump(self):
            raise RuntimeError("boom")

        def __repr__(self) -> str:
            return "Boom()"

    out = format_kwargs_for_log({"x": Boom()})
    parsed = json.loads(out)
    assert parsed == {"x": "Boom()"}


def test_formatter_returns_fallback_when_repr_explodes():
    class Disaster:
        def __repr__(self) -> str:
            raise RuntimeError("repr exploded")

    out = format_kwargs_for_log(Disaster())
    assert out == "<unserializable>"


def test_pydantic_class_not_called_as_instance():
    class Req(BaseModel):
        prompt: str

    out = format_kwargs_for_log({"cls": Req})
    parsed = json.loads(out)
    assert isinstance(parsed["cls"], str)
    assert "Req" in parsed["cls"]
