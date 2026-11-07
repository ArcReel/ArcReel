"""Test-time event-loop blocking guard: blocking lib/server calls on the event-loop thread fail cases.

Loaded by root conftest ``pytest_plugins`` throughout the session, covering synchronous and asynchronous
cases: synchronous ``TestClient`` application code runs on the anyio portal thread's own event loop and is checked too.

Detection uses blockbuster interception points (files, ``os``, sockets, sqlite, ``time.sleep``, contended
``threading.Lock``), adding four lock-wait categories: blocking portalocker file locks (project files,
synchronous generation admission, etc.), artifact-manifest polling waits, and version-management thread locks.
Nonblocking acquisition attempts are not violations.

**Record instead of raising.** blockbuster normally raises ``BlockingError`` at interception; broad business
exception handlers swallow it (estimation falls back to defaults; executors record task failures), hiding violations.
Replace each allow predicate with detect-record-allow: execute the original call and record violations against
the current case. On each phase report, violations outside the exemption list fail the case. No other exemption exists.

**Attribution.** Walk outward from the blocking point; the first lib/server async function identifies the violation
(file + qualified name), even through ``tests/`` frames: a substitute called by lib/server async code that reads/writes
disk under ``tests/`` would block at the same location with production dependencies. Replace substitute-only I/O
with memory implementations. Without any lib/server async frame, a ``tests/`` frame means test-driven blocking
(a direct test call to synchronous lib code), ignored; otherwise use the outermost lib/server frame for a synchronous
event-loop callback. Inline test ``to_thread`` runs on a worker thread and is inherently outside checks.

**Always allowed:** log writes, traceback source-line reads, ``realpath`` symlink resolution, delayed imports
inside functions, asyncpg connection-parameter parsing, coverage waiting for its own data lock, and bodies of
module-level unbounded caches (``functools.cache``) or zero-argument cached loaders. The last two run only once
per process; scheduling determines the first caller. Bounded argument-keyed caches can evict and reread and are not allowed.

**Stack preview.** Errors list lib/server/tests frames and retain, in actual call order, the innermost non-repository
frames (stdlib/third-party) between interception and the first repository frame. These identify non-repository code
triggered at a business await point as the blocker.

Permanent ``exemptions.toml`` accepts only one-time blocking on application startup/shutdown paths, each justified.
At the end of a full run (no selection/filtering), untriggered exemptions fail the session; the same applies to
filtered runs with ``--blocking-guard-expect-all-exemptions`` (CI enables this only in the unit job). Remove obsolete
entries whenever startup/shutdown paths change.

**Known limits.** Unbounded-cache exemptions cannot distinguish fixed key sets from input-growing sets; every new
key in the latter blocks once, requiring review under the rule that event-loop threads only schedule. Only executed
paths are checked: untested routes or routes whose tests replace the entire filesystem can still hide blocking.
When adding such tests, retain the real-I/O step in the substitute.
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import sysconfig
import threading
import tomllib
from collections import defaultdict
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

import blockbuster
import pytest
from blockbuster import BlockBuster, BlockBusterFunction

EXEMPTIONS_RELPATH = "tests/blocking_guard/exemptions.toml"
EXPECT_ALL_EXEMPTIONS_OPTION = "--blocking-guard-expect-all-exemptions"
SCANNED_DIRS = ("lib", "server")
TESTS_DIR = "tests"

_ASYNC_CODE_FLAGS = inspect.CO_COROUTINE | inspect.CO_ITERABLE_COROUTINE | inspect.CO_ASYNC_GENERATOR
_GUARD_DIR = str(Path(__file__).resolve().parent) + os.sep
_GUARD_MODULE = __name__
_LOGGING_DIR = str(Path(logging.__file__).resolve().parent) + os.sep
_BLOCKBUSTER_DIR = str(Path(blockbuster.__file__).resolve().parent) + os.sep
# Remove these prefixes from non-repository stack-preview frames, leaving package/stdlib-relative paths.
_STDLIB_DIRS = tuple(
    sorted(
        {
            str(path) + os.sep
            for name in ("stdlib", "platstdlib")
            for path in (Path(sysconfig.get_paths()[name]), Path(sysconfig.get_paths()[name]).resolve())
        },
        key=len,
        reverse=True,
    )
)

# Frames always allowed on any stack, unrelated to business blocking: log writes, traceback source reads, delayed imports.
# realpath uses lstat per component (outside interception), then readlink only for symlinks: macOS temporary directories
# live under /var -> /private/var, unlike Linux. Without this exemption identical code violates only on macOS.
# asyncpg probes ~/.pgpass and certificate files under ~/.postgresql on the event loop once per new connection
# while parsing connection parameters. Violations attach to whichever caller creates a pooled connection, vary with order,
# and occur only on PostgreSQL, making them invisible in SQLite runs.
# On each entry/resumption of a tested frame, coverage's tracer acquires its own data lock through lock_data (only with
# thread in concurrency). The event-loop thread waits when a worker tracer holds it. Depending on thread interleaving,
# violations attach to arbitrary tested coroutines, often the generation-queue worker loop resuming at await. This lock
# exists only with coverage, never in production. Exempt only this lock_data path; lock.acquire without lock_data in its
# stack remains a violation. The non-repository frame acquiring it appears in the stack preview.
_ALWAYS_ALLOWED_FRAMES: tuple[tuple[str, frozenset[str] | None], ...] = (
    ("posixpath.py", frozenset({"realpath", "_joinrealpath"})),
    ("<frozen posixpath>", frozenset({"realpath", "_joinrealpath"})),
    ("linecache.py", frozenset({"getlines", "updatecache", "checkcache", "lazycache"})),
    ("<frozen importlib._bootstrap>", None),
    ("<frozen importlib._bootstrap_external>", None),
    ("asyncpg/connect_utils.py", frozenset({"_parse_connect_arguments"})),
    ("coverage/collector.py", frozenset({"lock_data"})),
)

# Independent preview budgets: at most this many repository frames (lib/server/tests) show the caller; separately retain
# the innermost non-repository frames before the first repository frame to show the blocker, noting omitted counts.
_REPO_PREVIEW_FRAMES = 6
_NON_REPO_PREVIEW_FRAMES = 4


@dataclass(frozen=True, slots=True)
class Violation:
    """One intercepted blocking call."""

    key: str
    call: str
    thread: str
    stack: tuple[str, ...]


@dataclass(slots=True)
class BlockingGuard:
    """Session state: interception points, unsettled violations, and triggered exemptions."""

    root: Path
    exemptions: dict[str, str]
    scanned_prefixes: tuple[str, ...] = ()
    tests_prefix: str = ""
    pending: list[Violation] = field(default_factory=list)
    triggered: set[str] = field(default_factory=set)
    deselected: int = 0
    _blockbuster: BlockBuster | None = None
    _extra_functions: list[BlockBusterFunction] = field(default_factory=list)
    _restore: list[Callable[[], None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.scanned_prefixes = tuple(str(self.root / name) + os.sep for name in SCANNED_DIRS)
        self.tests_prefix = str(self.root / TESTS_DIR) + os.sep

    # ------------------------------------------------------------------ Startup/shutdown

    def activate(self) -> None:
        blockbuster = BlockBuster()
        # abspath only normalizes strings for absolute paths; only relative paths read cwd.
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

    # ------------------------------------------------------------------ Recording

    def _recording_predicate(self, function: BlockBusterFunction) -> Callable[..., bool]:
        allowed = function.can_block_predicate
        allowed_frames = function.can_block_functions
        call = function.full_name

        def predicate(*args: Any, **kwargs: Any) -> bool:
            # blockbuster reaches this only when the current thread has a running event loop and suppresses nested interception
            # during the predicate; returning True permits the original call, so only detect and record here, never raise.
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
        driven_by_tests = False
        for candidate in frames:
            filename = candidate.f_code.co_filename
            if _is_guard_frame(candidate):
                continue
            if self._is_tests_frame(candidate):
                # Substitute/helper frames do not stop the search: an outer lib/server async function would block at the same location
                # with production dependencies and remains responsible.
                driven_by_tests = True
                continue
            if filename.startswith(self.scanned_prefixes):
                if candidate.f_code.co_flags & _ASYNC_CODE_FLAGS:
                    key = self._key(candidate)
                    break
                outermost_scanned = candidate
        if key is None:
            # No lib/server async function encloses the blocking point: a tests/ frame means test-driven blocking
            # (the test calls synchronous lib code directly), ignored; otherwise use the outermost synchronous event-loop callback.
            if outermost_scanned is None or driven_by_tests:
                return
            key = self._key(outermost_scanned)

        self.pending.append(Violation(key, call, threading.current_thread().name, self._stack_preview(frames)))

    def _stack_preview(self, frames: list[FrameType]) -> tuple[str, ...]:
        """Inside-out preview: innermost non-repository frames before the first repository frame, then repository frames."""
        non_repo: list[FrameType] = []
        repo: list[FrameType] = []
        for frame in frames:
            if _is_guard_frame(frame) or frame.f_code.co_filename.startswith(_BLOCKBUSTER_DIR):
                continue
            if frame.f_code.co_filename.startswith(self.scanned_prefixes) or self._is_tests_frame(frame):
                repo.append(frame)
            elif not repo:
                non_repo.append(frame)
        lines = [self._describe_frame(f) for f in non_repo[:_NON_REPO_PREVIEW_FRAMES]]
        if len(non_repo) > _NON_REPO_PREVIEW_FRAMES:
            lines.append(f"…（省略 {len(non_repo) - _NON_REPO_PREVIEW_FRAMES} 个非仓库帧）")
        lines.extend(self._describe_frame(f) for f in repo[:_REPO_PREVIEW_FRAMES])
        return tuple(lines)

    def _describe_frame(self, frame: FrameType) -> str:
        return f"{self._display_path(frame.f_code.co_filename)}:{frame.f_lineno} in {frame.f_code.co_qualname}"

    def _display_path(self, filename: str) -> str:
        normalized = filename.replace(os.sep, "/")
        _, marker, inside = normalized.rpartition("/site-packages/")
        if marker:
            return inside
        for stdlib_dir in _STDLIB_DIRS:
            if filename.startswith(stdlib_dir):
                return filename.removeprefix(stdlib_dir).replace(os.sep, "/")
        return self._relpath(filename)

    def _is_tests_frame(self, frame: FrameType) -> bool:
        # Module-name fallback: pytest assertion-rewrite caches retain co_filename, so cross-machine cache reuse breaks paths.
        return frame.f_code.co_filename.startswith(self.tests_prefix) or frame.f_globals.get("__name__", "").startswith(
            f"{TESTS_DIR}."
        )

    def _relpath(self, filename: str) -> str:
        return filename.removeprefix(str(self.root) + os.sep).replace(os.sep, "/")

    def _key(self, frame: FrameType) -> str:
        return f"{self._relpath(frame.f_code.co_filename)}::{frame.f_code.co_qualname}"

    # ------------------------------------------------------------------ Settlement

    def settle(self) -> list[Violation]:
        """Drain pending violations, register triggered exemptions, and return nonexempt violations."""
        drained, self.pending[:] = list(self.pending), []
        unexpected = []
        for violation in drained:
            if violation.key in self.exemptions:
                self.triggered.add(violation.key)
            else:
                unexpected.append(violation)
        return unexpected


def _is_guard_frame(frame: FrameType) -> bool:
    return frame.f_code.co_filename.startswith(_GUARD_DIR) or frame.f_globals.get("__name__", "").startswith(
        _GUARD_MODULE
    )


def _runs_once_per_process(frame: FrameType) -> bool:
    """Whether the frame is a module-level cache-loader body executed once per argument set per process.

    These loaders (bundled schemas/rule tables) resemble delayed imports; xdist scheduling determines
    the first caller. Without exemption, violations appear or disappear with execution order. Accept only
    unbounded ``functools.cache`` (``lru_cache(maxsize=None)``) and zero-argument functions:
    bounded argument-keyed caches can evict and reread, and every reread is real blocking.
    """
    code = frame.f_code
    candidate = frame.f_globals.get(code.co_name)
    if candidate is None or not hasattr(candidate, "cache_info"):
        return False
    if getattr(getattr(candidate, "__wrapped__", None), "__code__", None) is not code:
        return False
    takes_no_arguments = not (
        code.co_argcount or code.co_kwonlyargcount or code.co_flags & (inspect.CO_VARARGS | inspect.CO_VARKEYWORDS)
    )
    return candidate.cache_info().maxsize is None or takes_no_arguments


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


# ---------------------------------------------------------------------- Lock rules


class _WatchedRLock:
    """Wrap version-management ``RLock`` so ``with`` acquisition passes through interceptable Python ``acquire``.

    C ``RLock.__enter__`` bypasses the ``acquire`` attribute; blockbuster lock rules do not cover RLock either.
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
    """Blocking waits for project-file, generation-admission, artifact-manifest, and version-management locks."""
    import portalocker
    import portalocker.portalocker as portalocker_impl

    import lib.artifacts.version_manager as version_manager
    from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter

    def portalocker_nonblocking(_file: object, flags: int, *_: object, **__: object) -> bool:
        return bool(flags & portalocker.LOCK_NB)

    def rlock_nonblocking(_lock: object, blocking: bool = True, timeout: float = -1) -> bool:
        return not blocking or timeout == 0

    # Version locks cache RLock by versions.json path; wrap at the factory while instances still share the same lock.
    original_factory = version_manager._get_versions_file_lock

    watched: dict[int, _WatchedRLock] = {}

    def watched_factory(versions_file: Path) -> Any:
        # The original factory caches paths forever, keeping lock ids stable; reuse wrappers per lock to preserve one lock per file.
        lock = original_factory(versions_file)
        return watched.setdefault(id(lock), _WatchedRLock(lock))

    version_manager._get_versions_file_lock = watched_factory
    restore.append(lambda: setattr(version_manager, "_get_versions_file_lock", original_factory))

    rules = [
        # Project-file locks, ProjectManager.file_lock, and synchronous admission locks reach flock through blocking portalocker.
        (
            "文件锁（portalocker 阻塞模式）",
            BlockBusterFunction(portalocker, "lock", can_block_predicate=portalocker_nonblocking),
        ),
        (
            "文件锁（portalocker 阻塞模式）",
            BlockBusterFunction(portalocker_impl, "lock", can_block_predicate=portalocker_nonblocking),
        ),
        # Artifact-manifest locks poll nonblocking attempts with time.sleep for at most 10 seconds; entering is a lock wait.
        ("产物清单锁（轮询等待）", BlockBusterFunction(ProjectArtifactManifestAdapter, "_locked")),
        ("版本管理锁（RLock）", BlockBusterFunction(_WatchedRLock, "acquire", can_block_predicate=rlock_nonblocking)),
    ]
    for label, function in rules:
        function.full_name = label
    return [function for _, function in rules]


