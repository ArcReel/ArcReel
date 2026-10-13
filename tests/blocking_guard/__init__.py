"""测试期的事件循环阻塞护栏：lib/server 代码在事件循环线程上的阻塞调用让用例失败。

由根 conftest 的 ``pytest_plugins`` 加载，会话内全程启用，同步与异步用例都覆盖：同步
``TestClient`` 的应用代码跑在 anyio portal 线程自己的事件循环上，同样受检。

检测借用 blockbuster 的拦截点（文件、``os``、socket、sqlite、``time.sleep``、有竞争的
``threading.Lock``），另补四类锁等待：阻塞模式的 portalocker 文件锁（项目文件锁、生成准入
同步锁等）、产物清单锁的轮询等待、版本管理的线程锁。非阻塞的尝试加锁不算违规。

**记录而不抛错。** blockbuster 默认在拦截点抛 ``BlockingError``，业务代码的宽泛异常捕获会把
它吞掉（推算失败回退默认值、执行器把异常记成任务失败），违规就此隐身。这里把每个拦截点的
放行谓词换成「判定、记录、放行」：调用照常执行，违规记进当前用例，用例每个阶段出报告时
把白名单外的违规改判为失败。

**归属。** 从阻塞点沿调用栈向外找，第一个 lib/server 的 async 函数就是违规条目（文件 +
限定名）；先遇到 ``tests/`` 的帧说明阻塞由测试自身驱动（同步辅助、替身、内联的
``to_thread``），不计。整条栈没有 async 帧时（事件循环直接回调的同步函数）取最外层的
lib/server 帧。

**始终放行**：日志落盘、traceback 取源码行、``realpath`` 解析符号链接、函数内的延迟 import，
以及模块级 ``functools.cache`` 加载器的函数体。后两类每个进程只执行一次，首次调用落在哪个
用例取决于调度顺序。

两份清单：

- ``allowlist.txt``：临时白名单，治理期间登记现有违规，修好一处删一处，Spec #3147 收尾时清空。
  由写出模式整体再生成，必须在 Linux 上生成，命令见文件头与 ``docs/agents/testing.md``。
- ``exemptions.toml``：永久豁免，只收应用启动与关停路径上的一次性阻塞，每条写明理由。

完整运行（未做任何选择或筛选）结束时，白名单与豁免清单里本次未触发的条目以警告列出。
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import threading
import tomllib
from collections import defaultdict
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

import pytest
from blockbuster import BlockBuster, BlockBusterFunction

WRITE_ENV = "ARCREEL_BLOCKING_ALLOWLIST_WRITE"
ALLOWLIST_RELPATH = "tests/blocking_guard/allowlist.txt"
EXEMPTIONS_RELPATH = "tests/blocking_guard/exemptions.toml"
SCANNED_DIRS = ("lib", "server")
TESTS_DIR = "tests"

REGENERATE_COMMAND = """\
docker run --rm -v "$PWD":/repo -w /repo \\
  -e UV_PROJECT_ENVIRONMENT=/opt/venv -e UV_LINK_MODE=copy -e PYTHONPYCACHEPREFIX=/tmp/pycache \\
  -e ARCREEL_BLOCKING_ALLOWLIST_WRITE=1 -v arcreel-linux-uv-cache:/root/.cache/uv \\
  python:3.12-slim sh -c 'apt-get update -qq && apt-get install -y -qq git media-types mediainfo bubblewrap socat >/dev/null \\
    && pip install -q uv && uv sync -q --frozen \\
    && uv run python -m pytest tests -n 4 --dist loadfile -q -p no:cacheprovider'"""

