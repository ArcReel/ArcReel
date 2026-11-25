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
from lib.episode.episode_paths import AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES
from lib.infra.content_digest import FileIdentity, file_identity
from lib.project.project_change_hints import (
    ProjectChangeBatch,
    ProjectChangeSource,
    register_project_change_batch_listener,
    register_project_change_listener,
)
from lib.project.project_manager import ProjectManager
from lib.script.script_skeleton import SKELETON_ENTITY_TYPES
from server.services.project.project_state_projection import (
    ProjectSnapshot,
    ProjectState,
    build_snapshot,
    diff_snapshots,
    locate_items,
    script_plan_state_key,
)
from server.sse_channel import IDLE, DropSubscriber, SseChannel, empty_stream

logger = logging.getLogger(__name__)

PROJECT_EVENTS_POLL_SECONDS = 0.5

PROJECT_DELETED_EVENT = "project_deleted"
ASSISTANT_SESSION_RESUMED_EVENT = "assistant_session_resumed"

ProjectStateReader = Callable[[str], ProjectState]


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _episode_draft_dirs(project_path: Path) -> list[tuple[int, Path]]:
    """``drafts/`` 下名为 ``episode_<数字>`` 的目录列表，路径沿用磁盘真实目录名。"""
    drafts_root = project_path / "drafts"
    dirs: list[tuple[int, Path]] = []
    if not drafts_root.is_dir():
        return dirs
    for episode_dir in sorted(drafts_root.glob("episode_*")):
        suffix = episode_dir.name.removeprefix("episode_")
        if not suffix.isdigit() or not episode_dir.is_dir():
            continue
        dirs.append((int(suffix), episode_dir))
    return dirs


def _script_plan_paths(project_path: Path) -> list[tuple[str, Path]]:
    """可能在场的正式脚本规划文件（状态键 → 路径）。"""
    filenames = sorted(AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES)
    return [
        (script_plan_state_key(episode, filename), episode_dir / filename)
        for episode, episode_dir in _episode_draft_dirs(project_path)
        for filename in filenames
    ]


