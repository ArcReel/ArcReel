"""真实 bundled CLI 执行工具；只有模型 HTTP 响应由 loopback 替身提供，无真实额度。"""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, PermissionResultDeny

from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.options_assembler import OptionsAssembler


@pytest.mark.local_port
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    ["write", "bash_passthrough", "bash_scrub_allowed", "bash_scrub_denied", "file_access", "subagent", "bash_env"],
)
async def test_security_hooks_at_cli_execution_boundary(tmp_path: Path, scenario: str) -> None:
    target = tmp_path / "created.txt"
    tool = "Bash" if scenario.startswith("bash_") else "Write"
    tool_input = (
        {"command": f"printf executed > {target}"}
        if tool == "Bash"
        else {"file_path": str(target), "content": "executed"}
    )
    requests = []

    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            has_result = any(
                block.get("type") == "tool_result"
                for message in body.get("messages", [])
                for block in message.get("content", [])
                if isinstance(block, dict)
            )
            block = (
                {"type": "text", "text": "Done"}
                if has_result
                else {"type": "tool_use", "id": "toolu_test", "name": tool, "input": {}}
            )
            events = [
                (
                    "message_start",
                    {
                        "message": {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "model": body["model"],
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 0},
                        }
                    },
                ),
                ("content_block_start", {"index": 0, "content_block": block}),
            ]
            if not has_result:
                events.append(
                    (
                        "content_block_delta",
                        {"index": 0, "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input)}},
                    )
                )
            events.extend(
                [
                    ("content_block_stop", {"index": 0}),
                    (
                        "message_delta",
                        {
                            "delta": {"stop_reason": "end_turn" if has_result else "tool_use", "stop_sequence": None},
                            "usage": {"output_tokens": 1},
                        },
                    ),
                    ("message_stop", {}),
                ]
            )
            payload = "".join(
                f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n" for kind, data in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:

        async def no_credentials():
            return {}

        permission_inputs = []

        async def deny_bash(_tool, input_data, _context):
            permission_inputs.append(input_data)
            return PermissionResultDeny(message="Test permission denial")

        policy = AgentAccessPolicy(
            project_root=tmp_path,
            data_root=tmp_path,
            agent_profile_root=tmp_path,
            sandbox_enabled=scenario.startswith("bash_scrub_"),
            in_docker=False,
        )
        assembler = OptionsAssembler(
            data_root=tmp_path,
            allowed_tools=[tool],
            setting_sources=[],
            access_policy_provider=lambda: policy,
            max_turns_provider=lambda: 2,
            resolve_project_cwd=lambda _name: tmp_path,
            provider_env_loader=no_credentials,
        )
        built = await assembler.build("demo", can_use_tool=deny_bash if scenario == "bash_scrub_denied" else None)
        failures = []

        async def broken_hook(*_args):
            failures.append(scenario)
            raise RuntimeError(f"injected {scenario} failure")

        if scenario in {"file_access", "subagent", "bash_env"}:
            matcher_index, callback_index = {"file_access": (0, 1), "subagent": (0, 0), "bash_env": (1, 0)}[scenario]
            built.hooks["PreToolUse"][matcher_index].hooks[callback_index] = broken_hook

        # 空环境 + 临时配置隔离真实凭证；代理阻断 loopback 之外的 HTTP。
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
            "ANTHROPIC_API_KEY": "local-test-only",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "127.0.0.1,localhost",
        }
        options = ClaudeAgentOptions(
            cwd=str(tmp_path),
            setting_sources=[],
            allowed_tools=[] if scenario == "bash_scrub_denied" else [tool],
            hooks=built.hooks,
            can_use_tool=deny_bash if scenario == "bash_scrub_denied" else None,
            max_turns=2,
            model="claude-sonnet-4-5",
            system_prompt="Local test",
            env=env,
        )
        messages = []
        with patch.dict(os.environ, env, clear=True):
            async with asyncio.timeout(45), ClaudeSDKClient(options=options) as client:
                await client.query("Run the supplied tool once.")
                messages = [message async for message in client.receive_response()]

        assert requests, messages
        if scenario in {"write", "bash_passthrough", "bash_scrub_allowed"}:
            assert await asyncio.to_thread(target.read_text) == "executed"
        else:
            if scenario == "bash_scrub_denied":
                assert len(permission_inputs) == 1
                assert permission_inputs[0]["command"].startswith("env -u ")
                assert tool_input["command"] in permission_inputs[0]["command"]
            else:
                assert failures == [scenario]
            assert not await asyncio.to_thread(target.exists)
            tool_results = [
                block
                for request in requests
                for message in request["messages"]
                for block in message.get("content", [])
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            assert any(result.get("is_error") for result in tool_results), messages
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await asyncio.to_thread(thread.join)
