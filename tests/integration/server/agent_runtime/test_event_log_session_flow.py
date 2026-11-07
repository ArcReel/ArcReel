"""FakeSDKClient-driven end-to-end event logs: monotonically increasing seq and correct types."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_manager import ProjectManager
from server.agent_runtime.event_log import EventLogService, EventLogStore, build_user_entry
from server.agent_runtime.models import LiveMessage, WithdrawalIntent
from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_manager import AgentStartupError, SessionManager, UnrecordedMessageError
from server.agent_runtime.session_store import SessionMetaStore
from tests.fakes import (
    FakeSDKClient,
    ScriptedFrame,
    assistant_frame,
    command_lifecycle_frame,
    empty_sdk_response_stream,
    replay_frame,
    result_frame,
    stream_event_frame,
    system_frame,
)


def user_frame(
    content: str | list[dict[str, Any]],
    *,
    uuid: str | None = None,
    parent_tool_use_id: str | None = None,
    session_id: str = "default",
    tool_use_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Raw CLI user frames: tool results or injected messages."""
    frame: dict[str, Any] = {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": parent_tool_use_id,
        "session_id": session_id,
        "uuid": uuid,
    }
    if tool_use_result is not None:
        frame["tool_use_result"] = tool_use_result
    return frame


def started_frame(index: int = -1, *, session_id: str = "default") -> Callable[[FakeSDKClient], dict[str, Any]]:
    """CLI starts submitted message ``index`` through ``command_lifecycle`` started."""
    return lambda client: command_lifecycle_frame(client.sent_messages[index]["uuid"], "started", session_id=session_id)


SDK_ID = "sdk-e2e-1"


def _session_state_frame(state: str) -> dict[str, Any]:
    return system_frame("session_state_changed", state=state, session_id=SDK_ID)


def _idle_frame() -> dict[str, Any]:
    """CLI reports idle, allowing sessions to leave running."""
    return _session_state_frame("idle")


def _billed_assistant_frame(message_id: str) -> dict[str, Any]:
    """One API request with usage, persisted when its round closes."""
    return assistant_frame(
        {"type": "text", "text": "好的"},
        session_id=SDK_ID,
        message_id=message_id,
        usage={"input_tokens": 10, "output_tokens": 5},
    )


@pytest.fixture
async def manager(tmp_path, file_db_factory):
    return SessionManager(
        project_root=tmp_path,
        meta_store=SessionMetaStore(session_factory=file_db_factory),
        event_log_store=EventLogStore(session_factory=file_db_factory),
    )


def _new_session_frames() -> list[ScriptedFrame]:
    """Complete round: init → user replay → streaming → assistant tool → tool_result → subagent → result."""
    return [
        system_frame("init", session_id=SDK_ID, uuid="init-1"),
        # CLI user replay must skip first messages already logged when sdk_session_id becomes ready.
        replay_frame(session_id=SDK_ID),
        stream_event_frame({"type": "message_start", "message": {"id": "msg_01"}}, uuid="se-1", session_id=SDK_ID),
        stream_event_frame(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "好的"}},
            uuid="se-2",
            session_id=SDK_ID,
        ),
        assistant_frame(
            {"type": "text", "text": "好的"},
            {"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {"command": "ls"}},
            message_id="msg_01",
            uuid="a-1",
            session_id=SDK_ID,
        ),
        user_frame(
            [{"type": "tool_result", "tool_use_id": "tu-1", "content": "file.txt", "is_error": False}],
            uuid="u-tr-1",
            session_id=SDK_ID,
        ),
        assistant_frame(
            {"type": "text", "text": "子智能体输出"},
            message_id="msg_02",
            uuid="a-sub",
            session_id=SDK_ID,
            parent_tool_use_id="tu-1",
        ),
        result_frame(session_id=SDK_ID, uuid="r-1"),
    ]


async def _wait_for_entries(store: EventLogStore, session_id: str, count: int, timeout: float = 5.0) -> list[dict]:  # noqa: ASYNC109 -- Test polling-helper deadlines, not production cancellation semantics.
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        entries = await store.list_after(session_id)
        if len(entries) >= count:
            return entries
        if asyncio.get_running_loop().time() >= deadline:
            return entries
        await asyncio.sleep(0.02)