def _read_file_digests(paths: list[tuple[str, Path]]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for key, path in paths:
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            continue
        digests[key] = hashlib.sha256(payload).hexdigest()
    return digests


def read_project_state(pm: ProjectManager, project_name: str) -> ProjectState:
    """只读加载项目状态：不回写剧本迁移、不同步集索引，无法解析的剧本跳过。"""
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
    return ProjectState(
        project=project,
        scripts=scripts,
        script_plans=_read_file_digests(_script_plan_paths(project_path)),
    )


ProjectInputStamp = tuple[tuple[str, FileIdentity], ...]


def _file_stat(path: Path) -> FileIdentity | None:
    try:
        return file_identity(path.stat())
    except FileNotFoundError:
        return None


def project_input_stamp(pm: ProjectManager, project_name: str) -> ProjectInputStamp:
    """:func:`read_project_state` 读取的全部输入文件的 stat 标识：``project.json``、``scripts/*.json``、
    正式脚本规划与在场草稿。只做 stat，不读内容；项目目录不存在时抛 ``FileNotFoundError``。

    标识不变就说明输入没变，轮询据此跳过重新解析与重建。读盘入口新增输入文件时须同步纳入这里，
    否则那类变更不会被轮询发现。
    """
    project_path = pm.get_project_path(project_name)
    candidates = [(ProjectManager.PROJECT_FILE, project_path / ProjectManager.PROJECT_FILE)]
    scripts_dir = project_path / "scripts"
    if scripts_dir.exists():
        candidates.extend((f"scripts/{path.name}", path) for path in sorted(scripts_dir.glob("*.json")))
    candidates.extend((f"script_plans:{key}", path) for key, path in _script_plan_paths(project_path))
    stamp: list[tuple[str, FileIdentity]] = []
    for label, path in candidates:
        identity = _file_stat(path)
        if identity is not None:
            stamp.append((label, identity))
    return tuple(stamp)


_EQUIVALENT_ACTIONS: dict[str, str] = {"reference_video_ready": "video_ready"}


def _change_identity(change: dict[str, Any]) -> tuple[Any, Any, Any]:
    """变更去重身份。

    只取 entity_type/action/entity_id：script_file 与 episode 由发布方按 task_type 选择性
    附带，纳入身份会让同一件事因一侧为 None 而判成两条。action 经 ``_EQUIVALENT_ACTIONS``
    归一，抹平发布方与快照差分对同一件事的命名差异。
    """
    action = change.get("action")
    if isinstance(action, str):
        action = _EQUIVALENT_ACTIONS.get(action, action)
    return (change.get("entity_type"), action, change.get("entity_id"))


@dataclass
class _ProjectChannel:
    sse: SseChannel
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    scan_now: asyncio.Event = field(default_factory=asyncio.Event)
    rebuild_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    inflight_batch_identities: Counter[tuple[Any, Any, Any]] = field(default_factory=Counter)
    pending_sources: set[ProjectChangeSource] = field(default_factory=set)
    task: asyncio.Task | None = None
    snapshot: ProjectSnapshot | None = None
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
        """``read_state`` 缺省为 :func:`read_project_state`（经本服务的 ``pm`` 只读加载）。"""
        self.project_root = Path(project_root or PROJECT_ROOT)
        resolved_data_root = (
            Path(data_root).resolve(strict=False) if data_root is not None else self.project_root / "projects"
        )
        self.pm = ProjectManager(resolved_data_root)
        self._read_state: ProjectStateReader = read_state or (lambda name: read_project_state(self.pm, name))
        self.poll_interval = max(0.1, float(poll_interval))
        self._channels: dict[str, _ProjectChannel] = {}
        self._stopping = False
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

    def stop_streams(self) -> None:
        self._stopping = True
        for channel in self._channels.values():
            channel.sse.end_streams()
            channel.ready_event.set()

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
        """构造项目通道：溢出策略「移除订阅者」，首/末订阅者钩子启停后台扫描。"""
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
        """首订阅者钩子：启动（或重启已自行退出的）后台扫描任务。

        溢出移除掉最后一个订阅者时 watch task 经 ``while has_subscribers`` 自行
        退出而通道仍留在注册表，故重启条件是「任务不在跑」而非仅「首次订阅」。
        """
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
        """末订阅者钩子：停止后台扫描任务并注销通道。

        先从注册表摘除通道再 await watch task 退出——摘除与取回之间无让出点，
        摘的正是当前通道。收尾期间让出事件循环时，并发进入的新订阅者取不到这个
        将死通道，会新建独立通道注册入表，不会被本次收尾的删除连带摘掉。
        """
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
        channel = self._channels.get(project_name)
        if channel is None:
            channel = self._create_channel(project_name)
            self._channels[project_name] = channel

        queue = channel.sse.subscribe()

        try:
            await channel.ready_event.wait()
        except BaseException:
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
        carried by ``__aexit__`` (see ADR-0046). Consume as
        ``async with stream_events(...) as stream: async for item in stream``.
        """
        if not self._stopping:
            await asyncio.to_thread(self.pm.get_project_path, project_name)
        if self._stopping:
            yield empty_stream()
            return
        sse, queue, snapshot = await self._subscribe(project_name)

        async def _iter() -> AsyncIterator[tuple[str, Any] | dict[str, Any]]:
            if self._stopping:
                return
            yield ("snapshot", snapshot)
            async for item in sse.iterate(queue, idle_timeout=idle_timeout):
                yield {"type": "_idle"} if item is IDLE else item
                if isinstance(item, tuple) and item[0] == PROJECT_DELETED_EVENT:
                    return

        try:
            yield _iter()
        finally:
            await self._unsubscribe(project_name, queue)

    def publish_task_progress(self, project_name: str, payload: dict[str, Any]) -> None:
        channel = self._channels.get(project_name)
        if channel is not None:
            channel.sse.broadcast(("task_progress", payload))


    def locate_items(self, project_name: str, ids: list[str]) -> list[dict[str, Any]]:
        channel = self._channels.get(project_name)
        return locate_items(channel.snapshot if channel else None, ids)

    def publish_assistant_session_resumed(self, project_name: str, session_id: str) -> None:
        """向项目订阅者广播会话回到 running；没有订阅者时丢弃。须在事件循环线程调用。"""
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

        identities = Counter(_change_identity(change) for change in changes)
        channel.inflight_batch_identities.update(identities)

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
        """文件 I/O 在线程中执行，状态更新和广播在事件循环线程中执行。"""
        try:
            async with channel.rebuild_lock:
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
        """向订阅者广播一批变更（轮询扫描与显式重建两条路径共用同一 payload 形状）。"""
        pending = [
            change
            for change in changes
            if change.get("entity_type") in SKELETON_ENTITY_TYPES.values() and "position" not in change
        ]
        for change, location in zip(
            pending, self.locate_items(project_name, [c["entity_id"] for c in pending]), strict=True
        ):
            change.update(
                item_id=location["item_id"],
                position=location["position"],
                episode=change.get("episode") or location["episode"],
            )
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
        """新快照相对基线多出、而任何在途显式批次都未描述的变更。

        重建读盘会捎带任何已落盘的变更，而本批 changes 只描述发布方自己那一件事。
        新快照被无条件写回基线，捎带进来的变更若不在本次广播里，后续扫描与基线已无
        差异，就再没有任何机制为它补发——故在此对基线做一次 diff 补发未覆盖的部分。

        ``covered`` 是在途批次身份登记表（含本批自己），这些身份让给各自那一批广播，
        免得同一件事被补扫先发一次、再被它自己的批次发一次。残留窗口：某批次的产物已
        落盘、而它的 emit 调用晚于本次读盘返回，此时该身份尚未在册，仍会重复一次。
        """
        if previous is None:
            return []
        return [change for change in diff_snapshots(previous, snapshot) if _change_identity(change) not in covered]

    def _rebuild_snapshot(self, project_name: str) -> tuple[ProjectInputStamp, ProjectSnapshot]:
        """同步方法（在线程池中执行）：只读加载项目状态并归一成快照，连同读盘前的输入标识。

        标识取在读盘之前：读盘期间落盘的变更只会让下一轮标识比对判为已变，多重建一次，
        不会把更新的盘面误记成旧标识下的快照。
        """
        stamp = project_input_stamp(self.pm, project_name)
        return stamp, build_snapshot(self._read_state(project_name))

    def _poll_snapshot(
        self, project_name: str, known_stamp: ProjectInputStamp | None
    ) -> tuple[ProjectInputStamp, ProjectSnapshot | None]:
        """同步方法（在线程池中执行）：轮询用的重建——输入标识与 ``known_stamp`` 一致时跳过，快照返回 ``None``。"""
        stamp = project_input_stamp(self.pm, project_name)
        if stamp == known_stamp:
            return stamp, None
        return stamp, build_snapshot(self._read_state(project_name))

    def _project_directory_gone(self, project_name: str) -> bool:
        """判定项目目录当前是否确已不存在（``get_project_path`` 语义）。

        供扫描 / hint 重建路径捕获 ``FileNotFoundError`` 后做一次独立的现状复核，
        与「project.json 等深层文件缺失但目录仍在」区分——后者维持现状，按通用
        异常兜底记 ERROR，不触发终止流程。用复核而非扫描起点的一次性判断，是因为
        目录删除（如 ``shutil.rmtree``）本身非原子：扫描可能在删除过程中的任意
        中间状态命中 ``FileNotFoundError``（如 project.json 先于目录本身被移除），
        起点检查会误判为「未删除」；复核反映的是异常发生后的当前实况。
        """
        try:
            self.pm.get_project_path(project_name)
        except FileNotFoundError:
            return True
        return False

    def _handle_project_deleted(self, project_name: str, channel: _ProjectChannel) -> None:
        """项目目录已被删除：终止该通道——广播终止事件、移出注册表、取消 watch task。

        轮询扫描与 hint 重建两条路径都可能独立探测到同一次删除并落到本方法；
        按「本通道是否仍是注册表现行通道」判定是否为首次终止，避免重复广播/
        重复日志，也避免误杀同名项目重建后已注册的新通道。
        """
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
        """扫描 / hint 重建路径捕获 ``FileNotFoundError`` 后的统一处理：复核目录是否确已
        消失，是则终止通道并返回 ``True``；否则维持现状按 ERROR 兜底并返回 ``False``。

        供 :meth:`_async_rebuild_and_broadcast` 与 :meth:`_watch_project` 两条独立路径
        共用，避免各自维护一份相同判定逻辑、日后修改判定条件时漏改其中一处。
        """
        if await asyncio.to_thread(self._project_directory_gone, project_name):
            self._handle_project_deleted(project_name, channel)
            return True
        logger.exception(log_message, project_name)
        return False

    async def _watch_project(self, project_name: str, channel: _ProjectChannel) -> None:
        forced = True
        try:
            while channel.sse.has_subscribers:
                try:
                    async with channel.rebuild_lock:
                        known_stamp = None if forced or channel.snapshot is None else channel.input_stamp
                        covered_sources = channel.pending_sources
                        channel.pending_sources = set()
                        try:
                            stamp, snapshot = await asyncio.to_thread(self._poll_snapshot, project_name, known_stamp)
                        except BaseException:
                            channel.pending_sources |= covered_sources
                            raise
                        if snapshot is not None:
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
        """在事件循环线程中更新 channel 状态并广播变更。"""
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
        """一轮读盘的来源：读盘前换出的标记，加上读盘期间已送达、仍留给下一轮的标记。"""
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
