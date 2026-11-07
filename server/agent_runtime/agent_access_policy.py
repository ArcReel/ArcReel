r"""Single source of truth for Agent access rules, shared by kernel sandbox settings compilation and application-level hook decisions.

Zero I/O, constructed purely from process-level root paths (including fake roots); no SDK type imports.
SDK wrappers (hook signatures, permission result types, permission-chain ordering) remain in the thin
session-management adapter (``server/agent_runtime/session_manager.py``)."""

import fnmatch
import functools
import logging
import os
import re
import shlex
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from lib.agent.agent_memory_paths import ARCREEL_DIRNAME, is_valid_memory_user_id
from lib.episode.episode_paths import (
    AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES,
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
)
from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_trash import TRASH_DIRNAME
from lib.script.draft_quarantine import OPEN_DRAFT_TOOL_NAME, PROMOTE_TOOL_NAME

logger = logging.getLogger(__name__)

#: Media directories the Agent cannot write directly: generated artifacts, asset images, grids, music, and finished videos; only main-process tools write here.
AGENT_PROTECTED_MEDIA_DIRS: tuple[str, ...] = (
    "characters",
    "scenes",
    "props",
    "products",
    "grids",
    "storyboards",
    "end_frames",
    "videos",
    "reference_videos",
    "audio",
    "thumbnails",
    "bgm",
    "renders",
)
AGENT_PROTECTED_MEDIA_FILES: tuple[str, ...] = tuple(f"style_reference.{ext}" for ext in ("png", "jpg", "jpeg", "webp"))


def _default_claude_projects_dir() -> Path:
    r"""Base directory where the SDK stores per-project session data."""
    return Path.home() / ".claude" / "projects"