# ---------------------------------------------------------------------- Manifest


def load_exemptions(root: Path) -> dict[str, str]:
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
    return exemptions


# ---------------------------------------------------------------------- pytest hooks

_GUARD_KEY = pytest.StashKey[BlockingGuard]()
_SESSION_KEY = pytest.StashKey["_SessionTotals"]()


@dataclass(slots=True)
class _SessionTotals:
    triggered: set[str] = field(default_factory=set)
    deselected: int = 0
    full_run: bool = False
    expect_all: bool = False
    errors: list[str] = field(default_factory=list)


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def _is_xdist_controller(config: pytest.Config) -> bool:
    return not _is_xdist_worker(config) and bool(getattr(config.option, "dist", "no") != "no")


def _executes_tests(config: pytest.Config) -> bool:
    # --collect-only/--setup-only/--setup-plan execute no cases and cannot record violations.
    return not any(getattr(config.option, name, None) for name in ("collectonly", "setuponly", "setupplan"))


def _selects_whole_suite(config: pytest.Config) -> bool:
    """Whether command-line selection covers all testpaths without any filtering."""
    root = config.rootpath.resolve()
    targets = {root, *((root / p).resolve() for p in config.getini("testpaths"))}
    for arg in config.args:
        if "::" in arg:
            return False
        path = Path(arg)
        resolved = (path if path.is_absolute() else config.invocation_params.dir / path).resolve()
        if resolved not in targets:
            return False
    # --ignore/--ignore-glob skip collection paths without changing config.args or passing through pytest_deselected.
    filters = ("keyword", "markexpr", "lf", "stepwise", "testmon", "deselect", "ignore", "ignore_glob")
    return not any(getattr(config.option, name, None) for name in filters)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        EXPECT_ALL_EXEMPTIONS_OPTION,
        action="store_true",
        default=False,
        help="本次运行应触发永久豁免清单的每一条：不论路径或关键字筛选，结束时未触发的豁免都让会话失败。",
    )