async def _wait_for_status(manager: SessionManager, session_id: str, status: str, timeout: float = 5.0) -> None:  # noqa: ASYNC109 -- Test polling-helper deadlines, not production cancellation semantics.
    """Poll memory state. CLI idle first changes memory, then DB metadata, then broadcasts runtime_status.
    Cases subsequently reading metadata, subscribing, or resending instead subscribe and wait for terminal broadcasts."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        managed = manager.sessions.get(session_id)
        if managed is not None and managed.status == status:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"session {session_id} did not reach status {status!r} within {timeout}s")


class _InterruptingClient(FakeSDKClient):
    """interrupt() injects optional interruption echoes plus result frames, matching real CLI behavior."""

    def __init__(self, *, echo: bool = True):
        super().__init__(frames=[started_frame(session_id=SDK_ID)])
        self._echo = echo

    async def interrupt(self) -> None:
        self._record("interrupt")
        self.interrupted = True
        if self._echo:
            self.push_frame(user_frame("[Request interrupted by user]", uuid="sdk-echo-1", session_id=SDK_ID))
        self.push_frame(result_frame("error_during_execution", is_error=True, uuid="r-int", session_id=SDK_ID))
        self.push_frame(_idle_frame())


class _CrashBeforeInitClient(FakeSDKClient):
    def __init__(self, stderr_callback=None):
        super().__init__()
        self._stderr_callback = stderr_callback

        self._query = SimpleNamespace(receive_messages=self._crash_before_init)

    async def _crash_before_init(self):
        if self._stderr_callback is not None:
            self._stderr_callback("OPENAI_API_KEY=pre-init-secret\nprovider stderr detail")
        async for frame in empty_sdk_response_stream():
            yield frame
        raise RuntimeError("receive_messages crashed before init")


class _QueryFailureClient(FakeSDKClient):
    async def query(self, prompt, session_id: str = "default") -> None:
        self._record("query")
        raise RuntimeError("query rejected before session init")


class TestNewSessionEventLogFlow:
    async def test_receive_crash_before_init_is_reported_as_structured_startup_failure(self, manager: SessionManager):
        captured_stderr: list = []

        async def build_options(*_args, **kwargs):
            captured_stderr.append(kwargs["stderr"])
            return SimpleNamespace(env=None)

        client = _CrashBeforeInitClient(lambda line: captured_stderr[0](line))

        with (
            patch.object(manager, "_build_options", new=build_options),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            pytest.raises(AgentStartupError) as exc_info,
        ):
            await manager.send_new_session("demo", "hello")

        assert exc_info.value.failure_observation is not None
        assert exc_info.value.failure_observation["phase"] == "startup"
        assert exc_info.value.failure_observation["summary"]["message"] == "receive_messages crashed before init"
        assert exc_info.value.failure_observation["raw"]["sdk_stderr"] == (
            "OPENAI_API_KEY=••••\nprovider stderr detail"
        )

    async def test_query_failure_before_init_is_reported_as_structured_startup_failure(self, manager: SessionManager):
        client = _QueryFailureClient()

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=SimpleNamespace(env=None))),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            pytest.raises(AgentStartupError) as exc_info,
        ):
            await manager.send_new_session("demo", "hello")

        assert exc_info.value.failure_observation is not None
        assert exc_info.value.failure_observation["phase"] == "startup"
        assert exc_info.value.failure_observation["summary"]["message"] == "query rejected before session init"

    async def test_inbox_processor_failure_before_init_is_cleaned_up_and_reported(self, manager: SessionManager):
        client = FakeSDKClient()

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=SimpleNamespace(env=None))),
            patch.object(
                manager,
                "_process_inbox",
                new=AsyncMock(side_effect=RuntimeError("inbox processor crashed before init")),
            ),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            pytest.raises(AgentStartupError) as exc_info,
        ):
            await manager.send_new_session("demo", "hello")

        assert manager.sessions == {}
        assert exc_info.value.failure_observation is not None
        assert exc_info.value.failure_observation["summary"]["message"] == "inbox processor crashed before init"

    async def test_full_round_produces_typed_monotonic_entries(self, manager: SessionManager):
        client = FakeSDKClient(frames=_new_session_frames())
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            user_entry = build_user_entry([{"type": "text", "text": "帮我写分镜"}])
            sdk_id = await manager.send_new_session(
                "demo",
                "帮我写分镜",
                user_entry=user_entry,
                client_key="ck-new-1",
            )

        assert sdk_id == SDK_ID
        # POST admission authoritative row: seq 0 with assigned identity.
        managed = manager.sessions[SDK_ID]
        assert managed.initial_user_log_entry is not None
        assert managed.initial_user_log_entry["seq"] == 0
        assert managed.initial_user_log_entry["type"] == "user"

        entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 4)
        try:
            assert [e["seq"] for e in entries] == [0, 1, 2, 3]
            assert [e["type"] for e in entries] == ["user", "assistant", "tool_result", "assistant"]

            # User rows are written on admission; SDK replay copies add no duplicates.
            assert entries[0]["content"] == [{"type": "text", "text": "帮我写分镜"}]

            # Assistant rows carry message_id for exact draft replacement.
            assert entries[1]["message_id"] == "msg_01"
            assert entries[1]["content"][1]["type"] == "tool_use"

            # tool_result is an independent row referencing tool_use_id, never disguised as user messages.
            assert entries[2]["tool_use_id"] == "tu-1"
            assert entries[2]["content"] == "file.txt"
            assert entries[2]["is_error"] is False

            # Include subagent rows with parent_tool_use_id.
            assert entries[3]["parent_tool_use_id"] == "tu-1"

            # result discards drafts at round completion.
            assert manager.get_draft_state(SDK_ID)["draft"] is None
        finally:
            await manager.close_session(SDK_ID)

    async def test_replayed_echo_persists_user_message_identity_mapping(self, manager: SessionManager):
        """Persist mappings before dropping replay copies; row uuids resolve transcript entry identities."""
        client = FakeSDKClient(frames=_new_session_frames())
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            user_entry = build_user_entry([{"type": "text", "text": "帮我写分镜"}])
            await manager.send_new_session("demo", "帮我写分镜", user_entry=user_entry)

        entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 4)
        try:
            # Timeline unchanged; replay adds no second user row.
            assert [e["type"] for e in entries] == ["user", "assistant", "tool_result", "assistant"]
            linked = await manager.event_log_store.find_user_message_link(SDK_ID, entries[0]["uuid"])
            assert linked == client.sent_messages[0]["uuid"]
        finally:
            await manager.close_session(SDK_ID)

    async def test_sdk_error_becomes_one_turn_failure_event_with_raw_assistant_and_result(
        self,
        manager: SessionManager,
        caplog: pytest.LogCaptureFixture,
    ):
        upstream_message = (
            "There's an issue with the selected model (gpt-5.6-sol). It may not exist or you may not have access to it."
        )
        assistant_error = assistant_frame(
            {"type": "text", "text": upstream_message},
            message_id="msg-error",
            uuid="a-error",
            session_id=SDK_ID,
            model="<synthetic>",
            usage={"kept": "verbatim", "api_key": "sk-ant-api03-secret-value"},
        )
        assistant_error["error"] = "invalid_request"
        assistant_error["message"]["stop_reason"] = "stop_sequence"
        # result lacks text, so summaries must use real-parser-serialized assistant text blocks.
        result_error = result_frame(
            is_error=True,
            api_error_status=404,
            errors=["upstream request failed"],
            session_id=SDK_ID,
            uuid="r-error",
            usage={"request_id": "req-visible"},
        )
        client = FakeSDKClient(
            frames=[
                system_frame("init", session_id=SDK_ID, uuid="init-error"),
                replay_frame(session_id=SDK_ID),
                assistant_error,
                result_error,
                _idle_frame(),
            ]
        )
        fake_options = SimpleNamespace(env=None)
        caplog.set_level("WARNING", logger="server.agent_runtime.entry_pipeline")

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            await manager.send_new_session(
                "demo",
                "你好",
                user_entry=build_user_entry([{"type": "text", "text": "你好"}]),
                client_key="ck-error",
            )

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 2)
            await _wait_for_status(manager, SDK_ID, "error")
            assert [entry["type"] for entry in entries] == ["user", "system"]

            failure_entry = entries[1]
            assert failure_entry["subtype"] == "agent_turn_failure"
            failure = failure_entry["failure"]
            assert failure["phase"] == "turn"
            assert failure["project_name"] == "demo"
            assert failure["session_id"] == SDK_ID
            assert failure["summary"] == {
                "key": "invalid_request",
                "source": "sdk_assistant",
                "type": "invalid_request",
                "status": 404,
                "message": upstream_message,
            }
            assert failure["raw"]["assistant_message"]["model"] == "<synthetic>"
            assert failure["raw"]["assistant_message"]["usage"] == {
                "kept": "verbatim",
                "api_key": "••••",
            }
            assert failure["raw"]["result_message"]["usage"] == {"request_id": "req-visible"}
            assert all(entry["type"] != "assistant" for entry in entries)

            rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
            assert "failure_observation=" in rendered_logs
            assert "invalid_request" in rendered_logs
            assert upstream_message in rendered_logs
            assert "sk-ant-api03-secret-value" not in rendered_logs
        finally:
            await manager.close_session(SDK_ID)

    async def test_interrupt_flow_produces_single_typed_entry(self, manager: SessionManager):
        """Interrupt actions/outcomes are timeline events; racing SDK echoes/result fallback create one typed row."""
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = _InterruptingClient(echo=True)
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            await manager.send_message(
                SDK_ID,
                "写分镜",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "写分镜"}]),
                client_key="ck-int",
            )
            await _wait_for_status(manager, SDK_ID, "running")  # Let query enter drain.
            await manager.interrupt_session(SDK_ID)

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 2)
            await _wait_for_status(manager, SDK_ID, "interrupted")
            assert [e["type"] for e in entries] == ["user", "system"]
            assert entries[1]["subtype"] == "interrupt"
            assert manager.sessions[SDK_ID].status == "interrupted"
            # Racing result fallback/echo creates no second interruption row.
            final_entries = await manager.event_log_store.list_after(SDK_ID)
            assert [e["type"] for e in final_entries] == ["user", "system"]
        finally:
            await manager.close_session(SDK_ID)

    async def test_interrupt_without_sdk_echo_still_produces_typed_entry(self, manager: SessionManager):
        """Without echoes, result(session_status=interrupted) fallback still provides stable interruption timeline entries."""
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = _InterruptingClient(echo=False)
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            await manager.send_message(
                SDK_ID,
                "写分镜",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "写分镜"}]),
                client_key="ck-int2",
            )
            await _wait_for_status(manager, SDK_ID, "running")
            await manager.interrupt_session(SDK_ID)

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 2)
            assert [e["type"] for e in entries] == ["user", "system"]
            assert entries[1]["subtype"] == "interrupt"
        finally:
            await manager.close_session(SDK_ID)

    async def test_ask_user_question_flow_produces_question_and_answer_entries(self, manager: SessionManager):
        """Logs contain AskUserQuestion assistant tool_use questions and typed answer entries."""
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = FakeSDKClient(
            frames=[
                started_frame(session_id=SDK_ID),
                assistant_frame(
                    {
                        "type": "tool_use",
                        "id": "tu-q",
                        "name": "AskUserQuestion",
                        "input": {"questions": [{"question": "继续吗?", "options": [{"label": "继续"}]}]},
                    },
                    message_id="msg_q",
                    uuid="a-q",
                    session_id=SDK_ID,
                ),
                user_frame(
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu-q",
                            "content": 'Your questions have been answered: "继续吗?"="继续".',
                        }
                    ],
                    uuid="u-ans",
                    session_id=SDK_ID,
                    tool_use_result={"questions": [], "answers": {"继续吗?": "继续"}, "annotations": {}},
                ),
                result_frame(session_id=SDK_ID, uuid="r-1"),
            ]
        )
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            await manager.send_message(
                SDK_ID,
                "开始",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "开始"}]),
                client_key="ck-q",
            )

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 3)
            assert [e["type"] for e in entries] == ["user", "assistant", "user"]
            # Questions: assistant AskUserQuestion tool_use with structured questions.
            assert entries[1]["content"][0]["name"] == "AskUserQuestion"
            # Answers: typed structured answers linked by tool_use_id.
            assert entries[2]["subtype"] == "question_answer"
            assert entries[2]["tool_use_id"] == "tu-q"
            assert entries[2]["answers"] == {"继续吗?": "继续"}
            assert entries[2]["is_error"] is False
        finally:
            await manager.close_session(SDK_ID)

    async def test_task_notification_flow_produces_typed_entries(self, manager: SessionManager):
        """Dual task notifications, typed system messages and injected XML user messages, both become system entries without generic user rows."""
        xml = (
            "<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>tu-a</tool-use-id>\n"
            "<status>completed</status>\n<summary>分析完成</summary>\n</task-notification>"
        )
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = FakeSDKClient(
            frames=[
                started_frame(session_id=SDK_ID),
                system_frame(
                    "task_started", task_id="t1", description="分析", tool_use_id="tu-a", uuid="s-1", session_id=SDK_ID
                ),
                user_frame(xml, uuid="n-1", session_id=SDK_ID),
                result_frame(session_id=SDK_ID, uuid="r-1"),
            ]
        )
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            await manager.send_message(
                SDK_ID,
                "跑子智能体",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "跑子智能体"}]),
                client_key="ck-t",
            )

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 3)
            assert [e["type"] for e in entries] == ["user", "system", "system"]
            assert entries[1]["subtype"] == "task_started"
            assert entries[2]["subtype"] == "task_notification"
            assert entries[2]["task_id"] == "t1"
            assert entries[2]["task_status"] == "completed"
            assert entries[2]["summary"] == "分析完成"
        finally:
            await manager.close_session(SDK_ID)

    async def test_retry_with_the_same_client_key_is_not_sent_again(self, manager: SessionManager):
        """Queued retries return the same queued message; postacceptance retries return authoritative rows; CLI receives each only once."""
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = FakeSDKClient()

        async def _send() -> dict[str, Any]:
            return await manager.send_message(
                SDK_ID,
                "继续",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "继续"}]),
                client_key="ck-run",
            )

        with _scripted_client(manager, client):
            first = await _send()
        try:
            assert (await _send())["queued_message"] == first["queued_message"]

            client.push_frame(started_frame(session_id=SDK_ID))
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 1)
            retry = await _send()

            assert retry == {"entry": entries[0]}
            assert entries[0]["uuid"] == first["queued_message"]["id"]
            assert client.sent_queries == ["继续"]
        finally:
            await manager.close_session(SDK_ID)

    async def test_send_failure_withdraws_the_queued_message_and_retry_delivers(self, manager: SessionManager):
        """Delivery failure means admission failure; remove queued messages so same-key retries redeliver to CLI."""
        meta = await manager.meta_store.create("demo", SDK_ID)

        class _FlakyQueryClient(FakeSDKClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fail_next_query = True

            async def query(self, prompt, session_id: str = "default") -> None:
                if self.fail_next_query:
                    self.fail_next_query = False
                    raise RuntimeError("transport down")
                await super().query(prompt, session_id)

        client = _FlakyQueryClient()

        async def _send() -> dict[str, Any]:
            return await manager.send_message(
                SDK_ID,
                "继续",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "继续"}]),
                client_key="ck-fail",
            )

        with _scripted_client(manager, client):
            with pytest.raises(RuntimeError):
                await _send()
            try:
                assert manager.get_queued_messages_snapshot(SDK_ID) == []

                # Actors exit on failure; close sessions to simulate cold recovery before retry.
                await manager.close_session(SDK_ID)
                retry = await _send()

                assert retry["queued_message"]["state"] == "queued"
                assert client.sent_queries == ["继续"]
            finally:
                await manager.close_session(SDK_ID)

    async def test_initial_user_entry_write_failure_reports_error(self, manager: SessionManager):
        """Initial user-row persistence failure explicitly fails admission and puts sessions in observable error states."""
        client = FakeSDKClient(frames=_new_session_frames())
        fake_options = SimpleNamespace(env=None)
        update_status_spy = AsyncMock(wraps=manager.meta_store.update_status)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch.object(
                manager.event_log_store,
                "append_user_entry",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch.object(manager.meta_store, "update_status", new=update_status_spy),
            pytest.raises(RuntimeError),
        ):
            await manager.send_new_session(
                "demo",
                "帮我写分镜",
                user_entry=build_user_entry([{"type": "text", "text": "帮我写分镜"}]),
                client_key="ck-fail-new",
            )

        # Persist observable error instead of silent success.
        meta = await manager.meta_store.get(SDK_ID)
        assert meta is not None
        assert meta.status == "error"
        # Failure cleanup leaves no memory state and disconnects SDK clients.
        assert SDK_ID not in manager.sessions
        assert client.disconnected is True
        # Set memory error before cleanup cancellation of _process_task, avoiding interrupted
        # branch terminal rewrites, which would persist interrupted before error.
        statuses_written = [call.args[1] for call in update_status_spy.call_args_list]
        assert "interrupted" not in statuses_written
        assert statuses_written.count("error") == 1

    async def test_initial_user_entry_failure_skips_finalize_for_early_result(self, manager: SessionManager):
        """After initial user persistence failure, do not finalize immediately following same-round results;
        otherwise broadcast/persist nonerror terminals such as completed before cleanup rewrites
        them to error, causing temporary status jumps."""
        client = FakeSDKClient(
            frames=[
                system_frame("init", session_id=SDK_ID, uuid="init-1"),
                result_frame(session_id=SDK_ID, uuid="r-1"),
            ]
        )
        fake_options = SimpleNamespace(env=None)
        update_status_spy = AsyncMock(wraps=manager.meta_store.update_status)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch.object(
                manager.event_log_store,
                "append_user_entry",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch.object(manager.meta_store, "update_status", new=update_status_spy),
            pytest.raises(RuntimeError),
        ):
            await manager.send_new_session(
                "demo",
                "帮我写分镜",
                user_entry=build_user_entry([{"type": "text", "text": "帮我写分镜"}]),
                client_key="ck-fail-early-result",
            )

        meta = await manager.meta_store.get(SDK_ID)
        assert meta is not None
        assert meta.status == "error"
        # "running" is ordinary new-session persistence; short-circuited result finalization
        # must not write intermediate completed/idle terminals.
        statuses_written = [call.args[1] for call in update_status_spy.call_args_list]
        assert statuses_written == ["running", "error"]

    async def test_initial_user_entry_retry_after_failure_delivers(self, manager: SessionManager):
        """Same-key retry after persistence failure has no residual-entry short-circuit; accept anew and assign seq 0."""
        retry_sdk_id = "sdk-e2e-retry"
        failing_client = FakeSDKClient(frames=_new_session_frames())
        retry_client = FakeSDKClient(
            frames=[
                system_frame("init", session_id=retry_sdk_id, uuid="init-2"),
                result_frame(session_id=retry_sdk_id, uuid="r-2"),
            ]
        )
        clients = iter([failing_client, retry_client])
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: next(clients)),
        ):
            with (
                patch.object(
                    manager.event_log_store,
                    "append_user_entry",
                    new=AsyncMock(side_effect=RuntimeError("db down")),
                ),
                pytest.raises(RuntimeError),
            ):
                await manager.send_new_session(
                    "demo",
                    "帮我写分镜",
                    user_entry=build_user_entry([{"type": "text", "text": "帮我写分镜"}]),
                    client_key="ck-retry-new",
                )

            # Failed SDK_ID has no entries; append genuinely failed, not partially persisted.
            assert not await manager.event_log_store.has_entries(SDK_ID)

            sdk_id = await manager.send_new_session(
                "demo",
                "帮我写分镜",
                user_entry=build_user_entry([{"type": "text", "text": "帮我写分镜"}]),
                client_key="ck-retry-new",
            )
        try:
            assert sdk_id == retry_sdk_id
            managed = manager.sessions[retry_sdk_id]
            assert managed.initial_user_log_entry is not None
            assert managed.initial_user_log_entry["seq"] == 0
            assert managed.initial_user_entry_error is None
            # Retry keys resolve newly written retry-session rows, not failed sessions.
            resolved = await manager.event_log_store.find_by_client_key(retry_sdk_id, "ck-retry-new")
            assert resolved == managed.initial_user_log_entry
        finally:
            await manager.close_session(retry_sdk_id)


async def _next_broadcast(stream, msg_type: str, timeout: float = 5.0) -> dict[str, Any]:  # noqa: ASYNC109 -- Test wait deadlines, not production cancellation semantics.
    """Read the next session broadcast of the specified type."""

    async def _read() -> dict[str, Any]:
        async for event in stream:
            if isinstance(event, LiveMessage) and event.message.get("type") == msg_type:
                return event.message
        raise AssertionError(f"stream ended before {msg_type!r}")

    return await asyncio.wait_for(_read(), timeout=timeout)


class TestSessionStatusFollowsCliIdle:
    """CLI-reported idle governs session status; result marks only round completion."""

    async def _send(self, manager: SessionManager, client: FakeSDKClient) -> None:
        meta = await manager.meta_store.create("demo", SDK_ID)
        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=SimpleNamespace(env=None))),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            await manager.send_message(
                SDK_ID, "写分镜", meta=meta, user_entry=build_user_entry([{"type": "text", "text": "写分镜"}])
            )

    async def _status(self, manager: SessionManager) -> tuple[str, str]:
        meta = await manager.meta_store.get(SDK_ID)
        assert meta is not None
        return manager.sessions[SDK_ID].status, meta.status

    async def test_result_keeps_running_until_cli_idle_then_takes_the_turn_outcome(self, manager: SessionManager):
        client = FakeSDKClient(
            frames=[
                _session_state_frame("running"),
                assistant_frame({"type": "text", "text": "好的"}, uuid="a-1", session_id=SDK_ID),
                result_frame(session_id=SDK_ID, uuid="r-1"),
            ]
        )
        async with manager.stream_messages(SDK_ID, idle_timeout=5) as stream:
            await self._send(manager, client)
            try:
                await _next_broadcast(stream, "log_turn_complete")
                assert await self._status(manager) == ("running", "running")

                client.push_frame(_idle_frame())
                settled = await _next_broadcast(stream, "runtime_status")

                assert settled["status"] == "completed"
                assert await self._status(manager) == ("completed", "completed")
            finally:
                await manager.close_session(SDK_ID)

    async def test_autonomous_turn_before_cli_idle_does_not_flash_a_terminal_status(self, manager: SessionManager):
        """Autonomous rounds after result but before idle never flash terminal states between rounds; use the latest outcome at final idle."""
        client = FakeSDKClient(
            frames=[
                _session_state_frame("running"),
                assistant_frame({"type": "text", "text": "先派个子智能体"}, uuid="a-1", session_id=SDK_ID),
                result_frame("error_during_execution", is_error=True, session_id=SDK_ID, uuid="r-1"),
            ]
        )
        resumed: list[tuple[str, str]] = []
        manager.set_autonomous_turn_listener(lambda project, sid: resumed.append((project, sid)))
        async with manager.stream_messages(SDK_ID, idle_timeout=5) as stream:
            await self._send(manager, client)
            try:
                await _next_broadcast(stream, "log_turn_complete")
                # Subagent completion starts the next CLI round without user messages.
                client.push_frame(assistant_frame({"type": "text", "text": "汇总"}, uuid="a-2", session_id=SDK_ID))
                client.push_frame(result_frame(session_id=SDK_ID, uuid="r-2"))
                await _next_broadcast(stream, "log_turn_complete")
                assert await self._status(manager) == ("running", "running")

                client.push_frame(_idle_frame())
                settled = await _next_broadcast(stream, "runtime_status")

                assert settled["status"] == "completed"
                assert await self._status(manager) == ("completed", "completed")
                assert resumed == []
            finally:
                await manager.close_session(SDK_ID)

    async def test_interrupt_that_reaches_no_turn_does_not_carry_past_cli_idle(self, manager: SessionManager):
        """A missed interruption between result/idle must not affect next-round failure, which remains error after CLI idle."""
        client = FakeSDKClient(
            frames=[
                _session_state_frame("running"),
                assistant_frame({"type": "text", "text": "好的"}, uuid="a-1", session_id=SDK_ID),
                result_frame(session_id=SDK_ID, uuid="r-1"),
            ]
        )
        async with manager.stream_messages(SDK_ID, idle_timeout=5) as stream:
            await self._send(manager, client)
            try:
                await _next_broadcast(stream, "log_turn_complete")
                await manager.interrupt_session(SDK_ID)
                client.push_frame(_idle_frame())
                assert (await _next_broadcast(stream, "runtime_status"))["status"] == "completed"

                client.push_frame(_session_state_frame("running"))
                client.push_frame(assistant_frame({"type": "text", "text": "汇总"}, uuid="a-2", session_id=SDK_ID))
                client.push_frame(result_frame("error_during_execution", is_error=True, session_id=SDK_ID, uuid="r-2"))
                client.push_frame(_idle_frame())
                assert (await _next_broadcast(stream, "runtime_status"))["status"] == "running"
                settled = await _next_broadcast(stream, "runtime_status")

                assert settled["status"] == "error"
            finally:
                await manager.close_session(SDK_ID)

    @pytest.mark.parametrize(
        ("turn_frames", "expected"),
        [
            ([result_frame(session_id=SDK_ID, uuid="r-1")], "completed"),
            ([], "interrupted"),
        ],
        ids=["after-result", "mid-turn"],
    )
    async def test_evicting_a_session_that_is_not_idle_settles_a_terminal_status(
        self, manager: SessionManager, turn_frames, expected
    ):
        """Eviction before CLI idle uses a completed round's outcome, or interrupted if unfinished."""
        client = FakeSDKClient(
            frames=[
                _session_state_frame("running"),
                started_frame(session_id=SDK_ID),
                assistant_frame({"type": "text", "text": "好的"}, uuid="a-1", session_id=SDK_ID),
                *turn_frames,
            ]
        )
        await self._send(manager, client)
        await _wait_for_entries(manager.event_log_store, SDK_ID, 2)

        await manager.close_session(SDK_ID)

        meta = await manager.meta_store.get(SDK_ID)
        assert meta is not None
        assert meta.status == expected


