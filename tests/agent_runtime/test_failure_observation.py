import json

from server.agent_runtime.failure_observation import (
    build_startup_failure_observation,
    build_turn_failure_observation,
)


def test_startup_observation_uses_standard_exception_chain_without_serializing_attributes() -> None:
    root = LookupError("credential lookup failed")
    try:
        raise RuntimeError("provider failed") from root
    except RuntimeError as exc:
        exc.response = {"future_payload": object()}  # type: ignore[attr-defined]
        observation = build_startup_failure_observation(exc, project_name="demo", session_id=None, sdk_stderr="")

    raw_exception = observation["raw"]["exception"]
    assert raw_exception["type"] == "RuntimeError"
    assert "LookupError: credential lookup failed" in raw_exception["traceback"]
    assert "RuntimeError: provider failed" in raw_exception["traceback"]
    assert "response" not in raw_exception
    json.dumps(observation, allow_nan=False)


def test_turn_observation_preserves_unknown_fields_and_redacts_secrets() -> None:
    observation = build_turn_failure_observation(
        assistant_message=None,
        result_message={
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "token": "ghp_structured-secret",
            "token_count": 42,
            "secret_reason": "credential rejected by upstream",
            "cookie_policy": "same-site",
            "authorization_status": "denied",
            "stderr": "Authorization: Bearer embedded-secret",
        },
        project_name="demo",
        session_id="session-1",
    )

    result = observation["raw"]["result_message"]
    assert result["token"] == "••••"
    assert result["token_count"] == 42
    assert result["secret_reason"] == "credential rejected by upstream"
    assert result["cookie_policy"] == "same-site"
    assert result["authorization_status"] == "denied"
    assert result["stderr"] == "Authorization: ••••"


def test_startup_observation_redacts_text_credentials_without_truncation() -> None:
    secrets = ["openai-secret", "custom-secret", "correct horse battery staple"]
    long_detail = "observed-upstream-detail-" * 200
    observation = build_startup_failure_observation(
        RuntimeError("provider failed"),
        project_name="demo",
        session_id=None,
        sdk_stderr=(
            f'OPENAI_API_KEY={secrets[0]}\nMY_AUTH_TOKEN={secrets[1]}\n{{"PASSWORD":"{secrets[2]}"}}\n{long_detail}'
        ),
    )

    rendered = json.dumps(observation, ensure_ascii=False)
    assert all(secret not in rendered for secret in secrets)
    assert long_detail in observation["raw"]["sdk_stderr"]


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