@dataclass(frozen=True)
class ProtectedWriteRule:
    r"""Complete declaration of one protected write-path category: hook predicate, denial message, and sandbox denyWrite projection in one row.

    ``AgentAccessPolicy.PROTECTED_WRITE_RULES`` is the single source of truth for these rules: both the
    application hook (``_check_write_access``, covering built-in Write/Edit) and kernel sandbox denyWrite
    (covering Bash subprocesses; see ``_build_protected_write_abs_paths``) project from this table.
    Adding a protected-path category requires only one row and takes effect in both layers. With scattered
    declarations, omitting either side leaves a single-layer bypass (violating ADR 0026's two-layer premise).

    Coverage may deliberately differ between layers (e.g. the hook denies only formal script_plan files under
    ``drafts/``, while the sandbox denies the entire directory). Predicates and projections are therefore
    independent columns, not derived from one another."""

    #: Rule name: readable row identifier with no runtime consumer (neither predicates nor projections look up by name).
    name: str
    #: Hook predicate: ``(target, bases) -> match?``. The caller invokes it once for the logical target and
    #: once for the resolved target; bases contains raw and resolved project_cwd forms.
    matches: Callable[[Path, list[Path]], bool]
    #: Hook denial message (must identify the legitimate alternative channel).
    deny_message: str
    #: Sandbox denyWrite projection: subpaths relative to project_cwd base (directories deny their entire subtree).
    sandbox_subpaths: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class AgentAccessPolicy:
    r"""Single source of truth for what the Agent can access, projecting the same rules in two ways:

    - Kernel sandbox: compile SandboxSettings (denyRead / denyWrite / network domain lists).
    - Application hooks: provide per-call read/write/command decisions and pure transformations (project_cwd passed per call).

    Windows fallback (kernel sandbox unavailable, ``sandbox_enabled=False``) stays within this class:
    Bash uses a prefix allowlist and credential stripping skips wrapping. These rules are mutually coupled
    (wrapped commands start with ``env -u`` and can never match the allowlist), so they must live together.

    Construction performs zero I/O: all fields are process-level facts (roots already resolved by the caller
    plus platform booleans), and sensitive-path tables are derived purely from fields. Credential injection
    (reading the DB) is outside this class: it is assembly-time I/O and would break pure, testable construction."""

    # Resolved source repository root: ``.env`` / ``.env.*`` are relative to this root (dotenv loads
    # from the repository root); also the fence base for allowing repository reference material.
    project_root: Path
    # Resolved data root (app_data_dir() in production): deny reads by default, allowing only the current project and current user memory;
    # the data-root layout determines its entry locations.
    data_root: Path
    # Resolved Agent profile root (controlled by caller-side environment resolution):
    # location of ``.claude/settings.json``.
    agent_profile_root: Path
    # False means the platform lacks kernel sandbox support (currently Windows only); Bash falls back to the code allowlist.
    sandbox_enabled: bool = True
    # SandboxSettings.enableWeakerNestedSandbox flag.
    in_docker: bool = False
    # Base directory for SDK per-project session data (base for the tool-results read exception).
    claude_projects_dir: Path = field(default_factory=_default_claude_projects_dir)

    # Bash tools enter allowed_tools when sandboxing is enabled (with autoAllowBashIfSandboxed
    # providing allowance); remove them when disabled (Windows fallback), sending commands to can_use_tool
    # and its prefix allowlist (see ``filter_allowed_tools`` / ``is_bash_command_whitelisted``).
    BASH_TOOLS: ClassVar[tuple[str, ...]] = ("Bash", "BashOutput", "KillBash")

    # Python skill entry prefix. Convention: ``python .claude/skills/<skill>/scripts/
    # <script>.py <args>``; the script must be under a skill's scripts/ directory (_SKILL_SCRIPT_RE
    # validates this), preventing arbitrary existing/future skills files from becoming executable entry points. Windows fallback
    # has no sandbox denyExec protection; prefix-only allowance would expose the entire skills tree for execution.
    _PYTHON_SKILLS_PREFIX: ClassVar[str] = "python .claude/skills/"
    _SKILL_SCRIPT_RE: ClassVar["re.Pattern[str]"] = re.compile(r"^\.claude/skills/[^/]+/scripts/[^/]+\.py$")

    # Bash command allowlist for Windows fallback (sandbox_enabled=False), equivalent to the pre-sandbox
    # settings.json permissions.allow section. Also the single source of truth for can_use_tool denial hints
    # (format_bash_whitelist_deny_message derives from this).
    WINDOWS_BASH_PREFIX_WHITELIST: ClassVar[tuple[str, ...]] = (_PYTHON_SKILLS_PREFIX,)

    # Shell metacharacter denylist for Windows fallback: ``;`` ``&`` ``|`` ``<`` ``>``
    # `` ` `` ``$`` and newlines can append arbitrary commands after an allowed prefix (chaining/pipes/redirection/
    # command substitution). Do not parse quoting: reject the whole string even inside quotes, preferring false denials (fail closed);
    # the denial message guides the Agent to rewrite the command.
    _BASH_METACHARS_RE: ClassVar["re.Pattern[str]"] = re.compile(r"[;&|<>`$\r\n]")

    # ``..`` path segments: ``python .claude/skills/../../evil.py`` has no metacharacters and matches
    # the ``python .claude/skills/`` prefix, but ``..`` escapes skills to execute arbitrary scripts.
    # Windows fallback has no sandbox denyWrite/denyExec protection, so reject the entire string. Match only ``..`` segments bounded by
    # separators/whitespace/string boundaries; do not reject filenames such as ``my..name``.
    _BASH_PATH_TRAVERSAL_RE: ClassVar["re.Pattern[str]"] = re.compile(r"(?:^|[\s/\\])\.\.(?:[\s/\\]|$)")

    # Additional environment-name patterns for Bash unset: catch credential variables possibly injected into SDK subprocesses
    # or inherited from the host (e.g. GEMINI_CLI_IDE_AUTH_TOKEN) when fixed lists miss them.
    _SECRET_ENV_NAME_PATTERNS: ClassVar[tuple[str, ...]] = (
        "API_KEY",
        "AUTH_TOKEN",
        "ACCESS_KEY",
        "ACCESS_TOKEN",
        "SECRET_KEY",
        "CREDENTIAL",
        "CLIENT_SECRET",
    )

    # File access control uses the PreToolUse hook (permission-chain step 1, applying to every tool call);
    # values are the keys carrying paths in each tool's input.
    PATH_TOOLS: ClassVar[dict[str, str]] = {
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Glob": "path",
        "Grep": "path",
    }
    _WRITE_TOOLS: ClassVar[set[str]] = {"Write", "Edit"}
    # Tools recursively reading from search root ``path``: decisions must consider its subtree as well as ``path`` itself.
    _SEARCH_TOOLS: ClassVar[set[str]] = {"Glob", "Grep"}
    #: Protected write-path rule table, the single source for hook denials and sandbox denyWrite. Predicates reference this class's
    #: classmethods, so assignment follows the class body (see module end); add protected categories only by adding a row here.
    PROTECTED_WRITE_RULES: ClassVar[tuple[ProtectedWriteRule, ...]]
    _CODE_EXTENSIONS_FORBIDDEN: ClassVar[set[str]] = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".sh",
        ".yaml",
        ".yml",
        ".toml",
    }

    @functools.cached_property
    def _layout(self) -> DataRootLayout:
        return DataRootLayout(self.data_root)

    @functools.cached_property
    def _sensitive_table(self) -> tuple[tuple[Path, ...], tuple[tuple[Path, str], ...]]:
        r"""Sensitive paths outside the data root, ``(files, globs)``: exact paths in ``files`` and ``(parent, pattern)`` pairs in ``globs``.

        Derive purely from constructor fields by logical category, reflecting the actual profile location after
        environment overrides (the caller resolves the environment; this class consumes only resolved roots):

        - ``.env`` / ``.env.*`` are always relative to the source repository root.
        - ``agent_runtime_profile/.claude/settings.json`` is under ``agent_profile_root``.

        Do not register data-root entries (databases, credentials, logs, etc.) individually: read decisions deny
        the data root by default, and the sandbox denies reads of whole top-level entries (see
        ``_build_data_root_deny_read_abs_paths``)."""
        repo = self.project_root
        files: tuple[Path, ...] = (
            repo / ".env",
            self.agent_profile_root / ".claude" / "settings.json",
        )
        globs: tuple[tuple[Path, str], ...] = ((repo, ".env.*"),)
        return files, globs

    def is_sensitive_path(self, resolved: Path) -> bool:
        r"""Check whether a resolved path matches the list of sensitive files outside the data root.

        Covers ``.env`` / ``.env.*`` / ``agent_runtime_profile/.claude/settings.json``."""
        files, globs = self._sensitive_table
        for sensitive_file in files:
            if resolved == sensitive_file:
                return True
        for parent, pattern in globs:
            try:
                rel = resolved.relative_to(parent)
            except ValueError:
                continue
            rel_posix = rel.as_posix()
            # Match only direct children of ``parent``, preventing the ``.env.local`` pattern from matching
            # ``project_root/sub/.env.local`` (a different file).
            if "/" in rel_posix:
                continue
            if fnmatch.fnmatchcase(rel_posix, pattern):
                return True
        return False

    def check_path_access(
        self,
        file_path: str,
        tool_name: str,
        project_cwd: Path,
        *,
        user_id: str,
    ) -> tuple[bool, str | None]:
        r"""Check whether the given tool may access file_path, returning ``(allowed, deny_reason)``.

        Three-step dispatch:
        - Rule 0: always deny sensitive files outside the data root (.env / settings.json, etc.).
        - Write tools (Write/Edit) → ``_check_write_access``.
        - Read tools (Read/Glob/Grep) → ``_check_read_access``.

        Pass ``user_id`` per call alongside ``project_cwd`` (the user memory directory derives from it).
        The policy itself remains a process-level, zero-I/O singleton that does not hold the current user."""
        try:
            p = Path(file_path)
            logical = p if p.is_absolute() else project_cwd / p
            # normpath collapses `.`/`..` without expanding symlinks. Keep both logical and resolved real targets
            # to detect symlink-origin bypasses (logical protected path resolves outside)
            # and symlink-destination bypasses (logical outside path resolves inside the protected region).
            logical_norm = Path(os.path.normpath(str(logical)))
            resolved = logical.resolve()
        except (ValueError, OSError):
            return False, "访问被拒绝：无效的文件路径"

        # Rule 0: unconditionally deny sensitive files.
        if self.is_sensitive_path(resolved):
            return False, f"访问被拒绝：敏感文件不可访问 ({resolved})"

        if tool_name in self._WRITE_TOOLS:
            return self._check_write_access(resolved, project_cwd, logical_norm=logical_norm, user_id=user_id)
        return self._check_read_access(
            resolved, project_cwd, user_id=user_id, is_search=tool_name in self._SEARCH_TOOLS
        )

    def build_sandbox_settings(self, project_cwd: Path) -> dict[str, Any]:
        r"""Build a SandboxSettings dict (the SDK's Python TypedDict does not declare the filesystem
        substructure, but the CLI runtime accepts the forwarded JSON), projecting the same rules into the kernel sandbox.

        - ``sandbox_enabled=False`` (Windows fallback): return only ``{"enabled": False}``;
          Bash tools instead use the ``is_bash_command_whitelisted`` code allowlist.
        - ``filesystem.denyRead``: kernel-level file-read denial (macOS Seatbelt / Linux
          bwrap profile), applying to every subprocess inside the sandbox.
        - ``filesystem.denyWrite``: kernel-level file-write denial covering each rule's
          ``sandbox_subpaths`` in ``PROTECTED_WRITE_RULES`` (``scripts/``, ``project.json``, ``edit_timelines/``, ``source/``,
          ``drafts/``, ``.arcreel/trash/``, media directories, and ``versions/``). These files may only be written through
          in-process MCP tools (``patch_episode_script`` / ``patch_project`` / ``upload_source`` /
          retrieval and promotion of reference splits, etc., running in the main process outside the sandbox).
          This closes Bash bypasses (``echo>`` / ``sed`` / ``python -c``) at OS level for all sandboxed subprocesses.
          No legitimate Bash writes to these paths remain inside the sandbox, so legitimate writes are unaffected.
        - Do not set ``filesystem.allowWrite``: Bash cannot read or write the user memory directory
          (``<数据根>/users/<user_id>/memory/``). Built-in Read/Write/Edit/Glob/Grep run in the main process,
          outside the kernel sandbox; hooks allow the memory directory without a kernel-layer exception.
          Adding ``allowWrite`` would leak access differently on each platform: bwrap bind-mounts the writable
          directory back, overriding ``users/`` ``denyRead`` so Bash can read and write; Seatbelt denies reads but
          allows writes, letting Bash write blindly. Project memory is in cwd, so Bash reads/writes it normally.
        - ``filesystem.denyRead`` also includes every top-level data-root entry except ``projects/``
          (see ``_build_data_root_deny_read_abs_paths``): the hook's default data-root denial covers only
          built-in Read/Glob/Grep; Bash does not pass through that hook (ADR 0026).
        - ``allowUnsandboxedCommands=False``: prohibit requests to retry unsandboxed after sandbox failure,
          which is unacceptable for red-line cases.
        - ``network``: ``allowedDomains`` is a preapproval list, not a restriction list. Omitting it means
          zero preapprovals; requests to allow new domains are denied in unattended sessions, blocking all outbound traffic.
          ``allowLocalBinding`` separately controls loopback: neither ``*`` nor ``127.0.0.1`` in
          ``allowedDomains`` permits loopback. The current values allow all domains plus loopback (ADR 0069),
          making loopback services across the host reachable by sandboxed Bash."""
        if not self.sandbox_enabled:
            return {"enabled": False}
        filesystem: dict[str, Any] = {
            "denyRead": self._build_sensitive_abs_paths() + self._build_data_root_deny_read_abs_paths(),
            "denyWrite": self._build_protected_write_abs_paths(project_cwd),
        }
        return {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "network": {"allowedDomains": ["*"], "allowLocalBinding": True},
            "enableWeakerNestedSandbox": bool(self.in_docker),
            "filesystem": filesystem,
        }

    def _build_data_root_deny_read_abs_paths(self) -> list[str]:
        r"""Kernel-sandbox data-root read-deny list: every top-level entry except ``projects/``.

        The default data-root denial in ``_check_read_access`` covers only built-in Read/Glob/Grep; Bash and
        its subprocesses bypass that hook and rely only on the kernel sandbox (ADR 0026). Either layer alone
        leaves a ``cat`` bypass.

        This projection is stricter than the hook (which allows the current user's memory; here the entire
        ``users/`` tree is denied), just as ``PROTECTED_WRITE_RULES`` denies only formal script_plan files
        under drafts/ in the hook but the whole directory in the sandbox. The Agent uses Read/Write/Edit
        (outside the sandbox) for memory, so Bash need not read it. ``projects/`` is excluded; reads between
        projects by Bash are not restricted here.

        Combine layout-registered system directories with top-level entries actually present in the data root.
        The latter covers default database files, old-layout remnants (``.arcreel.db*``, ``.arcreel/``,
        ``.system_config.json``, etc.), and unregistered entries without individual registration.

        Create layout-registered system directories before compilation: for nonexistent deny paths, the CLI says
        "Skipping non-existent read deny path" and installs no deny mount, while the fence compiles only once
        at session start. On a new installation, system directories missing at session start become readable by
        that session's Bash when later created (another user's memory, first-upload credentials). If creation
        fails (read-only mount, permissions), fall back to registering paths only: the CLI skips them, while
        hooks still block built-in read tools."""
        layout = self._layout
        for system_dir in layout.system_dirs:
            try:
                system_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                logger.warning("数据根系统目录建不出来,sandbox deny 可能被 CLI 跳过: %s", system_dir)
        entries: set[Path] = set(layout.system_dirs)
        try:
            entries.update(self.data_root.iterdir())
        except OSError:
            logger.warning("数据根无法列出,sandbox deny 只含布局登记的系统目录: %s", self.data_root)
        entries.discard(layout.projects_dir)
        return sorted(str(entry) for entry in entries)

    def _user_memory_dir_or_none(self, user_id: str) -> Path | None:
        r"""Current user's memory directory; return None unless ``user_id`` is a single path segment.

        The fence must fail closed: a directory derived from an invalid ``user_id`` can escape the data root
        and allow arbitrary paths, so prefer not to allow the memory directory. Log a warning for diagnostics."""
        if not is_valid_memory_user_id(user_id):
            logger.warning("user_id 不是合法路径段,记忆目录放行被跳过: %r", user_id)
            return None
        return self._layout.user_memory_dir(user_id)

    def _is_user_memory_path(self, resolved: Path, *, user_id: str) -> bool:
        r"""Whether a resolved path is within the current user's memory directory, including the directory itself.

        Check only user memory here: project memory is ``<cwd>/.arcreel/memory/`` within cwd and already
        allowed by both read and write cwd fences. Checking again would add a ``project_cwd`` resolve
        (``_check_read_access`` is a per-tool-use hook and deliberately avoids this lstat)."""
        user_dir = self._user_memory_dir_or_none(user_id)
        return user_dir is not None and resolved.is_relative_to(user_dir)

    @classmethod
    def _build_protected_write_abs_paths(cls, project_cwd: Path) -> list[str]:
        r"""Absolute write-deny paths for Bash subprocesses, projecting every rule's ``sandbox_subpaths``
        in ``PROTECTED_WRITE_RULES`` (a directory denies its entire subtree).

        Together with built-in Write/Edit denial in ``_check_write_access``, this forms two layers (ADR 0026):
        sandbox denyWrite covers Bash subprocesses at kernel level, while the ``_check_write_access`` hook
        covers built-in Write/Edit in the permission system on all platforms. Built-in file tools run in the
        main process outside Bash and kernel sandbox coverage, and vice versa. Both layers project from one
        rule table, so adding a protected-path category in one place updates both layers.

        Enumerate raw and resolved bases with ``_enumerate_cwd_bases``, as ``_check_write_access`` does.
        If a sandbox compares path strings rather than inodes, registering only raw paths misses writes to
        resolved paths after Bash subprocesses resolve symlinks (macOS ``/var↔/private/var``, Linux symlinked project roots)."""
        paths: list[str] = []
        for base in cls._enumerate_cwd_bases(project_cwd):
            for rule in cls.PROTECTED_WRITE_RULES:
                for subpath in rule.sandbox_subpaths:
                    target_s = str(base / subpath)
                    if target_s not in paths:
                        paths.append(target_s)
        return paths

    def _build_sensitive_abs_paths(self) -> list[str]:
        r"""Build absolute sensitive-file paths outside the data root for the sandbox profile's denyRead field.

        The SDK CLI skips nonexistent deny paths ("Skipping non-existent deny path"), so enumerate currently
        existing fixed-list paths plus glob matches.

        Re-enumerate at every session start to prevent later-created sensitive files (.env / .env.local) from
        bypassing the sandbox profile. The profile takes effect once when the SDK client starts; runtime-added
        files that match the naming convention must enter denyRead immediately."""
        files, globs = self._sensitive_table
        candidates: list[Path] = list(files)
        for parent, pattern in globs:
            if parent.exists():
                candidates.extend(parent.glob(pattern))
        return [str(p) for p in candidates if p.exists()]

    @classmethod
    @functools.cache
    def _collect_env_keys_to_scrub(cls) -> tuple[str, ...]:
        r"""Collect environment variable names to strip from Bash subprocesses.

        Three sources: fixed lists (ANTHROPIC + OTHER providers), pattern matching (scan ``os.environ`` for
        names containing KEY/TOKEN/CREDENTIAL, etc.), and deduplication. The parent process's environ does
        not add or remove credential variables after startup, so results are stable; caching avoids rescanning
        for every Bash command. Tests that change the environment must call
        ``cls._collect_env_keys_to_scrub.cache_clear()``."""
        from lib.config.env_keys import ANTHROPIC_ENV_KEYS, OTHER_PROVIDER_ENV_KEYS

        keys: set[str] = set(ANTHROPIC_ENV_KEYS)
        keys.update(OTHER_PROVIDER_ENV_KEYS)
        for name in os.environ:
            upper = name.upper()
            if any(pat in upper for pat in cls._SECRET_ENV_NAME_PATTERNS):
                keys.add(name)
        return tuple(sorted(keys))

    @classmethod
    @functools.cache
    def _env_scrub_wrap_prefix(cls) -> str:
        r"""The ``env -u VAR1 -u VAR2 ... sh -c `` prefix. ``_collect_env_keys_to_scrub`` determines the matched
        list, which does not change at runtime; caching reuses the entire string."""
        unset_flags = " ".join(f"-u {key}" for key in cls._collect_env_keys_to_scrub())
        return f"env {unset_flags} sh -c "

    def wrap_bash_command_for_env_scrub(self, command: object) -> str | None:
        r"""Pure Bash credential-stripping transformation: return the wrapped command, or None for no wrapping.

        The SDK subprocess holds real ANTHROPIC_* values (required for authentication) and empty placeholder
        OTHER_PROVIDER_* values (overridden with empty strings by options.env). Bash sandbox inherits the
        parent's entire environment by default, so the Agent can see variable names with ``env | grep``.
        Use ``env -u VAR ... sh -c '<cmd>'`` to unset every matched variable name in the Bash subshell;
        ``shlex.quote`` passes the entire original command as the sh subshell's -c argument.

        Do not wrap when the sandbox is unavailable (Windows fallback): ``env -u``/``sh -c`` are POSIX
        mechanisms unavailable on native Windows. Wrapped commands also start with ``env -u``, so they
        can never match the prefix allowlist in ``is_bash_command_whitelisted``. The mutual constraint that
        wrapping breaks allowlist matching stays between these two methods. Empty/non-string commands
        are also left unwrapped."""
        if not isinstance(command, str) or not command.strip():
            return None
        if not self.sandbox_enabled:
            return None
        return f"{self._env_scrub_wrap_prefix()}{shlex.quote(command)}"

    @classmethod
    def is_bash_command_whitelisted(cls, command: str) -> bool:
        r"""Check the Bash command allowlist for Windows fallback (sandbox unavailable).

        Plain startswith prefix matching has three bypasses: metacharacter chaining (``python .claude/skills/...; evil``
        matches the prefix as a whole, while the trailing command still runs, with no sandbox denyWrite fallback
        on Windows), command-name prefix collisions (a prefix without spaces matches ``<前缀>X``), and path
        traversal (``..`` escapes the skills directory). Check in four steps:

        1. Reject shell metacharacters anywhere (``_BASH_METACHARS_RE``), blocking chaining, pipes,
           redirection, and command substitution.
        2. Reject ``..`` path segments (``_BASH_PATH_TRAVERSAL_RE``): besides the original string,
           strip quotes and check both Windows separators (``\``→``/``) and POSIX escaping (remove ``\``).
           Shells turn ``".."`` / ``.\.`` into ``..``; checking only the original allows these obfuscations
           to escape the skills directory.
        3. Match ``WINDOWS_BASH_PREFIX_WHITELIST`` at token boundaries: prefixes without spaces require
           the exact command name or a following space.
        4. Python skill entry points additionally require the first argument to be ``<skill>/scripts/<script>.py``
           (``_is_allowed_python_skill_command``), not arbitrary files in the skills directory.

        Match the allowlist against a normalized string with quotes stripped and backslashes converted to
        forward slashes. This tolerates Windows Agent paths with ``\`` separators and quoted script paths,
        avoiding rejection of legitimate commands (matching does not rewrite the executed command; allowed
        calls still forward the original input). Metacharacters and ``..`` have already been rejected in the
        original and all normalized variants; normalization only decides allowlist matches and does not relax
        the security boundary."""
        cmd = command.strip()
        if not cmd or cls._BASH_METACHARS_RE.search(cmd):
            return False
        unquoted = cmd.replace('"', "").replace("'", "")
        for variant in (cmd, unquoted.replace("\\", "/"), unquoted.replace("\\", "")):
            if cls._BASH_PATH_TRAVERSAL_RE.search(variant):
                return False
        normalized = unquoted.replace("\\", "/")
        for prefix in cls.WINDOWS_BASH_PREFIX_WHITELIST:
            if prefix == cls._PYTHON_SKILLS_PREFIX:
                if normalized.startswith(prefix) and cls._is_allowed_python_skill_command(normalized):
                    return True
            elif " " in prefix:
                if normalized.startswith(prefix):
                    return True
            elif normalized == prefix or normalized.startswith(prefix + " "):
                return True
        return False

    @classmethod
    def _is_allowed_python_skill_command(cls, normalized_cmd: str) -> bool:
        r"""Validate the script entry point in ``python .claude/skills/...``: take the first argument (script path)
        and require ``.claude/skills/<skill>/scripts/<script>.py``. Restrict to explicit scripts entry points so
        arbitrary files under skills cannot execute in Windows fallback (without sandbox protection).

        Input must be normalized by ``is_bash_command_whitelisted`` (quotes stripped, backslashes converted
        to forward slashes), so splitting on whitespace suffices to get the first argument; no shell-level tokenization is needed."""
        parts = normalized_cmd.split(maxsplit=2)
        if len(parts) < 2:
            return False
        return cls._SKILL_SCRIPT_RE.match(parts[1]) is not None

    @classmethod
    def format_bash_whitelist_deny_message(cls, command: str) -> str:
        r"""Windows fallback Bash allowlist denial message. Derive the allowed list from
        WINDOWS_BASH_PREFIX_WHITELIST to prevent drift between the constant and message."""
        allowed_lines = "\n".join(f"  - {prefix}" for prefix in cls.WINDOWS_BASH_PREFIX_WHITELIST)
        return (
            f"未授权的 Bash 命令: {command[:200]}\n"
            "当前 Bash 白名单仅允许以下前缀:\n"
            f"{allowed_lines}\n"
            "且命令不得包含 shell 元字符（; & | < > ` $ 或换行）或 .. 路径穿越——"
            "复合命令请拆成多次独立调用，脚本路径不要用 .. 逃出目录。\n"
            "python 仅允许跑 .claude/skills/<skill>/scripts/<script>.py 入口脚本。\n"
            "其他 Bash 命令在 Windows 回退模式下不可用。"
        )

    # Read-only subagent (agent-definition ``name``) → all tools it may call. Agent frontmatter
    # ``tools`` narrows CLI-visible tools; this table denies unlisted calls again in PreToolUse,
    # so even widened definitions or a CLI ignoring frontmatter cannot expose write tools. Both lists must agree.
    READ_ONLY_SUBAGENT_TOOLS: ClassVar[dict[str, frozenset[str]]] = {
        "review-footage": frozenset({"Read", "Glob", "Grep", "mcp__arcreel__inspect_video_units"}),
        "review-images": frozenset({"Read", "Glob", "Grep", "mcp__arcreel__inspect_images"}),
    }

    def check_subagent_tool(self, agent_type: object, tool_name: str) -> str | None:
        r"""Return a denial explanation for read-only subagents calling unlisted tools; return None for other calls (including the main conversation)."""
        if not isinstance(agent_type, str):
            return None
        allowed = self.READ_ONLY_SUBAGENT_TOOLS.get(agent_type)
        if allowed is None or tool_name in allowed:
            return None
        return (
            f"子智能体 {agent_type} 是只读的，不能调用 {tool_name}；"
            f"只能使用 {'、'.join(sorted(allowed))}。需要改动时把建议写进报告，交给主对话执行。"
        )

    def filter_allowed_tools(self, tools: list[str]) -> list[str]:
        r"""Filter allowed_tools by sandbox availability: when disabled (Windows fallback), remove Bash tools
        so commands reach can_use_tool and the ``is_bash_command_whitelisted`` prefix allowlist.
        This is mutually exclusive with the kernel route (allowed by autoAllowBashIfSandboxed)."""
        if self.sandbox_enabled:
            return list(tools)
        bash_tools = set(self.BASH_TOOLS)
        return [t for t in tools if t not in bash_tools]

    @functools.cached_property
    def _sdk_tmp_prefixes(self) -> tuple[str, ...]:
        r"""Temporary-root prefixes for SDK background-task output (``<tmp>/claude-*/tasks``).

        ``tempfile.gettempdir()`` and its ``.resolve()`` result are stable for the process lifetime, but
        ``_check_read_access`` is a per-tool-use hook. Recomputing would cause needless ``.resolve()``
        system calls (lstat/readlink), so cached_property computes and caches once. Covers cross-platform
        temporary roots (Linux ``/tmp``, macOS default ``/var/folders/.../T``, Windows ``%TEMP%``).

        Include both the raw ``gettempdir()`` result and its ``resolve()`` result, plus ``/tmp`` and
        ``/private/tmp``: on macOS, ``/tmp`` symlinks to ``/private/tmp`` (likewise ``/var`` to
        ``/private/var``), and listing only one form causes startswith mismatches."""
        _tempdir = Path(tempfile.gettempdir())
        return (
            str(_tempdir / "claude-"),
            str(_tempdir.resolve() / "claude-"),
            "/tmp/claude-",  # noqa: S108 -- Fixed SDK-written temporary-path prefixes, matched literally
            "/private/tmp/claude-",
        )

    @functools.cached_property
    def _claude_projects_dir_resolved(self) -> Path | None:
        r"""Resolved ``claude_projects_dir`` base directory, computed and cached once per instance.

        Users may symlink ``~/.claude`` to dotfiles/cloud-sync directories. The compared ``resolved`` path
        has already undergone ``.resolve()``, so inconsistent forms cause is_relative_to mismatches and
        wrongly deny legitimate SDK tool-results reads. Resolve the base too, as for tmp / project_root
        comparisons. Only this stable prefix needs resolving: the per-session ``encoded`` subdirectory is a
        real SDK-created directory and needs only string concatenation, not per-call resolve
        (``_check_read_access`` is a per-tool-use hook, avoiding repeated lstat/readlink).

        resolve raises on symlink loops (RuntimeError) or inaccessible parents (OSError). Permission hooks
        must fail closed: return None on resolution failure so the caller skips the tool-results exception and
        falls through to stricter denial, rather than letting an exception interrupt the tool call."""
        try:
            return self.claude_projects_dir.resolve(strict=False)
        except (OSError, RuntimeError):
            return None

    @staticmethod
    def encode_sdk_project_path(project_cwd: Path) -> str:
        """Encode a project cwd the same way the SDK does for session storage:
        replace ``/`` and ``.`` with ``-``.
        """
        return project_cwd.as_posix().replace("/", "-").replace(".", "-")

    def _check_read_access(
        self, resolved: Path, project_cwd: Path, *, user_id: str, is_search: bool
    ) -> tuple[bool, str | None]:
        r"""Cross-project isolation and host-filesystem blocking for Read/Glob/Grep.

        Allow user memory; allow cwd (including project memory); allow SDK tool-results / /tmp/claude-*/tasks
        exceptions. Deny everything else within the data root (other projects, system entries, others' memory,
        files directly under the root). Allow reference material in the repository root (lib/docs, etc.);
        deny other paths (host filesystem: ~/.ssh, /etc, etc.) by default.

        The default data-root denial must precede repository-root allowance: the data root often lies inside
        the repository root (development default ``<仓库根>/projects``, Docker ``/app/projects``), otherwise its
        entries would be allowed as reference material. Likewise, deny Glob/Grep search roots (``is_search``)
        containing the data root, since recursion would enter it.

        Determine data-root membership using ``_normalize_path_for_protected_compare``: ``resolve`` does
        not normalize case. On case-insensitive volumes (default macOS APFS and Windows NTFS),
        ``<仓库根>/PROJECTS/...`` is the same directory as the data root; raw comparison would allow it
        through the repository-root exception."""
        if self._is_user_memory_path(resolved, user_id=user_id):
            return True, None
        if resolved.is_relative_to(project_cwd):
            return True, None
        # SDK tool-results exception (resolved base: _claude_projects_dir_resolved).
        claude_projects_dir = self._claude_projects_dir_resolved
        if claude_projects_dir is not None:
            sdk_project_dir = claude_projects_dir / self.encode_sdk_project_path(project_cwd)
            if resolved.is_relative_to(sdk_project_dir) and "tool-results" in resolved.parts:
                return True, None
        # SDK background-task output exception (prefixes: _sdk_tmp_prefixes, cached once per instance).
        if str(resolved).startswith(self._sdk_tmp_prefixes) and "tasks" in resolved.parts:
            return True, None
        # Within the data root: current project and current user memory are already allowed above; deny everything else.
        if self._is_within_for_compare(resolved, self._layout.projects_dir):
            return False, (f"访问被拒绝：不允许跨项目读取 ({resolved} 不在当前项目 {project_cwd} 内)")
        if self._is_within_for_compare(resolved, self.data_root):
            return False, (f"访问被拒绝：数据根内只能读取当前项目与你的记忆 ({resolved})")
        if is_search and self._is_within_for_compare(self.data_root, resolved):
            return False, (f"访问被拒绝：搜索范围包含数据根，请缩小到当前项目或具体的参考资料目录 ({resolved})")
        # Allow reference material within the repository root (lib/docs/agent_runtime_profile, etc.).
        if resolved.is_relative_to(self.project_root):
            return True, None
        # Deny other paths (host filesystem: ~/.ssh, /etc, etc.) by default.
        return False, (f"访问被拒绝：路径在项目根外 ({resolved})")

    def _check_write_access(
        self, resolved: Path, project_cwd: Path, *, logical_norm: Path, user_id: str
    ) -> tuple[bool, str | None]:
        r"""Write/Edit restrictions: deny all paths outside cwd except user memory. Within cwd, first apply
        ``PROTECTED_WRITE_RULES`` (``scripts/*.json`` / ``project.json`` / formal script_plan, writable only
        through the centralized MCP tools), then deny code extensions (the Agent does not write code).

        All cwd-relative checks (inside/outside cwd and protected-region matches) compare targets against
        **both raw and resolved bases**: the caller's ``resolved`` has expanded symlinks, but ``project_cwd``
        may be a symlink entry (macOS ``/var↔/private/var``, Linux symlinked project roots). Comparing protected
        paths built only from raw bases against resolved target strings can mismatch and permit bypass;
        enumerating both bases keeps forms consistent."""
        # _enumerate_cwd_bases enumerates raw and resolved bases once, preventing is_relative_to / protected-predicate
        # misses from inconsistent base/target forms under symlinked project_cwd. Reuse bases
        # across table predicates, which consume the list without another resolve (eliminating redundant lstat).
        bases = self._enumerate_cwd_bases(project_cwd)

        # User memory is outside cwd but writable by the Agent (project memory is inside cwd and follows the normal path below);
        # code-extension denial still applies: memory contains notes; relaxing extensions would hand executable scripts to Bash.
        in_user_memory = self._is_user_memory_path(resolved, user_id=user_id)
        if not in_user_memory and not any(resolved.is_relative_to(base) for base in bases):
            return False, (f"访问被拒绝：不允许写入当前项目目录之外的路径 ({resolved})")

        for rule in self.PROTECTED_WRITE_RULES:
            if any(rule.matches(target, bases) for target in (resolved, logical_norm)):
                return False, rule.deny_message

        ext = resolved.suffix.lower()
        if ext in self._CODE_EXTENSIONS_FORBIDDEN:
            return False, (
                f"不允许在项目内创建/编辑 {ext} 类型的代码文件。"
                "Write/Edit 应用于数据文件 (.json/.md/.txt 等)；"
                "代码逻辑请通过现有 skill 脚本完成。"
            )

        return True, None

    @staticmethod
    def _enumerate_cwd_bases(project_cwd: Path) -> list[Path]:
        r"""List of raw and resolved project_cwd bases.

        ``project_cwd`` may be a symlink entry (macOS ``/var↔/private/var``, Linux symlinked project roots);
        paths built only from its raw form can mismatch resolved targets. ``_check_write_access`` (hook layer)
        and ``_build_protected_write_abs_paths`` (sandbox denyWrite) share this enumeration to keep both
        layers' path bases consistent.

        Fail closed if resolve fails: bases contains only raw (the hook safely denies writes when the target
        is outside raw). Log a warning for diagnostics instead of silently swallowing the failure."""
        bases: list[Path] = [project_cwd]
        try:
            resolved_cwd = project_cwd.resolve(strict=False)
            if resolved_cwd != project_cwd:
                bases.append(resolved_cwd)
        except (OSError, RuntimeError) as exc:
            logger.warning("project_cwd 解析失败,路径围栏降级为仅 raw base: %s (%s)", project_cwd, exc)
        return bases

    @classmethod
    def _normalize_path_for_protected_compare(cls, path: Path | str) -> str:
        r"""Normalize a path string into a common key for protected-region comparison.

        Three steps cover three kinds of representation drift:

        - Windows ``\\?\`` extended-length prefix: ``Path.resolve`` returns ``\\?\C:\...`` /
          ``\\?\UNC\server\...`` near MAX_PATH or for UNC shares. Mixing these with ordinary
          bases causes startswith mismatches; strip to ordinary form before comparing.
        - ``unicodedata.normalize("NFC", ...)``: macOS HFS+ stores filenames in NFD. The NFD
          form returned by resolve differs from NFC input even after casefold.
        - ``os.path.normcase`` + ``casefold``: normcase unifies Windows separators
          (``/``→``\``, identity on POSIX); casefold handles case-insensitive comparison.
          Default Windows NTFS / macOS APFS volumes are case-insensitive, so ``PROJECT.JSON``
          and ``project.json`` refer to the same physical file. On case-sensitive Linux volumes the Agent
          does not actually use case variants; occasional overmatching does not break fail-loud semantics."""
        s = str(path)
        if s.startswith("\\\\?\\"):
            rest = s[4:]
            # \\?\UNC\server\share → \\server\share；\\?\C:\... → C:\...
            s = "\\\\" + rest[4:] if rest[:4].casefold() == "unc\\" else rest
        s = unicodedata.normalize("NFC", s)
        return os.path.normcase(s).casefold()

    @classmethod
    def _is_within_for_compare(cls, target: Path, base: Path) -> bool:
        r"""Whether ``target`` is ``base`` itself or its descendant, comparing both after ``_normalize_path_for_protected_compare`` normalization."""
        target_s = cls._normalize_path_for_protected_compare(target)
        base_s = cls._normalize_path_for_protected_compare(base)
        return target_s == base_s or target_s.startswith(base_s.rstrip(os.sep) + os.sep)

    @classmethod
    def _is_protected_project_json(cls, target: Path, bases: list[Path]) -> bool:
        r"""Match protected project JSON (any .json under ``scripts/``, or root ``project.json``).

        The caller should call once for the logical target (normpath collapses `.`/`..` without expanding
        symlinks) and once for the resolved real target: either matching the protected region counts.
        This covers both symlink-origin bypasses (logical protected path resolves outside) and
        symlink-destination bypasses (logical outside path resolves inside).

        The caller (`_check_write_access`) passes ``bases`` once as a list of raw and resolved project_cwd
        forms, matching target forms to avoid misses on macOS ``/var↔/private/var`` and Linux symlinked
        project roots. This predicate consumes the ready list without resolving again (eliminating redundant lstat).

        Normalize both sides with ``_normalize_path_for_protected_compare`` (NFC + normcase + casefold +
        strip ``\\?\``) to handle case, Unicode normalization forms, and Windows extended-length prefixes.

        Shares its source with sandbox ``denyWrite``. This predicate covers built-in Write/Edit (permission
        system, all platforms), forming two layers with denyWrite (Bash subprocesses, kernel level)."""
        target_s = cls._normalize_path_for_protected_compare(target)

        for base in bases:
            if target_s == cls._normalize_path_for_protected_compare(base / "project.json"):
                return True
            scripts_dir = cls._normalize_path_for_protected_compare(base / "scripts")
            # Deny the scripts/ subtree, including the directory: sandbox denyWrite lists all scripts/ at kernel level;
            # hooks must agree, or Agent Write to scripts/foo.bak / .tmp / .md would pollute the script
            # directory and break project structure conventions (scripts/ is exclusively for script .json; drafts belong in drafts/).
            # Explicitly cover the directory itself (target == scripts_dir): the filesystem would reject Write using a directory name
            # as a file path, but the hook should fail fast rather than rely on the OS.
            if target_s == scripts_dir or target_s.startswith(scripts_dir + os.sep):
                return True
        return False

    #: Normalized write-protected script_plan filenames (same normalization as path comparisons). Classmethods cannot be called in the class body,
    #: so declare a placeholder here and assign actual values after ``PROTECTED_WRITE_RULES``.
    _PROTECTED_SCRIPT_PLAN_FILENAMES_NORM: ClassVar[frozenset[str]] = frozenset()
    _PROTECTED_QUARANTINE_FILENAMES_NORM: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def _is_protected_edit_timeline(cls, target: Path, bases: list[Path]) -> bool:
        r"""Match the edit timeline directory (the entire ``edit_timelines/`` subtree, including the directory itself).

        Only edit timeline commands maintain timeline revision chains and optimistic concurrency; direct edits
        bypass revisions and episode locks. ``bases`` and target use the same raw/resolved dual forms as
        ``_is_protected_project_json``."""
        target_s = cls._normalize_path_for_protected_compare(target)
        for base in bases:
            timelines_dir = cls._normalize_path_for_protected_compare(base / "edit_timelines")
            if target_s == timelines_dir or target_s.startswith(timelines_dir + os.sep):
                return True
        return False

    @classmethod
    def _is_protected_source(cls, target: Path, bases: list[Path]) -> bool:
        r"""Match the source directory (the entire ``source/`` subtree, including the directory itself).

        Whole-source text, episode source text, and snapshots are written only through service commands:
        changes must remap the episode ledger using before/after alignment; direct edits misalign source and ledger.
        ``bases`` and target use the same raw/resolved dual forms as ``_is_protected_project_json``."""
        target_s = cls._normalize_path_for_protected_compare(target)
        for base in bases:
            source_dir = cls._normalize_path_for_protected_compare(base / "source")
            if target_s == source_dir or target_s.startswith(source_dir + os.sep):
                return True
        return False

    @classmethod
    def _is_protected_trash(cls, target: Path, bases: list[Path]) -> bool:
        r"""Match the project trash (the entire ``.arcreel/trash/`` subtree, including the directory itself).

        Only the trash module adds/removes deletion records; direct writes bypass the rule that emptying and
        permanent deletion belong only to the creator. ``bases`` and target use the same raw/resolved dual forms
        as ``_is_protected_project_json``."""
        target_s = cls._normalize_path_for_protected_compare(target)
        for base in bases:
            trash_dir = cls._normalize_path_for_protected_compare(base / ARCREEL_DIRNAME / TRASH_DIRNAME)
            if target_s == trash_dir or target_s.startswith(trash_dir + os.sep):
                return True
        return False

    @classmethod
    def _is_protected_media(cls, target: Path, bases: list[Path]) -> bool:
        r"""Match media directories (each entire subtree in ``AGENT_PROTECTED_MEDIA_DIRS``, including the directory itself).

        Only generation and editing tools write media; direct writes or deletions bypass project trash,
        leaving accidental deletions unrecoverable (``docs/adr/0107``)."""
        return any(
            cls._is_within_for_compare(target, base / name) for base in bases for name in AGENT_PROTECTED_MEDIA_DIRS
        ) or any(
            cls._normalize_path_for_protected_compare(target) == cls._normalize_path_for_protected_compare(base / name)
            for base in bases
            for name in AGENT_PROTECTED_MEDIA_FILES
        )

    @classmethod
    def _is_protected_versions(cls, target: Path, bases: list[Path]) -> bool:
        r"""Match the version history directory (the entire ``versions/`` subtree, including the directory itself): only version management maintains records and snapshots."""
        return any(cls._is_within_for_compare(target, base / "versions") for base in bases)

    @classmethod
    def _is_protected_formal_script_plan(cls, target: Path, bases: list[Path]) -> bool:
        r"""Match write-protected formal script_plan files (``AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES`` under ``drafts/episode_N/``).

        Protected for the same reason as ``scripts/*.json`` / ``project.json``: these files have multiple write
        paths (migration read-modify-write, Web saves, regeneration/promotion writes, Agent edits). All except
        the Agent hold the same per-path ``ProjectManager.file_lock``. Agent Write/Edit run in the sandbox
        and cannot acquire that lock, so direct edits create a lost-update window against concurrent Web saves.
        Use ``open_draft`` → ``patch_draft`` → promotion instead; only the lock-holding promotion side writes files.

        Match filenames rather than parsing project variants: protection must hold before session assembly,
        while project content_mode / generation_mode can change at runtime. Recognizing a few script_plan
        filenames unused by the project is harmless: those files have no legitimate writer in that project anyway.

        Block both formal files and ``.invalid.json`` drafts: draft changes must use revisioned MCP tools to
        share the per-path lock and OCC with Web/remote patch, promotion, and generation, preventing direct
        writes from bypassing concurrency control.

        ``bases`` and target use the same raw/resolved dual forms as ``_is_protected_project_json``.
        Do not enumerate episode numbers; match ``episode_*`` directories, since episodes are added/removed at runtime."""
        target_s = cls._normalize_path_for_protected_compare(target)
        for base in bases:
            drafts_dir = cls._normalize_path_for_protected_compare(base / "drafts")
            if not target_s.startswith(drafts_dir + os.sep):
                continue
            parts = target_s[len(drafts_dir) + 1 :].split(os.sep)
            if (
                len(parts) == 2
                and parts[0].startswith("episode_")
                and parts[1] in cls._PROTECTED_SCRIPT_PLAN_FILENAMES_NORM | cls._PROTECTED_QUARANTINE_FILENAMES_NORM
            ):
                return True
        return False


