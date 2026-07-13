"""SDK options 装配器：持依赖、允许 I/O，异步 build 产出 ClaudeAgentOptions。

从 SessionManager 析出会话冷启动/重连时"现场收集"的全部装配职责：DB 凭证注入
（Anthropic 真值 + 其他供应商空值覆盖）、prompt 变体与项目上下文装配、各 PreToolUse/
PostToolUse hook 工厂（含 JSON 校验 hook）、以及 AgentAccessPolicy 的 settings 编译与
裁决 adapter 接线。与 AgentAccessPolicy 的 I/O 分工遵循同一判据：规则静态则零 I/O 归
policy，装配天职是开会话时现场读 DB / 扫盘则允许 I/O 归本类。

访问规则本身不在此类——policy 通过 ``access_policy_provider`` 每次 build 时现取，
``configure_sandbox_runtime`` 整体换新 policy 后对后续所有会话立即生效。
"""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from lib.agent_session_store import (
    is_known_session_store_mode,
    session_store_flush_mode,
    session_store_mode,
)
from lib.agent_session_store.store import DbSessionStore
from lib.db.base import DEFAULT_USER_ID
from lib.db.engine import async_session_factory as default_async_session_factory
from lib.i18n import DEFAULT_LOCALE, LOCALE_LANGUAGE_MAP
from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.sdk_tools import build_arcreel_mcp_server

logger = logging.getLogger(__name__)

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import HookMatcher, SystemPromptPreset

SDK_AVAILABLE = True


async def load_provider_env_overrides() -> dict[str, str]:
    """构造 options.env 注入字典。

    - ANTHROPIC_* 从 DB active credential 取真值
    - 其他 provider env 全部空值覆盖（防御性兜底）

    环境变量名单以 ``env_keys`` 为单一真相源；SDK 子进程只认 env 认证，父进程环境
    又是外部输入，故真值注入与空值围堵是常驻机制而非技术债（见 ADR）。
    """
    from lib.config.env_keys import OTHER_PROVIDER_ENV_KEYS
    from lib.config.service import build_anthropic_env_dict
    from lib.db import async_session_factory

    async with async_session_factory() as session:
        anthropic_env = await build_anthropic_env_dict(session)

    result = dict(anthropic_env)
    for key in OTHER_PROVIDER_ENV_KEYS:
        result[key] = ""
    return result


_PERSONA_PROMPT = """\
## 身份

你是 ArcReel 智能体，一个专业的 AI 视频内容创作助手。你的职责是将小说转化为可发布的短视频内容。

## 行为准则

- 主动引导用户完成视频创作工作流，而不仅仅被动回答问题
- 遇到不确定的创作决策时，向用户提出选项并给出建议，而不是自行决定
- 涉及多步骤任务时，使用 TodoWrite 跟踪进度并向用户汇报
- Write/Edit 不要写入代码文件（扩展名 .py/.js/.ts/.tsx/.sh/.yaml/.yml/.toml）；数据文件（.json/.md/.txt/.html/.csv 等）可以正常写入。代码逻辑应通过现有 skill 脚本完成
- 你是用户的视频制作搭档，专业、友善、高效"""


