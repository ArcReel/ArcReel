import json

from pydantic import BaseModel

from lib.logging_utils import format_kwargs_for_log, sanitize_diagnostic_payload


def test_diagnostic_payload_preserves_unknown_fields_without_truncation():
    long_detail = "observed-" * 200
    assert sanitize_diagnostic_payload(
        {
            "api_key": "sk-secret",
            "token_count": 42,
            "secret_reason": long_detail,
            "cookie_policy": "same-site",
        }
    ) == {
        "api_key": "••••",
        "token_count": 42,
        "secret_reason": long_detail,
        "cookie_policy": "same-site",
    }


def test_diagnostic_payload_redacts_credentials_embedded_in_text():
    sanitized = sanitize_diagnostic_payload(
        {
            "stderr": (
                "prefix Authorization: Basic dXNlcjpwYXNz; "
                "Proxy-Authorization=Bearer abc123\nhttps://x.test?a=1&signature=signed"
            )
        }
    )
    assert sanitized == {
        "stderr": ("prefix Authorization: ••••; Proxy-Authorization=••••\nhttps://x.test?a=1&signature=••••")
    }


def test_diagnostic_payload_redacts_camel_case_credentials():
    sanitized = sanitize_diagnostic_payload(
        {
            "clientSecret": "structured-secret",
            "providerApiKey": "provider-secret",
            "githubAccessToken": "github-secret",
            "tokenCount": 42,
            "stderr": (
                'refreshToken=refresh-secret idToken="identity-secret" '
                "providerApiKey=embedded-provider githubAccessToken=embedded-github"
            ),
        }
    )
    assert sanitized == {
        "clientSecret": "••••",
        "providerApiKey": "••••",
        "githubAccessToken": "••••",
        "tokenCount": 42,
        "stderr": 'refreshToken=•••• idToken="••••" providerApiKey=•••• githubAccessToken=••••',
    }


def test_log_formatter_redacts_prefixed_camel_case_credentials():
    assert json.loads(
        format_kwargs_for_log(
            {
                "providerApiKey": "provider-secret",
                "githubAccessToken": "github-secret",
                "tokenCount": 42,
            }
        )
    ) == {
        "providerApiKey": "prov…cret",
        "githubAccessToken": "gith…cret",
        "tokenCount": 42,
    }


def test_diagnostic_payload_redacts_private_keys_and_pem_blocks():
    pem = "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"
    assert sanitize_diagnostic_payload(
        {
            "private_key": pem,
            "servicePrivateKey": pem,
            "signingKey": "signing-material",
            "stderr": f"privateKey={pem}",
        }
    ) == {
        "private_key": "••••",
        "servicePrivateKey": "••••",
        "signingKey": "••••",
        "stderr": "privateKey=••••",
    }


def test_diagnostic_payload_redacts_cloud_credentials_and_url_userinfo():
    assert sanitize_diagnostic_payload(
        {
            "aws_secret_access_key": "aws-secret",
            "providerSecretAccessKey": "provider-secret",
            "stderr": (
                "AWS_SECRET_ACCESS_KEY=embedded-aws "
                "postgresql://user:db-password@db/app redis://:cache-password@cache:6379/0"
            ),
        }
    ) == {
        "aws_secret_access_key": "••••",
        "providerSecretAccessKey": "••••",
        "stderr": "AWS_SECRET_ACCESS_KEY=•••• postgresql://user:••••@db/app redis://:••••@cache:6379/0",
    }


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


def test_bytearray_summarized():
    out = format_kwargs_for_log({"image": bytearray(b"\x00\x01\x02\x03\x04")})
    assert json.loads(out) == {"image": "<bytes:5>"}


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


def test_fallback_returns_fixed_placeholder_not_raw_repr():
    """_to_safe 抛错时必须返回固定占位符，不能回退到 repr(payload)，
    否则会把未脱敏的原始对象内容（含敏感字段字面量）重新带回日志。"""

    class Disaster:
        def __repr__(self) -> str:
            raise RuntimeError("repr exploded")

    out = format_kwargs_for_log(Disaster())
    assert out == "<unserializable>"


def test_fallback_does_not_leak_sensitive_via_repr(monkeypatch):
    """即使 _to_safe 抛错，也不应通过 repr 路径把 api_key 等字面量泄漏到日志。"""
    from lib import logging_utils

    def boom(*args, **kwargs):
        raise RuntimeError("simulated _to_safe failure")

    monkeypatch.setattr(logging_utils, "_to_safe", boom)
    out = logging_utils.format_kwargs_for_log({"api_key": "sk-real-secret-1234"})
    assert "sk-real-secret-1234" not in out
    assert out == "<unserializable>"


def test_sensitive_key_with_numeric_value_masked():
    out = format_kwargs_for_log({"password": 123456, "pin_token": 9999})
    parsed = json.loads(out)
    assert parsed["password"] == "••••"
    assert parsed["pin_token"] == "••••"


def test_sensitive_key_with_bool_value_masked():
    out = format_kwargs_for_log({"api_key": True})
    assert json.loads(out)["api_key"] == "••••"


def test_pydantic_class_not_called_as_instance():
    class Req(BaseModel):
        prompt: str

    out = format_kwargs_for_log({"cls": Req})
    parsed = json.loads(out)
    assert isinstance(parsed["cls"], str)
    assert "Req" in parsed["cls"]
