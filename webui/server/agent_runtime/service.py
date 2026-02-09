"""
Assistant service orchestration.
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from lib.project_manager import ProjectManager
from webui.server.agent_runtime.models import AgentMessage, AgentSession, SessionStatus
from webui.server.agent_runtime.session_store import AgentSessionStore
from webui.server.agent_runtime.skill_bridge import SkillBridge, SkillExecutionResult
from webui.server.agent_runtime.streaming import StreamRequest, StreamRequestRegistry

try:
    from claude_agent_sdk import ClaudeAgentOptions, query

    CLAUDE_AGENT_SDK_AVAILABLE = True
except ImportError:
    ClaudeAgentOptions = None  # type: ignore[assignment]
    query = None  # type: ignore[assignment]
    CLAUDE_AGENT_SDK_AVAILABLE = False


@dataclass
class AssistantReply:
    text: str
    action: Optional[dict[str, Any]] = None


class AssistantService:
    DEFAULT_SETTING_SOURCES = ["user", "project"]
    ALLOWED_SETTING_SOURCES = {"user", "project"}
    DEFAULT_ALLOWED_TOOLS = [
        "Skill",
        "Read",
        "Write",
        "Edit",
        "MultiEdit",
        "Bash",
        "Grep",
        "Glob",
        "LS",
    ]

    def __init__(
        self,
        project_root: Path,
        session_store: Optional[AgentSessionStore] = None,
        skill_bridge: Optional[SkillBridge] = None,
    ):
        self.project_root = Path(project_root)
        self._load_project_env(self.project_root)
        self.projects_root = self.project_root / "projects"
        self.pm = ProjectManager(self.projects_root)
        self.store = session_store or AgentSessionStore(
            self.projects_root / ".agent_sessions.db"
        )
        self.skill_bridge = skill_bridge or SkillBridge(self.project_root)
        self.enable_sdk = (
            os.environ.get("ASSISTANT_USE_CLAUDE_SDK", "1").strip().lower()
            not in {"0", "false", "no"}
        )
        self.sdk_setting_sources = self._normalize_setting_sources(
            self._parse_csv_env(
                "ASSISTANT_SETTING_SOURCES", default=self.DEFAULT_SETTING_SOURCES
            ),
            default=self.DEFAULT_SETTING_SOURCES,
        )
        self.sdk_allowed_tools = self._parse_csv_env(
            "ASSISTANT_ALLOWED_TOOLS", default=self.DEFAULT_ALLOWED_TOOLS
        )
        self.sdk_max_turns = self._parse_int_env("ASSISTANT_MAX_TURNS", default=8)
        self.sdk_system_prompt = os.environ.get(
            "ASSISTANT_SYSTEM_PROMPT",
            (
                "你是视频项目协作助手。优先复用项目中的 Skills 与现有文件结构，"
                "避免擅自改写数据格式。"
            ),
        ).strip()
        self.sdk_base_url = os.environ.get("ASSISTANT_ANTHROPIC_BASE_URL", "").strip()
        self.sdk_auth_token = os.environ.get(
            "ASSISTANT_ANTHROPIC_AUTH_TOKEN", ""
        ).strip()
        self.sdk_cli_path = os.environ.get("ASSISTANT_CLAUDE_CLI_PATH", "").strip()
        self.stream_registry = StreamRequestRegistry()
        self.stream_heartbeat_seconds = self._parse_int_env(
            "ASSISTANT_STREAM_HEARTBEAT_SECONDS", default=20
        )

    def create_session(self, project_name: str, title: str = "") -> AgentSession:
        self.pm.get_project_path(project_name)
        normalized_title = title.strip() or f"{project_name} 会话"
        return self.store.create_session(project_name=project_name, title=normalized_title)

    def list_sessions(
        self,
        project_name: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentSession]:
        return self.store.list_sessions(
            project_name=project_name, status=status, limit=limit, offset=offset
        )

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        return self.store.get_session(session_id)

    def list_messages(self, session_id: str, limit: int = 200) -> list[AgentMessage]:
        if self.store.get_session(session_id) is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        return self.store.list_messages(session_id=session_id, limit=limit)

    def archive_session(self, session_id: str) -> bool:
        return self.store.archive_session(session_id)

    def update_session_title(self, session_id: str, title: str) -> Optional[AgentSession]:
        if self.store.get_session(session_id) is None:
            return None
        normalized_title = title.strip() or "未命名会话"
        updated = self.store.update_session_title(session_id, normalized_title)
        if not updated:
            return None
        return self.store.get_session(session_id)

    async def delete_session(self, session_id: str) -> bool:
        request_ids = await self.stream_registry.list_request_ids(session_id=session_id)
        for request_id in request_ids:
            await self.cancel_stream_request(session_id=session_id, request_id=request_id)
            await self.stream_registry.remove_request(
                session_id=session_id,
                request_id=request_id,
            )
        return self.store.delete_session(session_id)

    async def send_user_message(self, session_id: str, content: str) -> dict[str, Any]:
        text = content.strip()
        if not text:
            raise ValueError("消息内容不能为空")

        session = self._require_active_session(session_id)

        self.store.add_message(session_id=session_id, role="user", content=text)

        reply = await self._build_reply(session, text)

        assistant_message = self.store.add_message(
            session_id=session_id,
            role="assistant",
            content=reply.text,
            event_type="message",
        )

        return {
            "session": self.store.get_session(session_id),
            "assistant_message": assistant_message,
            "action": reply.action,
        }

    async def start_stream_request(self, session_id: str, content: str) -> dict[str, Any]:
        text = content.strip()
        if not text:
            raise ValueError("消息内容不能为空")

        session = self._require_active_session(session_id)
        user_message = self.store.add_message(session_id=session_id, role="user", content=text)
        stream_request = await self.stream_registry.create_request(
            session_id=session_id,
            user_message_id=user_message.id,
        )
        stream_request.producer_task = asyncio.create_task(
            self._run_stream_request(stream_request=stream_request, session=session, text=text)
        )

        return {
            "session": self.store.get_session(session_id),
            "user_message": user_message,
            "request_id": stream_request.request_id,
        }

    async def stream_events(
        self, session_id: str, request_id: str
    ) -> AsyncIterator[str]:
        stream_request = await self.stream_registry.get_request(
            session_id=session_id,
            request_id=request_id,
        )
        if stream_request is None:
            raise FileNotFoundError(f"stream request not found: {request_id}")

        try:
            while True:
                try:
                    event = await stream_request.next_event(
                        timeout_seconds=self.stream_heartbeat_seconds
                    )
                except asyncio.TimeoutError:
                    yield self.stream_registry.ping_event()
                    continue

                yield event.to_sse()
                if event.event in {"done", "error"}:
                    break
        except asyncio.CancelledError:
            await self.cancel_stream_request(session_id=session_id, request_id=request_id)
            raise
        finally:
            await self.stream_registry.remove_request(
                session_id=session_id,
                request_id=request_id,
            )

    async def has_stream_request(self, session_id: str, request_id: str) -> bool:
        stream_request = await self.stream_registry.get_request(
            session_id=session_id,
            request_id=request_id,
        )
        return stream_request is not None

    async def cancel_stream_request(self, session_id: str, request_id: str) -> None:
        stream_request = await self.stream_registry.get_request(
            session_id=session_id,
            request_id=request_id,
        )
        if stream_request is None:
            return
        stream_request.closed = True
        producer_task = stream_request.producer_task
        if producer_task and not producer_task.done():
            producer_task.cancel()

    async def _run_stream_request(
        self, stream_request: StreamRequest, session: AgentSession, text: str
    ) -> None:
        try:
            await stream_request.emit(
                "ack",
                {
                    "request_id": stream_request.request_id,
                    "session_id": session.id,
                    "user_message_id": stream_request.user_message_id,
                },
            )
            reply = await self._build_stream_reply(session=session, text=text, stream_request=stream_request)
            reply_text = reply.text.strip() or "任务已完成，但未生成可显示内容。"
            assistant_message = self.store.add_message(
                session_id=session.id,
                role="assistant",
                content=reply_text,
                event_type="message",
            )
            await stream_request.emit(
                "done",
                {
                    "assistant_message_id": assistant_message.id,
                    "action": reply.action,
                },
            )
        except asyncio.CancelledError:
            await stream_request.emit(
                "error",
                {"code": "STREAM_CANCELLED", "message": "stream cancelled"},
            )
            raise
        except Exception as exc:
            await stream_request.emit(
                "error",
                {"code": "STREAM_ERROR", "message": str(exc)},
            )

    async def _build_stream_reply(
        self, session: AgentSession, text: str, stream_request: StreamRequest
    ) -> AssistantReply:
        if text == "/":
            reply = AssistantReply(
                text=self._build_skills_help_text(session.project_name),
                action={"mode": "skills_help"},
            )
            await self._emit_delta_chunks(stream_request, reply.text)
            return reply

        legacy_reply = self._try_legacy_bridge(session, text)
        if legacy_reply is not None:
            await self._emit_delta_chunks(stream_request, legacy_reply.text)
            return legacy_reply

        if self._can_use_sdk():
            try:
                return await self._reply_with_claude_sdk_stream(
                    session=session,
                    user_text=text,
                    on_delta=lambda chunk: stream_request.emit("delta", {"text": chunk}),
                )
            except Exception as exc:
                error_detail = self._format_sdk_error(exc)
                fallback_text = (
                    "Claude Agent SDK 调用失败，已切换到基础回复。\n"
                    f"失败原因：{error_detail}"
                )
                await self._emit_delta_chunks(stream_request, fallback_text)
                return AssistantReply(
                    text=fallback_text,
                    action={
                        "mode": "claude_agent_sdk",
                        "success": False,
                        "error": error_detail,
                    },
                )

        fallback_reply = AssistantReply(
            text=self._build_default_reply(session.project_name),
            action={
                "mode": "fallback",
                "sdk_available": CLAUDE_AGENT_SDK_AVAILABLE,
                "sdk_enabled": self.enable_sdk,
            },
        )
        await self._emit_delta_chunks(stream_request, fallback_reply.text)
        return fallback_reply

    async def _emit_delta_chunks(
        self, stream_request: StreamRequest, text: str, chunk_size: int = 80
    ) -> None:
        value = text or ""
        if not value:
            return
        for index in range(0, len(value), chunk_size):
            chunk = value[index : index + chunk_size]
            await stream_request.emit("delta", {"text": chunk})

    def list_available_skills(
        self, project_name: Optional[str] = None
    ) -> list[dict[str, str]]:
        if project_name:
            self.pm.get_project_path(project_name)

        source_roots = {
            "project": self.project_root / ".claude" / "skills",
            "user": Path.home() / ".claude" / "skills",
        }
        enabled_sources = self._normalize_setting_sources(
            self.sdk_setting_sources, default=self.DEFAULT_SETTING_SOURCES
        )
        roots = [
            (source, source_roots[source])
            for source in enabled_sources
            if source in source_roots
        ]

        skills: list[dict[str, str]] = []
        seen_keys: set[str] = set()

        for scope, root in roots:
            if not root.exists() or not root.is_dir():
                continue
            try:
                directories = sorted(root.iterdir())
            except OSError:
                continue
            for skill_dir in directories:
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue

                try:
                    metadata = self._load_skill_metadata(
                        skill_file, fallback_name=skill_dir.name
                    )
                except OSError:
                    continue
                key = f"{scope}:{metadata['name']}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                skills.append(
                    {
                        "name": metadata["name"],
                        "description": metadata["description"],
                        "scope": scope,
                        "path": str(skill_file),
                    }
                )

        return skills

    async def _build_reply(self, session: AgentSession, text: str) -> AssistantReply:
        if text == "/":
            return AssistantReply(
                text=self._build_skills_help_text(session.project_name),
                action={"mode": "skills_help"},
            )

        legacy_reply = self._try_legacy_bridge(session, text)
        if legacy_reply is not None:
            return legacy_reply

        if self._can_use_sdk():
            try:
                return await self._reply_with_claude_sdk(session, text)
            except Exception as exc:
                error_detail = self._format_sdk_error(exc)
                fallback_text = (
                    "Claude Agent SDK 调用失败，已切换到基础回复。\n"
                    f"失败原因：{error_detail}"
                )
                return AssistantReply(
                    text=fallback_text,
                    action={
                        "mode": "claude_agent_sdk",
                        "success": False,
                        "error": error_detail,
                    },
                )

        return AssistantReply(
            text=self._build_default_reply(session.project_name),
            action={
                "mode": "fallback",
                "sdk_available": CLAUDE_AGENT_SDK_AVAILABLE,
                "sdk_enabled": self.enable_sdk,
            },
        )

    def _require_active_session(self, session_id: str) -> AgentSession:
        session = self.store.get_session(session_id)
        if session is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        if session.status != "active":
            raise ValueError("会话不是 active 状态，无法继续发送消息")
        return session

    def _can_use_sdk(self) -> bool:
        return self.enable_sdk and CLAUDE_AGENT_SDK_AVAILABLE and query is not None

    @staticmethod
    def _accumulate_stream_candidate(current: str, candidate: str) -> tuple[str, str]:
        text = candidate.strip()
        if not text:
            return current, ""
        if not current:
            return text, text
        if text.startswith(current):
            return text, text[len(current) :]
        if current.startswith(text) or text in current:
            return current, ""

        separator = "\n\n"
        merged = f"{current}{separator}{text}"
        return merged, f"{separator}{text}"

    async def _reply_with_claude_sdk(
        self, session: AgentSession, user_text: str
    ) -> AssistantReply:
        return await self._reply_with_claude_sdk_stream(
            session=session,
            user_text=user_text,
            on_delta=None,
        )

    async def _reply_with_claude_sdk_stream(
        self,
        session: AgentSession,
        user_text: str,
        on_delta: Optional[Callable[[str], Awaitable[Any]]],
        on_tool_use: Optional[Callable[[dict[str, Any]], Awaitable[Any]]] = None,
        on_tool_result: Optional[Callable[[dict[str, Any]], Awaitable[Any]]] = None,
    ) -> AssistantReply:
        self._apply_sdk_network_env()
        prompt = self._build_sdk_prompt(session, user_text)
        options = self._build_sdk_options()

        stream_messages: list[Any] = []
        merged_text = ""
        emitted_delta = False

        async for message in query(prompt=prompt, options=options):
            stream_messages.append(message)

            # Extract and emit content blocks
            blocks = self._extract_content_blocks(message)
            for block in blocks:
                if block["type"] == "text":
                    text = block["text"]
                    if text and on_delta:
                        merged_text += text
                        emitted_delta = True
                        await on_delta(text)
                elif block["type"] == "tool_use" and on_tool_use:
                    await on_tool_use(block)
                elif block["type"] == "tool_result" and on_tool_result:
                    await on_tool_result(block)

            # Fallback: extract partial delta from stream events
            if not blocks:
                partial_delta = self._extract_partial_delta(message)
                if partial_delta:
                    merged_text += partial_delta
                    emitted_delta = True
                    if on_delta:
                        await on_delta(partial_delta)
                    continue

                candidate = self._extract_text_candidate(message)
                if not candidate:
                    continue
                merged_text, delta = self._accumulate_stream_candidate(merged_text, candidate)
                if on_delta and delta:
                    emitted_delta = True
                    await on_delta(delta)

        reply_text = merged_text.strip() or self._extract_reply_text(stream_messages)
        if not reply_text:
            reply_text = "任务已提交，但没有收到可解析文本输出。"
            if on_delta:
                await on_delta(reply_text)
                emitted_delta = True
        elif on_delta and not emitted_delta:
            for chunk in self._split_text_chunks(reply_text):
                await on_delta(chunk)

        return AssistantReply(
            text=reply_text,
            action={
                "mode": "claude_agent_sdk",
                "success": True,
                "messages_count": len(stream_messages),
                "setting_sources": self.sdk_setting_sources,
                "allowed_tools": self.sdk_allowed_tools,
                "base_url": os.environ.get("ANTHROPIC_BASE_URL", ""),
            },
        )

    def _build_sdk_options(self) -> ClaudeAgentOptions:
        if ClaudeAgentOptions is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        return ClaudeAgentOptions(
            cwd=str(self.project_root),
            cli_path=self.sdk_cli_path or None,
            setting_sources=self.sdk_setting_sources,
            allowed_tools=self.sdk_allowed_tools,
            max_turns=self.sdk_max_turns,
            system_prompt=self.sdk_system_prompt,
            include_partial_messages=True,
        )

    @staticmethod
    def _format_sdk_error(exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return message
        return f"{type(exc).__name__}: {repr(exc)}"

    def _apply_sdk_network_env(self) -> None:
        if self.sdk_base_url:
            os.environ["ANTHROPIC_BASE_URL"] = self.sdk_base_url
            if not self.sdk_auth_token:
                raise RuntimeError(
                    "已设置 ASSISTANT_ANTHROPIC_BASE_URL，但缺少 "
                    "ASSISTANT_ANTHROPIC_AUTH_TOKEN。"
                )
            os.environ["ANTHROPIC_AUTH_TOKEN"] = self.sdk_auth_token

    def _build_sdk_prompt(self, session: AgentSession, user_text: str) -> str:
        project = self.pm.load_project(session.project_name)
        history = self.store.list_messages(session.id, limit=24)
        if history and history[-1].role == "user":
            history = history[:-1]
        project_title = project.get("title", session.project_name)
        content_mode = project.get("content_mode", "narration")

        normalized_user_text = self._normalize_slash_prompt(
            session.project_name, user_text
        )
        history_text = self._format_history_for_prompt(history)

        return (
            f"当前项目: {session.project_name}\n"
            f"项目标题: {project_title}\n"
            f"内容模式: {content_mode}\n\n"
            "请优先复用项目已有数据结构（project.json、scripts/*.json），"
            "避免引入新格式。\n\n"
            f"历史对话（最近消息）:\n{history_text}\n\n"
            f"用户最新消息:\n{normalized_user_text}"
        )

    def _normalize_slash_prompt(self, project_name: str, user_text: str) -> str:
        text = user_text.strip()
        if not text.startswith("/"):
            return text

        stripped = text[1:].strip()
        if not stripped:
            return "请列出当前可用的 Skills，并简述每个技能用途。"

        parts = stripped.split(maxsplit=1)
        skill_token = parts[0]
        payload = parts[1].strip() if len(parts) > 1 else ""

        if skill_token in {"skills", "skill", "help"}:
            return "请列出当前可用的 Skills，并说明每个 Skill 适用场景。"

        skill_names = {item["name"] for item in self.list_available_skills(project_name)}
        if skill_token in skill_names:
            if payload:
                return (
                    f"请优先调用名为 `{skill_token}` 的 Skill 处理以下请求：\n{payload}"
                )
            return (
                f"请说明 Skill `{skill_token}` 的用途，并告诉我执行它还需要哪些输入。"
            )

        return text

    def _format_history_for_prompt(self, history: list[AgentMessage]) -> str:
        lines: list[str] = []
        for message in history[-10:]:
            if message.event_type in {"tool_call", "tool_result"}:
                continue
            role = "user" if message.role == "user" else "assistant"
            lines.append(f"{role}: {message.content}")
        return "\n".join(lines) if lines else "(empty)"

    def _extract_reply_text(self, stream_messages: list[Any]) -> str:
        candidates: list[str] = []
        for message in stream_messages:
            text = self._extract_text_candidate(message)
            if text:
                candidates.append(text)
        return self._merge_candidates(candidates)

    def _extract_text_candidate(self, message: Any) -> str:
        text = self._value_to_text(message)
        return text.strip() if text else ""

    def _extract_partial_delta(self, message: Any) -> str:
        event = getattr(message, "event", None)
        if not isinstance(event, dict):
            return ""

        event_type = str(event.get("type") or "").strip()
        if event_type == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict):
                return ""
            delta_type = str(delta.get("type") or "").strip()
            if delta_type == "text_delta":
                value = delta.get("text")
                return str(value) if isinstance(value, str) else ""
            return ""

        if event_type == "content_block_start":
            content_block = event.get("content_block")
            if not isinstance(content_block, dict):
                return ""
            if content_block.get("type") != "text":
                return ""
            value = content_block.get("text")
            return str(value) if isinstance(value, str) else ""

        return ""

    def _value_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        if isinstance(value, list):
            parts = [self._value_to_text(item) for item in value]
            return "\n".join(part for part in parts if part.strip())
        if isinstance(value, tuple):
            parts = [self._value_to_text(item) for item in value]
            return "\n".join(part for part in parts if part.strip())
        if isinstance(value, dict):
            for key in ("result", "text", "message", "output"):
                item = value.get(key)
                item_text = self._value_to_text(item)
                if item_text.strip():
                    return item_text
            content_text = self._value_to_text(value.get("content"))
            if content_text.strip():
                return content_text
            return ""

        for attr in ("result", "text", "message", "output", "content"):
            if hasattr(value, attr):
                item_text = self._value_to_text(getattr(value, attr))
                if item_text.strip():
                    return item_text
        return ""

    def _merge_candidates(self, candidates: list[str]) -> str:
        unique: list[str] = []
        for item in candidates:
            normalized = item.strip()
            if normalized and normalized not in unique:
                unique.append(normalized)

        if not unique:
            return ""
        if len(unique) == 1:
            return unique[0]

        last = unique[-1]
        if all(prev in last for prev in unique[:-1]):
            return last

        return "\n\n".join(unique)

    @staticmethod
    def _split_text_chunks(text: str, chunk_size: int = 80) -> list[str]:
        value = text or ""
        if not value:
            return []
        return [value[index : index + chunk_size] for index in range(0, len(value), chunk_size)]

    def _try_legacy_bridge(
        self, session: AgentSession, text: str
    ) -> Optional[AssistantReply]:
        if not text.startswith("/"):
            return None

        try:
            skill_command = self.skill_bridge.parse_command(text)
        except ValueError as exc:
            if str(exc) == "暂不支持的命令":
                return None
            return AssistantReply(
                text=self._format_command_error(str(exc)),
                action={"mode": "legacy_bridge", "success": False},
            )

        if skill_command is None:
            return None

        self.store.add_message(
            session_id=session.id,
            role="tool",
            content=" ".join([skill_command.action, *skill_command.args]).strip(),
            event_type="tool_call",
        )
        action_result = self.skill_bridge.execute(
            project_name=session.project_name,
            skill_command=skill_command,
        )
        tool_output = (
            action_result.stdout
            if action_result.success
            else action_result.stderr or action_result.stdout
        )
        if tool_output.strip():
            self.store.add_message(
                session_id=session.id,
                role="tool",
                content=tool_output.strip(),
                event_type="tool_result",
            )
        return AssistantReply(
            text=self._format_action_result(action_result),
            action=self._action_payload(action_result),
        )

    def _build_skills_help_text(self, project_name: str) -> str:
        skills = self.list_available_skills(project_name)
        if not skills:
            return "当前未发现可用 Skill。请检查 .claude/skills 目录。"

        lines = [
            "可用 Skills（输入 `/技能名 你的任务` 指定）：",
        ]
        for item in skills[:20]:
            desc = item["description"] or "No description"
            lines.append(f"- {item['name']} ({item['scope']}): {desc}")
        if len(skills) > 20:
            lines.append(f"... 还有 {len(skills) - 20} 个 Skill")
        return "\n".join(lines)

    def _build_default_reply(self, project_name: str) -> str:
        project = self.pm.load_project(project_name)
        title = project.get("title", project_name)
        mode = project.get("content_mode", "narration")
        commands = "、".join(self.skill_bridge.supported_commands())
        sdk_status = "已启用" if self._can_use_sdk() else "未启用"
        return (
            f"已收到消息，当前项目为《{title}》（{mode} 模式）。\n"
            f"Claude Agent SDK 状态：{sdk_status}。\n"
            "你可以直接自然语言提问，或输入 `/` 查看 Skills，再用 `/技能名 任务` 指定技能。\n"
            f"兼容命令：{commands}"
        )

    def _format_command_error(self, detail: str) -> str:
        commands = "\n".join(self.skill_bridge.supported_commands())
        return f"命令格式错误：{detail}\n可用命令：\n{commands}"

    @staticmethod
    def _format_action_result(result: SkillExecutionResult) -> str:
        if result.success:
            if result.stdout:
                return f"命令执行成功：\n{result.stdout}"
            return "命令执行成功。"

        if result.stderr:
            return f"命令执行失败（exit={result.return_code}）：\n{result.stderr}"
        return f"命令执行失败（exit={result.return_code}）。"

    @staticmethod
    def _action_payload(result: Optional[SkillExecutionResult]) -> Optional[dict[str, Any]]:
        if result is None:
            return None
        return {
            "action": result.action,
            "success": result.success,
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": result.command,
        }

    @staticmethod
    def _load_project_env(project_root: Path) -> None:
        env_path = project_root / ".env"
        if not env_path.exists():
            return
        try:
            from dotenv import load_dotenv
        except ImportError:
            return
        load_dotenv(env_path, override=False)

    @staticmethod
    def _parse_csv_env(name: str, default: list[str]) -> list[str]:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return list(default)
        normalized = raw.replace("，", ",")
        items = [item.strip() for item in normalized.split(",")]
        return [item for item in items if item]

    @classmethod
    def _normalize_setting_sources(
        cls, values: list[str], default: list[str]
    ) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip().strip("，,").lower()
            if normalized not in cls.ALLOWED_SETTING_SOURCES:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result if result else list(default)

    @staticmethod
    def _parse_int_env(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @staticmethod
    def _load_skill_metadata(skill_file: Path, fallback_name: str) -> dict[str, str]:
        content = skill_file.read_text(encoding="utf-8", errors="ignore")
        name = fallback_name
        description = ""

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                body = parts[2]
                for line in frontmatter.splitlines():
                    if ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key == "name" and value:
                        name = value
                    elif key == "description" and value:
                        description = value
                if not description:
                    description = AssistantService._extract_first_paragraph(body)
        else:
            description = AssistantService._extract_first_paragraph(content)

        return {"name": name, "description": description}

    @staticmethod
    def _extract_first_paragraph(markdown: str) -> str:
        for line in markdown.splitlines():
            text = line.strip()
            if not text:
                continue
            if text.startswith("#"):
                continue
            return text
        return ""

    @staticmethod
    def _clean_tool_output(content: str, max_length: int = 2000) -> str:
        """Clean tool output by removing line numbers and truncating if too long."""
        if not content:
            return ""

        import re
        # Remove line number prefixes like "    1→", "   12→", "  123→"
        lines = content.split("\n")
        cleaned_lines = []
        for line in lines:
            # Match pattern: optional spaces + digits + arrow (→)
            cleaned = re.sub(r"^\s*\d+→", "", line)
            cleaned_lines.append(cleaned)

        result = "\n".join(cleaned_lines)

        # Truncate if too long, keeping head and tail
        if len(result) > max_length:
            head_size = max_length // 2 - 50
            tail_size = max_length // 2 - 50
            result = (
                result[:head_size]
                + "\n\n... [内容已截断] ...\n\n"
                + result[-tail_size:]
            )

        return result.strip()

    def _extract_content_blocks(self, message: Any) -> list[dict[str, Any]]:
        """Extract ContentBlock list from SDK message."""
        blocks: list[dict[str, Any]] = []

        # Check for AssistantMessage with content list
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            return blocks

        for block in content:
            block_type = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )

            if block_type == "text" or hasattr(block, "text"):
                text = getattr(block, "text", None) or (
                    block.get("text") if isinstance(block, dict) else ""
                )
                if text:
                    blocks.append({"type": "text", "text": text})

            elif block_type == "tool_use" or hasattr(block, "name"):
                blocks.append({
                    "type": "tool_use",
                    "id": getattr(block, "id", "") or block.get("id", ""),
                    "name": getattr(block, "name", "") or block.get("name", ""),
                    "input": getattr(block, "input", {}) or block.get("input", {}),
                })

            elif block_type == "tool_result" or hasattr(block, "tool_use_id"):
                raw_content = getattr(block, "content", "") or block.get("content", "")
                if isinstance(raw_content, list):
                    # Extract text from content list
                    text_parts = []
                    for item in raw_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                    raw_content = "\n".join(text_parts)

                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": getattr(block, "tool_use_id", "") or block.get("tool_use_id", ""),
                    "content": self._clean_tool_output(str(raw_content)),
                    "is_error": getattr(block, "is_error", False) or block.get("is_error", False),
                })

            elif block_type == "thinking" or hasattr(block, "thinking"):
                thinking = getattr(block, "thinking", "") or block.get("thinking", "")
                if thinking:
                    blocks.append({
                        "type": "thinking",
                        "thinking": thinking,
                        "signature": getattr(block, "signature", "") or block.get("signature", ""),
                    })

        return blocks