def pytest_configure(config: pytest.Config) -> None:
    executes = _executes_tests(config)
    config.stash[_SESSION_KEY] = _SessionTotals(
        full_run=executes and _selects_whole_suite(config),
        expect_all=executes and bool(config.getoption(EXPECT_ALL_EXEMPTIONS_OPTION)),
    )
    if _is_xdist_controller(config):
        return
    guard = BlockingGuard(root=config.rootpath, exemptions=load_exemptions(config.rootpath))
    guard.activate()
    config.stash[_GUARD_KEY] = guard


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
    lines = ["事件循环线程上发生了阻塞调用（不在永久豁免清单内）："]
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
        totals.deselected += guard.deselected
    if _is_xdist_worker(config):
        config.workeroutput["blocking_guard"] = {"triggered": sorted(totals.triggered), "deselected": totals.deselected}
        return
    _finish(config, session, totals)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    payload = getattr(node, "workeroutput", {}).get("blocking_guard")
    if not payload:
        return
    totals = node.config.stash[_SESSION_KEY]
    totals.triggered |= set(payload["triggered"])
    totals.deselected += payload["deselected"]


def _finish(config: pytest.Config, session: pytest.Session, totals: _SessionTotals) -> None:
    # An explicit option declares that these cases must trigger every exemption; deselected cases do not affect that decision.
    complete = (
        (totals.expect_all or (totals.full_run and totals.deselected == 0))
        and not session.shouldstop
        and not session.shouldfail
        and session.exitstatus not in (pytest.ExitCode.INTERRUPTED, pytest.ExitCode.USAGE_ERROR)
    )
    if not complete:
        return
    stale = sorted(set(load_exemptions(config.rootpath)) - totals.triggered)
    if stale:
        totals.errors.extend(f"永久豁免未触发，请从 {EXEMPTIONS_RELPATH} 删除：{key}" for key in stale)
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    totals = config.stash.get(_SESSION_KEY, None)
    if totals is None or not totals.errors:
        return
    terminalreporter.write_sep("=", "blocking guard", red=True)
    for line in totals.errors:
        terminalreporter.write_line(line, red=True)