ALLOWLIST_HEADER = f"""\
# 事件循环阻塞的临时白名单（Spec #3147）。每行一个条目：<文件>::<最近的 lib/server async 函数>，
# 行尾注释是该函数里被拦截的调用，仅供参考，匹配只看条目本身。
#
# 修好一处就删掉对应条目；Spec 收尾时本文件必须为空。不要手工追加条目：新违规要么修掉，
# 要么（仅限启动与关停路径的一次性阻塞）写进同目录的 exemptions.toml 并注明理由。
#
# 本文件由写出模式整体再生成，必须在 Linux 上生成（macOS 的文件复制走另一套系统调用，
# 生成结果会与 CI 对不上）。在仓库根目录运行：
#
{"".join(f"#   {line}\n" for line in REGENERATE_COMMAND.splitlines())}"""

_ASYNC_CODE_FLAGS = inspect.CO_COROUTINE | inspect.CO_ITERABLE_COROUTINE | inspect.CO_ASYNC_GENERATOR
_GUARD_DIR = str(Path(__file__).resolve().parent) + os.sep
_GUARD_MODULE = __name__
_LOGGING_DIR = str(Path(logging.__file__).resolve().parent) + os.sep

# 与业务阻塞无关、在任何栈上都放行的帧：日志落盘、traceback 取源码行、函数内延迟 import。
# realpath 逐段 lstat（不在拦截点内），只有路径经过符号链接时才 readlink：macOS 的临时目录
# 在 /var -> /private/var 之下，Linux 不是，不放行会让同一段代码只在 macOS 上违规。
_ALWAYS_ALLOWED_FRAMES: tuple[tuple[str, frozenset[str] | None], ...] = (
    ("posixpath.py", frozenset({"realpath", "_joinrealpath"})),
    ("<frozen posixpath>", frozenset({"realpath", "_joinrealpath"})),
    ("linecache.py", frozenset({"getlines", "updatecache", "checkcache", "lazycache"})),
    ("<frozen importlib._bootstrap>", None),
    ("<frozen importlib._bootstrap_external>", None),
)

_STACK_PREVIEW_FRAMES = 6


@dataclass(frozen=True, slots=True)
class Violation:
    """一次被拦截的阻塞调用。"""

    key: str
    call: str
    thread: str
    stack: tuple[str, ...]


@dataclass(slots=True)
class _Lists:
    allowlist: frozenset[str]
    exemptions: dict[str, str]