@contextlib.contextmanager
def _scripted_client(manager: SessionManager, client: FakeSDKClient) -> Generator[None]:
    """Sessions receive this substitute when connecting to CLI."""
    with (
        patch.object(manager, "_build_options", new=AsyncMock(return_value=SimpleNamespace(env=None))),
        patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
    ):
        yield


class _NoTranscript:
    async def read_raw_messages(self, sdk_session_id=None, project_cwd=None):
        return []

    async def read_subagent_timelines(self, sdk_session_id=None, project_cwd=None):
        return {}

    async def read_subagent_descriptions(self, sdk_session_id=None, project_cwd=None, tool_use_ids=()):
        return {}


class _EntryStream:
    """Consume session entry streams in the background, extracting specified events in arrival order."""

    def __init__(self, service: AssistantService, session_id: str) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._cursor = 0
        self._changed = asyncio.Event()
        self._task = asyncio.create_task(self._consume(service, session_id))

    async def _consume(self, service: AssistantService, session_id: str) -> None:
        async for event in service.stream_entry_events(session_id):
            self.events.append((event.event or "", event.data if isinstance(event.data, dict) else {}))
            self._changed.set()

    async def next(self, name: str) -> dict[str, Any]:
        """Take the first ``name`` event after cursor and advance cursor past it."""

        async def _wait() -> dict[str, Any]:
            while True:
                for index in range(self._cursor, len(self.events)):
                    if self.events[index][0] == name:
                        self._cursor = index + 1
                        return self.events[index][1]
                self._changed.clear()
                await self._changed.wait()

        return await asyncio.wait_for(_wait(), timeout=5.0)

    async def close(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


@pytest.fixture
async def service(manager: SessionManager, tmp_path) -> AssistantService:
    """AssistantService on real SessionManager, with production send/entry-stream paths."""
    (DataRootLayout(tmp_path).projects_dir / "demo").mkdir(parents=True)
    svc = AssistantService(project_root=tmp_path)
    svc.pm = ProjectManager(tmp_path)
    svc.session_manager = manager
    svc.meta_store = manager.meta_store
    svc.event_log_store = manager.event_log_store
    svc.event_log = EventLogService(manager.event_log_store, _NoTranscript())
    await manager.meta_store.create("demo", SDK_ID)
    return svc


class TestQueuedMessages:
    """Messages sent during replies queue first, entering timelines only when CLI begins processing them."""

    async def test_duplicate_send_waits_for_delivery_and_does_not_accept_a_failed_write(
        self, service: AssistantService
    ):
        entered, release = asyncio.Event(), asyncio.Event()

        class FailingClient(FakeSDKClient):
            async def query(self, prompt, session_id: str = "default") -> None:
                entered.set()
                await release.wait()
                raise RuntimeError("CLI write failed")

        client = FailingClient()
        tasks: list[asyncio.Task] = []
        try:
            with _scripted_client(service.session_manager, client):
                tasks.append(
                    asyncio.create_task(
                        service.send_or_create("demo", "补充要求", session_id=SDK_ID, client_key="same-key")
                    )
                )
                await entered.wait()
                tasks.append(
                    asyncio.create_task(service.session_manager.send_message(SDK_ID, "补充要求", client_key="same-key"))
                )
                scheduled = asyncio.Event()
                asyncio.get_running_loop().call_soon(scheduled.set)
                await scheduled.wait()
                assert not tasks[1].done()
                release.set()
                outcomes = await asyncio.gather(*tasks, return_exceptions=True)
                assert all(isinstance(outcome, Exception) for outcome in outcomes)
                assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await service.session_manager.close_session(SDK_ID)

    async def test_a_send_abandoned_by_eviction_fails_as_a_plain_error_and_leaves_nothing_queued(
        self, service: AssistantService
    ):
        """Actor cancellation on eviction makes in-flight sends raise ordinary exceptions, not CancelledError; remove queued messages and terminalize sessions."""
        entered = asyncio.Event()

        class HangingClient(FakeSDKClient):
            async def query(self, prompt, session_id: str = "default") -> None:
                entered.set()
                await asyncio.Event().wait()

        # Zero disconnect timeout: entered query cannot finish disconnect, so immediately use cancellation fallback without wall-clock waits.
        service.session_manager._session_actor_shutdown_timeout = 0
        with _scripted_client(service.session_manager, HangingClient()):
            sending = asyncio.create_task(
                service.send_or_create("demo", "补充要求", session_id=SDK_ID, client_key="ck-1")
            )
            await asyncio.wait_for(entered.wait(), timeout=5.0)
            await service.session_manager.close_session(SDK_ID)

        outcome = (await asyncio.gather(sending, return_exceptions=True))[0]
        assert isinstance(outcome, Exception)
        assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
        # Send first persists error; eviction finally persists interrupted, whose outcome takes precedence.
        meta = await service.meta_store.get(SDK_ID)
        assert meta is not None
        assert meta.status == "interrupted"

    async def test_a_send_that_resumes_after_a_graceful_eviction_keeps_the_eviction_status(
        self, service: AssistantService
    ):
        """Sends paused in running-state persistence resume after complete eviction with ordinary errors, without replacing eviction terminals with error."""
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        await self._finish_running_turn(service, client)
        update_status = service.meta_store.update_status
        suspended, release = asyncio.Event(), asyncio.Event()

        async def _suspend_running(session_id: str, status: str, *args: Any, **kwargs: Any) -> Any:
            if status == "running":
                suspended.set()
                await release.wait()
            return await update_status(session_id, status, *args, **kwargs)

        with patch.object(service.meta_store, "update_status", new=_suspend_running):
            sending = asyncio.create_task(service.send_or_create("demo", "加一段旁白", session_id=SDK_ID))
            await asyncio.wait_for(suspended.wait(), timeout=5.0)
            # No in-flight command; actors disconnect normally and eviction fully persists terminal states.
            await service.session_manager.close_session(SDK_ID)
            release.set()
            outcome = (await asyncio.gather(sending, return_exceptions=True))[0]

        assert isinstance(outcome, Exception)
        assert SDK_ID not in service.session_manager.sessions
        # Eviction sees registered running sessions and finalizes interrupted; late running writes restore that same terminal afterward.
        meta = await service.meta_store.get(SDK_ID)
        assert meta is not None
        assert meta.status == "interrupted"

    async def _start_running_turn(self, service: AssistantService, client: FakeSDKClient) -> None:
        """Send the first message and await Agent output; the session stays running afterward."""
        with _scripted_client(service.session_manager, client):
            await service.send_or_create("demo", "写分镜", session_id=SDK_ID)
        await _wait_for_entries(service.event_log_store, SDK_ID, 2)

    @staticmethod
    async def _finish_running_turn(service: AssistantService, client: FakeSDKClient) -> None:
        """Finish the round and report CLI idle, awaiting settlement; terminals broadcast after metadata persistence. Waiting only for memory
        would give later subscriptions delayed completed frames and interleave sends with unfinished persistence."""
        async with service.session_manager.stream_messages(SDK_ID, idle_timeout=5) as stream:
            client.push_frame(result_frame(session_id=SDK_ID, uuid="r-1"))
            client.push_frame(_idle_frame())
            while (await _next_broadcast(stream, "runtime_status"))["status"] != "completed":
                pass

    @staticmethod
    def _running_turn_client() -> FakeSDKClient:
        return FakeSDKClient(
            frames=[
                _session_state_frame("running"),
                started_frame(session_id=SDK_ID),
                assistant_frame({"type": "text", "text": "先读剧本"}, uuid="a-1", session_id=SDK_ID),
            ]
        )

    async def test_send_while_running_is_handed_to_the_cli_with_a_uuid_and_queued(self, service: AssistantService):
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            assert (await stream.next("queue"))["messages"] == []

            response = await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID)

            queued = response["queued_message"]
            assert response["entry"] is None
            assert queued["state"] == "queued"
            assert queued["content"] == [{"type": "text", "text": "加一段旁白"}]
            sent = client.sent_messages[-1]
            assert sent["message"]["content"] == "加一段旁白"
            assert sent["uuid"] not in (None, client.sent_messages[0]["uuid"])
            assert (await stream.next("queue_upsert"))["message"] == queued
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [queued]
            assert [entry["type"] for entry in await service.event_log_store.list_after(SDK_ID)] == [
                "user",
                "assistant",
            ]
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_started_logs_the_message_after_earlier_output_and_leaves_the_queue(self, service: AssistantService):
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            queued = (await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID))["queued_message"]
            # Agents still output between send and acceptance.
            client.push_frame(assistant_frame({"type": "text", "text": "读完了"}, uuid="a-2", session_id=SDK_ID))
            client.push_frame(started_frame(session_id=SDK_ID))

            entries = await _wait_for_entries(service.event_log_store, SDK_ID, 4)
            removed = await stream.next("queue_remove")

            assert [entry["type"] for entry in entries] == ["user", "assistant", "assistant", "user"]
            assert entries[2]["uuid"] == "a-2"
            assert entries[3]["uuid"] == queued["id"]
            assert entries[3]["content"] == queued["content"]
            assert removed["id"] == queued["id"]
            # Entries arrive before tray removal; disappearing queued messages already exist on timelines.
            order = [(name, data.get("uuid") or data.get("id")) for name, data in stream.events]
            assert order.index(("entry", queued["id"])) < order.index(("queue_remove", queued["id"]))
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_usage_of_each_turn_is_recorded_against_the_prompt_it_handled(
        self, service: AssistantService, monkeypatch
    ):
        prompts: list[str] = []
        record_turn = service.session_manager.ledger.record_agent_turn

        async def _capture(**kwargs: Any) -> Any:
            prompts.append(kwargs["prompt"])
            return await record_turn(**kwargs)

        monkeypatch.setattr(service.session_manager.ledger, "record_agent_turn", _capture)
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        try:
            await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID)
            # Finish round one before queued messages begin another round.
            client.push_frame(_billed_assistant_frame("msg-1"))
            client.push_frame(result_frame(session_id=SDK_ID, uuid="r-1"))
            client.push_frame(started_frame(session_id=SDK_ID))
            client.push_frame(_billed_assistant_frame("msg-2"))
            client.push_frame(result_frame(session_id=SDK_ID, uuid="r-2"))
            client.push_frame(_idle_frame())
            await _wait_for_status(service.session_manager, SDK_ID, "completed")

            assert prompts == ["写分镜", "加一段旁白"]
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_messages_merged_into_the_running_turn_share_its_usage_prompt(
        self, service: AssistantService, monkeypatch
    ):
        prompts: list[str] = []
        record_turn = service.session_manager.ledger.record_agent_turn

        async def _capture(**kwargs: Any) -> Any:
            prompts.append(kwargs["prompt"])
            return await record_turn(**kwargs)

        monkeypatch.setattr(service.session_manager.ledger, "record_agent_turn", _capture)
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        try:
            # CLI merges queued messages at tool boundaries: two started events but one result.
            await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID)
            client.push_frame(started_frame(1, session_id=SDK_ID))
            await service.send_or_create("demo", "结尾再加一个空镜", session_id=SDK_ID)
            client.push_frame(started_frame(2, session_id=SDK_ID))
            client.push_frame(_billed_assistant_frame("msg-1"))
            client.push_frame(result_frame(session_id=SDK_ID, uuid="r-1"))
            await service.send_or_create("demo", "第 5 镜删掉", session_id=SDK_ID)
            client.push_frame(started_frame(3, session_id=SDK_ID))
            client.push_frame(_billed_assistant_frame("msg-2"))
            client.push_frame(result_frame(session_id=SDK_ID, uuid="r-2"))
            client.push_frame(_idle_frame())
            await _wait_for_status(service.session_manager, SDK_ID, "completed")

            assert prompts == ["写分镜\n加一段旁白\n结尾再加一个空镜", "第 5 镜删掉"]
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_replay_links_the_transcript_uuid_and_repeated_replays_add_no_entry(self, service: AssistantService):
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        try:
            queued = (await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID))["queued_message"]
            # Merged messages replay before started, then CLI replays again.
            client.push_frame(replay_frame(session_id=SDK_ID))
            client.push_frame(started_frame(session_id=SDK_ID))
            client.push_frame(replay_frame(session_id=SDK_ID))
            client.push_frame(result_frame(session_id=SDK_ID, uuid="r-1"))
            client.push_frame(_idle_frame())
            await _wait_for_status(service.session_manager, SDK_ID, "completed")

            entries = await service.event_log_store.list_after(SDK_ID)
            assert [entry["type"] for entry in entries] == ["user", "assistant", "user"]
            linked = await service.event_log_store.find_user_message_link(SDK_ID, queued["id"])
            assert linked == client.sent_messages[-1]["uuid"]
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_queued_idempotency_and_replays_survive_many_later_sends(self, service: AssistantService):
        client = FakeSDKClient()
        try:
            with _scripted_client(service.session_manager, client):
                responses = [
                    await service.send_or_create(
                        "demo", f"message {index}", session_id=SDK_ID, client_key=f"key-{index}"
                    )
                    for index in range(501)
                ]
            first = responses[0]["queued_message"]
            retry = await service.send_or_create("demo", "message 0", session_id=SDK_ID, client_key="key-0")
            assert retry["queued_message"] == first
            assert len(client.sent_messages) == 501

            client.push_frame(replay_frame(0, session_id=SDK_ID))
            client.push_frame(started_frame(0, session_id=SDK_ID))
            client.push_frame(replay_frame(0, session_id=SDK_ID))
            client.push_frame(result_frame(session_id=SDK_ID))
            client.push_frame(_idle_frame())
            await _wait_for_status(service.session_manager, SDK_ID, "completed")

            entries = await service.event_log_store.list_after(SDK_ID)
            assert [entry["uuid"] for entry in entries] == [first["id"]]
            linked = await service.event_log_store.find_user_message_link(SDK_ID, first["id"])
            assert linked == client.sent_messages[0]["uuid"]
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_a_failed_send_during_a_turn_leaves_the_turn_running_and_settled_by_the_cli(
        self, service: AssistantService
    ):
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        update_status = service.meta_store.update_status

        async def _fail_running(session_id: str, status: str, *args: Any, **kwargs: Any) -> Any:
            if status == "running":
                raise RuntimeError("db down")
            return await update_status(session_id, status, *args, **kwargs)

        try:
            with patch.object(service.meta_store, "update_status", new=_fail_running), pytest.raises(RuntimeError):
                await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID)

            assert service.session_manager.sessions[SDK_ID].status == "running"
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
            client.push_frame(result_frame(session_id=SDK_ID, uuid="r-1"))
            client.push_frame(_idle_frame())
            await _wait_for_status(service.session_manager, SDK_ID, "completed")
        finally:
            await service.session_manager.close_session(SDK_ID)

    @pytest.mark.parametrize("idle_timing", ["read_before_send", "handled_during_write", "read_during_write"])
    async def test_a_failed_send_does_not_strand_an_idle_the_cli_reported(
        self, service: AssistantService, monkeypatch, idle_timing: str
    ):
        """Send failure before CLI delivery during replies still lets surrounding CLI idle settle, preventing permanent running state."""
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        manager = service.session_manager
        inbox_released, replay_linked = asyncio.Event(), asyncio.Event()
        record_turn = manager.ledger.record_agent_turn
        record_link = service.event_log_store.record_user_message_link

        async def _blocked_record_turn(**kwargs: Any) -> Any:
            await inbox_released.wait()
            return await record_turn(**kwargs)

        async def _observed_link(*args: Any, **kwargs: Any) -> Any:
            result = await record_link(*args, **kwargs)
            replay_linked.set()
            return result

        monkeypatch.setattr(manager.ledger, "record_agent_turn", _blocked_record_turn)
        monkeypatch.setattr(service.event_log_store, "record_user_message_link", _observed_link)
        update_status = service.meta_store.update_status
        try:
            async with manager.stream_messages(SDK_ID, idle_timeout=5) as stream:

                async def _idle_read() -> None:
                    while (await _next_broadcast(stream, "system")).get("data", {}).get("state") != "idle":
                        pass

                async def _fail_running(session_id: str, status: str, *args: Any, **kwargs: Any) -> Any:
                    if status != "running":
                        return await update_status(session_id, status, *args, **kwargs)
                    if idle_timing == "read_during_write":
                        client.push_frame(_idle_frame())
                        await _idle_read()
                    elif idle_timing == "handled_during_write":
                        # Replay follows idle; inbox processed idle during this persistence by the time replay commits.
                        inbox_released.set()
                        await replay_linked.wait()
                    raise RuntimeError("db down")

                # Usage accounting blocks result while subsequent frames remain in inbox.
                client.push_frame(_billed_assistant_frame("msg-1"))
                client.push_frame(result_frame(session_id=SDK_ID, uuid="r-1"))
                if idle_timing != "read_during_write":
                    client.push_frame(_idle_frame())
                    client.push_frame(replay_frame(0, session_id=SDK_ID))
                    await _idle_read()

                with patch.object(service.meta_store, "update_status", new=_fail_running), pytest.raises(RuntimeError):
                    await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID)
                inbox_released.set()

                # Memory changes before metadata; terminals broadcast afterward, so await broadcasts before reads.
                while (await _next_broadcast(stream, "runtime_status"))["status"] != "completed":
                    pass

            assert (await service.meta_store.get(SDK_ID)).status == "completed"
        finally:
            inbox_released.set()
            await manager.close_session(SDK_ID)

    async def test_a_failed_send_to_an_idle_session_broadcasts_the_error_status(self, service: AssistantService):
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        await self._finish_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        update_status = service.meta_store.update_status

        async def _fail_running(session_id: str, status: str, *args: Any, **kwargs: Any) -> Any:
            if status == "running":
                raise RuntimeError("db down")
            return await update_status(session_id, status, *args, **kwargs)

        try:
            assert (await stream.next("status"))["status"] == "completed"
            with patch.object(service.meta_store, "update_status", new=_fail_running), pytest.raises(RuntimeError):
                await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID)

            # Streams may already align to running via queued messages; failed terminals arrive without waiting for heartbeat.
            statuses = [(await stream.next("status"))["status"]]
            if statuses[0] == "running":
                statuses.append((await stream.next("status"))["status"])
            assert statuses[-1] == "error"
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_retry_of_an_accepted_message_whose_entry_failed_to_record_is_refused_not_resent(
        self, service: AssistantService
    ):
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, client_key="ck-1")
            with patch.object(
                service.event_log_store, "append_user_entry", new=AsyncMock(side_effect=RuntimeError("db down"))
            ):
                client.push_frame(started_frame(session_id=SDK_ID))
                await stream.next("queue_remove")

            with pytest.raises(UnrecordedMessageError):
                await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, client_key="ck-1")

            assert len(client.sent_messages) == 2
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_a_message_the_cli_cancels_leaves_the_queue_and_its_retry_is_sent_again(
        self, service: AssistantService
    ):
        client = self._running_turn_client()
        await self._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            queued = (await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, client_key="ck-1"))[
                "queued_message"
            ]
            client.push_frame(
                lambda c: command_lifecycle_frame(c.sent_messages[-1]["uuid"], "cancelled", session_id=SDK_ID)
            )
            removed = await stream.next("queue_remove")

            retry = await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, client_key="ck-1")

            assert removed["id"] == queued["id"]
            assert retry["queued_message"]["id"] != queued["id"]
            assert len(client.sent_messages) == 3
            assert [entry["type"] for entry in await service.event_log_store.list_after(SDK_ID)] == [
                "user",
                "assistant",
            ]
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)