class OptionsAssembler:
    """把开会话时现场收集的依赖装配成 ClaudeAgentOptions。

    构造参数分两类：静态依赖（``data_dir`` / ``projects_root`` / ``allowed_tools`` /
    ``setting_sources``）在实例化时锁定；随运行时变化的依赖用 provider 回调每次 build
    时现取——``access_policy_provider``（``configure_sandbox_runtime`` 会整体换新）与
    ``max_turns_provider``（``refresh_config`` 会改写）。``resolve_project_cwd`` 由
    SessionManager 注入（项目名校验/作用域是会话管理侧职责）。

    ``session_factory_provider`` / ``user_id_provider`` 同样用回调而非构造期快照：
    store 在首次 ``build_session_store`` 时按当时取值建好并缓存，与析出前
    ``_build_session_store`` 惰性读 SessionManager 属性的时点一致——避免用量记录
    （实时读 ``_user_id``）与 transcript store 落到不同的 per-user 命名空间。
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        projects_root: Path,
        allowed_tools: Sequence[str],
        setting_sources: Sequence[str],
        access_policy_provider: Callable[[], AgentAccessPolicy],
        max_turns_provider: Callable[[], int | None],
        resolve_project_cwd: Callable[[str], Path],
        provider_env_loader: Callable[[], Awaitable[dict[str, str]]] | None = None,
        session_factory_provider: Callable[[], Any] | None = None,
        user_id_provider: Callable[[], str] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.projects_root = Path(projects_root)
        self._allowed_tools = list(allowed_tools)
        self._setting_sources = list(setting_sources)
        self._access_policy_provider = access_policy_provider
        self._max_turns_provider = max_turns_provider
        self._resolve_project_cwd = resolve_project_cwd
        self._provider_env_loader = provider_env_loader
        self._session_factory_provider = session_factory_provider or (lambda: None)
        self._user_id_provider = user_id_provider or (lambda: DEFAULT_USER_ID)
        # session store 单例缓存：每个 assembler 一份，避免每次 build 都新建 store。
        self._cached_session_store: DbSessionStore | None = None
        self._session_store_resolved = False

    async def build_provider_env_overrides(self) -> dict[str, str]:
        """DB 凭证注入入口。默认走模块级 ``load_provider_env_overrides``（现取 module
        global 以便测试 patch）；构造时注入 ``provider_env_loader`` 则改用注入源。"""
        loader = self._provider_env_loader or load_provider_env_overrides
        return await loader()

    def _build_append_prompt(self, project_name: str, locale: str = DEFAULT_LOCALE) -> str:
        """Build the append portion for SystemPromptPreset.

        Combines the ArcReel persona, the locale language regulation, and the
        session-invariant project context (identity, cwd, operating rules).
        Mutable project metadata is not included here — it lives in project.json
        and is read on demand. The project's CLAUDE.md (mode variant projected
        into the cwd) is auto-loaded by the SDK via setting_sources=["project"].
        """
        parts = [_PERSONA_PROMPT]

        lang = LOCALE_LANGUAGE_MAP.get(locale, "中文")
        parts.append(
            f"\n## 语言规范\n\n"
            f"- **回答用户必须使用{lang}**：所有回复、思考过程、任务清单及计划文件，均须使用{lang}\n"
            f"- **视频内容语言**：所有生成的视频对话、旁白、字幕均使用{lang}\n"
            f"- **文档使用{lang}**：所有的 Markdown 文件均使用{lang}编写\n"
            f"- **Prompt 使用{lang}**：图片生成/视频生成使用的 prompt 应使用{lang}编写"
        )

        project_context = self._build_project_context(project_name)
        if project_context:
            parts.append(project_context)

        return "\n".join(parts)

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
            "## 当前项目上下文",
            "",
            f"- 项目标识：{project_name}",
            f"- 项目目录（即当前工作目录 cwd）：{project_cwd.as_posix()}",
            "- 项目元数据（标题、风格、概述等）存于 project.json，需要时读取。",
            "- Bash 命令必须写在单行，禁止使用 `\\` 换行，JSON 参数使用紧凑格式。",
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
    ) -> Any:
        """Build ClaudeAgentOptions for a session.

        ``stderr`` 在 SDK 子进程退出非 0 时是唯一拿到真实错误的途径
        （``ProcessError.stderr`` 在 SDK 内部被写死为占位符）；上层应在
        会话启动失败时把回调累积的行包装到 ``AgentStartupError`` 透传。
        """
        if not SDK_AVAILABLE or ClaudeAgentOptions is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        policy = self._access_policy_provider()

        project_cwd = self._resolve_project_cwd(project_name)

        # Build PreToolUse hooks — file access control MUST use hooks because
        # Read/Glob/Grep are matched by allow rules (step 4 in the SDK
        # permission chain) before reaching can_use_tool (step 5).  Hooks
        # (step 1) fire for ALL tool calls and can override allow rules.
        hooks = None
        if HookMatcher is not None:
            hook_callbacks: list[Any] = [
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
                        hooks=[self._bash_env_scrub_hook],  # type: ignore[list-item]
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
        sandbox_typed = policy.build_sandbox_settings(project_cwd)

        # Windows 回退：sandbox 关闭时 Bash 系列被剥离出 allowed_tools，
        # 让 _can_use_tool 接管 prefix 白名单匹配。
        allowed_tools = policy.filter_allowed_tools(self._allowed_tools)
        # 内置 ArcReel SDK MCP server — handler 跑在主进程，绕过 sandbox。
        # 通配符让后续新增 tool 不必同步改 allowed_tools。
        allowed_tools.append("mcp__arcreel__*")

        arcreel_server = build_arcreel_mcp_server(
            project_name=project_name,
            projects_root=self.projects_root,
        )

        return ClaudeAgentOptions(
            cwd=str(project_cwd),
            setting_sources=self._setting_sources,  # type: ignore[arg-type]
            allowed_tools=allowed_tools,
            max_turns=self._max_turns_provider(),
            system_prompt=SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=self._build_append_prompt(project_name, locale=locale),
            ),
            include_partial_messages=True,
            resume=resume_id,
            can_use_tool=can_use_tool,
            hooks=hooks,  # type: ignore[arg-type]
            mcp_servers={"arcreel": arcreel_server},
            session_store=self.build_session_store(),  # type: ignore[arg-type]
            session_store_flush=session_store_flush_mode(),
            sandbox=sandbox_typed,  # type: ignore[arg-type]
            env=provider_env,
            stderr=stderr,
        )

    @staticmethod
    async def _keep_stream_open_hook(
        _input_data: dict[str, Any], _tool_use_id: str | None, _context: Any
    ) -> dict[str, bool]:
        """Required keep-alive hook for Python can_use_tool callback."""
        return {"continue_": True}

    async def _bash_env_scrub_hook(
        self,
        input_data: dict[str, Any],
        _tool_use_id: str | None,
        _context: Any,
    ) -> dict[str, Any]:
        """Bash 密钥剥离 hook（SDK 封皮）：变换语义与 Windows 回退跳包装的约束见
        ``AgentAccessPolicy.wrap_bash_command_for_env_scrub``。

        只返回 ``updatedInput``、不返回 ``permissionDecision``：PreToolUse hook
        是权限链第 1 步，``allow`` 会短路后续所有步骤（包括 ``_can_use_tool``）。
        sandbox 启用时 Bash 在 allowed_tools 内，包装后的命令由 allow 规则放行；
        权限决策始终留给链上后续步骤。不包装时（Windows 回退 / 空命令）直接
        continue，原始命令落到 ``_can_use_tool`` 做白名单匹配。
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
                allowed, deny_reason = policy.check_path_access(
                    file_path,
                    tool_name,
                    project_cwd,
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

                p = Path(file_path)
                resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()
                try:
                    current = resolved.read_text(encoding="utf-8")
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

            p = Path(file_path)
            resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()

            try:
                actual = resolved.read_text(encoding="utf-8")
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
                        backup_path.write_text(backup_content, encoding="utf-8")
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
                        f"⚠ JSON 损坏已检测并回滚：{tool_name} 导致 "
                        f"{file_path} 变成无效 JSON（{exc}）。"
                        "文件已恢复到编辑前状态，请修正后重试。"
                    )
                else:
                    ctx = (
                        f"⚠ JSON 损坏已检测但无法恢复：{tool_name} 导致 "
                        f"{file_path} 变成无效 JSON（{exc}）。"
                        "文件当前仍为损坏状态（无可用备份或恢复写入失败），"
                        "请先读取文件确认内容，再手动修正为合法 JSON。"
                    )

                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": ctx,
                    },
                }

        return _json_post_validation_hook
