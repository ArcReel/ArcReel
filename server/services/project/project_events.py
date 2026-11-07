"""
Project data change detection and SSE fanout for workspace realtime updates.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib import PROJECT_ROOT
from lib.infra.content_digest import FileIdentity, file_identity
from lib.project.project_change_hints import (
    ProjectChangeBatch,
    ProjectChangeSource,
    register_project_change_batch_listener,
    register_project_change_listener,
)
from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import DOC_TYPE_TO_QUARANTINE_KIND, quarantine_filename
from server.services.project.project_state_projection import (
    ProjectSnapshot,
    ProjectState,
    build_snapshot,
    diff_snapshots,
    draft_state_key,
)
from server.sse_channel import IDLE, DropSubscriber, SseChannel

logger = logging.getLogger(__name__)

PROJECT_EVENTS_POLL_SECONDS = 0.5

# Termination event broadcast after project-directory deletion; the stream then ends normally (see stream_events._iter).
PROJECT_DELETED_EVENT = "project_deleted"
# Project Agent sessions return to running when the CLI autonomously starts a turn (wake-up after background task completion);
# open session panels receive it through resident entry streams; session lists and other views learn status changes here.
ASSISTANT_SESSION_RESUMED_EVENT = "assistant_session_resumed"

#: Disk-read entry point for current project state; called in a thread pool. Raise ``FileNotFoundError`` if the project directory is absent.
ProjectStateReader = Callable[[str], ProjectState]


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _draft_paths(project_path: Path) -> list[tuple[str, Path]]:
    """Potential draft files (state key → path): list every episode/source; callers determine existence.

    Handle only directories named ``episode_<数字>`` under ``drafts/``. Preserve actual on-disk directory
    names (do not normalize ``episode_007`` to ``episode_7``); only state-key episode numbers become integers."""
    drafts_root = project_path / "drafts"
    paths: list[tuple[str, Path]] = []
    if not drafts_root.is_dir():
        return paths
    for episode_dir in sorted(drafts_root.glob("episode_*")):
        suffix = episode_dir.name.removeprefix("episode_")
        if not suffix.isdigit() or not episode_dir.is_dir():
            continue
        episode = int(suffix)
        paths.extend(
            (draft_state_key(episode, kind), episode_dir / quarantine_filename(kind))
            for kind in DOC_TYPE_TO_QUARANTINE_KIND.values()
        )
    return paths


def _read_draft_digests(project_path: Path) -> dict[str, str]:
    """Content digests of present drafts; malformed envelopes still count as present and are compared by byte digest."""
    digests: dict[str, str] = {}
    for key, path in _draft_paths(project_path):
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            continue
        digests[key] = hashlib.sha256(payload).hexdigest()
    return digests


def read_project_state(pm: ProjectManager, project_name: str) -> ProjectState:
    """Load project state read-only: do not persist script migrations or synchronize episode indexes; skip unparseable scripts."""
    project_path = pm.get_project_path(project_name)
    scripts_dir = project_path / "scripts"
    project = pm.load_project(project_name)
    scripts: dict[str, dict[str, Any]] = {}
    if scripts_dir.exists():
        for script_path in sorted(scripts_dir.glob("*.json")):
            try:
                scripts[script_path.name] = pm.load_script_readonly(project_name, script_path.name)
            except Exception:
                logger.warning("跳过无法解析的剧本快照 project=%s file=%s", project_name, script_path.name)
    return ProjectState(project=project, scripts=scripts, drafts=_read_draft_digests(project_path))


#: Stat identities for all project-state input files: (project-relative path, (device, inode, size, nanosecond mtime)), sorted by path;
#: exclude absent files.
ProjectInputStamp = tuple[tuple[str, FileIdentity], ...]


def _file_stat(path: Path) -> FileIdentity | None:
    try:
        return file_identity(path.stat())
    except FileNotFoundError:
        return None


def project_input_stamp(pm: ProjectManager, project_name: str) -> ProjectInputStamp:
    """Stat identities of all inputs read by :func:`read_project_state`: ``project.json``, ``scripts/*.json``,
    and present drafts. Only stat, without reading content; raise ``FileNotFoundError`` if the project directory is absent.

    Unchanged stamps mean unchanged inputs, letting polling skip parsing/reconstruction. Add new inputs
    here whenever the reader gains input files, or polling will miss their changes."""
    project_path = pm.get_project_path(project_name)
    candidates = [(ProjectManager.PROJECT_FILE, project_path / ProjectManager.PROJECT_FILE)]
    scripts_dir = project_path / "scripts"
    if scripts_dir.exists():
        candidates.extend((f"scripts/{path.name}", path) for path in sorted(scripts_dir.glob("*.json")))
    candidates.extend((f"drafts:{key}", path) for key, path in _draft_paths(project_path))
    stamp: list[tuple[str, FileIdentity]] = []
    for label, path in candidates:
        identity = _file_stat(path)
        if identity is not None:
            stamp.append((label, identity))
    return tuple(stamp)


# Publisher and snapshot-diff action names differ for one event: reference-to-video task completion maps task_type to
# ``reference_video_ready`` in publishers (generation_tasks._SKELETON_DRIVEN_TASK_ACTIONS), while snapshot
# diffs use ``video_ready``. Both sides share entity_type/entity_id;
# only action differs, so without normalization one completion broadcasts twice.
_EQUIVALENT_ACTIONS: dict[str, str] = {"reference_video_ready": "video_ready"}


def _change_identity(change: dict[str, Any]) -> tuple[Any, Any, Any]:
    """Change deduplication identity.

    Use only entity_type/action/entity_id: publishers attach script_file and episode selectively by task_type,
    so including them would split one event when one side is None. Normalize action with
    ``_EQUIVALENT_ACTIONS`` to reconcile publisher and snapshot-diff names for the same event."""
    action = change.get("action")
    if isinstance(action, str):
        action = _EQUIVALENT_ACTIONS.get(action, action)
    return (change.get("entity_type"), action, change.get("entity_id"))


@dataclass
class _ProjectChannel:
    sse: SseChannel
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    scan_now: asyncio.Event = field(default_factory=asyncio.Event)
    # Serialize disk read → baseline settlement → broadcast as one read-modify-write. Concurrent polling and explicit rebuilds
    # can read at different speeds, letting an older reader settle later, overwrite baseline with a stale snapshot, and emit reverse changes
    # (existing entities reported deleted). Never reset this lock: rebuild tasks may retain it across watch-task restarts.
    rebuild_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Change identities described by explicit batches published but not yet broadcast. Rebuild reads include artifacts persisted by
    # other batches; sweeps must leave them to their own batches, or one event broadcasts first in a sweep and again in its batch.
    inflight_batch_identities: Counter[tuple[Any, Any, Any]] = field(default_factory=Counter)
    pending_sources: set[ProjectChangeSource] = field(default_factory=set)
    task: asyncio.Task | None = None
    snapshot: ProjectSnapshot | None = None
    # Input stat stamp before the ``snapshot`` read, updated together with ``snapshot``; polling skips reconstruction when unchanged.
    input_stamp: ProjectInputStamp | None = None


class ProjectEventService:
    def __init__(
        self,
        project_root: Path | None = None,
        *,
        data_root: Path | None = None,
        poll_interval: float = PROJECT_EVENTS_POLL_SECONDS,
        read_state: ProjectStateReader | None = None,
    ):
        """``read_state`` defaults to :func:`read_project_state` (read-only loading through this service's ``pm``)."""
        self.project_root = Path(project_root or PROJECT_ROOT)
        # Prefer explicit ``data_root`` (production passes the configured root);
        # otherwise use the default (repository ``projects/``) for test-fixture compatibility.
        resolved_data_root = (
            Path(data_root).resolve(strict=False) if data_root is not None else self.project_root / "projects"
        )
        self.pm = ProjectManager(resolved_data_root)
        self._read_state: ProjectStateReader = read_state or (lambda name: read_project_state(self.pm, name))
        self.poll_interval = max(0.1, float(poll_interval))
        self._channels: dict[str, _ProjectChannel] = {}
        self._listener_unregister = None
        self._batch_listener_unregister = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_batch_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        if self._listener_unregister is not None or self._batch_listener_unregister is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._listener_unregister = register_project_change_listener(self._on_hint)
        self._batch_listener_unregister = register_project_change_batch_listener(self._on_batch_hint)

    async def shutdown(self) -> None:
        unregister = self._listener_unregister
        self._listener_unregister = None
        if unregister is not None:
            unregister()
        batch_unregister = self._batch_listener_unregister
        self._batch_listener_unregister = None
        if batch_unregister is not None:
            batch_unregister()

        tasks = [channel.task for channel in self._channels.values() if channel.task is not None]
        tasks.extend(self._pending_batch_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending_batch_tasks.clear()
        self._channels.clear()
        self._loop = None

    def _create_channel(self, project_name: str) -> _ProjectChannel:
        """Construct a project channel: remove subscribers on overflow; first/last subscriber hooks start/stop background scanning."""
        sse = SseChannel(
            overflow=DropSubscriber(
                on_removed=lambda count: logger.warning(
                    "项目事件订阅队列溢出，移除 %s 个订阅者 project=%s",
                    count,
                    project_name,
                ),
            ),
            on_first_subscriber=lambda: self._start_watch(project_name),
            on_last_subscriber=lambda: self._stop_watch(project_name),
        )
        return _ProjectChannel(sse=sse)

    def _start_watch(self, project_name: str) -> None:
        """First-subscriber hook: start background scanning, or restart a task that exited by itself.

        If overflow removes the last subscriber, the watch task exits through ``while has_subscribers`` while
        the channel remains registered, so restart when the task is not running, not only on first subscription."""
        channel = self._channels.get(project_name)
        if channel is None:
            return
        if channel.task is not None and not channel.task.done():
            return
        channel.ready_event = asyncio.Event()
        channel.scan_now = asyncio.Event()
        channel.pending_sources.clear()
        channel.task = asyncio.create_task(
            self._watch_project(project_name, channel),
            name=f"project-events-{project_name}",
        )

    async def _stop_watch(self, project_name: str) -> None:
        """Last-subscriber hook: stop background scanning and unregister the channel.

        Remove the channel from the registry before awaiting watch-task exit. No yield occurs between removal
        and retrieval, so it is the current channel. During teardown yields, concurrent new subscribers cannot
        get this dying channel; they register an independent new one that teardown will not accidentally remove."""
        channel = self._channels.pop(project_name, None)
        if channel is None:
            return
        task = channel.task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _subscribe(self, project_name: str) -> tuple[SseChannel, asyncio.Queue, dict[str, Any]]:
        """Register a queue for *project_name* and return it with the initial snapshot.

        Private: the only consumer is :meth:`stream_events`, which owns the
        deterministic unsubscribe via its context-manager ``__aexit__``.
        """
        await asyncio.to_thread(self.pm.get_project_path, project_name)
        channel = self._channels.get(project_name)
        if channel is None:
            channel = self._create_channel(project_name)
            self._channels[project_name] = channel

        # Register queues before initial scanning (the first-subscriber hook runs after registration), or miss events
        # broadcast between scan completion and registration.
        queue = channel.sse.subscribe()

        try:
            await channel.ready_event.wait()
        except BaseException:
            # Client disconnect during initial scanning cancels here before _subscribe returns queue,
            # so stream_events cannot enter try/finally. Synchronously remove the new subscriber (idle-project
            # watch tasks do not self-heal), without await to avoid cancellation reentry; bypass the async last-subscriber hook
            # and handle teardown ourselves.
            if channel.sse.unsubscribe_nowait(queue) and channel.task is not None:
                channel.task.cancel()
                self._channels.pop(project_name, None)
            raise
        return channel.sse, queue, self._build_snapshot_payload(project_name, channel)

    async def _unsubscribe(self, project_name: str, queue: asyncio.Queue) -> None:
        """Remove a queue; the last-subscriber hook stops the watch task."""
        channel = self._channels.get(project_name)
        if channel is None:
            return
        await channel.sse.unsubscribe(queue)

    @contextlib.asynccontextmanager
    async def stream_events(
        self, project_name: str, *, idle_timeout: float = 1.0
    ) -> AsyncGenerator[AsyncIterator[tuple[str, Any] | dict[str, Any]]]:
        """Subscribe to a project's events as a self-cleaning async iterator.

        Yields an async iterator producing, in order:

        - a ``("snapshot", payload)`` tuple as the first event (initial state),
        - live ``(event_name, payload)`` tuples as changes are broadcast,
        - a ``{"type": "_idle"}`` sentinel whenever *idle_timeout* elapses with no
          event (consumers poll disconnect on it).

        When the subscriber's queue fills, :class:`DropSubscriber` removes it and
        the iterator ends; the client reconnects and the fresh snapshot realigns
        it. Subscription and unsubscribe live behind this seam; cleanup is
        carried by ``__aexit__`` (see ADR-0005). Consume as
        ``async with stream_events(...) as stream: async for item in stream``.
        """
        sse, queue, snapshot = await self._subscribe(project_name)

        async def _iter() -> AsyncIterator[tuple[str, Any] | dict[str, Any]]:
            # NOTE: intentionally NO ``finally: _unsubscribe`` here — cleanup is owned
            # by the enclosing context manager's __aexit__ (ADR-0005). Do not add one.
            yield ("snapshot", snapshot)
            async for item in sse.iterate(queue, idle_timeout=idle_timeout):
                yield {"type": "_idle"} if item is IDLE else item
                # Project deleted: end normally after termination, without waiting for another broadcast or idle heartbeat.
                if isinstance(item, tuple) and item[0] == PROJECT_DELETED_EVENT:
                    return

        try:
            yield _iter()
        finally:
            await self._unsubscribe(project_name, queue)

    def publish_assistant_session_resumed(self, project_name: str, session_id: str) -> None:
        """Broadcast a session returning to running to project subscribers; discard without subscribers. Must run on the event-loop thread."""
        channel = self._channels.get(project_name)
        if channel is None:
            return
        payload = {"project_name": project_name, "session_id": session_id, "status": "running"}
        channel.sse.broadcast((ASSISTANT_SESSION_RESUMED_EVENT, payload))

    def _on_hint(
        self,
        project_name: str,
        source: ProjectChangeSource,
        changed_paths: tuple[str, ...],
    ) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self._apply_hint,
            project_name,
            source,
            changed_paths,
        )

    def _on_batch_hint(
        self,
        project_name: str,
        source: ProjectChangeSource,
        changes: tuple[ProjectChangeBatch, ...],
    ) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self._apply_emitted_batch,
            project_name,
            source,
            changes,
        )

    def _apply_hint(
        self,
        project_name: str,
        source: ProjectChangeSource,
        changed_paths: tuple[str, ...],
    ) -> None:
        channel = self._channels.get(project_name)
        if channel is None:
            return
        channel.pending_sources.add(source)
        channel.scan_now.set()
        logger.debug(
            "项目变更 hint project=%s source=%s paths=%s",
            project_name,
            source,
            changed_paths,
        )

    def _apply_emitted_batch(
        self,
        project_name: str,
        source: ProjectChangeSource,
        changes: tuple[ProjectChangeBatch, ...],
    ) -> None:
        channel = self._channels.get(project_name)
        if channel is None or not changes:
            return

        channel.scan_now.clear()

        # Register before this batch rebuild reads disk, letting other batches' sweeps reserve these identities.
        identities = Counter(_change_identity(change) for change in changes)
        channel.inflight_batch_identities.update(identities)

        # Offload file I/O to the thread pool; retain state updates and broadcasts in the event loop.
        task = asyncio.create_task(
            self._async_rebuild_and_broadcast(project_name, channel, source, changes, identities),
            name=f"batch-rebuild-{project_name}",
        )
        self._pending_batch_tasks.add(task)
        task.add_done_callback(self._pending_batch_tasks.discard)

    async def _async_rebuild_and_broadcast(
        self,
        project_name: str,
        channel: _ProjectChannel,
        source: ProjectChangeSource,
        changes: tuple[ProjectChangeBatch, ...],
        identities: Counter[tuple[Any, Any, Any]],
    ) -> None:
        """Run file I/O in a thread, with state updates and broadcasts on the event-loop thread."""
        try:
            async with channel.rebuild_lock:
                # Swap out the whole registered source-marker set before reading: hints arriving during I/O enter a new set, join this settlement,
                # and remain for the next scan. Subtracting the old set after reading would also remove repeated arrivals from the same source
                # within the window, making _resolve_batch_source mislabel the new change as filesystem on the next scan.
                covered_sources = channel.pending_sources
                channel.pending_sources = set()
                try:
                    stamp, snapshot = await asyncio.to_thread(self._rebuild_snapshot, project_name)
                except FileNotFoundError:
                    channel.pending_sources |= covered_sources
                    await self._handle_scan_file_not_found(
                        project_name, channel, log_message="构建显式项目事件快照失败 project=%s"
                    )
                    return
                except Exception:
                    channel.pending_sources |= covered_sources
                    logger.exception("构建显式项目事件快照失败 project=%s", project_name)
                    return

                # The following runs on the event-loop thread and is thread-safe.
                previous = channel.snapshot
                channel.snapshot = snapshot
                channel.input_stamp = stamp

                self._broadcast_changes(
                    project_name,
                    channel,
                    fingerprint=snapshot.fingerprint,
                    source=source,
                    changes=[dict(change) for change in changes],
                )

                swept = self._sweep_uncovered_changes(previous, snapshot, channel.inflight_batch_identities)
                if swept:
                    # Sweep changes do not belong to this batch; resolve their own source hints as polling does.
                    # Reusing this batch's source would mark WebUI edits as worker (triggering frontend notifications and automatic
                    # navigation), or conversely suppress worker-artifact notifications.
                    self._broadcast_changes(
                        project_name,
                        channel,
                        fingerprint=snapshot.fingerprint,
                        source=self._settled_source(channel, covered_sources),
                        changes=swept,
                    )
        finally:
            channel.inflight_batch_identities -= identities

    def _broadcast_changes(
        self,
        project_name: str,
        channel: _ProjectChannel,
        *,
        fingerprint: str,
        source: ProjectChangeSource,
        changes: list[dict[str, Any]],
    ) -> None:
        """Broadcast a change batch to subscribers (poll scanning and explicit reconstruction share one payload shape)."""
        payload = {
            "project_name": project_name,
            "batch_id": uuid.uuid4().hex,
            "fingerprint": fingerprint,
            "generated_at": _utc_now_iso(),
            "source": source,
            "changes": changes,
        }
        channel.sse.broadcast(("changes", payload))

    def _sweep_uncovered_changes(
        self,
        previous: ProjectSnapshot | None,
        snapshot: ProjectSnapshot,
        covered: Counter[tuple[Any, Any, Any]],
    ) -> list[dict[str, Any]]:
        """Changes added by the new snapshot relative to baseline, not described by any in-flight explicit batch.

        Reconstruction reads include any persisted changes, while this batch's changes describe only its
        publisher's own event. The new snapshot unconditionally replaces the baseline. If incidental changes
        are not broadcast now, later scans have no difference from baseline and no mechanism remains to
        send them; diff against the baseline here to broadcast uncovered changes.

        ``covered`` registers in-flight batch identities (including this batch); reserve them for their own
        broadcasts to avoid sending once in the sweep and again in their batch. A residual window remains:
        if artifacts are persisted but their emit call occurs after this read returns, their identity is not yet
        registered, so one duplicate remains possible."""
        if previous is None:
            return []
        return [change for change in diff_snapshots(previous, snapshot) if _change_identity(change) not in covered]

    def _rebuild_snapshot(self, project_name: str) -> tuple[ProjectInputStamp, ProjectSnapshot]:
        """Synchronous method (thread pool): load project state read-only and normalize to a snapshot, with its pre-read input stamp.

        Take the stamp before reading: changes persisted during reading only make the next stamp comparison
        report changed, causing one extra rebuild, never misrecording newer disk state under an old stamp."""
        stamp = project_input_stamp(self.pm, project_name)
        return stamp, build_snapshot(self._read_state(project_name))

    def _poll_snapshot(
        self, project_name: str, known_stamp: ProjectInputStamp | None
    ) -> tuple[ProjectInputStamp, ProjectSnapshot | None]:
        """Synchronous polling rebuild (thread pool): skip when inputs match ``known_stamp``, returning ``None`` for the snapshot."""
        stamp = project_input_stamp(self.pm, project_name)
        if stamp == known_stamp:
            return stamp, None
        return stamp, build_snapshot(self._read_state(project_name))

    def _project_directory_gone(self, project_name: str) -> bool:
        """Determine whether the project directory really is currently absent (``get_project_path`` semantics).

        After scanning/hint reconstruction catches ``FileNotFoundError``, independently recheck current state
        to distinguish missing deeper files such as project.json with the directory still present. For those,
        preserve current state and log ERROR through generic fallback, without termination. Recheck rather
        than testing once at scan start because directory deletion (e.g. ``shutil.rmtree``) is non-atomic:
        scanning may hit ``FileNotFoundError`` at any intermediate stage (e.g. project.json removed before
        the directory), making an initial check falsely report not deleted. Rechecking reflects reality after the exception."""
        try:
            self.pm.get_project_path(project_name)
        except FileNotFoundError:
            return True
        return False

    def _handle_project_deleted(self, project_name: str, channel: _ProjectChannel) -> None:
        """Project directory deleted: terminate the channel by broadcasting termination, unregistering, and canceling its watch task.

        Polling and hint reconstruction may independently detect the same deletion and reach here. Check
        whether this channel is still the current registered one to identify first termination, preventing
        duplicate broadcasts/logs and avoiding killing a new channel registered after recreating the same project name."""
        if self._channels.get(project_name) is not channel:
            return
        self._channels.pop(project_name, None)
        channel.sse.broadcast((PROJECT_DELETED_EVENT, {"project_name": project_name}))
        logger.info("项目已被删除，终止事件流 project=%s", project_name)
        task = channel.task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _handle_scan_file_not_found(
        self, project_name: str, channel: _ProjectChannel, *, log_message: str
    ) -> bool:
        """Shared handling after scanning/hint reconstruction catches ``FileNotFoundError``: recheck directory
        absence, terminating the channel and returning ``True`` if gone; otherwise preserve state, log ERROR,
        and return ``False``.

        Shared by independent :meth:`_async_rebuild_and_broadcast` and :meth:`_watch_project` paths to avoid
        duplicate decision logic and missing one side when conditions later change."""
        if await asyncio.to_thread(self._project_directory_gone, project_name):
            self._handle_project_deleted(project_name, channel)
            return True
        logger.exception(log_message, project_name)
        return False

    async def _watch_project(self, project_name: str, channel: _ProjectChannel) -> None:
        # Initial and hint-woken scans read/rebuild normally; only timed polls skip on unchanged input stamps.
        forced = True
        try:
            while channel.sse.has_subscribers:
                try:
                    async with channel.rebuild_lock:
                        known_stamp = None if forced or channel.snapshot is None else channel.input_stamp
                        # As in explicit rebuilds, swap out preread source markers for this settlement. Hints arriving during I/O
                        # enter a new set, participate in this settlement, and remain for the next round. Reading may precede their write;
                        # clearing them now would leave no marker when the next scan sees the write, mislabeling it as filesystem.
                        covered_sources = channel.pending_sources
                        channel.pending_sources = set()
                        try:
                            # Run only file I/O in the thread.
                            stamp, snapshot = await asyncio.to_thread(self._poll_snapshot, project_name, known_stamp)
                        except BaseException:
                            channel.pending_sources |= covered_sources
                            raise
                        # Reuse snapshots for unchanged inputs and settle swapped-out source markers (as in scans with no differences).
                        if snapshot is not None:
                            # State updates and broadcasts run on the event-loop thread (thread-safe).
                            channel.input_stamp = stamp
                            self._apply_scan_result(project_name, channel, snapshot, covered_sources)
                except asyncio.CancelledError:
                    raise
                except FileNotFoundError:
                    if await self._handle_scan_file_not_found(
                        project_name, channel, log_message="项目事件扫描失败 project=%s"
                    ):
                        return
                except Exception:
                    logger.exception("项目事件扫描失败 project=%s", project_name)
                finally:
                    channel.ready_event.set()

                try:
                    await asyncio.wait_for(channel.scan_now.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    # Hints arriving after timeout cancellation but before this coroutine resumes leave a set event; rebuild as hint-triggered too.
                    forced = channel.scan_now.is_set()
                else:
                    forced = True
                finally:
                    channel.scan_now.clear()
        except asyncio.CancelledError:
            raise

    def _apply_scan_result(
        self,
        project_name: str,
        channel: _ProjectChannel,
        snapshot: ProjectSnapshot,
        covered_sources: set[ProjectChangeSource],
    ) -> None:
        """Update channel state and broadcast changes on the event-loop thread."""
        previous = channel.snapshot
        channel.snapshot = snapshot
        source = self._settled_source(channel, covered_sources)
        if previous is None or previous.fingerprint == snapshot.fingerprint:
            return

        changes = diff_snapshots(previous, snapshot)
        if not changes:
            return

        self._broadcast_changes(
            project_name,
            channel,
            fingerprint=snapshot.fingerprint,
            source=source,
            changes=changes,
        )

    def _build_snapshot_payload(
        self,
        project_name: str,
        channel: _ProjectChannel,
    ) -> dict[str, Any]:
        return {
            "project_name": project_name,
            "fingerprint": channel.snapshot.fingerprint if channel.snapshot is not None else "",
            "generated_at": _utc_now_iso(),
        }

    @classmethod
    def _settled_source(
        cls, channel: _ProjectChannel, covered_sources: set[ProjectChangeSource]
    ) -> ProjectChangeSource:
        """Source for one disk read: markers swapped out before reading plus markers delivered during reading and retained for the next round."""
        return cls._resolve_batch_source(covered_sources | channel.pending_sources)

    @staticmethod
    def _resolve_batch_source(
        pending_sources: set[ProjectChangeSource],
    ) -> ProjectChangeSource:
        if "worker" in pending_sources:
            return "worker"
        if "webui" in pending_sources:
            return "webui"
        return "filesystem"
