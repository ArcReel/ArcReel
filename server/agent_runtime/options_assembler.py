"""SDK options assembler: holds dependencies, permits I/O, and builds ClaudeAgentOptions asynchronously.

Extracts all on-demand assembly responsibilities for session cold starts/reconnections from SessionManager: DB credential injection
(actual Anthropic values + empty overrides for other providers), prompt variants and project context, PreToolUse/
PostToolUse hook factories (including JSON validation), AgentAccessPolicy settings compilation, and
decision-adapter wiring. I/O responsibilities follow the same criterion as AgentAccessPolicy: static, zero-I/O rules belong to
policy; session-opening assembly that inherently reads DB / scans disk permits I/O and belongs here.

Access rules themselves are outside this class: each build retrieves policy through ``access_policy_provider``,
so replacing policy through ``configure_sandbox_runtime`` immediately affects all subsequent sessions.
"""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from lib.agent.agent_memory_index import INDEX_FILENAME, truncate_memory_index
from lib.agent.agent_memory_paths import is_valid_memory_user_id, project_memory_dir
from lib.agent.agent_session_store import (
    is_known_session_store_mode,
    session_store_flush_mode,
    session_store_mode,
)
from lib.agent.agent_session_store.store import DbSessionStore
from lib.agent.credential_tiers import AgentPricing
from lib.db.base import DEFAULT_USER_ID
from lib.db.engine import async_session_factory as default_async_session_factory
from lib.i18n import DEFAULT_LOCALE, LOCALE_LANGUAGE_MAP
from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import DataRootLayout
from lib.prompts.prompt_templates.builtin import builtin_templates
from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.arcreel_mcp import build_arcreel_mcp_server
from server.auth import EMBEDDED_AGENT_SUB, create_token, is_auth_enabled

logger = logging.getLogger(__name__)

from claude_agent_sdk import ClaudeAgentOptions, McpSdkServerConfig
from claude_agent_sdk.types import HookEvent, HookMatcher, SettingSource, SystemPromptPreset

SDK_AVAILABLE = True
_EMBEDDED_AGENT_TOKEN_EXPIRY_SECONDS = 15 * 60
# Maximum bytes per NDJSON line on CLI stdout (does not constrain SDK stdin writes).
# SDK defaults to 1 MiB; excess causes CLIJSONDecodeError, killing the message reader and hanging sessions.
# Image requests hit this boundary twice: replay-user-messages echoes base64-image user messages
# unchanged, and image Read tool_result uses the same stdout. Size for our payload: 5 images ×
# 1.5 MB source × 4/3 base64 ≈ 10 MB; use 32 MiB for headroom. No official recommendation exists.
CLI_STDOUT_MAX_BUFFER_BYTES = 32 * 1024 * 1024


class AgentEnv(dict[str, str]):
    """Environment variables injected into ``ClaudeAgentOptions.env``, carrying a pricing-basis snapshot for injected credentials.

    Account using credentials injected at connection and their then-current multiplier/tier bindings (docs/adr/0017, docs/adr/0102); pass the snapshot with env to
    the session layer; use defaults without effective credentials when env falls back to system settings.
    """

    def __init__(self, env: dict[str, str], *, agent_pricing: AgentPricing) -> None:
        super().__init__(env)
        self.agent_pricing = agent_pricing


def agent_pricing_of(options: object) -> AgentPricing:
    """Pricing basis for Agent credentials injected through session options; default when env is not :class:`AgentEnv`."""
    env = getattr(options, "env", None)
    return env.agent_pricing if isinstance(env, AgentEnv) else AgentPricing()


async def load_provider_env_overrides() -> dict[str, str]:
    """Build the options.env injection dictionary and return :class:`AgentEnv`.

    - ANTHROPIC_* receives actual values from the DB active credential
    - Override every other provider env with empty values (defensive fallback)

    ``env_keys`` is the sole environment-variable source of truth. SDK subprocesses authenticate only through env, and parent environments
    are external inputs, so actual-value injection and empty-value containment are permanent mechanisms, not technical debt (see ADR).
    """
    from lib.config.env_keys import OTHER_PROVIDER_ENV_KEYS
    from lib.config.service import build_anthropic_env
    from lib.db import async_session_factory

    async with async_session_factory() as session:
        anthropic = await build_anthropic_env(session)

    result = AgentEnv(anthropic.env, agent_pricing=anthropic.pricing)
    for key in OTHER_PROVIDER_ENV_KEYS:
        result[key] = ""
    return result


