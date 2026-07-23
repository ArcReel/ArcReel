import base64
import json
import math

from server.agent_runtime.failure_observation import (
    build_startup_failure_observation,
    build_turn_failure_observation,
)


def test_startup_observation_remains_json_safe_for_unprintable_unknown_values() -> None:
    class UnprintableError(RuntimeError):
        def __str__(self) -> str:
            raise RuntimeError("broken __str__")

    exc = UnprintableError()
    exc.future_payload = {"non_finite_metric": math.nan, "opaque": object()}  # type: ignore[attr-defined]

    observation = build_startup_failure_observation(
        exc,
        project_name="demo",
        session_id=None,
        sdk_stderr="",
    )

    assert observation["summary"]["type"] == "UnprintableError"
    assert "UnprintableError" in observation["summary"]["message"]
    assert observation["raw"]["exception_chain"][0]["attributes"]["future_payload"]["non_finite_metric"] == "nan"
    # FastAPI / DB 边界使用严格 JSON 时也不能再次把原始启动异常遮蔽掉。
    json.dumps(observation, allow_nan=False)


def test_binary_payload_redacts_embedded_text_credentials_without_discarding_other_bytes() -> None:
    secret = b"bytes-secret-must-not-leak"
    exc = RuntimeError("binary response")
    exc.response_body = b"prefix\xff\nAuthorization: Bearer " + secret + b"\nsuffix"  # type: ignore[attr-defined]

    observation = build_startup_failure_observation(
        exc,
        project_name="demo",
        session_id=None,
        sdk_stderr="",
    )

    encoded = observation["raw"]["exception_chain"][0]["attributes"]["response_body"]["data"]
    sanitized = base64.b64decode(encoded)
    assert secret not in sanitized
    assert b"Authorization: " + "••••".encode() in sanitized
    assert sanitized.startswith(b"prefix\xff")
    assert sanitized.endswith(b"suffix")


def test_startup_observation_redacts_prefixed_environment_secret_names() -> None:
    secrets = ["openai-secret", "dashscope-secret", "custom-secret"]
    observation = build_startup_failure_observation(
        RuntimeError("provider failed"),
        project_name="demo",
        session_id=None,
        sdk_stderr=(f"OPENAI_API_KEY={secrets[0]}\nDASHSCOPE_API_KEY: {secrets[1]}\nMY_AUTH_TOKEN={secrets[2]}"),
    )

    rendered = json.dumps(observation, ensure_ascii=False)
    assert all(secret not in rendered for secret in secrets)
    assert rendered.count("••••") == 3


def test_turn_observation_falls_back_to_result_message_when_assistant_has_no_text() -> None:
    observation = build_turn_failure_observation(
        assistant_message={"type": "assistant", "error": "api_error", "content": []},
        result_message={
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "errors": ["upstream rejected the selected model"],
        },
        project_name="demo",
        session_id="session-1",
    )

    assert observation["summary"]["source"] == "sdk_assistant"
    assert observation["summary"]["message"] == "upstream rejected the selected model"
