import asyncio
import json
import sys

import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError, CLINotFoundError, ProcessError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from server.agent_runtime.failure_observation import (
    build_startup_failure_observation,
    build_turn_failure_observation,
)


def test_startup_observation_uses_standard_exception_chain_without_serializing_attributes() -> None:
    root = LookupError("credential lookup failed")
    try:
        raise RuntimeError("provider failed") from root
    except RuntimeError as exc:
        exc.response = {"future_payload": object()}
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


def _turn_key(*, assistant_message=None, result_message=None) -> str:
    observation = build_turn_failure_observation(
        assistant_message=assistant_message,
        result_message=result_message,
        project_name="demo",
        session_id="session-1",
    )
    return observation["summary"]["key"]


@pytest.mark.parametrize(
    ("assistant_message", "result_message", "key"),
    [
        # SDK 的 AssistantMessageError 枚举原样作为 key
        ({"error": "rate_limit"}, {"subtype": "success", "is_error": True, "api_error_status": 500}, "rate_limit"),
        # SDK 自己也说不清的 unknown 不算证据，继续看 result
        ({"error": "unknown"}, {"subtype": "error_max_turns", "is_error": True}, "error_max_turns"),
        (None, {"subtype": "error_max_budget_usd", "is_error": True}, "error_max_budget_usd"),
        # 只有 HTTP 状态时按状态归到 SDK 的同一组枚举
        (None, {"subtype": "success", "is_error": True, "api_error_status": 401}, "authentication_failed"),
        (None, {"subtype": "success", "is_error": True, "api_error_status": 429}, "rate_limit"),
        (None, {"subtype": "success", "is_error": True, "api_error_status": 529}, "server_error"),
        (None, {"subtype": "success", "is_error": True, "api_error_status": 404}, "invalid_request"),
        # 没有可用的结构化证据：通用 key，不解析错误文案
        ({"error": "api_error", "content": [{"type": "text", "text": "401 Unauthorized"}]}, None, "turn_failed"),
        (None, {"subtype": "error_during_execution", "is_error": True, "errors": ["rate limit"]}, "turn_failed"),
    ],
)
def test_turn_failure_key_comes_only_from_structured_evidence(assistant_message, result_message, key) -> None:
    assert _turn_key(assistant_message=assistant_message, result_message=result_message) == key


def _startup_key(exc: BaseException) -> str:
    return build_startup_failure_observation(exc, project_name="demo", session_id=None, sdk_stderr="")["summary"]["key"]


def test_startup_failure_key_follows_the_exception_class_and_its_cause_chain() -> None:
    assert _startup_key(CLINotFoundError("Claude Code not found")) == "cli_not_found"
    assert _startup_key(ProcessError("exit", exit_code=1)) == "process_failed"
    assert _startup_key(CLIConnectionError("closed")) == "cli_connection_failed"
    assert _startup_key(TimeoutError()) == "startup_timeout"
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = CLINotFoundError("missing")
    assert _startup_key(wrapped) == "cli_not_found"
    assert _startup_key(RuntimeError("cli not found in PATH")) == "startup_failed"


class _NoSubprocessEventLoop(asyncio.SelectorEventLoop):
    """复刻 Windows 的 SelectorEventLoop：沿用 asyncio 基类不支持子进程的实现。"""

    async def _make_subprocess_transport(self, *args, **kwargs):
        return await asyncio.BaseEventLoop._make_subprocess_transport(self, *args, **kwargs)


def test_startup_failure_key_reports_event_loop_without_subprocess_support(monkeypatch) -> None:
    """Windows 上 uvicorn --reload 换成 SelectorEventLoop 后，SDK 拉起 CLI 只得到空消息的连接错误。"""
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    transport = SubprocessCLITransport(prompt="", options=ClaudeAgentOptions(cli_path=sys.executable))

    with asyncio.Runner(loop_factory=_NoSubprocessEventLoop) as runner, pytest.raises(CLIConnectionError) as caught:
        runner.run(transport.connect())

    assert isinstance(caught.value.__cause__, NotImplementedError)
    assert _startup_key(caught.value) == "subprocess_unsupported"


def test_not_implemented_outside_cli_connection_keeps_generic_key() -> None:
    assert _startup_key(NotImplementedError()) == "startup_failed"
    wrapped = RuntimeError("provider")
    wrapped.__cause__ = NotImplementedError()
    assert _startup_key(wrapped) == "startup_failed"