def _cancel_queued(client: FakeSDKClient) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """CLI retracts queued messages by sending cancelled frames before answering ``{cancelled: true}``."""

    def _respond(request: dict[str, Any]) -> dict[str, Any]:
        client.push_frame(command_lifecycle_frame(request["message_uuid"], "cancelled", session_id=SDK_ID))
        return {"cancelled": True}

    return _respond


class TestQueuedMessageWithdrawal:
    """Editing/deleting queued tray messages first retracts from CLI; only successful retraction restores input or discards."""

    @staticmethod
    def _client() -> FakeSDKClient:
        client = TestQueuedMessages._running_turn_client()
        client.respond_control("cancel_async_message", _cancel_queued(client))
        return client

    async def _queue(self, service: AssistantService, client: FakeSDKClient, **kwargs: Any) -> dict[str, Any]:
        await TestQueuedMessages()._start_running_turn(service, client)
        return (await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, **kwargs))["queued_message"]

    async def test_editing_a_queued_message_the_cli_cancels_returns_its_content(self, service: AssistantService):
        client = self._client()
        image = {"data": "aGVsbG8=", "media_type": "image/png"}
        await TestQueuedMessages()._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            queued = (
                await service.send_or_create(
                    "demo", "加一段旁白", session_id=SDK_ID, images=[SimpleNamespace(**image)], client_key="ck-1"
                )
            )["queued_message"]

            result = await service.withdraw_queued_message("demo", SDK_ID, queued["id"], intent="edit")

            assert result["outcome"] == "withdrawn"
            assert result["message"]["content"] == [
                {"type": "image", "source": {"type": "base64", **image}},
                {"type": "text", "text": "加一段旁白"},
            ]
            assert client.control_requests == [
                {"subtype": "cancel_async_message", "message_uuid": client.sent_messages[-1]["uuid"]}
            ]
            removed = await stream.next("queue_remove")
            assert removed["id"] == queued["id"]
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
            # Retracted messages never enter timelines; release idempotency keys so resending reaches CLI again.
            retry = await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, client_key="ck-1")
            assert retry["queued_message"]["id"] != queued["id"]
            assert [entry["type"] for entry in await service.event_log_store.list_after(SDK_ID)] == [
                "user",
                "assistant",
            ]
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_deleting_a_queued_message_the_cli_cancels_discards_it(self, service: AssistantService):
        client = self._client()
        queued = await self._queue(service, client)
        try:
            result = await service.withdraw_queued_message("demo", SDK_ID, queued["id"], intent="delete")

            assert result["outcome"] == "withdrawn"
            assert result["message"] is None
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
            assert len(await service.event_log_store.list_after(SDK_ID)) == 2
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_a_withdrawal_the_agent_already_took_leaves_the_message_on_its_way(self, service: AssistantService):
        client = self._client()
        client.respond_control("cancel_async_message", {"cancelled": False})
        queued = await self._queue(service, client)
        try:
            result = await service.withdraw_queued_message("demo", SDK_ID, queued["id"], intent="edit")

            assert result == {"session_id": SDK_ID, "id": queued["id"], "outcome": "accepted", "message": None}
            client.push_frame(started_frame(session_id=SDK_ID))
            entries = await _wait_for_entries(service.event_log_store, SDK_ID, 3)
            assert entries[2]["uuid"] == queued["id"]
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_a_message_already_in_the_conversation_reports_accepted(self, service: AssistantService):
        client = self._client()
        queued = await self._queue(service, client)
        try:
            client.push_frame(started_frame(session_id=SDK_ID))
            await _wait_for_entries(service.event_log_store, SDK_ID, 3)

            result = await service.withdraw_queued_message("demo", SDK_ID, queued["id"], intent="delete")

            assert result["outcome"] == "accepted"
            assert client.control_requests == []
        finally:
            await service.session_manager.close_session(SDK_ID)

    @pytest.mark.parametrize("intent", ["edit", "delete"])
    async def test_a_cancel_after_a_failed_withdrawal_follows_the_withdrawal(
        self, service: AssistantService, intent: WithdrawalIntent
    ):
        client = self._client()
        client.respond_control("cancel_async_message", {"cancelled": False})
        await TestQueuedMessages()._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            await stream.next("queue")
            queued = (await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID))["queued_message"]
            result = await service.withdraw_queued_message("demo", SDK_ID, queued["id"], intent=intent)
            assert result["outcome"] == "accepted"

            # CLI removed it from the queue but has not merged it into the round.
            client.push_frame(
                lambda c: command_lifecycle_frame(c.sent_messages[-1]["uuid"], "cancelled", session_id=SDK_ID)
            )
            removed = await stream.next("queue_remove")

            assert removed["id"] == queued["id"]
            assert removed["withdrawn"] == intent
            if intent == "edit":
                assert removed["message"]["content"] == queued["content"]
            else:
                assert "message" not in removed
            assert len(await service.event_log_store.list_after(SDK_ID)) == 2
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_a_cancel_the_user_did_not_ask_for_is_not_reported_as_a_withdrawal(self, service: AssistantService):
        client = self._client()
        await TestQueuedMessages()._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            await stream.next("queue")
            queued = (await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID))["queued_message"]
            client.push_frame(
                lambda c: command_lifecycle_frame(c.sent_messages[-1]["uuid"], "cancelled", session_id=SDK_ID)
            )
            removed = await stream.next("queue_remove")
            assert removed == {"session_id": SDK_ID, "id": queued["id"]}
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_a_cancel_request_error_keeps_the_message_queued_and_the_session_usable(
        self, service: AssistantService
    ):
        client = self._client()
        client.respond_control("cancel_async_message", RuntimeError("control request timed out"))
        queued = await self._queue(service, client)
        try:
            with pytest.raises(RuntimeError):
                await service.withdraw_queued_message("demo", SDK_ID, queued["id"], intent="delete")
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [queued]

            client.respond_control("cancel_async_message", _cancel_queued(client))
            result = await service.withdraw_queued_message("demo", SDK_ID, queued["id"], intent="delete")

            assert result["outcome"] == "withdrawn"
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
        finally:
            await service.session_manager.close_session(SDK_ID)


