"""LiteLLM-backed session client for ArcReel assistant.

Uses ``litellm.acompletion()`` to call any supported LLM provider via
OpenAI-compatible function calling. ArcReel tools are exposed as OpenAI
functions and executed in-process via ``tool_definitions.execute_tool()``.

Usage::

    client = LiteLLMSessionClient(
        model="openai/gpt-4o",
        api_key="sk-...",
        projects_root=Path("/path/to/projects"),
    )
    session_id = await client.create_session("my_project")
    await client.send_message(session_id, "请帮我生成第一集的分镜图")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import uuid4

import litellm

from server.agent_runtime.session_client import AssistantEvent, OnEvent
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.tool_definitions import execute_tool, get_openai_tools

logger = logging.getLogger(__name__)

# Suppress LiteLLM's verbose logging
litellm.suppress_debug_info = True


class LiteLLMSessionClient:
    """LiteLLM-backed session client implementing the SessionClient protocol.

    Manages per-session conversation history and executes the tool-calling
    loop (LLM → tool call → execute → feed result → LLM continues).
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        projects_root: Path,
        max_tool_rounds: int = 20,
        system_prompt: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._projects_root = projects_root
        self._max_tool_rounds = max_tool_rounds
        self._system_prompt = system_prompt or self._default_system_prompt()

        # Per-session state
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._project_names: dict[str, str] = {}
        self._interrupted: set[str] = set()
        self._running: set[str] = set()

        # OpenAI tool definitions (shared across sessions)
        self._tools = get_openai_tools()

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "你是 ArcReel AI 视频生成助手。你可以帮助用户管理项目、生成剧本、"
            "生成分镜图、生成视频等。使用提供的工具来执行操作。\n\n"
            "当用户要求生成内容时，使用对应的工具。如果不确定当前项目状态，"
            "先使用 list_pending_assets 或 get_video_capabilities 了解情况。"
        )

    # ------------------------------------------------------------------
    # SessionClient protocol
    # ------------------------------------------------------------------

    async def create_session(self, project_name: str) -> str:
        session_id = uuid4().hex
        self._histories[session_id] = [
            {"role": "system", "content": self._system_prompt},
        ]
        self._project_names[session_id] = project_name
        logger.info("LiteLLM session created: %s (project=%s)", session_id, project_name)
        return session_id

    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        on_event: OnEvent | None = None,
    ) -> dict[str, Any]:
        if session_id not in self._histories:
            raise ValueError(f"Session not found: {session_id}")

        self._interrupted.discard(session_id)
        self._running.add(session_id)

        history = self._histories[session_id]
        project_name = self._project_names[session_id]
        ctx = ToolContext(
            project_name=project_name,
            projects_root=self._projects_root,
        )

        # Append user message
        history.append({"role": "user", "content": message})

        try:
            result = await self._run_assistant_loop(session_id, history, ctx, on_event)
            return result
        finally:
            self._running.discard(session_id)

    async def interrupt(self, session_id: str) -> None:
        self._interrupted.add(session_id)
        logger.info("LiteLLM session interrupted: %s", session_id)

    async def delete_session(self, session_id: str) -> None:
        self._histories.pop(session_id, None)
        self._project_names.pop(session_id, None)
        self._interrupted.discard(session_id)
        self._running.discard(session_id)
        logger.info("LiteLLM session deleted: %s", session_id)

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._histories.get(session_id, []))

    # ------------------------------------------------------------------
    # Internal: assistant loop with tool calling
    # ------------------------------------------------------------------

    async def _run_assistant_loop(
        self,
        session_id: str,
        history: list[dict[str, Any]],
        ctx: ToolContext,
        on_event: OnEvent | None,
    ) -> dict[str, Any]:
        """Run the LLM ↔ tool loop until the model produces a final text response.

        Returns ``{"status": "completed"}`` on success or
        ``{"status": "error", "error": "..."}`` on failure.
        """
        for round_num in range(self._max_tool_rounds):
            if session_id in self._interrupted:
                if on_event:
                    on_event(AssistantEvent("status", {"status": "interrupted"}))
                return {"status": "interrupted"}

            # Emit status
            if on_event:
                on_event(AssistantEvent("status", {
                    "status": "running",
                    "round": round_num + 1,
                }))

            try:
                response = await self._call_llm(history)
            except Exception as exc:
                logger.exception("LiteLLM call failed (round %d): %s", round_num, exc)
                if on_event:
                    on_event(AssistantEvent("error", {"error": str(exc)}))
                return {"status": "error", "error": str(exc)}

            choice = response.choices[0] if response.choices else None
            if not choice:
                return {"status": "error", "error": "Empty response from LLM"}

            message = choice.message

            # Collect text content
            if message.content:
                if on_event:
                    on_event(AssistantEvent("text_delta", {"text": message.content}))

            # Check for tool calls
            tool_calls = message.tool_calls or []
            if not tool_calls:
                # No tool calls — this is the final response
                # Append assistant message to history
                history.append({
                    "role": "assistant",
                    "content": message.content or "",
                })
                if on_event:
                    on_event(AssistantEvent("done", {"status": "completed"}))
                return {"status": "completed"}

            # Has tool calls — append assistant message with tool_calls
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
            history.append(assistant_msg)

            # Execute each tool call
            for tc in tool_calls:
                if session_id in self._interrupted:
                    break

                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                if on_event:
                    on_event(AssistantEvent("tool_use", {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "tool_use_id": tc.id,
                    }))

                # Execute the tool
                try:
                    tool_result = await execute_tool(tool_name, tool_args, ctx)
                except Exception as exc:
                    logger.exception("Tool execution failed: %s", tool_name)
                    tool_result = {
                        "content": [{"type": "text", "text": f"Tool error: {exc}"}],
                        "is_error": True,
                    }

                # Format result for LLM
                result_text = self._format_tool_result(tool_result)

                if on_event:
                    on_event(AssistantEvent("tool_result", {
                        "tool_use_id": tc.id,
                        "tool_name": tool_name,
                        "result": result_text[:500],  # Truncate for SSE
                    }))

                # Append tool result to history
                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })

        # Exceeded max rounds
        logger.warning("LiteLLM assistant loop exceeded %d rounds", self._max_tool_rounds)
        if on_event:
            on_event(AssistantEvent("error", {"error": "Max tool rounds exceeded"}))
        return {"status": "error", "error": "Max tool rounds exceeded"}

    async def _call_llm(self, history: list[dict[str, Any]]) -> Any:
        """Call the LLM via litellm with the current history and tools."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": history,
            "tools": self._tools,
            "tool_choice": "auto",
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["api_base"] = self._base_url

        return await litellm.acompletion(**kwargs)

    @staticmethod
    def _format_tool_result(result: dict[str, Any]) -> str:
        """Format a tool result dict into a plain-text string for the LLM."""
        content = result.get("content", [])
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
                elif isinstance(block, str):
                    parts.append(block)
            text = "\n".join(parts)
        elif isinstance(content, str):
            text = content
        else:
            text = json.dumps(content, ensure_ascii=False)

        if result.get("is_error"):
            text = f"[ERROR] {text}"
        return text