def _resolve_and_read_text(project_cwd: Path, file_path: str) -> tuple[Path, str]:
    """Resolve hook file paths (relative to project cwd) and read UTF-8 text."""
    p = Path(file_path)
    resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()
    return resolved, resolved.read_text(encoding="utf-8")


class OptionsAssembler:
    """Assemble dependencies collected at session opening into ClaudeAgentOptions.

    Constructor arguments have two categories: static dependencies (``data_root`` / ``allowed_tools`` /
    ``setting_sources``) fixed at instantiation; runtime-varying dependencies retrieved through provider callbacks on every build:
    ``access_policy_provider`` (replaced by ``configure_sandbox_runtime``) and
    ``max_turns_provider`` (changed by ``refresh_config``). ``resolve_project_cwd`` is injected by
    SessionManager (project-name validation/scope belongs to session management).

    ``session_factory_provider`` / ``user_id_provider`` likewise use callbacks, not construction-time snapshots:
    create and cache the store using current values on the first ``build_session_store``, matching the pre-extraction
    timing of ``_build_session_store`` lazy SessionManager reads; prevent usage records
    (reading live ``_user_id``) and transcript stores from using different per-user namespaces.
    """

    def __init__(
        self,
        *,
        data_root: Path,
        allowed_tools: Sequence[str],
        setting_sources: Sequence[SettingSource],
        access_policy_provider: Callable[[], AgentAccessPolicy],
        max_turns_provider: Callable[[], int | None],
        resolve_project_cwd: Callable[[str], Path],
        provider_env_loader: Callable[[], Awaitable[dict[str, str]]] | None = None,
        session_factory_provider: Callable[[], Any] | None = None,
        user_id_provider: Callable[[], str] | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.layout = DataRootLayout(self.data_root)
        self._allowed_tools = list(allowed_tools)
        self._setting_sources = list(setting_sources)
        self._access_policy_provider = access_policy_provider
        self._max_turns_provider = max_turns_provider
        self._resolve_project_cwd = resolve_project_cwd
        self._provider_env_loader = provider_env_loader
        self._session_factory_provider = session_factory_provider or (lambda: None)
        self._user_id_provider = user_id_provider or (lambda: DEFAULT_USER_ID)
        # Session-store singleton cache: one per assembler, avoiding a new store on every build.
        self._cached_session_store: DbSessionStore | None = None
        self._session_store_resolved = False

    async def build_provider_env_overrides(self) -> dict[str, str]:
        """DB credential injection entry point. Default to module-level ``load_provider_env_overrides`` (read module
        global live for test patching); use an injected ``provider_env_loader`` instead when supplied at construction."""
        loader = self._provider_env_loader or load_provider_env_overrides
        return await loader()

    async def _build_append_prompt(self, project_name: str, locale: str = DEFAULT_LOCALE) -> str:
        """Build the append portion for SystemPromptPreset.

        Combines the locale language regulation, the session-invariant project
        context (identity, cwd, operating rules) and the user-memory segment.
        Mutable project metadata is not included here — it lives in project.json
        and is read on demand. The project's CLAUDE.md (mode variant projected
        into the cwd, carrying the Agent persona) is auto-loaded by the SDK via
        setting_sources=["project"].
        """
        lang = LOCALE_LANGUAGE_MAP.get(locale, "中文")
        parts = [builtin_templates.render("text/agent_language_rule", lang=lang)]

        project_context = await asyncio.to_thread(self._build_project_context, project_name)
        if project_context:
            parts.append(project_context)

        user_memory = await self._build_user_memory_section()
        if user_memory:
            parts.append(user_memory)

        return "\n\n".join(parts)

    async def _build_user_memory_section(self) -> str:
        """User-memory section: directory, two-level scope rules, access method, and truncated index.

        Native auto memory knows only the project-memory directory. It cannot know user memory exists or the two-level scope rules,
        so add only those two facts; native system prompts carry categories, format, and write criteria.
        Omit the index bullet when missing or empty: injecting an empty index would make the Agent
        infer no past memories, distinct from a directory not yet created.

        Do not create directories during assembly: first writes belong to the Agent or memory REST API; unreadable means absent.
        Omit the whole section for invalid ``user_id``, matching ``AgentAccessPolicy`` fail-closed behavior:
        do not provide write instructions for directories outside the allowed fence.
        """
        user_id = self._user_id_provider()
        if not is_valid_memory_user_id(user_id):
            logger.error("user_id 不是单个路径段，用户记忆段省略: %r", user_id)
            return ""

        memory_dir = self.layout.user_memory_dir(user_id)
        index = truncate_memory_index(await self._read_user_memory_index(memory_dir))

        lines = [
            "## User memory",
            "",
            "In addition to project memory (auto memory scoped to this project), you have **user memory** belonging to the current user, "
            f"applying across all their projects, in `{memory_dir.as_posix()}`. Use the same file structure and maintenance approach as project memory.",
            "",
            "- **Scope**: store preferences that apply across all of this user's projects in user memory; keep project-specific information in project memory. When uncertain, use project memory.",
            "- **Access**: use Read / Write / Edit / Glob / Grep directly on files in this directory; Bash cannot read or write this directory.",
        ]
        if index:
            lines.append(
                f"- **Index**: the following is user memory's `{INDEX_FILENAME}`, containing notes from your previous sessions."
            )
            lines.append("")
            lines.append(index)
        return "\n".join(lines)

    @staticmethod
    async def _read_user_memory_index(memory_dir: Path) -> str:
        """Read the user-memory index; missing directories/files, unreadable content, or non-UTF-8 count as empty.

        Offload reads to a thread: assembly runs on the event loop and the small data-root index may live on network storage.

        Swallow encoding errors like I/O failures: users may edit indexes directly, and non-UTF-8 saves make
        ``read_text`` raise ``UnicodeDecodeError``; propagation would let a note saved with the wrong encoding
        prevent all sessions from opening. Do not use ``errors="replace"``: systematically misdecoded GBK-like contents
        cannot become readable that way, and injecting gibberish is worse than omitting the index.
        """
        index_path = memory_dir / INDEX_FILENAME
        try:
            return await asyncio.to_thread(index_path.read_text, encoding="utf-8")
        except FileNotFoundError:
            return ""
        except (OSError, UnicodeDecodeError):
            logger.exception("用户记忆索引读取失败，当前会话按空索引装配: %s", index_path)
            return ""

    def _build_project_context(self, project_name: str) -> str:
        """Build session-invariant project context for the system prompt.

        Holds only facts that cannot change within a session: project identity,
        cwd, and static operating rules. Mutable metadata (title, style,
        overview, ...) lives in project.json and is read on demand by the agent
        and tools — never baked into the session-fixed system prompt.
        """
        try:
            project_cwd = self._resolve_project_cwd(project_name)
        except (ValueError, FileNotFoundError):
            return ""

        parts = [
            "## Current project context",
            "",
            f"- Project identifier: {project_name}",
            f"- Project directory (the current working directory, cwd): {project_cwd.as_posix()}",
            "- Project metadata (title, style, overview, etc.) lives in project.json; read it when needed.",
            "- Write Bash commands on a single line, without `\\` line continuation; use compact JSON arguments.",
        ]
        return "\n".join(parts)

    def build_session_store(self) -> DbSessionStore | None:
        """Return a cached per-user DbSessionStore, or None when env disables it.

        Set ARCREEL_SDK_SESSION_STORE=off to roll back to SDK's filesystem path.
        The result is cached on first call so every session shares one instance
        instead of allocating a fresh store per ``build`` invocation.
        """
        if self._cached_session_store is not None or self._session_store_resolved:
            return self._cached_session_store

        mode = session_store_mode()
        store: DbSessionStore | None
        if mode == "off":
            store = None
        else:
            if not is_known_session_store_mode(mode):
                logger.warning("Unknown ARCREEL_SDK_SESSION_STORE=%r; defaulting to db", mode)
            factory = self._session_factory_provider() or default_async_session_factory
            store = DbSessionStore(factory, user_id=self._user_id_provider())
        self._cached_session_store = store
        self._session_store_resolved = True
        return store

    async def build(
        self,
        project_name: str,
        resume_id: str | None = None,
        can_use_tool: Callable[[str, dict[str, Any], Any], Any] | None = None,
        locale: str = DEFAULT_LOCALE,
        stderr: Callable[[str], None] | None = None,
        session_id: str | None = None,
    ) -> Any:
        """Build ClaudeAgentOptions for a session.

        ``stderr`` is the sole source of actual errors when SDK subprocesses exit nonzero
        (``ProcessError.stderr`` is hardcoded to a placeholder inside the SDK); on session startup failure,
        upper layers should wrap callback-accumulated lines in ``AgentStartupError`` and propagate them.

        ``session_id`` opens a brand-new session with a predetermined ID (no resumable history),
        mutually exclusive with ``resume_id``; the SDK permits both only under ``fork_session``.
        """
        if not SDK_AVAILABLE:
            raise RuntimeError("claude_agent_sdk is not installed")
        if resume_id is not None and session_id is not None:
            raise ValueError("resume_id and session_id are mutually exclusive")

        policy = self._access_policy_provider()

        project_cwd = await asyncio.to_thread(self._resolve_project_cwd, project_name)

        # Build PreToolUse hooks — file access control MUST use hooks because
        # Read/Glob/Grep are matched by allow rules (step 4 in the SDK
        # permission chain) before reaching can_use_tool (step 5).  Hooks
        # (step 1) fire for ALL tool calls and can override allow rules.
        hooks: dict[HookEvent, list[HookMatcher]] | None = None
        hook_callbacks: list[Any] = [
            self._subagent_tool_hook,
            self._build_file_access_hook(project_cwd),
        ]
        if can_use_tool is not None:
            # Official Python SDK guidance: keep stream open when using
            # can_use_tool.
            hook_callbacks.insert(0, self._keep_stream_open_hook)

        # Shared dict: PreToolUse saves file backup, PostToolUse restores
        # on corruption.  Keyed by tool_use_id.
        json_backups: dict[str, tuple[Path, str]] = {}

        hooks = {
            "PreToolUse": [
                HookMatcher(matcher=None, hooks=hook_callbacks),
                HookMatcher(
                    matcher="Bash",
                    hooks=[self._bash_env_scrub_hook],  # type: ignore[list-item]  # Attached only to PreToolUse Bash matchers; read tool_input from that event's dict. SDK HookCallback declares a union of all event inputs
                ),
                HookMatcher(
                    matcher="Write|Edit",
                    hooks=[
                        self._build_json_validation_hook(project_cwd, json_backups),
                    ],
                ),
            ],
            "PostToolUse": [
                HookMatcher(
                    matcher="Write|Edit",
                    hooks=[
                        self._build_json_post_validation_hook(project_cwd, json_backups),
                    ],
                ),
            ],
        }

        provider_env = await self.build_provider_env_overrides()
        provider_env.update(
            {
                "ARCREEL_EMBEDDED_AGENT": "1",
                # Subscribe to session_state_changed frames: while the CLI owes another round after background-subagent completion it keeps reporting running,
                # matching SDK run-completion criteria; the session layer uses this to decide whether CLI disconnection is safe.
                "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1",
                "ARCREEL_API_BASE": (os.environ.get("ARCREEL_API_BASE") or "http://127.0.0.1:1241/api/v1").rstrip("/"),
                "ARCREEL_API_TOKEN": (
                    create_token(EMBEDDED_AGENT_SUB, expiry_seconds=_EMBEDDED_AGENT_TOKEN_EXPIRY_SECONDS)
                    if is_auth_enabled()
                    else ""
                ),
            }
        )
        user_id = self._user_id_provider()

        def _build_sandbox_and_mcp_server() -> tuple[dict[str, Any], McpSdkServerConfig]:
            # Both scan disk/create directories (sandbox sensitive-path resolution and MCP ProjectManager); combine and offload to a thread.
            sandbox = policy.build_sandbox_settings(project_cwd)
            # Built-in ArcReel in-process MCP server: handlers run in the main process, bypassing sandboxing.
            server = build_arcreel_mcp_server(project_name=project_name, data_root=self.data_root, user_id=user_id)
            return sandbox, server

        sandbox_typed, arcreel_server = await asyncio.to_thread(_build_sandbox_and_mcp_server)

        # Windows fallback: with sandboxing disabled, remove Bash tools from allowed_tools,
        # letting _can_use_tool handle prefix-whitelist matching.
        allowed_tools = policy.filter_allowed_tools(self._allowed_tools)
        # Wildcards avoid updating allowed_tools for every newly added tool.
        allowed_tools.append("mcp__arcreel__*")

        return ClaudeAgentOptions(
            cwd=str(project_cwd),
            setting_sources=self._setting_sources,
            # Project memory: redirect native auto memory from git-root-derived directories into the project directory,
            # or all ArcReel projects and developer interactive sessions on one machine would share MEMORY.md.
            # Use a JSON string instead of a materialized settings file: paths vary per session, and disk writes conflict with the profile manifest's
            # unmodified-built-in-file detection (ADR 0074). Settings use the flag-settings layer, higher priority than
            # project .claude/settings.json; the SDK later merges ``sandbox`` into the same JSON.
            # Do not explicitly set autoMemoryEnabled: native default is enabled.
            settings=json.dumps({"autoMemoryDirectory": str(project_memory_dir(project_cwd))}),
            allowed_tools=allowed_tools,
            max_turns=self._max_turns_provider(),
            system_prompt=SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=await self._build_append_prompt(project_name, locale=locale),
                # The SDK records the first-turn system prompt and reuses it across requests and resume
                # until compaction or a new session. Within that boundary, changed memory/locale append
                # text does not rebuild the cached prompt. File-based instruction loading is separate.
                snapshot=True,
            ),
            include_partial_messages=True,
            max_buffer_size=CLI_STDOUT_MAX_BUFFER_BYTES,
            # Only this flag makes CLI replay stdin user messages with uuid to stdout.
            # Without it no replay copy appears, so neither echo deduplication nor user-message identity mapping triggers.
            extra_args={"replay-user-messages": None},
            resume=resume_id,
            session_id=session_id,
            can_use_tool=can_use_tool,
            hooks=hooks,
            mcp_servers={"arcreel": arcreel_server},
            session_store=self.build_session_store(),
            session_store_flush=session_store_flush_mode(),
            sandbox=sandbox_typed,  # type: ignore[arg-type]  # SDK SandboxSettings TypedDict omits the filesystem substructure, accepted by CLI as pass-through JSON
            env=provider_env,
            stderr=stderr,
        )

    @staticmethod
    async def _keep_stream_open_hook(
        _input_data: dict[str, Any], _tool_use_id: str | None, _context: Any
    ) -> dict[str, bool]:
        """Required keep-alive hook for Python can_use_tool callback."""
        return {"continue_": True}

    async def _subagent_tool_hook(
        self,
        input_data: dict[str, Any],
        _tool_use_id: str | None,
        _context: Any,
    ) -> dict[str, Any]:
        """SDK wrapper for the read-only-subagent tool list (``AgentAccessPolicy.check_subagent_tool``).

        Subagent hook inputs contain ``agent_type``; main conversations omit it and are not constrained by this list.
        """
        deny_reason = self._access_policy_provider().check_subagent_tool(
            input_data.get("agent_type"), str(input_data.get("tool_name") or "")
        )
        if deny_reason is None:
            return {"continue_": True}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            },
        }

    async def _bash_env_scrub_hook(
        self,
        input_data: dict[str, Any],
        _tool_use_id: str | None,
        _context: Any,
    ) -> dict[str, Any]:
        """Bash secret-stripping hook (SDK wrapper): see
        ``AgentAccessPolicy.wrap_bash_command_for_env_scrub`` for transformation semantics and Windows fallback bypass constraints.

        Return only ``updatedInput``, not ``permissionDecision``: PreToolUse hooks are
        the first permission-chain step, and ``allow`` short-circuits every later step, including ``_can_use_tool``.
        With sandboxing, Bash is in allowed_tools and allow rules admit wrapped commands;
        leave permission decisions to later chain steps. Without wrapping (Windows fallback / empty command),
        continue directly so ``_can_use_tool`` whitelist-matches the original command.
        """
        tool_input = input_data.get("tool_input") or {}
        wrapped = self._access_policy_provider().wrap_bash_command_for_env_scrub(tool_input.get("command"))
        if wrapped is None:
            return {"continue_": True}
        updated_input = {**tool_input, "command": wrapped}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated_input,
            },
        }

    def _build_file_access_hook(
        self,
        project_cwd: Path,
    ) -> Callable[..., Any]:
        """Build a PreToolUse hook callback that enforces file access control.

        PreToolUse hooks are step 1 in the SDK permission chain and fire for
        **every** tool call, including Read/Glob/Grep which would otherwise
        be auto-approved by allow rules at step 4.
        """

        async def _file_access_hook(
            input_data: dict[str, Any],
            _tool_use_id: str | None,
            _context: Any,
        ) -> dict[str, Any]:
            policy = self._access_policy_provider()
            tool_name = input_data.get("tool_name", "")
            path_tools = policy.PATH_TOOLS
            if tool_name not in path_tools:
                return {"continue_": True}

            tool_input = input_data.get("tool_input", {})
            path_key = path_tools[tool_name]
            file_path = tool_input.get(path_key)

            if file_path:
                allowed, deny_reason = await asyncio.to_thread(
                    policy.check_path_access,
                    file_path,
                    tool_name,
                    project_cwd,
                    # Retrieve live like policy: user_id determines the memory directory and varies with session ownership.
                    user_id=self._user_id_provider(),
                )
                if not allowed:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": deny_reason,
                        },
                    }

            return {"continue_": True}

        return _file_access_hook

    def _build_json_validation_hook(
        self,
        project_cwd: Path,
        json_backups: dict[str, tuple[Path, str]] | None = None,
    ) -> Callable[..., Any]:
        """Build a PreToolUse hook that blocks Write/Edit when the result would
        produce invalid JSON.

        For Edit: reads the current file, simulates the string replacement, and
        validates the result with ``json.loads()``.
        For Write: validates the ``content`` parameter directly.

        When *json_backups* is provided, the hook saves the current file
        content before the edit so the PostToolUse hook can restore it if
        the actual result turns out to be invalid.

        Returns ``permissionDecision: "deny"`` to block the operation before it
        executes, giving the agent a chance to fix its input and retry.
        """

        async def _json_validation_hook(
            input_data: dict[str, Any],
            _tool_use_id: str | None,
            _context: Any,
        ) -> dict[str, Any]:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            file_path = tool_input.get("file_path", "")
            if not file_path or not file_path.endswith(".json"):
                return {}

            # --- Reject curly/smart quotes that would corrupt JSON ---
            _CURLY_QUOTES = "“”„‟"  # ""„‟

            def _has_curly_quotes(text: str) -> bool:
                """Return True if *text* contains Unicode curly/smart quotes."""
                return any(ch in _CURLY_QUOTES for ch in text)

            # --- Simulate the result without touching the file ---
            simulated: str | None = None

            if tool_name == "Write":
                simulated = tool_input.get("content")
                logger.info(
                    "JSON 校验 hook: tool=Write file=%s content_len=%s",
                    file_path,
                    len(simulated) if simulated else 0,
                )
            elif tool_name == "Edit":
                old_string = tool_input.get("old_string", "")
                new_string = tool_input.get("new_string", "")
                if not old_string:
                    logger.info(
                        "JSON 校验 hook: tool=Edit file=%s skip=old_string为空",
                        file_path,
                    )
                    return {}

                # Detect curly quotes early — Claude Code may normalise
                # old_string internally (allowing the edit to succeed) while
                # the hook's exact-match ``old_string not in current`` check
                # below would skip validation, letting curly quotes slip into
                # the file and corrupt JSON.
                if _has_curly_quotes(new_string):
                    curly_found = [f"U+{ord(ch):04X}" for ch in new_string if ch in _CURLY_QUOTES]
                    logger.warning(
                        "PreToolUse JSON 校验拦截(弯引号): file=%s curly=%s",
                        file_path,
                        curly_found[:5],
                    )
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                "操作被阻止：new_string 包含弯引号"
                                "（“ 或 ”），"
                                "这会破坏 JSON 格式。"
                                "请将所有弯引号替换为标准 ASCII "
                                "双引号 (U+0022) 后重试。"
                            ),
                        },
                    }

                try:
                    resolved, current = await asyncio.to_thread(_resolve_and_read_text, project_cwd, file_path)
                except OSError as read_err:
                    logger.info(
                        "JSON 校验 hook: tool=Edit file=%s skip=读取失败 error=%s",
                        file_path,
                        read_err,
                    )
                    return {}

                # Save backup for PostToolUse restore on corruption
                if json_backups is not None and _tool_use_id:
                    json_backups[_tool_use_id] = (resolved, current)

                if old_string not in current:
                    # Edit tool will fail on its own; no need to intervene.
                    logger.info(
                        "JSON 校验 hook: tool=Edit file=%s skip=old_string未匹配 old_len=%d new_len=%d file_len=%d",
                        file_path,
                        len(old_string),
                        len(new_string),
                        len(current),
                    )
                    return {}

                replace_all = tool_input.get("replace_all", False)
                if replace_all:
                    simulated = current.replace(old_string, new_string)
                else:
                    simulated = current.replace(old_string, new_string, 1)

                logger.info(
                    "JSON 校验 hook: tool=Edit file=%s matched=True "
                    "old_len=%d new_len=%d simulated_len=%d replace_all=%s",
                    file_path,
                    len(old_string),
                    len(new_string),
                    len(simulated),
                    replace_all,
                )

            if simulated is None:
                return {}

            try:
                json.loads(simulated)
                logger.info(
                    "JSON 校验 hook: tool=%s file=%s result=valid",
                    tool_name,
                    file_path,
                )
                return {}
            except json.JSONDecodeError as exc:
                logger.warning(
                    "PreToolUse JSON 校验拦截: file=%s tool=%s error=%s",
                    file_path,
                    tool_name,
                    exc,
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"操作被阻止：此次 {tool_name} 会导致 {file_path} "
                            f"变成无效 JSON。错误：{exc}。"
                            "请检查你的输入内容中是否包含未转义的双引号或其他"
                            "JSON 语法问题，修正后重试。"
                        ),
                    },
                }

        return _json_validation_hook

    def _build_json_post_validation_hook(
        self,
        project_cwd: Path,
        json_backups: dict[str, tuple[Path, str]],
    ) -> Callable[..., Any]:
        """Build a PostToolUse hook that validates JSON files after Write/Edit.

        This is a safety net for cases where the PreToolUse simulation fails
        to catch invalid edits (e.g. due to old_string mismatch or escaping
        differences between the hook simulation and the actual Edit tool).

        If the file is invalid JSON after the edit, the hook:
        1. Restores the file from the backup saved by the PreToolUse hook
        2. Returns ``additionalContext`` telling the agent what went wrong
        """

        async def _json_post_validation_hook(
            input_data: dict[str, Any],
            tool_use_id: str | None,
            _context: Any,
        ) -> dict[str, Any]:
            # Top-level guard: unhandled exceptions in hooks interrupt the
            # agent (per SDK docs), so we catch everything and log.
            try:
                return await _json_post_validation_impl(
                    input_data,
                    tool_use_id,
                )
            except Exception:
                logger.exception("PostToolUse JSON 校验 hook 异常")
                return {}

        async def _json_post_validation_impl(
            input_data: dict[str, Any],
            tool_use_id: str | None,
        ) -> dict[str, Any]:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            file_path = tool_input.get("file_path", "")
            if not file_path or not file_path.endswith(".json"):
                return {}

            # Pop the backup regardless of outcome to avoid memory leaks
            backup = json_backups.pop(tool_use_id, None) if tool_use_id else None

            try:
                _, actual = await asyncio.to_thread(_resolve_and_read_text, project_cwd, file_path)
            except OSError:
                return {}

            try:
                json.loads(actual)
                logger.info(
                    "PostToolUse JSON 校验: tool=%s file=%s result=valid",
                    tool_name,
                    file_path,
                )
                return {}
            except json.JSONDecodeError as exc:
                # File is corrupt — restore from backup if available
                restored = False
                if backup:
                    backup_path, backup_content = backup
                    try:
                        await run_sync_transaction(backup_path.write_text, backup_content, encoding="utf-8")
                        restored = True
                        logger.warning(
                            "PostToolUse JSON 校验拦截并恢复: file=%s tool=%s error=%s backup_restored=True",
                            file_path,
                            tool_name,
                            exc,
                        )
                    except OSError as write_err:
                        logger.error(
                            "PostToolUse JSON 备份恢复失败: file=%s error=%s",
                            file_path,
                            write_err,
                        )
                else:
                    logger.warning(
                        "PostToolUse JSON 校验拦截(无备份): file=%s tool=%s error=%s",
                        file_path,
                        tool_name,
                        exc,
                    )

                if restored:
                    ctx = (
                        f"⚠ JSON corruption detected and rolled back: {tool_name} made "
                        f"{file_path} invalid JSON ({exc}). "
                        "The file has been restored to its pre-edit state. Correct the edit and retry."
                    )
                else:
                    ctx = (
                        f"⚠ JSON corruption detected but could not be restored: {tool_name} made "
                        f"{file_path} invalid JSON ({exc}). "
                        "The file is still corrupt (no backup available or the restore write failed). "
                        "Read the file to confirm its contents first, then manually repair it to valid JSON."
                    )

                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": ctx,
                    },
                }

        return _json_post_validation_hook