#: Single source for protected write paths: each row declares a hook predicate, denial message, and sandbox denyWrite projection;
#: ``_check_write_access`` (built-in Write/Edit) and ``_build_protected_write_abs_paths``
#: (kernel-level Bash subprocesses) project from the same table. Predicates reference classmethods, so assign after the class body.
#:
#: - ``project_json``: centralized write entry points; ``scripts/*.json`` and ``project.json`` require MCP
#:   tools. Both layers have the same coverage (entire ``scripts/`` subtree + ``project.json``).
#: - ``edit_timeline``: centralized writes; only edit timeline tools append timeline revisions; both layers have the same coverage.
#: - ``source``: centralized writes; only upload/source-editing tools write source text, remapping the episode ledger by alignment; both layers have the same coverage.
#: - ``trash``: only the trash module adds/removes project trash; emptying and permanent deletion are creator-only; both layers have the same coverage.
#: - ``media`` / ``versions``: only generation tools/version management write media/history; only project trash deletes; both layers have the same coverage.
#: - ``formal_script_plan``: locked write entry points; formal script_plan files have multiple write paths holding the same per-path
#:   lock, unavailable to Write/Edit; direct edits create a lost-update window. Layers deliberately differ: the sandbox denies all ``drafts/``
#:   (the list is built once at session assembly while episodes change at runtime, so enumerating files inevitably misses some; Bash
#:   has no legitimate writer anyway), while hooks deny formal script_plan and four revisioned quarantine draft types.
AgentAccessPolicy.PROTECTED_WRITE_RULES = (
    ProtectedWriteRule(
        name="project_json",
        matches=AgentAccessPolicy._is_protected_project_json,
        deny_message=(
            "访问被拒绝：scripts/*.json 与 project.json 不可用 Write/Edit 直改，"
            "请改用 MCP 工具——剧本编辑走 mcp__arcreel__patch_episode_script，"
            "角色/场景/道具走 mcp__arcreel__patch_project，资产改名走 mcp__arcreel__rename_asset。"
        ),
        sandbox_subpaths=("scripts", "project.json"),
    ),
    ProtectedWriteRule(
        name="edit_timeline",
        matches=AgentAccessPolicy._is_protected_edit_timeline,
        deny_message=(
            "访问被拒绝：edit_timelines/ 下的剪辑时间线不可直接写入，每次剪辑都要经剪辑时间线工具追加修订；"
            "新建与复制走 mcp__arcreel__create_timeline，查看走 mcp__arcreel__read_timeline，"
            "修改走 mcp__arcreel__edit_timeline，改名走 mcp__arcreel__rename_timeline，"
            "回滚走 mcp__arcreel__restore_revision。"
        ),
        sandbox_subpaths=("edit_timelines",),
    ),
    ProtectedWriteRule(
        name="source",
        matches=AgentAccessPolicy._is_protected_source,
        deny_message=(
            "访问被拒绝：source/ 下的源文不可直接写入，改动要按对齐重映射分集账本；"
            "新增或整份替换源文走 mcp__arcreel__upload_source，修改整本源文的文件或自带原文的集的原文走 "
            "mcp__arcreel__edit_source_text。删除、调序整本源文的文件请用户在「分集」视图里操作。"
        ),
        sandbox_subpaths=("source",),
    ),
    ProtectedWriteRule(
        name="trash",
        matches=AgentAccessPolicy._is_protected_trash,
        deny_message=(
            "访问被拒绝：.arcreel/trash/ 是项目回收站，不可直接写入或删除。"
            "回收站的清空与彻底删除只能由用户在文件视图里操作。"
        ),
        sandbox_subpaths=(f"{ARCREEL_DIRNAME}/{TRASH_DIRNAME}",),
    ),
    ProtectedWriteRule(
        name="media",
        matches=AgentAccessPolicy._is_protected_media,
        deny_message=(
            "访问被拒绝：媒体目录（"
            + "、".join(f"{name}/" for name in AGENT_PROTECTED_MEDIA_DIRS)
            + "）下的文件与根目录的 style_reference 风格参考图不可直接写入、覆盖或删除，媒体只经生成与编辑工具写入。"
            "删除未引用文件走 mcp__arcreel__delete_project_file，文件移入项目回收站，可用 "
            "mcp__arcreel__restore_trash_record 恢复。"
        ),
        sandbox_subpaths=(*AGENT_PROTECTED_MEDIA_DIRS, *AGENT_PROTECTED_MEDIA_FILES),
    ),
    ProtectedWriteRule(
        name="versions",
        matches=AgentAccessPolicy._is_protected_versions,
        deny_message=(
            "访问被拒绝：versions/ 是版本历史，不可直接写入、覆盖或删除；版本记录与快照只经版本管理维护。"
            "切换视频版本走 mcp__arcreel__select_video_version。"
        ),
        sandbox_subpaths=("versions",),
    ),
    ProtectedWriteRule(
        name="formal_script_plan",
        matches=AgentAccessPolicy._is_protected_formal_script_plan,
        deny_message=(
            "访问被拒绝：正式 script_plan 与待修复草稿（"
            + " / ".join(sorted(AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES))
            + "）不可用 Write/Edit 直改。"
            "这些文件与 Web 端保存、迁移读改写、重生成共享一把文件锁，而 Write/Edit 取不到这把锁，"
            "直改会与并发的保存互相丢失更新。"
            f'请改用 MCP 工具——mcp__arcreel__{OPEN_DRAFT_TOOL_NAME}({{"episode_id": N, "doc_type": "..."}}) '
            "读取可编辑草稿，用 mcp__arcreel__patch_draft 提交修改，再用 "
            f"mcp__arcreel__{PROMOTE_TOOL_NAME} 校验并晋升回正式文件。"
        ),
        sandbox_subpaths=("drafts",),
    ),
)

#: Normalized write-protected filenames (same normalization as path comparisons). Classmethods are unavailable in the class body, so assign after the table.
AgentAccessPolicy._PROTECTED_SCRIPT_PLAN_FILENAMES_NORM = frozenset(
    AgentAccessPolicy._normalize_path_for_protected_compare(Path(name))
    for name in AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES
)
AgentAccessPolicy._PROTECTED_QUARANTINE_FILENAMES_NORM = frozenset(
    AgentAccessPolicy._normalize_path_for_protected_compare(Path(name))
    for name in (
        DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
        NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
        REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
        REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    )
)