@dataclass(slots=True)
class BlockingGuard:
    """会话级状态：拦截点、待结算的违规与各清单的触发情况。"""

    root: Path
    write_mode: bool
    lists: _Lists
    scanned_prefixes: tuple[str, ...] = ()
    tests_prefix: str = ""
    pending: list[Violation] = field(default_factory=list)
    triggered: set[str] = field(default_factory=set)
    observed: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    deselected: int = 0
    _blockbuster: BlockBuster | None = None
    _extra_functions: list[BlockBusterFunction] = field(default_factory=list)
    _restore: list[Callable[[], None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.scanned_prefixes = tuple(str(self.root / name) + os.sep for name in SCANNED_DIRS)
        self.tests_prefix = str(self.root / TESTS_DIR) + os.sep

    # ------------------------------------------------------------------ 启停

    def activate(self) -> None:
        blockbuster = BlockBuster()
        # 绝对路径的 abspath 只做字符串规范化，不碰文件系统；相对路径才会读 cwd。
        blockbuster.functions["os.path.abspath"].can_block_predicate = _absolute_path_arg
        self._blockbuster = blockbuster
        self._extra_functions = _lock_rules(self._restore)
        for function in [*blockbuster.functions.values(), *self._extra_functions]:
            function.can_block_predicate = self._recording_predicate(function)
            function.activate()

    def deactivate(self) -> None:
        for function in self._extra_functions:
            function.deactivate()
        if self._blockbuster is not None:
            self._blockbuster.deactivate()
        while self._restore:
            self._restore.pop()()

    # ------------------------------------------------------------------ 记录

    def _recording_predicate(self, function: BlockBusterFunction) -> Callable[..., bool]:
        allowed = function.can_block_predicate
        allowed_frames = function.can_block_functions
        call = function.full_name

        def predicate(*args: Any, **kwargs: Any) -> bool:
            # blockbuster 只在当前线程有运行中的事件循环时才走到这里，并在谓词期间
            # 屏蔽了嵌套拦截；返回 True 即放行原调用，所以这里只判定和记录，从不抛错。
            if not allowed(*args, **kwargs):
                self._inspect(call, allowed_frames, sys._getframe(1))
            return True

        return predicate

    def _inspect(self, call: str, allowed_frames: Iterable[tuple[str, Iterable[str]]], frame: FrameType | None) -> None:
        frames: list[FrameType] = []
        while frame is not None:
            filename = frame.f_code.co_filename
            if _frame_allowed(filename, frame.f_code.co_name, allowed_frames) or _runs_once_per_process(frame):
                return
            frames.append(frame)
            frame = frame.f_back

        key: str | None = None
        outermost_scanned: FrameType | None = None
        for candidate in frames:
            filename = candidate.f_code.co_filename
            module = candidate.f_globals.get("__name__", "")
            if filename.startswith(_GUARD_DIR) or module.startswith(_GUARD_MODULE):
                continue
            # 模块名兜底：pytest 断言重写的缓存不修正 co_filename，跨机器复用缓存时路径对不上。
            if filename.startswith(self.tests_prefix) or module.startswith(f"{TESTS_DIR}."):
                return
            if filename.startswith(self.scanned_prefixes):
                if candidate.f_code.co_flags & _ASYNC_CODE_FLAGS:
                    key = self._key(candidate)
                    break
                outermost_scanned = candidate
        if key is None:
            if outermost_scanned is None:
                return
            key = self._key(outermost_scanned)

        stack = tuple(
            f"{self._relpath(f.f_code.co_filename)}:{f.f_lineno} in {f.f_code.co_qualname}"
            for f in frames
            if f.f_code.co_filename.startswith(self.scanned_prefixes)
        )[:_STACK_PREVIEW_FRAMES]
        self.pending.append(Violation(key, call, threading.current_thread().name, stack))

    def _relpath(self, filename: str) -> str:
        return filename.removeprefix(str(self.root) + os.sep).replace(os.sep, "/")

    def _key(self, frame: FrameType) -> str:
        return f"{self._relpath(frame.f_code.co_filename)}::{frame.f_code.co_qualname}"

    # ------------------------------------------------------------------ 结算

    def settle(self) -> list[Violation]:
        """取走待结算的违规，登记触发情况，返回白名单与豁免之外的部分。"""
        drained, self.pending[:] = list(self.pending), []
        unexpected = []
        for violation in drained:
            if violation.key in self.lists.exemptions:
                self.triggered.add(violation.key)
                continue
            self.observed[violation.key].add(violation.call)
            if violation.key in self.lists.allowlist:
                self.triggered.add(violation.key)
            elif not self.write_mode:
                unexpected.append(violation)
        return unexpected


def _runs_once_per_process(frame: FrameType) -> bool:
    """帧是否是模块级 ``functools.cache`` / ``lru_cache`` 函数的函数体。

    这类加载器（随包分发的 schema、规则表）每个进程按参数只执行一次，与函数内的延迟 import
    同类。它们的首次调用落在哪个用例取决于 xdist 的调度，不放行会让白名单随运行顺序变化。
    """
    code = frame.f_code
    candidate = frame.f_globals.get(code.co_name)
    if candidate is None or not hasattr(candidate, "cache_info"):
        return False
    return getattr(getattr(candidate, "__wrapped__", None), "__code__", None) is code


def _absolute_path_arg(path: object, *_: object, **__: object) -> bool:
    return isinstance(path, (str, bytes, os.PathLike)) and os.path.isabs(path)


def _frame_allowed(filename: str, function: str, allowed_frames: Iterable[tuple[str, Iterable[str]]]) -> bool:
    if filename.startswith(_LOGGING_DIR):
        return True
    normalized = filename.replace("\\", "/")
    for suffix, names in _ALWAYS_ALLOWED_FRAMES:
        if normalized.endswith(suffix) and (names is None or function in names):
            return True
    return any(normalized.endswith(suffix) and function in names for suffix, names in allowed_frames)


# ---------------------------------------------------------------------- 锁规则


class _WatchedRLock:
    """包住版本管理的 ``RLock``，让 ``with`` 加锁经过可拦截的 Python 层 ``acquire``。

    C 实现的 ``RLock.__enter__`` 不走 ``acquire`` 属性，blockbuster 的锁规则也不覆盖 RLock。
    """

    __slots__ = ("_lock",)

    def __init__(self, lock: Any) -> None:
        self._lock = lock

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def _lock_rules(restore: list[Callable[[], None]]) -> list[BlockBusterFunction]:
    """项目文件锁、生成准入锁、产物清单锁、版本管理锁的阻塞等待规则。"""
    import portalocker
    import portalocker.portalocker as portalocker_impl

    import lib.artifacts.version_manager as version_manager
    from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter

    def portalocker_nonblocking(_file: object, flags: int, *_: object, **__: object) -> bool:
        return bool(flags & portalocker.LOCK_NB)

    def rlock_nonblocking(_lock: object, blocking: bool = True, timeout: float = -1) -> bool:
        return not blocking or timeout == 0

    # 版本管理锁按 versions.json 路径缓存 RLock；从工厂处换成可拦截的包装，实例照旧共享同一把锁。
    original_factory = version_manager._get_versions_file_lock

    watched: dict[int, _WatchedRLock] = {}

    def watched_factory(versions_file: Path) -> Any:
        # 原工厂按路径缓存且从不释放，同一把锁的 id 稳定；包装同样按锁复用，保持「同一文件同一把锁」。
        lock = original_factory(versions_file)
        return watched.setdefault(id(lock), _WatchedRLock(lock))

    version_manager._get_versions_file_lock = watched_factory
    restore.append(lambda: setattr(version_manager, "_get_versions_file_lock", original_factory))

    rules = [
        # 项目文件锁、ProjectManager.file_lock、生成准入的同步锁都经 portalocker 阻塞模式落到 flock。
        (
            "文件锁（portalocker 阻塞模式）",
            BlockBusterFunction(portalocker, "lock", can_block_predicate=portalocker_nonblocking),
        ),
        (
            "文件锁（portalocker 阻塞模式）",
            BlockBusterFunction(portalocker_impl, "lock", can_block_predicate=portalocker_nonblocking),
        ),
        # 产物清单锁以非阻塞尝试加 time.sleep 轮询，最长等 10 秒；进入它就是一次锁等待。
        ("产物清单锁（轮询等待）", BlockBusterFunction(ProjectArtifactManifestAdapter, "_locked")),
        ("版本管理锁（RLock）", BlockBusterFunction(_WatchedRLock, "acquire", can_block_predicate=rlock_nonblocking)),
    ]
    for label, function in rules:
        function.full_name = label
    return [function for _, function in rules]


# ---------------------------------------------------------------------- 清单


def load_lists(root: Path, *, regenerating: bool = False) -> _Lists:
    allowlist_path = root / ALLOWLIST_RELPATH
    entries: set[str] = set()
    if allowlist_path.exists():
        for raw in allowlist_path.read_text(encoding="utf-8").splitlines():
            entry = raw.split("#", 1)[0].strip()
            if entry:
                entries.add(entry)

    exemptions: dict[str, str] = {}
    exemptions_path = root / EXEMPTIONS_RELPATH
    if exemptions_path.exists():
        with exemptions_path.open("rb") as fh:
            data = tomllib.load(fh)
        for item in data.get("exemption", []):
            key, reason = item.get("key", ""), str(item.get("reason", "")).strip()
            if not key or not reason:
                raise pytest.UsageError(f"{EXEMPTIONS_RELPATH}: 每条豁免都要有 key 与 reason：{item!r}")
            exemptions[key] = reason
    # 写出模式会整体重写白名单，条目刚从白名单挪进豁免清单时允许暂时重叠。
    overlap = entries & exemptions.keys()
    if overlap and not regenerating:
        raise pytest.UsageError(f"{ALLOWLIST_RELPATH} 与 {EXEMPTIONS_RELPATH} 重复登记：{sorted(overlap)}")
    return _Lists(frozenset(entries), exemptions)


def render_allowlist(observed: dict[str, set[str]]) -> str:
    lines = [f"{key}  # {', '.join(sorted(calls))}" for key, calls in sorted(observed.items())]
    return ALLOWLIST_HEADER + "\n" + "".join(f"{line}\n" for line in lines)


# ---------------------------------------------------------------------- pytest 钩子

_GUARD_KEY = pytest.StashKey[BlockingGuard]()
_WORKER_WRITE_KEY = "blocking_guard_write"
_SESSION_KEY = pytest.StashKey["_SessionTotals"]()


@dataclass(slots=True)
class _SessionTotals:
    write_mode: bool = False
    triggered: set[str] = field(default_factory=set)
    observed: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    deselected: int = 0
    full_run: bool = False
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def _is_xdist_controller(config: pytest.Config) -> bool:
    return not _is_xdist_worker(config) and bool(getattr(config.option, "dist", "no") != "no")


def _selects_whole_suite(config: pytest.Config) -> bool:
    """命令行是否选中了整个 testpaths 且没有任何筛选。"""
    root = config.rootpath.resolve()
    targets = {root, *((root / p).resolve() for p in config.getini("testpaths"))}
    for arg in config.args:
        if "::" in arg:
            return False
        path = Path(arg)
        resolved = (path if path.is_absolute() else config.invocation_params.dir / path).resolve()
        if resolved not in targets:
            return False
    filters = ("keyword", "markexpr", "lf", "stepwise", "testmon", "deselect")
    return not any(getattr(config.option, name, None) for name in filters)


def pytest_configure(config: pytest.Config) -> None:
    # 写出模式只属于本次会话：读完即从环境里摘掉，免得用例另起的 pytest 子进程继承它；
    # xdist worker 改由 controller 经 workerinput 传入。
    write_mode = os.environ.pop(WRITE_ENV, None) == "1"
    if _is_xdist_worker(config):
        write_mode = bool(config.workerinput.get(_WORKER_WRITE_KEY))
    totals = _SessionTotals(write_mode=write_mode, full_run=_selects_whole_suite(config))
    config.stash[_SESSION_KEY] = totals
    if write_mode and not totals.full_run:
        raise pytest.UsageError(
            f"{WRITE_ENV}=1 只能用于完整运行（不带路径、-k、-m 等筛选）：部分运行会让白名单丢条目。"
        )
    if _is_xdist_controller(config):
        return
    guard = BlockingGuard(
        root=config.rootpath, write_mode=write_mode, lists=load_lists(config.rootpath, regenerating=write_mode)
    )
    guard.activate()
    config.stash[_GUARD_KEY] = guard


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    node.workerinput[_WORKER_WRITE_KEY] = node.config.stash[_SESSION_KEY].write_mode


def pytest_unconfigure(config: pytest.Config) -> None:
    guard = config.stash.get(_GUARD_KEY, None)
    if guard is not None:
        guard.deactivate()


def pytest_deselected(items: list[pytest.Item]) -> None:
    if items:
        guard = items[0].config.stash.get(_GUARD_KEY, None)
        if guard is not None:
            guard.deselected += len(items)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    guard = item.config.stash.get(_GUARD_KEY, None)
    if guard is None:
        return report
    unexpected = guard.settle()
    if unexpected:
        text = _describe(unexpected)
        if report.failed:
            report.sections.append(("blocking guard", text))
        else:
            report.outcome = "failed"
            report.longrepr = text
    return report


def _describe(violations: list[Violation]) -> str:
    grouped: dict[str, list[Violation]] = defaultdict(list)
    for violation in violations:
        grouped[violation.key].append(violation)
    lines = ["事件循环线程上发生了阻塞调用（不在临时白名单与永久豁免内）："]
    for key, items in grouped.items():
        calls = ", ".join(sorted({v.call for v in items}))
        lines.append(f"  {key}  ← {calls}  [线程 {items[0].thread}]")
        lines.extend(f"      {frame}" for frame in items[0].stack)
    lines.append(
        "把这段同步工作整体放进 asyncio.to_thread（写正式文件用 run_sync_transaction），"
        "或改用异步锁；见 docs/standards/backend.md「事件循环线程只做调度」。"
    )
    return "\n".join(lines)


def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    totals = config.stash[_SESSION_KEY]
    guard = config.stash.get(_GUARD_KEY, None)
    if guard is not None:
        guard.settle()
        totals.triggered |= guard.triggered
        for key, calls in guard.observed.items():
            totals.observed[key] |= calls
        totals.deselected += guard.deselected
    if _is_xdist_worker(config):
        config.workeroutput["blocking_guard"] = {
            "triggered": sorted(totals.triggered),
            "observed": {key: sorted(calls) for key, calls in totals.observed.items()},
            "deselected": totals.deselected,
        }
        return
    _finish(config, session, totals)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    payload = getattr(node, "workeroutput", {}).get("blocking_guard")
    if not payload:
        return
    totals = node.config.stash[_SESSION_KEY]
    totals.triggered |= set(payload["triggered"])
    for key, calls in payload["observed"].items():
        totals.observed[key] |= set(calls)
    totals.deselected += payload["deselected"]


def _finish(config: pytest.Config, session: pytest.Session, totals: _SessionTotals) -> None:
    complete = (
        totals.full_run
        and totals.deselected == 0
        and not session.shouldstop
        and not session.shouldfail
        and session.exitstatus not in (pytest.ExitCode.INTERRUPTED, pytest.ExitCode.USAGE_ERROR)
    )
    lists = load_lists(config.rootpath, regenerating=totals.write_mode)
    if totals.write_mode:
        if not complete:
            totals.warnings.append("本次运行不完整（有筛选、中途停止或被中断），未写出白名单。")
            return
        observed = {k: v for k, v in totals.observed.items() if k not in lists.exemptions}
        (config.rootpath / ALLOWLIST_RELPATH).write_text(render_allowlist(observed), encoding="utf-8")
        totals.notes.append(f"已写出 {ALLOWLIST_RELPATH}：{len(observed)} 个条目。")
        stale_exemptions = sorted(set(lists.exemptions) - totals.triggered)
        totals.warnings.extend(f"永久豁免未触发：{key}" for key in stale_exemptions)
        return
    if not complete:
        return
    stale = sorted(lists.allowlist - totals.triggered)
    stale_exemptions = sorted(set(lists.exemptions) - totals.triggered)
    totals.warnings.extend(f"临时白名单未触发：{key}" for key in stale)
    totals.warnings.extend(f"永久豁免未触发：{key}" for key in stale_exemptions)
    if stale:
        totals.notes.append("未触发的白名单条目可以删除；macOS 上的文件复制走另一套系统调用，以 Linux 上的结果为准。")


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    totals = config.stash.get(_SESSION_KEY, None)
    if totals is None or not (totals.warnings or totals.notes):
        return
    terminalreporter.write_sep("=", "blocking guard", yellow=bool(totals.warnings))
    for line in totals.warnings:
        terminalreporter.write_line(line, yellow=True)
    for line in totals.notes:
        terminalreporter.write_line(line)
