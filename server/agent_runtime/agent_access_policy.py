"""agent 访问规则真相源：内核 sandbox settings 编译与应用层 hook 裁决共用同一份规则。

零 I/O、以进程级根路径纯构造（假根路径亦可构造）；不 import SDK 类型。
SDK 封皮（hook 签名、权限结果类型、权限链顺序）留在会话管理侧薄 adapter
（``server/agent_runtime/session_manager.py``）。
"""

import fnmatch
import functools
import logging
import os
import re
import shlex
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


def _default_claude_projects_dir() -> Path:
    """SDK 存放 per-project 会话数据的基准目录。"""
    return Path.home() / ".claude" / "projects"


@dataclass(frozen=True, kw_only=True)
class AgentAccessPolicy:
    """「agent 能碰什么」的单一规则真相源，同一份规则做两种投影：

    - 内核沙箱层：编译 SandboxSettings（denyRead / denyWrite / 网络域名单）；
    - 应用层 hook：提供逐次读/写/命令裁决与纯变换（project_cwd 逐调用传参）。

    Windows 降级（内核沙箱不可用，``sandbox_enabled=False``）收在类内：
    Bash 走前缀白名单、密钥剥离跳过包装——两条规则互斥耦合（包装后的命令
    以 ``env -u`` 开头，白名单永远匹配不上），必须同处一地。

    构造零 I/O：字段全部为进程级事实（调用方已 resolve 的根路径 + 平台布尔），
    敏感路径表由字段纯推导。凭证注入（读 DB）不属于本类——注入是装配期 I/O，
    塞进来会破坏纯构造可测的根基。
    """

    # 源仓库根（已 resolve）：``.env`` / ``.env.*`` 相对此根（dotenv 从仓库根
    # 加载），也是「仓库内参考资料放行」的围栏基准。
    project_root: Path
    # 数据根（已 resolve，生产为 app_data_dir()）：``.arcreel.db*`` /
    # ``.system_config.json*`` 所在地，也是跨项目读隔离的基准。
    projects_root: Path
    # agent profile 根（已 resolve，受调用方 env 解析控制）：
    # ``.claude/settings.json`` 所在地。
    agent_profile_root: Path
    # 日志目录（已 resolve）：服务器日志含 HTTP 请求路径、provider 探测、异常栈，
    # 默认 read 规则会把 project_root 当成参考资料根放行，不显式 deny 会让任意
    # 项目 session 里的 agent 通过 Read/Grep 读到全局日志。无论落在 repo 内还是
    # 外（如 /var/log/arcreel）都必须 deny。
    log_dir: Path
    # False 表示内核沙箱不支持当前平台（目前仅 Windows）——Bash 走代码白名单回退。
    sandbox_enabled: bool = True
    # SandboxSettings.enableWeakerNestedSandbox 标志。
    in_docker: bool = False
    # SDK 存放 per-project 会话数据的基准目录（tool-results 读放行例外的基准）。
    claude_projects_dir: Path = field(default_factory=_default_claude_projects_dir)

    # Bash 系列工具：sandbox 启用时进 allowed_tools（autoAllowBashIfSandboxed
    # 协同放行）；sandbox 关闭（Windows 回退）时剥离，命令落到 can_use_tool
    # 走前缀白名单（见 ``filter_allowed_tools`` / ``is_bash_command_whitelisted``）。
    BASH_TOOLS: ClassVar[tuple[str, ...]] = ("Bash", "BashOutput", "KillBash")

    # 沙箱网络默认允许的域名。所有 provider HTTP 调用已迁到 in-process MCP tool
    # （server/agent_runtime/sdk_tools/，主进程跑不经 sandbox），所以 sandbox 内
    # 只需要保留 Anthropic SDK 自身 + 通用 dev 域名（docs / 包仓库等）。
    # 自定义 provider 不再需要手动 ALLOWED_DOMAINS 放行。
    _DEFAULT_SANDBOX_ALLOWED_DOMAINS: ClassVar[tuple[str, ...]] = (
        # Anthropic
        "anthropic.com",
        "*.anthropic.com",
        # dev: docs / 包仓库 / acceptance 用例
        "code.claude.com",
        "github.com",
        "*.github.com",
        "*.githubusercontent.com",
        "pypi.org",
        "*.pypi.org",
        "*.npmjs.org",
        "registry.yarnpkg.com",
        "example.com",
    )

    # python skills 入口前缀。约定形态 ``python .claude/skills/<skill>/scripts/
    # <script>.py <args>``：脚本路径须落在某 skill 的 scripts/ 下（_SKILL_SCRIPT_RE
    # 校验），挡住 skills 目录里任意现有/未来文件被当作可执行入口——Windows 回退
    # 无 sandbox denyExec 兜底，仅靠前缀放行会把整棵 skills 树暴露为可执行面。
    _PYTHON_SKILLS_PREFIX: ClassVar[str] = "python .claude/skills/"
    _SKILL_SCRIPT_RE: ClassVar["re.Pattern[str]"] = re.compile(r"^\.claude/skills/[^/]+/scripts/[^/]+\.py$")

    # Windows 回退（sandbox_enabled=False）的 Bash 命令白名单：等价于沙箱化前
    # settings.json permissions.allow 段。也是 can_use_tool deny hint 文案的
    # 单一真相源（format_bash_whitelist_deny_message 从此派生）。
    WINDOWS_BASH_PREFIX_WHITELIST: ClassVar[tuple[str, ...]] = (
        _PYTHON_SKILLS_PREFIX,
        "ffmpeg",
        "ffprobe",
    )

    # Windows 回退白名单的 shell metachar 黑名单：``;`` ``&`` ``|`` ``<`` ``>``
    # `` ` `` ``$`` 与换行都可能在白名单前缀后挂任意命令（链式/管道/重定向/
    # 命令替换）。不解析引号语境，引号内出现也整串拒——宁可误拒（fail-closed），
    # deny 文案会引导 agent 改写命令。
    _BASH_METACHARS_RE: ClassVar["re.Pattern[str]"] = re.compile(r"[;&|<>`$\r\n]")

    # ``..`` 路径段：``python .claude/skills/../../evil.py`` 不含 metachar 且满足
    # ``python .claude/skills/`` 前缀，但 ``..`` 逃出 skills 目录执行任意脚本——
    # Windows 回退无 sandbox denyWrite/denyExec 兜底，整串拒。仅匹配被分隔符/
    # 空白/串首尾界定的 ``..`` 段，``my..name`` 这类文件名不误伤。
    _BASH_PATH_TRAVERSAL_RE: ClassVar["re.Pattern[str]"] = re.compile(r"(?:^|[\s/\\])\.\.(?:[\s/\\]|$)")

    # Bash unset 时额外匹配的环境变量名模式：兜底 SDK 子进程里可能注入或宿主机
    # 继承下来的密钥类变量（如 GEMINI_CLI_IDE_AUTH_TOKEN），名单覆盖不到时靠模式拦。
    _SECRET_ENV_NAME_PATTERNS: ClassVar[tuple[str, ...]] = (
        "API_KEY",
        "AUTH_TOKEN",
        "ACCESS_KEY",
        "ACCESS_TOKEN",
        "SECRET_KEY",
        "CREDENTIAL",
        "CLIENT_SECRET",
    )

    # 文件访问控制走 PreToolUse hook（权限链第 1 步，对所有工具调用生效）；
    # 值为该工具入参中承载路径的键名。
    PATH_TOOLS: ClassVar[dict[str, str]] = {
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Glob": "path",
        "Grep": "path",
    }
    _WRITE_TOOLS: ClassVar[set[str]] = {"Write", "Edit"}
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
    def _sensitive_table(
        self,
    ) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[tuple[Path, str], ...]]:
        """敏感路径表 ``(files, prefixes, globs)``：``files`` 为精确路径、
        ``prefixes`` 为子树根、``globs`` 为 ``(parent, pattern)`` 对。

        按"逻辑类别"从构造字段纯推导，正确反映数据/profile/日志目录被环境
        覆盖后的真实位置（env 解析由调用方完成，本类只消费 resolve 后的根）：

        - ``.env`` / ``.env.*`` 总是相对源仓库根
        - ``.arcreel.db`` / ``.system_config.json`` / ``.arcreel.db-*`` 在
          ``projects_root``（生产为 ``app_data_dir()``）下
        - ``vertex_keys/`` 在 ``projects_root.parent`` 下（与
          ``server.routers.providers.upload_vertex_credential`` 写入位置一致）
        - ``agent_runtime_profile/.claude/settings.json`` 在
          ``agent_profile_root`` 下
        - ``log_dir`` 整目录为敏感前缀
        """
        repo = self.project_root
        data = self.projects_root
        profile = self.agent_profile_root
        files: tuple[Path, ...] = (
            repo / ".env",
            data / ".arcreel.db",
            data / ".system_config.json",
            data / ".system_config.json.bak",
            profile / ".claude" / "settings.json",
        )
        prefixes: tuple[Path, ...] = (data.parent / "vertex_keys", self.log_dir)
        # ``.arcreel.db-wal`` / ``.arcreel.db-shm`` 与主 db 同目录
        globs: tuple[tuple[Path, str], ...] = (
            (repo, ".env.*"),
            (data, ".arcreel.db-*"),
        )
        return files, prefixes, globs

    def is_sensitive_path(self, resolved: Path) -> bool:
        """判断已 resolve 的路径是否命中敏感文件清单。

        覆盖 ``.env`` / ``.env.*`` / ``vertex_keys/`` 子树 / ``.system_config.json*`` /
        ``.arcreel.db*`` / ``agent_runtime_profile/.claude/settings.json`` / 日志目录。
        """
        files, prefixes, globs = self._sensitive_table
        for sensitive_file in files:
            if resolved == sensitive_file:
                return True
        for prefix in prefixes:
            try:
                if resolved == prefix or resolved.is_relative_to(prefix):
                    return True
            except ValueError:
                continue
        for parent, pattern in globs:
            try:
                rel = resolved.relative_to(parent)
            except ValueError:
                continue
            rel_posix = rel.as_posix()
            # 仅匹配 ``parent`` 直系子项，避免 ``.env.local`` 模式吃掉
            # ``project_root/sub/.env.local``（不是同一文件）。
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
    ) -> tuple[bool, str | None]:
        """检查 file_path 是否允许给定工具访问，返回 ``(allowed, deny_reason)``。

        三步 dispatch：
        - 规则 0：敏感文件（.env / vertex_keys / settings.json 等）一律拒
        - 写工具（Write/Edit）→ ``_check_write_access``
        - 读工具（Read/Glob/Grep）→ ``_check_read_access``
        """
        try:
            p = Path(file_path)
            logical = p if p.is_absolute() else project_cwd / p
            # normpath 收敛 `.`/`..` 但不展开 symlink——保留「逻辑目标」与「resolve 后的真实
            # 目标」两个视角，用来识别 symlink 起点（逻辑在 protected 区、resolve 跳到外面）
            # 与 symlink 终点（逻辑在外、resolve 落入 protected 区）两类绕过。
            logical_norm = Path(os.path.normpath(str(logical)))
            resolved = logical.resolve()
        except (ValueError, OSError):
            return False, "访问被拒绝：无效的文件路径"

        # 规则 0: 敏感文件强制拒绝
        if self.is_sensitive_path(resolved):
            return False, f"访问被拒绝：敏感文件不可访问 ({resolved})"

        if tool_name in self._WRITE_TOOLS:
            return self._check_write_access(resolved, project_cwd, logical_norm=logical_norm)
        return self._check_read_access(resolved, project_cwd)

    def build_sandbox_settings(self, project_cwd: Path) -> dict[str, Any]:
        """构造 SandboxSettings dict（SDK Python TypedDict 未声明 filesystem
        子结构，但 CLI 运行时透传 JSON 接受）——内核沙箱层对同一份规则的编译投影。

        - ``sandbox_enabled=False``（Windows 回退）：仅返回 ``{"enabled": False}``，
          Bash 工具改走 ``is_bash_command_whitelisted`` 代码白名单。
        - ``filesystem.denyRead``：内核级文件读拒绝（macOS Seatbelt / Linux
          bwrap profile），对 sandbox 内所有子进程生效。
        - ``filesystem.denyWrite``：内核级文件写拒绝，覆盖 ``scripts/`` 目录与
          ``project.json``——这两类项目 JSON 的写入只能走 in-process MCP 工具
          （``patch_episode_script`` / ``patch_project`` 等，跑在主进程不受 sandbox 约束），
          堵死 Bash（``echo>`` / ``sed`` / ``python -c``）旁路。OS 级对 sandbox 内所有
          子进程生效。sandbox 内已无合法 Bash 写这两类文件（compose 写视频输出、
          split 写 ``source/``，均不碰），故不误伤。
        - ``allowUnsandboxedCommands=False``：禁止 agent 在 sandbox 失败时
          请求"重试 unsandboxed"，对红线场景不可接受。
        """
        if not self.sandbox_enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "network": {"allowedDomains": list(self._DEFAULT_SANDBOX_ALLOWED_DOMAINS)},
            "enableWeakerNestedSandbox": bool(self.in_docker),
            "filesystem": {
                "denyRead": self._build_sensitive_abs_paths(),
                "denyWrite": self._build_protected_json_abs_paths(project_cwd),
            },
        }

    @classmethod
    def _build_protected_json_abs_paths(cls, project_cwd: Path) -> list[str]:
        """项目 JSON 写禁清单（绝对路径）：``scripts/`` 目录子树 + ``project.json``。

        与 ``_check_write_access`` 的内置 Write/Edit 拒绝同源（同两类路径），二者构成双层：
        sandbox denyWrite 管 Bash 子进程（内核级），``_check_write_access`` hook 管内置
        Write/Edit（权限系统，全平台）。

        base 经 ``_enumerate_cwd_bases`` 同时枚举 raw + resolved 两种形式（与
        ``_check_write_access`` 同口径）：sandbox 实现若按字符串路径比对而非 inode，
        仅注册 raw 形式会在 Bash 子进程经 symlink 解析（macOS ``/var↔/private/var``、
        Linux symlinked 项目根）后写 resolved 路径时失配。
        """
        paths: list[str] = []
        for base in cls._enumerate_cwd_bases(project_cwd):
            for target in (base / "scripts", base / "project.json"):
                target_s = str(target)
                if target_s not in paths:
                    paths.append(target_s)
        return paths

    def _build_sensitive_abs_paths(self) -> list[str]:
        """构造敏感文件绝对路径列表，传给 sandbox profile 的 denyRead 字段。

        SDK CLI 会跳过不存在的 deny 路径（"Skipping non-existent deny path"），
        所以这里枚举当前真实存在的固定清单 + glob 命中项 + prefix 目录
        （vertex_keys / 日志整目录交给 sandbox profile 递归 deny）。

        每次会话启动重新枚举，避免后建敏感文件（.env / .env.local）绕过
        sandbox profile — sandbox profile 在 SDK 客户端启动时一次性生效，
        run-time 新增的文件若已落入命名约定就要立刻进入 denyRead。
        """
        files, prefixes, globs = self._sensitive_table
        candidates: list[Path] = list(files)
        candidates.extend(prefixes)
        for parent, pattern in globs:
            if parent.exists():
                candidates.extend(parent.glob(pattern))
        return [str(p) for p in candidates if p.exists()]

    @classmethod
    @functools.cache
    def _collect_env_keys_to_scrub(cls) -> tuple[str, ...]:
        """汇总要从 Bash 子进程剥离的 env 变量名。

        来源三路：固定清单（ANTHROPIC + OTHER provider）+ 模式匹配（扫
        ``os.environ`` 找名字含 KEY/TOKEN/CREDENTIAL 等模式的变量）+ 去重。
        父进程 environ 在启动后不再增减密钥类变量，结果稳定 — cache 避免每条
        Bash 命令都重扫。测试需要切环境时调
        ``cls._collect_env_keys_to_scrub.cache_clear()``。
        """
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
        """``env -u VAR1 -u VAR2 ... sh -c `` 前缀。命中清单由
        ``_collect_env_keys_to_scrub`` 决定，运行期不变 — cache 复用整段字符串。
        """
        unset_flags = " ".join(f"-u {key}" for key in cls._collect_env_keys_to_scrub())
        return f"env {unset_flags} sh -c "

    def wrap_bash_command_for_env_scrub(self, command: object) -> str | None:
        """Bash 密钥剥离的纯变换：返回包装后的命令，None 表示不包装。

        SDK 子进程持有真值的 ANTHROPIC_*（认证需要），及空值 placeholder 的
        OTHER_PROVIDER_*（options.env 空字符串覆盖），Bash sandbox 默认从父进程
        继承全部 env，agent 跑 ``env | grep`` 能看到变量名。通过
        ``env -u VAR ... sh -c '<cmd>'`` 把所有命中的变量名从 Bash subshell 中
        unset，原 command 经 ``shlex.quote`` 整体作为 sh 子壳的 -c 参数。

        sandbox 不可用（Windows 回退）时不包装：``env -u``/``sh -c`` 是 POSIX
        机制，原生 Windows 不可执行；且包装后命令以 ``env -u`` 开头，会让
        ``is_bash_command_whitelisted`` 的前缀白名单永远匹配不上——「包装破坏
        白名单匹配」的互斥约束就锁在这两个方法之间。空/非字符串 command 同样
        不包装。
        """
        if not isinstance(command, str) or not command.strip():
            return None
        if not self.sandbox_enabled:
            return None
        return f"{self._env_scrub_wrap_prefix()}{shlex.quote(command)}"

    @classmethod
    def is_bash_command_whitelisted(cls, command: str) -> bool:
        """Windows 回退（sandbox 不可用）的 Bash 命令白名单判定。

        纯 startswith 前缀匹配有三类绕过：metachar 链（``ffmpeg ...; evil`` 整串
        满足前缀，尾部命令照常执行，且 Windows 上无 sandbox denyWrite 兜底）、
        命令名前缀碰撞（``ffmpegX`` 也以 ``ffmpeg`` 开头）、路径穿越（``..`` 逃出
        skills 目录）。判定分四步：

        1. 整串拒 shell metachar（``_BASH_METACHARS_RE``），挡链式/管道/重定向/
           命令替换；
        2. 拒 ``..`` 路径段（``_BASH_PATH_TRAVERSAL_RE``）：原串之外，再剥引号、
           按 Windows 分隔符（``\\``→``/``）与 POSIX 转义（去 ``\\``）两解后各查一遍
           ——shell 会把 ``".."`` / ``.\\.`` 还原成 ``..``，只查原串会被这类混淆
           绕过逃出 skills 目录；
        3. 按 token 边界匹配 ``WINDOWS_BASH_PREFIX_WHITELIST``：不含空格的前缀
           （ffmpeg/ffprobe）要求命令名完全相等或后跟空格；
        4. python skills 入口额外要求首个参数是 ``<skill>/scripts/<script>.py``
           （``_is_allowed_python_skill_command``），不放行 skills 目录下任意文件。

        白名单匹配在剥引号 + 反斜杠转正斜杠的归一化串上做：容忍 Windows agent 发出
        的 ``\\`` 分隔符路径与带引号的脚本路径，避免合法命令被误拒（matching 不改写
        实际执行的命令，放行时仍透传原始 input）。metachar 与 ``..`` 已先对原串及
        各归一化变体拒过，归一化只用于「是否命中白名单」的判定，不会放宽安全边界。
        """
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
        """``python .claude/skills/...`` 的脚本入口校验：取首个参数（脚本路径），
        要求匹配 ``.claude/skills/<skill>/scripts/<script>.py``。约束到显式 scripts
        入口，避免 skills 目录下任意文件在 Windows 回退（无 sandbox 兜底）下可执行。

        入参须为 ``is_bash_command_whitelisted`` 归一化后的串（已剥引号、反斜杠转
        正斜杠），故按空白切分取首参即可，无需 shell 级 tokenize。
        """
        parts = normalized_cmd.split(maxsplit=2)
        if len(parts) < 2:
            return False
        return cls._SKILL_SCRIPT_RE.match(parts[1]) is not None

    @classmethod
    def format_bash_whitelist_deny_message(cls, command: str) -> str:
        """Windows 回退 Bash 白名单拒绝文案。从 WINDOWS_BASH_PREFIX_WHITELIST
        派生 allowed 列表，避免常量与文案双份漂移。"""
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

    def filter_allowed_tools(self, tools: list[str]) -> list[str]:
        """按沙箱可用性过滤 allowed_tools：sandbox 关闭（Windows 回退）时剥离
        Bash 系列，让命令落到 can_use_tool 走 ``is_bash_command_whitelisted``
        前缀白名单——与内核路线（autoAllowBashIfSandboxed 放行）互斥的另一半。
        """
        if self.sandbox_enabled:
            return list(tools)
        bash_tools = set(self.BASH_TOOLS)
        return [t for t in tools if t not in bash_tools]

    # SDK 后台任务输出（``<tmp>/claude-*/tasks``）的 tmp 根前缀。
    # ``tempfile.gettempdir()`` 与 ``.resolve()`` 的结果在进程生命周期内稳定，
    # 但 ``_check_read_access`` 是 per-tool-use 钩子，每次重算会做无谓的
    # ``.resolve()`` 系统调用（lstat/readlink）——cached_property 算一次缓存。
    # 覆盖跨平台 tmp 根（Linux ``/tmp``、macOS 默认 ``/var/folders/.../T``、
    # Windows ``%TEMP%``）。``resolved`` 已 ``.resolve()`` 过：macOS 上 ``/var``
    # 是 ``/private/var`` 的 symlink、``/tmp`` 是 ``/private/tmp``，原始 + resolve
    # 两种形态都列出，避免 startswith 因别名失配。
    @functools.cached_property
    def _sdk_tmp_prefixes(self) -> tuple[str, ...]:
        _tempdir = Path(tempfile.gettempdir())
        return (
            str(_tempdir / "claude-"),
            str(_tempdir.resolve() / "claude-"),
            "/tmp/claude-",
            "/private/tmp/claude-",
        )

    @functools.cached_property
    def _claude_projects_dir_resolved(self) -> Path | None:
        """已 resolve 的 ``claude_projects_dir`` 基准目录（实例内算一次缓存）。

        ``~/.claude`` 可能被用户软链到 dotfiles / 云同步目录，而被比较的
        ``resolved`` 已 ``.resolve()`` 过，两侧不一致会让 is_relative_to 失配、
        误拒合法的 SDK tool-results 读取——故基准也 resolve（与 tmp / project_root
        比较保持同一口径）。只有这段稳定前缀需要 resolve；每会话变化的 ``encoded``
        子目录是 SDK 创建的真实目录、纯字符串拼接即可，无需 per-call resolve
        （``_check_read_access`` 是 per-tool-use 钩子，避免重复 lstat/readlink）。

        resolve 在符号链接环（RuntimeError）/ 无权限父目录（OSError）下会抛——
        权限钩子必须 fail-closed，解析失败返回 None，调用方据此跳过 tool-results
        例外、落到更严格的拒绝分支，不让异常冒泡中断工具调用。
        """
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

    def _check_read_access(self, resolved: Path, project_cwd: Path) -> tuple[bool, str | None]:
        """Read/Glob/Grep 的跨项目隔离 + host 文件系统封锁。

        cwd 内放行；SDK tool-results / /tmp/claude-*/tasks 例外放行；
        projects_root 下其他项目子目录拒、根直放文件放行；仓库根内参考资料
        （lib/docs 等）放行；其余（host 文件系统：~/.ssh、/etc 等）默认拒。
        """
        if resolved.is_relative_to(project_cwd):
            return True, None
        # SDK tool-results 例外（已 resolve 的基准见 _claude_projects_dir_resolved）。
        claude_projects_dir = self._claude_projects_dir_resolved
        if claude_projects_dir is not None:
            sdk_project_dir = claude_projects_dir / self.encode_sdk_project_path(project_cwd)
            if resolved.is_relative_to(sdk_project_dir) and "tool-results" in resolved.parts:
                return True, None
        # SDK 后台任务输出例外（前缀计算见 _sdk_tmp_prefixes，实例内缓存一次）。
        if str(resolved).startswith(self._sdk_tmp_prefixes) and "tasks" in resolved.parts:
            return True, None
        # projects_root 下：当前项目以外的子目录拒，根直放文件放行
        projects_root = self.projects_root
        if resolved.is_relative_to(projects_root):
            rel_to_projects = resolved.relative_to(projects_root)
            if rel_to_projects.parts:
                first_entry = projects_root / rel_to_projects.parts[0]
                if first_entry.is_dir() and first_entry.name != project_cwd.name:
                    return False, (f"访问被拒绝：不允许跨项目读取 ({resolved} 不在当前项目 {project_cwd} 内)")
            return True, None
        # 仓库根内的参考资料（lib/docs/agent_runtime_profile 等）放行
        if resolved.is_relative_to(self.project_root):
            return True, None
        # 其余路径（host 文件系统：~/.ssh、/etc 等）默认拒
        return False, (f"访问被拒绝：路径在项目根外 ({resolved})")

    def _check_write_access(self, resolved: Path, project_cwd: Path, *, logical_norm: Path) -> tuple[bool, str | None]:
        """Write/Edit 的写入约束：cwd 外一律拒，cwd 内代码扩展名拒（agent 不写代码），
        且 ``scripts/*.json`` 与 ``project.json`` 一律拒——只能走收归后的 MCP 工具。

        所有 cwd-relative 判定（cwd 内外、protected 区命中）都按 **base 同时枚举 raw + resolved**
        两种形式与 target 比对：caller 传入的 ``resolved`` 已展开 symlink，但 ``project_cwd`` 可能
        是 symlink 入口（macOS ``/var↔/private/var``、Linux symlinked 项目根）。仅用 raw base 拼
        protected 路径与 resolved target 字符串比对会失配 → bypass；同时枚举两种 base 保证同口径。
        """
        # raw + resolved 两种形式的 base 由 _enumerate_cwd_bases 一次性枚举，避免 symlinked
        # project_cwd 下 is_relative_to / 受保护谓词因 base↔target 形式不一致漏判。bases 复用
        # 给下游 `_is_protected_project_json`,后者直接消费列表不再做第二次 resolve（消除冗余 lstat）。
        bases = self._enumerate_cwd_bases(project_cwd)

        if not any(resolved.is_relative_to(base) for base in bases):
            return False, (f"访问被拒绝：不允许写入当前项目目录之外的路径 ({resolved})")

        if any(self._is_protected_project_json(target, bases) for target in (resolved, logical_norm)):
            return False, (
                "访问被拒绝：scripts/*.json 与 project.json 不可用 Write/Edit 直改，"
                "请改用 MCP 工具——剧本编辑走 mcp__arcreel__patch_episode_script / "
                "mcp__arcreel__insert_segment / mcp__arcreel__remove_segment / mcp__arcreel__split_segment，"
                "角色/场景/道具走 mcp__arcreel__patch_project。"
            )

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
        """raw + resolved 两种形式的 project_cwd base 列表。

        ``project_cwd`` 可能是 symlink 入口（macOS ``/var↔/private/var``、Linux
        symlinked 项目根），仅用 raw 形式拼路径与已 resolve 的 target 比对会失配。
        ``_check_write_access``（hook 层）与 ``_build_protected_json_abs_paths``
        （sandbox denyWrite）共用此枚举，保证两层路径基同口径。

        resolve 失败时 fail-closed：bases 仅含 raw（hook 层 target 不在 raw 下时
        拒绝写入仍安全），加 warning 保留诊断信号而非静默吞掉。
        """
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
        """把路径字符串归一化为受保护区比对用的统一键。

        三步处理，覆盖三类形态漂移：

        - Windows ``\\\\?\\`` 扩展长度前缀：``Path.resolve`` 在路径接近 MAX_PATH 或
          UNC 共享时返回 ``\\\\?\\C:\\...`` / ``\\\\?\\UNC\\server\\...`` 形式，与常规
          形式混入 bases 时 startswith 失配——剥成常规形式再比；
        - ``unicodedata.normalize("NFC", ...)``：macOS HFS+ 按 NFD 存储文件名，
          resolve 返回的 NFD 形式与 NFC 输入即使 casefold 后仍是不同字符串；
        - ``os.path.normcase`` + ``casefold``：normcase 统一 Windows 分隔符
          （``/``→``\\``，POSIX 上恒等）；casefold 承担大小写不敏感比较——
          Windows NTFS / macOS APFS 默认卷大小写不敏感，``PROJECT.JSON`` 与
          ``project.json`` 指向同一物理文件。Linux case-sensitive 卷上 agent
          实际不会用大小写变体，偶尔 over-match 不破坏 fail-loud 语义。
        """
        s = str(path)
        if s.startswith("\\\\?\\"):
            rest = s[4:]
            # \\?\UNC\server\share → \\server\share；\\?\C:\... → C:\...
            s = "\\\\" + rest[4:] if rest[:4].casefold() == "unc\\" else rest
        s = unicodedata.normalize("NFC", s)
        return os.path.normcase(s).casefold()

    @classmethod
    def _is_protected_project_json(cls, target: Path, bases: list[Path]) -> bool:
        """命中受保护的项目 JSON（``scripts/`` 下任意 .json，或根 ``project.json``）。

        caller 应分别对「逻辑目标」（normpath 收敛 `.`/`..` 但不展开 symlink）和「resolve
        后的真实目标」各调一次：任一落入 protected 区都判定命中——覆盖项目内 symlink 起点
        指 protected 路径（resolved 跳到外）与终点指 protected 路径（逻辑在外、resolved 跳入）
        两类绕过。

        ``bases`` 由 caller(`_check_write_access`)一次性传入 raw + resolved 两种形式的
        project_cwd 列表（同口径 raw/resolved 与 target 比对，避免 macOS ``/var↔/private/var``、
        Linux symlinked 项目根下漏判），本谓词消费现成 list 不再自行 resolve（消除冗余 lstat）。

        比对两侧都经 ``_normalize_path_for_protected_compare`` 归一化（NFC + normcase +
        casefold + 剥 ``\\\\?\\`` 前缀），处理大小写、Unicode 归一化形式与 Windows
        扩展长度前缀三类形态漂移。

        与 sandbox ``denyWrite`` 同源；此谓词覆盖内置 Write/Edit（权限系统，全平台），
        与 denyWrite（Bash 子进程，内核级）构成双层。
        """
        target_s = cls._normalize_path_for_protected_compare(target)

        for base in bases:
            if target_s == cls._normalize_path_for_protected_compare(base / "project.json"):
                return True
            scripts_dir = cls._normalize_path_for_protected_compare(base / "scripts")
            # 拒绝 scripts/ 子树（含目录本身）：sandbox denyWrite 把整个 scripts/ 列入内核级 deny，
            # hook 层须保持一致——否则 agent 用 Write 写 scripts/foo.bak / .tmp / .md 会污染剧本
            # 目录，破坏项目结构约定（scripts/ 是剧本 .json 专属，drafts/ 才放草稿）。
            # 同时显式覆盖目录路径本身（target == scripts_dir）：agent 把目录名当文件路径 Write 时
            # 文件系统会拒，但 hook 层 fail-fast 优先，不依赖 OS 兜底。
            if target_s == scripts_dir or target_s.startswith(scripts_dir + os.sep):
                return True
        return False