class TestUnsentQueuedMessages:
    """CLI exit turns queued messages into unsent for explicit user send/edit/delete, never automatic resend."""

    async def _queue_then_exit(self, service: AssistantService, client: FakeSDKClient, **kwargs: Any) -> dict[str, Any]:
        await TestQueuedMessages()._start_running_turn(service, client)
        queued = (await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, **kwargs))["queued_message"]
        stream = _EntryStream(service, SDK_ID)
        try:
            await stream.next("queue")
            client.close_stream()
            await stream.next("queue_upsert")
        finally:
            await stream.close()
        return queued

    async def test_queued_messages_turn_unsent_when_the_cli_exits_and_are_not_resent(self, service: AssistantService):
        client = TestQueuedMessages._running_turn_client()
        await TestQueuedMessages()._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            queued = (await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, client_key="ck-1"))[
                "queued_message"
            ]
            await stream.next("queue_upsert")
            client.close_stream()

            upsert = await stream.next("queue_upsert")
            assert upsert["message"] == {**queued, "state": "unsent"}
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [upsert["message"]]
            # Same-key retries return the same unsent message without CLI delivery.
            retry = await service.send_or_create("demo", "加一段旁白", session_id=SDK_ID, client_key="ck-1")
            assert retry["queued_message"] == upsert["message"]
            assert len(client.sent_messages) == 2
            assert [entry["type"] for entry in await service.event_log_store.list_after(SDK_ID)] == [
                "user",
                "assistant",
            ]
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_sending_an_unsent_message_reconnects_and_hands_it_to_the_cli_again(self, service: AssistantService):
        client = TestQueuedMessages._running_turn_client()
        queued = await self._queue_then_exit(service, client)
        revived = FakeSDKClient(frames=[_session_state_frame("running")])
        try:
            with _scripted_client(service.session_manager, revived):
                result = await service.resend_queued_message("demo", SDK_ID, queued["id"])

            assert result["queued_message"] == queued
            assert len(revived.sent_messages) == 1
            resent = revived.sent_messages[0]
            assert resent["message"]["content"] == "加一段旁白"
            assert resent["uuid"] != client.sent_messages[-1]["uuid"]
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [queued]
            # Repeated clicks do not resend.
            await service.resend_queued_message("demo", SDK_ID, queued["id"])
            assert len(revived.sent_messages) == 1

            revived.push_frame(started_frame(session_id=SDK_ID))
            entries = await _wait_for_entries(service.event_log_store, SDK_ID, 3)
            assert entries[2]["uuid"] == queued["id"]
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_a_new_message_after_the_cli_exits_reconnects_and_leaves_unsent_ones_in_the_tray(
        self, service: AssistantService
    ):
        client = TestQueuedMessages._running_turn_client()
        unsent = await self._queue_then_exit(service, client)
        stale = service.session_manager.sessions[SDK_ID]
        revived = FakeSDKClient(frames=[_session_state_frame("running")])
        try:
            with _scripted_client(service.session_manager, revived):
                result = await service.send_or_create("demo", "换个结局", session_id=SDK_ID)

            assert service.session_manager.sessions[SDK_ID] is not stale
            # Send new messages only; unsent entries remain earlier in trays without automatic replay.
            assert [sent["message"]["content"] for sent in revived.sent_messages] == ["换个结局"]
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [
                {**unsent, "state": "unsent"},
                result["queued_message"],
            ]

            revived.push_frame(started_frame(session_id=SDK_ID))
            entries = await _wait_for_entries(service.event_log_store, SDK_ID, 3)
            assert entries[2]["uuid"] == result["queued_message"]["id"]
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [{**unsent, "state": "unsent"}]
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_concurrent_sends_after_the_cli_exits_share_one_new_connection(self, service: AssistantService):
        client = TestQueuedMessages._running_turn_client()
        unsent = await self._queue_then_exit(service, client)
        revived = FakeSDKClient(frames=[_session_state_frame("running")])
        connections: list[object] = []

        def _connect(options: object) -> FakeSDKClient:
            connections.append(options)
            return revived

        try:
            with (
                patch.object(
                    service.session_manager, "_build_options", new=AsyncMock(return_value=SimpleNamespace(env=None))
                ),
                patch("server.agent_runtime.session_manager.ClaudeSDKClient", _connect),
            ):
                first, second = await asyncio.gather(
                    service.send_or_create("demo", "换个结局", session_id=SDK_ID),
                    service.resend_queued_message("demo", SDK_ID, unsent["id"]),
                )

            assert len(connections) == 1
            assert sorted(sent["message"]["content"] for sent in revived.sent_messages) == ["加一段旁白", "换个结局"]
            assert second["queued_message"] == unsent
            assert {message["id"] for message in service.session_manager.get_queued_messages_snapshot(SDK_ID)} == {
                unsent["id"],
                first["queued_message"]["id"],
            }
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_a_send_during_eviction_waits_for_it_and_connects_once(self, service: AssistantService):
        client = TestQueuedMessages._running_turn_client()
        await self._queue_then_exit(service, client)
        manager = service.session_manager
        stale = manager.sessions[SDK_ID]
        revived = FakeSDKClient(frames=[_session_state_frame("running")])
        release = asyncio.Event()
        disconnect_entered = asyncio.Event()
        disconnect = stale.send_disconnect

        async def _slow_disconnect() -> None:
            disconnect_entered.set()
            await release.wait()
            await disconnect()

        try:
            with _scripted_client(manager, revived), patch.object(stale, "send_disconnect", _slow_disconnect):
                # Slow eviction starts first; sends await completion then rebuild cold sessions, avoiding removal by eviction cleanup.
                eviction = asyncio.create_task(manager.close_session(SDK_ID))
                await asyncio.wait_for(disconnect_entered.wait(), timeout=5.0)
                eviction_event = manager._disconnecting[SDK_ID]
                wait = eviction_event.wait
                waiting_for_eviction = asyncio.Event()

                async def _wait() -> None:
                    waiting_for_eviction.set()
                    await wait()

                with patch.object(eviction_event, "wait", _wait):
                    sending = asyncio.create_task(service.send_or_create("demo", "换个结局", session_id=SDK_ID))
                    await asyncio.wait_for(waiting_for_eviction.wait(), timeout=5.0)
                    assert not sending.done()
                    release.set()
                    await eviction
                    result = await sending

            managed = manager.sessions[SDK_ID]
            assert managed is not stale
            assert managed.actor_exited is False
            # Eviction still discards unsent messages.
            assert manager.get_queued_messages_snapshot(SDK_ID) == [result["queued_message"]]
            assert [sent["message"]["content"] for sent in revived.sent_messages] == ["换个结局"]
        finally:
            await manager.close_session(SDK_ID)

    @pytest.mark.parametrize("intent", ["edit", "delete"])
    async def test_withdrawing_an_unsent_message_removes_it_without_asking_the_cli(
        self, service: AssistantService, intent: WithdrawalIntent
    ):
        client = TestQueuedMessages._running_turn_client()
        queued = await self._queue_then_exit(service, client)
        try:
            result = await service.withdraw_queued_message("demo", SDK_ID, queued["id"], intent=intent)

            assert result["outcome"] == "withdrawn"
            assert result["message"] == ({**queued, "state": "unsent"} if intent == "edit" else None)
            assert client.control_requests == []
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == []
        finally:
            await service.session_manager.close_session(SDK_ID)

    @pytest.mark.parametrize("action", ["edit", "delete", "send_now"])
    async def test_acting_on_an_unsent_message_while_the_session_reconnects_waits_for_it(
        self, service: AssistantService, action: str
    ):
        client = TestQueuedMessages._running_turn_client()
        unsent = await self._queue_then_exit(service, client)
        revived = FakeSDKClient(frames=[_session_state_frame("running")])
        building = asyncio.Event()
        proceed = asyncio.Event()

        async def _build_options(*args: Any, **kwargs: Any) -> SimpleNamespace:
            building.set()
            await proceed.wait()
            return SimpleNamespace(env=None)

        try:
            with (
                patch.object(service.session_manager, "_build_options", _build_options),
                patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: revived),
            ):
                # New messages trigger reconnection; unsent entries leave old sessions before connection but have not transferred yet.
                sending = asyncio.create_task(service.send_or_create("demo", "换个结局", session_id=SDK_ID))
                await asyncio.wait_for(building.wait(), timeout=5.0)
                get_meta = service.meta_store.get
                checked = asyncio.Event()

                async def _get_meta(session_id: str) -> Any:
                    meta = await get_meta(session_id)
                    if asyncio.current_task() is acting:
                        # After reading metadata, synchronously reach retraction or the wait point before tests resume.
                        checked.set()
                    return meta

                with patch.object(service.meta_store, "get", _get_meta):
                    if action == "send_now":
                        acting = asyncio.create_task(service.send_queued_message_now("demo", SDK_ID, unsent["id"]))
                    else:
                        acting = asyncio.create_task(
                            service.withdraw_queued_message("demo", SDK_ID, unsent["id"], intent=action)
                        )
                    await asyncio.wait_for(checked.wait(), timeout=5.0)
                proceed.set()
                sent = (await sending)["queued_message"]
                outcome = (await acting)["outcome"]

            snapshot = service.session_manager.get_queued_messages_snapshot(SDK_ID)
            if action == "send_now":
                assert outcome == "sent"
                assert [message["message"]["content"] for message in revived.sent_messages] == [
                    "换个结局",
                    "加一段旁白",
                ]
                assert snapshot == [unsent, sent]
            else:
                assert outcome == "withdrawn"
                assert snapshot == [sent]
        finally:
            await service.session_manager.close_session(SDK_ID)


class TestSendQueuedMessageNow:
    """Send now first retracts, then resends the same queued message at ``now`` priority, interrupting current rounds to handle it first."""

    async def test_a_withdrawn_message_is_sent_again_with_now_priority(self, service: AssistantService):
        client = TestQueuedMessageWithdrawal._client()
        await TestQueuedMessages()._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            await stream.next("queue")
            queued = (await service.send_or_create("demo", "先停下改结局", session_id=SDK_ID, client_key="ck-1"))[
                "queued_message"
            ]
            first_uuid = client.sent_messages[-1]["uuid"]

            result = await service.send_queued_message_now("demo", SDK_ID, queued["id"])

            assert result == {"session_id": SDK_ID, "id": queued["id"], "outcome": "sent"}
            assert client.control_requests == [{"subtype": "cancel_async_message", "message_uuid": first_uuid}]
            resent = client.sent_messages[-1]
            assert resent["priority"] == "now"
            assert resent["message"]["content"] == "先停下改结局"
            assert resent["uuid"] != first_uuid
            # Frontend retains the same tray message and idempotency-key target.
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [queued]
            retry = await service.send_or_create("demo", "先停下改结局", session_id=SDK_ID, client_key="ck-1")
            assert retry["queued_message"] == queued
            assert len(client.sent_messages) == 3

            # CLI accepts a new uuid, logging the entry and removing it from trays.
            client.push_frame(started_frame(session_id=SDK_ID))
            entries = await _wait_for_entries(service.event_log_store, SDK_ID, 3)
            assert entries[2]["uuid"] == queued["id"]
            assert (await stream.next("queue_remove")) == {"session_id": SDK_ID, "id": queued["id"]}
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_a_message_sent_now_moves_to_the_front_of_the_tray(self, service: AssistantService):
        client = TestQueuedMessageWithdrawal._client()
        await TestQueuedMessages()._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            await stream.next("queue")
            earlier = (await service.send_or_create("demo", "顺便配乐", session_id=SDK_ID))["queued_message"]
            urgent = (await service.send_or_create("demo", "先停下改结局", session_id=SDK_ID))["queued_message"]
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [earlier, urgent]

            assert (await service.send_queued_message_now("demo", SDK_ID, urgent["id"]))["outcome"] == "sent"

            # Tray order follows CLI processing; replay full snapshots with prioritized messages first.
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [urgent, earlier]
            assert (await stream.next("queue")) == {"session_id": SDK_ID, "messages": [urgent, earlier]}
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_a_message_the_agent_already_took_is_not_sent_again(self, service: AssistantService):
        client = TestQueuedMessageWithdrawal._client()
        client.respond_control("cancel_async_message", {"cancelled": False})
        queued = await TestQueuedMessageWithdrawal()._queue(service, client)
        try:
            result = await service.send_queued_message_now("demo", SDK_ID, queued["id"])

            assert result == {"session_id": SDK_ID, "id": queued["id"], "outcome": "accepted"}
            assert len(client.sent_messages) == 2
            assert all("priority" not in sent for sent in client.sent_messages)
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [queued]
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_the_turn_it_preempts_is_interrupted_and_a_dropped_message_runs_after_it(
        self, service: AssistantService
    ):
        client = TestQueuedMessageWithdrawal._client()
        await TestQueuedMessages()._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            await stream.next("queue")
            dropped = (await service.send_or_create("demo", "顺便配乐", session_id=SDK_ID))["queued_message"]
            urgent = (await service.send_or_create("demo", "先停下改结局", session_id=SDK_ID))["queued_message"]
            head_uuid, dropped_uuid = client.sent_messages[0]["uuid"], client.sent_messages[1]["uuid"]
            assert (await service.send_queued_message_now("demo", SDK_ID, urgent["id"]))["outcome"] == "sent"
            now_uuid = client.sent_messages[-1]["uuid"]

            # CLI interrupts current rounds but result still says success; terminal_reason identifies interruption. Round messages receive cancelled;
            # already-conversational first messages survive, while never-conversational messages are discarded.
            client.push_frame(result_frame(session_id=SDK_ID, terminal_reason="aborted_streaming", result=""))
            client.push_frame(command_lifecycle_frame(head_uuid, "cancelled", session_id=SDK_ID))
            client.push_frame(command_lifecycle_frame(dropped_uuid, "cancelled", session_id=SDK_ID))
            client.push_frame(command_lifecycle_frame(now_uuid, "started", session_id=SDK_ID))

            # Acceptance persists/broadcasts entries before tray removal; await removal broadcasts, not intermediate-visible log rows.
            assert (await stream.next("queue_remove")) == {"session_id": SDK_ID, "id": urgent["id"]}
            entries = await service.event_log_store.list_after(SDK_ID)
            assert [(entry["type"], entry.get("subtype")) for entry in entries] == [
                ("user", None),
                ("assistant", None),
                ("system", "interrupt"),
                ("user", None),
            ]
            assert entries[3]["uuid"] == urgent["id"]

            # Redeliver discarded messages with new uuids/ordinary priority after prioritized ones, retaining the same tray entry.
            assert len(client.sent_messages) == 5
            requeued = client.sent_messages[4]
            assert requeued["message"]["content"] == "顺便配乐"
            assert requeued["uuid"] not in (dropped_uuid, now_uuid)
            assert "priority" not in requeued
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [dropped]

            # Prioritized rounds complete normally, not interrupted; reordered messages are subsequently accepted.
            client.push_frame(assistant_frame({"type": "text", "text": "结局改好了"}, uuid="a-2", session_id=SDK_ID))
            client.push_frame(result_frame(session_id=SDK_ID, terminal_reason="completed"))
            client.push_frame(command_lifecycle_frame(requeued["uuid"], "started", session_id=SDK_ID))
            client.push_frame(result_frame(session_id=SDK_ID, terminal_reason="completed"))
            client.push_frame(_idle_frame())
            await _wait_for_status(service.session_manager, SDK_ID, "completed")
            entries = await service.event_log_store.list_after(SDK_ID)
            assert [entry["type"] for entry in entries[4:]] == ["assistant", "user"]
            assert entries[5]["uuid"] == dropped["id"]
            assert not any(entry.get("subtype") == "agent_turn_failure" for entry in entries)
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)

    async def test_a_cancel_after_the_agent_took_it_still_sends_it_now(self, service: AssistantService):
        client = TestQueuedMessageWithdrawal._client()
        client.respond_control("cancel_async_message", {"cancelled": False})
        queued = await TestQueuedMessageWithdrawal()._queue(service, client)
        try:
            assert (await service.send_queued_message_now("demo", SDK_ID, queued["id"]))["outcome"] == "accepted"

            # CLI removed it without merging into rounds; redeliver according to send-now intent.
            delivered = asyncio.Event()
            query = client.query

            async def _query(prompt, session_id: str = "default") -> None:
                await query(prompt, session_id=session_id)
                delivered.set()

            with patch.object(client, "query", _query):
                client.push_frame(
                    lambda c: command_lifecycle_frame(c.sent_messages[-1]["uuid"], "cancelled", session_id=SDK_ID)
                )
                await asyncio.wait_for(delivered.wait(), timeout=5.0)

            assert client.sent_messages[2]["priority"] == "now"
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [queued]
        finally:
            await service.session_manager.close_session(SDK_ID)

    async def test_a_late_cancel_also_moves_the_message_to_the_front_of_the_tray(self, service: AssistantService):
        client = TestQueuedMessageWithdrawal._client()
        client.respond_control("cancel_async_message", {"cancelled": False})
        await TestQueuedMessages()._start_running_turn(service, client)
        stream = _EntryStream(service, SDK_ID)
        try:
            await stream.next("queue")
            earlier = (await service.send_or_create("demo", "顺便配乐", session_id=SDK_ID))["queued_message"]
            urgent = (await service.send_or_create("demo", "先停下改结局", session_id=SDK_ID))["queued_message"]
            urgent_uuid = client.sent_messages[-1]["uuid"]
            assert (await service.send_queued_message_now("demo", SDK_ID, urgent["id"]))["outcome"] == "accepted"
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [earlier, urgent]

            # Failed retract responses followed by CLI cancelled still cause inbox resend at the front.
            delivered = asyncio.Event()
            query = client.query

            async def _query(prompt, session_id: str = "default") -> None:
                await query(prompt, session_id=session_id)
                delivered.set()

            with patch.object(client, "query", _query):
                client.push_frame(command_lifecycle_frame(urgent_uuid, "cancelled", session_id=SDK_ID))
                await asyncio.wait_for(delivered.wait(), timeout=5.0)

            assert client.sent_messages[-1]["priority"] == "now"
            assert service.session_manager.get_queued_messages_snapshot(SDK_ID) == [urgent, earlier]
            assert (await stream.next("queue")) == {"session_id": SDK_ID, "messages": [urgent, earlier]}
        finally:
            await stream.close()
            await service.session_manager.close_session(SDK_ID)
