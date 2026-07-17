"""
Background worker that consumes generation tasks from SQLite queue.

Per-provider × media_type 调度，拆成两件独立的东西：CapacityTable（上限，来自
ConfigService 的用户配置）+ SlotTable（运行时占用台账）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.config.registry import ProviderMeta

logger = logging.getLogger(__name__)

from datetime import UTC

# Lease 丢失超过 ``lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT`` 才认为是真切换 owner
# （另一个 worker 进程曾持过 lease 且写入了新 orphan），需要重扫；短 flap（续约抖动）
# 不触发。lease_ttl 默认 10s → 阈值 30s。常量化便于单测注入与未来调参。
_ORPHAN_RESCAN_LEASE_LOST_MULT = 3

from lib.generation_queue import (
    TASK_POLL_INTERVAL_SEC,
    TASK_WORKER_HEARTBEAT_SEC,
    TASK_WORKER_LEASE_TTL_SEC,
    GenerationQueue,
    get_generation_queue,
)
from lib.task_failure import encode_failure

# Default provider used when a task payload does not specify one.
DEFAULT_PROVIDER = "gemini-aistudio"


def _non_resumable_video_providers() -> frozenset[str]:
    """不实现 VideoBackend.resume_video 的视频 provider 集合。

    orphan handler 据此把这些 provider 的 running 孤儿标记为 [resume_unsupported]
    失败，而非主动 requeue 重跑——避免对已经提交给供应商的请求二次扣费
    （Grok 同步型无 job_id；Vidu 因 generate 内联 poll 未抽出独立 resume，列为
    follow-up）。新增不支持 resume 的 backend 时同步在这里登记。
    """
    from lib.providers import PROVIDER_GROK, PROVIDER_VIDU

    return frozenset({PROVIDER_GROK, PROVIDER_VIDU})


NON_RESUMABLE_VIDEO_PROVIDERS = _non_resumable_video_providers()


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _parse_lane_max(config: dict[str, str], key: str, default: int, provider_id: str) -> int:
    """逐 key 容错解析单条 lane 的并发上限。

    解析失败回退默认值并告警，不让单个坏值（写入校验上线前的存量脏数据）拖垮
    整表加载；可解析的负数沿用 clamp 语义（→0，即该 lane fail-fast）并告警。
    """
    raw = config.get(key)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("供应商 %s 的 %s 配置值非法（%r），回退默认值 %d", provider_id, key, raw, default)
        return default
    if parsed < 0:
        logger.warning("供应商 %s 的 %s 配置值为负（%r），按 0 处理（该 lane 关闭）", provider_id, key, raw)
    return max(0, parsed)


@dataclass
class CapacityTable:
    """Per-provider concurrency limits keyed by ``provider_id × media_type``.

    纯标量配置表，唯一真相来自 provider config。改并发 = 只改这张表上一个数字，
    占用台账不受影响。``get`` 三态语义区分"已知但不支持（0）"与"provider 未知（懒默认）"。
    """

    _limits: dict[str, dict[str, int]]  # provider_id → {media_type → 上限}
    _defaults: dict[str, int]  # {"image": 5, "video": 3, "audio": 10}，未知 provider 懒默认

    def get(self, provider_id: str, media_type: str) -> int:
        """返回 ``(provider, media)`` 的并发上限。

        - provider 已知 + lane 在表 → 登记值（可能 0=不支持该 lane）
        - provider 已知 + lane 不在表 → 0（不支持）
        - provider 整个未知 → ``_defaults[media_type]``（纯查询，不写回表）
        """
        lanes = self._limits.get(provider_id)
        if lanes is None:
            return self._defaults.get(media_type, 0)
        return lanes.get(media_type, 0)

    def replace(self, new_limits: dict[str, dict[str, int]]) -> None:
        """整表换数字（reload 入口）。占用台账与默认值不受影响。"""
        self._limits = new_limits

    @staticmethod
    def _lane_limits(media_types: Any, image: int, video: int, audio: int) -> dict[str, int]:
        """按 provider 支持的 media_types 把上限投影成 lane 字典；不支持的 lane → 0。

        容量装载的单一映射点：新增 lane 时在这里加一行即可。
        """
        return {
            "image": image if "image" in media_types else 0,
            "video": video if "video" in media_types else 0,
            "audio": audio if "audio" in media_types else 0,
        }

    @staticmethod
    def _lane_default(meta: ProviderMeta, lane: str, global_default: int) -> int:
        """某条 lane 在用户未配时的回退默认：供应商注册表声明默认（若有）→ 否则全局默认。

        三层回退的中间层单点：from_env / from_db 共用，避免两路漂移。声明默认的合法性
        （key 是合法 lane、值为 >=1 整数）由 ProviderMeta.__post_init__ 在 import 期保证。
        """
        return meta.default_concurrency.get(lane, global_default)

    @classmethod
    def from_env(cls) -> CapacityTable:
        """从环境变量 / 默认值构造（DB 不可用前或测试用）。"""
        from lib.config.registry import PROVIDER_REGISTRY

        image_max = _read_int_env("IMAGE_MAX_WORKERS", 5, minimum=1)
        video_max = _read_int_env("VIDEO_MAX_WORKERS", 3, minimum=1)
        audio_max = _read_int_env("AUDIO_MAX_WORKERS", 10, minimum=1)
        # 无用户配置的装载路径：每条 lane 取声明默认（若有）→ 否则全局默认
        limits = {
            pid: cls._lane_limits(
                meta.media_types,
                cls._lane_default(meta, "image", image_max),
                cls._lane_default(meta, "video", video_max),
                cls._lane_default(meta, "audio", audio_max),
            )
            for pid, meta in PROVIDER_REGISTRY.items()
        }
        return cls(_limits=limits, _defaults={"image": image_max, "video": video_max, "audio": audio_max})

    @classmethod
    async def from_db(cls) -> CapacityTable:
        """从 ConfigService + PROVIDER_REGISTRY + 自定义供应商加载容量表。"""
        from lib.config.registry import PROVIDER_REGISTRY
        from lib.config.service import ConfigService
        from lib.custom_provider.endpoints import endpoint_to_media_type
        from lib.db import safe_session_factory
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        default_image = _read_int_env("IMAGE_MAX_WORKERS", 5, minimum=1)
        default_video = _read_int_env("VIDEO_MAX_WORKERS", 3, minimum=1)
        default_audio = _read_int_env("AUDIO_MAX_WORKERS", 10, minimum=1)

        limits: dict[str, dict[str, int]] = {}
        async with safe_session_factory() as session:
            svc = ConfigService(session)
            all_configs = await svc.get_all_provider_configs()
            for provider_id, meta in PROVIDER_REGISTRY.items():
                config = all_configs.get(provider_id, {})
                # 用户未配某条 lane 时，_parse_lane_max 回退到此处给的 default——
                # 即声明默认（若有）→ 否则全局默认；用户配了则解析值覆盖之
                image_max = _parse_lane_max(
                    config, "image_max_workers", cls._lane_default(meta, "image", default_image), provider_id
                )
                video_max = _parse_lane_max(
                    config, "video_max_workers", cls._lane_default(meta, "video", default_video), provider_id
                )
                audio_max = _parse_lane_max(
                    config, "audio_max_workers", cls._lane_default(meta, "audio", default_audio), provider_id
                )
                # _lane_limits 统一负责"不支持的 lane → 0"，三个装载路径共用同一投影点
                limits[provider_id] = cls._lane_limits(meta.media_types, image_max, video_max, audio_max)

            repo = CustomProviderRepository(session)
            for provider, models in await repo.list_providers_with_models():
                pid = provider.provider_id  # "custom-{id}"
                media_types = {endpoint_to_media_type(m.endpoint) for m in models if m.is_enabled}
                # 自定义供应商不在内置注册表，无声明默认层 → 两层回退：列有值取列值，
                # 列为 NULL 走全局默认。投影仍交给 _lane_limits 统一处理不支持的 lane。
                image_max = provider.image_max_workers if provider.image_max_workers is not None else default_image
                video_max = provider.video_max_workers if provider.video_max_workers is not None else default_video
                audio_max = provider.audio_max_workers if provider.audio_max_workers is not None else default_audio
                limits[pid] = cls._lane_limits(media_types, image_max, video_max, audio_max)

        logger.info("从 DB 加载供应商容量表: %s", limits)
        return cls(
            _limits=limits,
            _defaults={"image": default_image, "video": default_video, "audio": default_audio},
        )


@dataclass
class _Occupant:
    """一条占用：执行体 + phase 标志。

    ``pending=True`` 仅由 video 的 sem-throttled dispatcher 在 sub-task 排队期产生；
    image / audio 无 sem dispatcher（audio 后端同步），一律 ``pending=False``。promote
    只翻这个标志（天然原子），避免"两层容器间瞬时既不在 pending 也不在 inflight"的窗口。
    """

    task: asyncio.Future[Any]
    pending: bool = False


class SlotTable:
    """占用台账：``(provider_id, media_type)`` → ``{task_id: _Occupant}``。

    被动纯内存数据结构，**容量无关**：``has_room`` 由 caller 传入 ``capacity``。
    不写 DB、不解析 provider、不决定孤儿策略、不碰状态机守卫。

    **空 bucket 不残留（by design）**：``release`` / ``drain_finished`` 移除最后一个
    占用时一并删掉该 ``(provider,media)`` bucket，保证 ``occupied_providers`` 永不
    返回已清空的 provider（池满黑名单决策的支点）。
    """

    def __init__(self) -> None:
        self._slots: dict[tuple[str, str], dict[str, _Occupant]] = {}

    def register(
        self,
        provider: str,
        media: str,
        task_id: str,
        task: asyncio.Future[Any],
        *,
        pending: bool = False,
    ) -> None:
        """登记占用；幂等覆盖。bucket 不存在时自动创建。"""
        self._slots.setdefault((provider, media), {})[task_id] = _Occupant(task=task, pending=pending)

    def promote(self, provider: str, media: str, task_id: str) -> None:
        """PENDING→INFLIGHT：sem.acquire 成功后调用，只翻 ``pending`` 标志。

        占用对象已是同一 sub-task（``asyncio.current_task()`` 即登记时的 task），
        无需替换 task。不存在则 no-op。
        """
        bucket = self._slots.get((provider, media))
        if bucket is None:
            return
        occ = bucket.get(task_id)
        if occ is not None:
            occ.pending = False

    def release(self, provider: str, media: str, task_id: str) -> None:
        """释放，不论 phase；幂等；清空后移除该 ``(provider,media)`` bucket。"""
        bucket = self._slots.get((provider, media))
        if bucket is None:
            return
        bucket.pop(task_id, None)
        if not bucket:
            del self._slots[(provider, media)]

    def has_room(self, provider: str, media: str, capacity: int) -> bool:
        """``capacity>0`` 且 占用数（含 pending）< capacity。"""
        if capacity <= 0:
            return False
        bucket = self._slots.get((provider, media))
        return (0 if bucket is None else len(bucket)) < capacity

    def occupied(self, provider: str, media: str) -> int:
        """当前占用数（含 pending）。"""
        bucket = self._slots.get((provider, media))
        return 0 if bucket is None else len(bucket)

    def occupied_providers(self, media: str) -> set[str]:
        """该 ``media`` 下有占用(≥1)的 provider；空 bucket 不计（黑名单源，含未知 provider）。"""
        return {provider for (provider, m), bucket in self._slots.items() if m == media and bucket}

    def find_by_task(self, task_id: str) -> asyncio.Future[Any] | None:
        """跨全表按 ``task_id`` 找执行体（cancel 用）；未命中返回 None。"""
        for bucket in self._slots.values():
            occ = bucket.get(task_id)
            if occ is not None:
                return occ.task
        return None

    def drain_finished(self) -> list[tuple[str, asyncio.Future[Any]]]:
        """移除并返回所有 done 的 INFLIGHT 占用（pending 不动）。``(task_id, task)``。"""
        finished: list[tuple[str, asyncio.Future[Any]]] = []
        for key in list(self._slots.keys()):
            bucket = self._slots[key]
            done_ids = [tid for tid, occ in bucket.items() if not occ.pending and occ.task.done()]
            for tid in done_ids:
                finished.append((tid, bucket.pop(tid).task))
            if not bucket:
                del self._slots[key]
        return finished

    def active_task_ids(self) -> set[str]:
        """所有占用的 task_id（pending+inflight）：self-active 扫描用。"""
        return {tid for bucket in self._slots.values() for tid in bucket}

    def all_active_tasks(self) -> list[asyncio.Future[Any]]:
        """所有占用的执行体（pending+inflight）：shutdown wait 用。"""
        return [occ.task for bucket in self._slots.values() for occ in bucket.values()]

    def clear(self) -> None:
        """清空全表（shutdown 收尾）。"""
        self._slots.clear()


async def _extract_provider(task: dict[str, Any]) -> str:
    """Extract a provider_id from a claimed task, used **only** for rate-limit slot routing.

    这是解析链的薄投影：按 media lane（``media_type``）派发到 ``resolve_video_backend`` /
    ``resolve_image_backend``，取 ``.provider_id``。image 任务按 ``capability="t2i"`` 取一个
    **代表性** provider——worker 认领时拿不到真实 capability（见 ``docs/adr/0001``），这点近似不影响
    生成正确性（执行层会独立精确再解析一次）；``image_edit`` 是唯一例外（必然 i2i、入队即知），
    按 i2i 槽精确解析。解析失败（未配置供应商）时回退到 DEFAULT_PROVIDER 仅供限流，不阻断认领。
    """
    project_name = task.get("project_name")
    payload = task.get("payload") or {}
    # 以 media lane 区分 video / audio / image：reference_video 等 task_type 同属 video lane。
    is_video = task.get("media_type") == "video" or task.get("task_type") in ("video", "reference_video")
    is_audio = task.get("media_type") == "audio" or task.get("task_type") == "tts"

    # 整体兜底：含项目加载（队列里可能有指向已删除/不可读项目的历史任务，load_project 会抛
    # FileNotFoundError）在内的任何失败都回退 DEFAULT_PROVIDER，绝不冒泡阻断认领循环（见 docstring）。
    try:
        project: dict | None = None
        if project_name:
            from lib.config.resolver import get_project_manager

            project = await asyncio.to_thread(get_project_manager().load_project, project_name)

        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        resolver = ConfigResolver(async_session_factory)
        if is_video:
            resolved = await resolver.resolve_video_backend(project, payload)
        elif is_audio:
            resolved = await resolver.resolve_audio_backend(project, payload)
        else:
            capability = "i2i" if task.get("task_type") == "image_edit" else "t2i"
            resolved = await resolver.resolve_image_backend(project, payload, capability=capability)
    except Exception:
        logger.debug("provider 解析失败，回退 DEFAULT_PROVIDER 仅供限流路由", exc_info=True)
        return DEFAULT_PROVIDER
    return resolved.provider_id or DEFAULT_PROVIDER


class GenerationWorker:
    """Queue worker with per-provider image/video/audio lanes and single-active lease."""

    def __init__(
        self,
        queue: GenerationQueue | None = None,
        lease_name: str = "default",
        capacity: CapacityTable | None = None,
        slots: SlotTable | None = None,
    ):
        self.queue = queue or get_generation_queue()
        self.lease_name = lease_name
        self.owner_id = f"worker-{uuid.uuid4().hex[:10]}"

        # 容量表（上限，用户配置驱动）与占用台账（运行时记账）分离：前者是配置真相，
        # reload 只换它的数字；后者承载 inflight/pending，占用容器引用恒定不被重建。
        self._capacity: CapacityTable = capacity or CapacityTable.from_env()
        self._slots: SlotTable = slots or SlotTable()
        logger.info("Worker 初始容量表: %s", self._capacity._limits)
        self.lease_ttl = max(1.0, float(TASK_WORKER_LEASE_TTL_SEC))
        self.heartbeat_interval = max(0.5, float(TASK_WORKER_HEARTBEAT_SEC))
        self.poll_interval = max(0.1, float(TASK_POLL_INTERVAL_SEC))

        self._main_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._owns_lease = False
        # Orphan dispatcher 句柄持久化：shutdown 时 await 它跑完；lease 切换重夺时
        # 第二次进 _handle_orphan_tasks_on_start，旧句柄未 done 不能直接覆盖。
        self._orphan_dispatcher_task: asyncio.Task | None = None
        # 一次性扫描开关：单 lease 互斥架构下，进程一旦扫过 orphan 就不再重扫；
        # 配合 _lease_lost_monotonic 阈值在「真切换 owner」时清零、「短 flap」不清零。
        self._orphan_handled_once: bool = False
        self._lease_lost_monotonic: float | None = None

    # ------------------------------------------------------------------
    # Capacity management
    # ------------------------------------------------------------------

    async def reload_limits(self) -> None:
        """Reload per-provider concurrency limits from DB into the CapacityTable.

        只换容量表的数字（``replace``）——占用台账纹丝不动，inflight/pending 容器
        引用恒定，彻底消除"reload 时活体搬运在跑 task"的脆弱性。某 provider 被删后其
        在跑占用照常 drain；新任务的容量判定经 ``CapacityTable.get``：lane 被降级为不
        支持→0（fail-fast），provider 整个消失→懒默认（此时该 provider 已无法解析，
        任务回退 DEFAULT_PROVIDER 或在执行层失败，不会真按默认容量占用它）。
        """
        try:
            new = await CapacityTable.from_db()
        except Exception:
            logger.warning("从 DB 加载供应商配置失败，保持当前配置", exc_info=True)
            return
        self._capacity.replace(new._limits)
        logger.info("已更新供应商容量表: %s", self._capacity._limits)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._main_task and not self._main_task.done():
            return
        self._stop_event.clear()
        self._main_task = asyncio.create_task(self._run_loop(), name="generation-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._main_task:
            await self._main_task
            self._main_task = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                had_lease = self._owns_lease
                self._owns_lease = await self.queue.acquire_or_renew_worker_lease(
                    name=self.lease_name,
                    owner_id=self.owner_id,
                    ttl_seconds=self.lease_ttl,
                )

                if self._owns_lease and not had_lease:
                    logger.info("获得 worker lease (owner=%s)", self.owner_id)
                if had_lease and not self._owns_lease:
                    logger.warning("失去 worker lease (owner=%s)", self.owner_id)

                await self._drain_finished_tasks()

                # Lease 状态变化跟踪：首次失去 lease 时打点；重夺 lease 后判断
                # 「真切换 owner」（>= 3× ttl）→ 重置开关；「续约 flap」（< 3× ttl）→ 保持。
                if had_lease and not self._owns_lease and self._lease_lost_monotonic is None:
                    self._lease_lost_monotonic = time.monotonic()
                if self._owns_lease and self._lease_lost_monotonic is not None:
                    lost_duration = time.monotonic() - self._lease_lost_monotonic
                    if lost_duration > self.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
                        logger.info(
                            "lease 丢失 %.1fs（> %d×ttl=%.1fs），认为另一进程曾持过 lease，重扫 orphan",
                            lost_duration,
                            _ORPHAN_RESCAN_LEASE_LOST_MULT,
                            self.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT,
                        )
                        self._orphan_handled_once = False
                    self._lease_lost_monotonic = None

                # 一次性扫描：进程持 lease 后只扫一次 orphan；后续主循环不再重扫。
                # 单 lease 互斥保证不会与另一个 worker 同时扫；跨进程接管由上述阈值兜底。
                if self._owns_lease and not self._orphan_handled_once:
                    await self._handle_orphan_tasks_on_start()
                    self._orphan_handled_once = True

                if not self._owns_lease:
                    await asyncio.sleep(self.heartbeat_interval)
                    continue

                claimed_any = await self._claim_tasks()

                if claimed_any:
                    await asyncio.sleep(0.05)
                else:
                    await asyncio.sleep(self.poll_interval)

            await self._wait_inflight_completion()
        finally:
            if self._owns_lease:
                await self.queue.release_worker_lease(name=self.lease_name, owner_id=self.owner_id)
            self._owns_lease = False

    def _pool_full_providers(self, media_type: str) -> frozenset[str]:
        """返回当前 cycle ``media_type`` 已满的 provider_id 集合（黑名单，用于 claim SQL）。

        黑名单源是**有占用的 provider**（``SlotTable.occupied_providers``），而非容量表
        已知 provider 全集：只有占着槽的 provider 才可能"满"。空 provider 本就 has_room、
        不该进黑名单；"未知但有占用"的 provider（首次 reload 前的启动窗口 / 运行中途被删的
        custom provider）照旧能进黑名单，避免其池满任务每 cycle 被 claim→requeue 刷屏。

        守卫 ``cap > 0`` 保留：``has_room`` 在 ``cap == 0`` 时也返回 False，若不加守卫
        会把"不支持该 lane 的 provider"也归入黑名单，让 SQL 把这些 task 静默 drop，
        而不是走 worker 二次校验的 ``cap == 0`` fail-fast mark_failed 路径。
        """
        return frozenset(
            pid
            for pid in self._slots.occupied_providers(media_type)
            if (cap := self._capacity.get(pid, media_type)) > 0 and not self._slots.has_room(pid, media_type, cap)
        )

    async def _claim_tasks(self) -> bool:
        """Claim tasks from queue and route to per-provider slots.

        池满 task 不再 claim → requeue 反复刷屏；改为在 SQL 层按
        ``pool_full_providers`` 黑名单过滤，池满 task 始终保持 ``queued``。
        ``provider_id IS NULL`` 老数据和未知 provider 任务不被过滤，claim 后由
        worker 二次 ``_extract_provider`` 派生 provider 再校验容量。
        """
        claimed_any = False

        for media_type in ("image", "video", "audio"):
            while True:
                # 每轮重算池满集合：刚 claim 的任务可能让某 provider 进入满状态
                pool_full = self._pool_full_providers(media_type)
                task = await self.queue.claim_next_task(
                    media_type=media_type,
                    pool_full_providers=pool_full,
                )
                if not task:
                    break

                provider_id = await _extract_provider(task)
                cap = self._capacity.get(provider_id, media_type)

                if cap <= 0:
                    # 供应商不支持此媒体类型（容量 ≤ 0），直接失败（与 has_room 守卫一致）
                    logger.warning(
                        "供应商 %s 不支持 %s 生成，任务 %s 标记失败",
                        provider_id,
                        media_type,
                        task["task_id"],
                    )
                    await self.queue.mark_task_failed(
                        task["task_id"],
                        encode_failure(
                            "provider_unsupported_media",
                            provider_id=provider_id,
                            media_type=media_type,
                        ),
                    )
                    claimed_any = True
                    continue

                if not self._slots.has_room(provider_id, media_type, cap):
                    # NULL 老数据 / 未知 provider 通过 SQL 兜底走到这里：二次校验仍满
                    # → 回队让下次 cycle 再试（FIFO 顺序由 queued_at 维持）。绝不能
                    # mark_failed：入队后 provider_id 才被派生，资料完整的任务也可能
                    # 因部署窗口 / 解析失败而 NULL，这条路径必须保持可重试。
                    logger.info(
                        "供应商 %s 的 %s 池满，task %s 回队等待下一 cycle",
                        provider_id,
                        media_type,
                        task["task_id"],
                    )
                    await self._requeue_single_task(task["task_id"])
                    # break 当前 media_type 循环：下一轮 SQL 会按重算的 pool_full
                    # 过滤掉这个 provider，避免反复 claim 同一 task
                    break

                # Dispatch：登记占用（INFLIGHT），bucket 由 register 自动创建
                claimed_any = True
                self._slots.register(
                    provider_id,
                    media_type,
                    task["task_id"],
                    asyncio.create_task(
                        self._process_task(task),
                        name=f"generation-{media_type}-{task['task_id']}",
                    ),
                )

        return claimed_any

    async def _requeue_single_task(self, task_id: str) -> None:
        """Put a claimed (running) task back to queued status.

        正常路径下大多数池满任务通过 ``pool_full_providers`` SQL 过滤在 claim 阶段被
        排除；当 ``provider_id IS NULL`` 走 IS NULL 兜底而 worker 二次校验发现池满时，
        本方法把任务放回 queued 等下次 cycle 重试（不可 mark_failed——派生 provider 在
        入队后才发生，NULL 不等于"无效任务"）。
        """
        try:
            from datetime import datetime

            from sqlalchemy import update

            from lib.db import safe_session_factory
            from lib.db.models.task import Task

            async with safe_session_factory() as session:
                await session.execute(
                    update(Task)
                    .where(Task.task_id == task_id, Task.status == "running")
                    .values(
                        status="queued",
                        started_at=None,
                        updated_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            logger.debug("回队任务 %s (供应商池已满)", task_id)
        except Exception:
            logger.warning("回队任务 %s 失败", task_id, exc_info=True)

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    async def _drain_finished_tasks(self) -> None:
        for task_id, finished_task in self._slots.drain_finished():
            # 同步判定取消/异常：drain_finished() 只返回 done() 的 task，无需 await。
            # 不 await 就没有挂起点，自然不会误吞针对 _run_loop 自身的取消信号。
            if finished_task.cancelled():
                # 子任务被取消。正常路径 _process_task 已 mark_cancelled 并 re-raise；
                # 但取消可能落在 _process_task 进入 try 之前（协程尚未开始执行，或仍停在
                # 入口的 _extract_provider await），那一刻子任务来不及落终态。drain 端兜底
                # mark_cancelled——SQL 守卫 status IN (queued, cancelling, running) 保证幂等：
                # 已落终态则 0 rows 无副作用，避免任务永久卡在 running/cancelling。
                try:
                    await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                except Exception:
                    logger.warning("drain 兜底 mark_cancelled 失败 task_id=%s", task_id, exc_info=True)
                continue
            try:
                finished_task.result()
            except Exception:
                logger.debug("已处理的任务异常已在 _process_task 中记录")

    async def _wait_inflight_completion(self) -> None:
        # shutdown：先等 dispatcher 派完最后一批 sub-task（否则 sub-task 可能在 dispatcher
        # 退出后才创建），再等所有 active task。dispatcher 异常不能断 shutdown 链。
        if self._orphan_dispatcher_task is not None and not self._orphan_dispatcher_task.done():
            try:
                await self._orphan_dispatcher_task
            except Exception:
                logger.exception("orphan dispatcher 在 shutdown 等待时异常")

        active_tasks = self._slots.all_active_tasks()
        if not active_tasks:
            return
        await asyncio.gather(*active_tasks, return_exceptions=True)
        self._slots.clear()

    async def _process_task(self, task: dict[str, Any]) -> None:
        """Run a generation task with 0-rows-cancelled finally protocol (ADR 0006).

        所有 DB 写入（mark_succeeded / mark_failed / mark_cancelled）都用 ``asyncio.shield``
        包裹：若取消信号在 DB 写入 await 期间到达，inner shield 让 UPDATE 跑完再向外
        传播，避免任务停在 cancelling/running 中间态。
        """
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")
        provider_id = await _extract_provider(task)
        logger.info("开始处理任务 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

        from server.services.generation_tasks import execute_generation_task

        try:
            result = await execute_generation_task(task)
        except asyncio.CancelledError:
            # 用户/级联取消：worker.request_cancel 触发 asyncio.Task.cancel()
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            raise
        except Exception as exc:
            logger.exception("任务失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            rows = await asyncio.shield(self.queue.mark_task_failed(task_id, str(exc)))
            if rows == 0:
                # 外部已抢先翻 cancelling → 落地 cancelled 终态
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return

        try:
            rows = await asyncio.shield(self.queue.mark_task_succeeded(task_id, result))
        except asyncio.CancelledError:
            # mark_succeeded 期间被取消：shield 让 inner 跑完了；inner 完成情况由
            # rowcount 决定——拿不到了，按"被外部取消"语义兜底。
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            raise
        except Exception:
            # mark_succeeded 自身抛错（DB 超时 / OperationalError）：上层 _drain_finished_tasks
            # 只吞掉异常 debug 日志，stack trace 会丢失，因此在这里显式 logger.exception 保留现场。
            logger.exception("标记任务成功失败 %s", task_id)
            raise
        if rows == 0:
            # 0-rows-cancelled 协议：execute 跑赢但 DB 已被外部翻 cancelling
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
        else:
            logger.info("任务完成 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

    async def _process_resume_task(self, task: dict[str, Any]) -> None:
        """重启自愈入口：直接调 backend.resume_video，绕过 normal executor 流水线。

        provider 锁定：把持久化的 ``task["provider_id"]`` 注入 payload 的
        ``video_provider`` 字段，让 ``ConfigResolver`` 按持久化 provider 而非当前
        项目配置解析 backend。否则任务提交后到重启前若项目 provider 配置切换，
        会拿旧 ``provider_job_id`` 去新 provider 轮询，导致可恢复任务被误判失败。
        """
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")

        job_id = task.get("provider_job_id") or ""
        if not job_id:
            # 防御：本不该被派发到这里（_handle_orphan_tasks_on_start 已 mark_failed [restart_lost]）
            rows = await asyncio.shield(
                self.queue.mark_task_failed(task_id, encode_failure("restart_lost_resume_no_job_id"))
            )
            if rows == 0:
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return

        # 锁定持久化 provider 到 payload（resolver 优先级：payload > project > 默认）。
        persisted_provider_id = task.get("provider_id")
        if persisted_provider_id:
            payload = task.get("payload")
            if payload is None:
                payload = {}
                task["payload"] = payload
            is_video = task.get("media_type") == "video" or task_type in ("video", "reference_video")
            if is_video:
                payload["video_provider"] = persisted_provider_id
            else:
                payload["image_provider"] = persisted_provider_id

        provider_id = await _extract_provider(task)
        logger.info(
            "重启自愈处理任务 %s (type=%s, provider=%s, job=%s)",
            task_id,
            task_type,
            provider_id,
            job_id,
        )

        from lib.video_backends.base import ResumeExpiredError
        from server.services.resume_executor import execute_resume_video_task

        try:
            result = await execute_resume_video_task(task, job_id=job_id)
        except asyncio.CancelledError:
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            raise
        except NotImplementedError as exc:
            logger.warning("resume 不支持 task %s: %s", task_id, exc)
            rows = await asyncio.shield(
                self.queue.mark_task_failed(task_id, encode_failure("resume_unsupported_detail", detail=str(exc)))
            )
            if rows == 0:
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return
        except ResumeExpiredError as exc:
            logger.warning("resume 已过期 task %s: %s", task_id, exc)
            rows = await asyncio.shield(
                self.queue.mark_task_failed(task_id, encode_failure("resume_expired_detail", detail=str(exc)))
            )
            if rows == 0:
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return
        except Exception as exc:
            logger.exception("resume 失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            rows = await asyncio.shield(self.queue.mark_task_failed(task_id, str(exc)))
            if rows == 0:
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return

        try:
            rows = await asyncio.shield(self.queue.mark_task_succeeded(task_id, result))
        except asyncio.CancelledError:
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            raise
        if rows == 0:
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
        else:
            logger.info("重启自愈完成 %s", task_id)

    # ------------------------------------------------------------------
    # Cancel & orphan recovery
    # ------------------------------------------------------------------

    def request_cancel(self, task_id: str) -> bool:
        """In-process cancel 信号：把 task 对应 asyncio.Task cancel()，返回是否找到。

        由 GenerationQueue.cancel_task 同步调用（ADR 0006 秒级响应）。``find_by_task`` 也
        覆盖 sem 排队中的 pending sub-task：cancel 会让 sem.acquire 抛 CancelledError 让
        sub-task 直接退出。callback 不命中是 best-effort 失败——worker finally 走
        mark_cancelled 兜底（SQL 守卫 IN queued|cancelling|running 接住）。
        """
        t = self._slots.find_by_task(task_id)
        if t is not None and not t.done():
            t.cancel()
            logger.info("已对 task %s 发出 in-process cancel 信号", task_id)
            return True
        logger.info("request_cancel: task %s 不在 inflight (worker finally 兜底)", task_id)
        return False

    async def _handle_orphan_tasks_on_start(self) -> None:
        """重启自愈：扫 running + cancelling 孤儿，按"是否可安全 resume"分流。

        原则——**不主动产生额外扣费**：只要 worker 不能确认能接续供应商已收单的 job，
        就把孤儿标记为失败丢弃，绝不重新提交。

        - cancelling → mark_cancelled
        - image running → [restart_lost]（image 任务不持久化 job_id，无法接续；
          且 image 提交本身已计费，重跑等于双重扣费）
        - video running，provider ∈ NON_RESUMABLE_VIDEO_PROVIDERS（Grok/Vidu）
          → [resume_unsupported]（backend 不实现 resume_video，原 job 无接续手段）
        - video running，可 resume backend (ark/gemini/openai/newapi)：
          - 无 provider_job_id → [restart_lost]
          - 有 job_id → 收集到 `resumable_by_provider` 桶，后台 dispatcher 受
            video 容量约束分批 dispatch（fix #647 第 1 项）

        启动期 fast path（本函数）**只做终结类处理**，立刻返回；可 resume 的视频孤儿
        派发给后台 dispatcher 处理，避免 N 个 Sora orphan × 每个 5min poll 把启动期
        阻塞数十分钟。Dispatcher 不调 `_drain_finished_tasks`，完全依赖主循环每 cycle
        清理占用台账；`_stop_event` 触发时 dispatcher 自然退出。
        """
        orphans = await self.queue.list_orphan_tasks_on_start()
        if not orphans:
            return
        logger.info(
            "等待 lease 获取后开始扫孤儿（待处理 %d 个）…lease_ttl=%.0fs",
            len(orphans),
            self.lease_ttl,
        )

        # self-active 防 self-preemption：lease flap > 3×TTL 后 _orphan_handled_once
        # 重置，再扫 DB running 会包含本进程仍 inflight 的 task。若不排除：
        # - image 任务 → 错误标 [restart_lost]（任务还在跑就被标失败）
        # - video 任务 → 启动重复 resume 流，同一 provider job 被并发 poll/finalize，
        #   崩溃窗口可能导致 provider 端双重扣费（违反 ADR 0007 红线）
        # active_task_ids 含 pending+inflight 全部占用，DB 扫到的同 id task 视为本进程的活。
        self_active_task_ids = self._slots.active_task_ids()

        resumable_by_provider: dict[str, list[dict[str, Any]]] = {}

        for task in orphans:
            task_id = task["task_id"]
            if task_id in self_active_task_ids:
                logger.info("孤儿扫到本进程仍 active 的 task %s，跳过避免 self-preemption", task_id)
                continue
            status = task.get("status")
            if status == "cancelling":
                await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                logger.info("孤儿 cancelling → cancelled: %s", task_id)
                continue

            # status == "running"
            task_type = task.get("task_type")
            if task.get("media_type"):
                media_type = task["media_type"]
            elif task_type in ("video", "reference_video"):
                media_type = "video"
            elif task_type == "tts":
                media_type = "audio"
            else:
                media_type = "image"

            # image 任务不持久化 job_id 也无 resume 入口——已提交给供应商的请求无法回收，
            # 主动 requeue 会双重扣费。直接丢弃，等用户决定是否手动重试。
            if media_type == "image":
                logger.warning("孤儿 image running → [restart_lost]: %s", task_id)
                rows = await self.queue.mark_task_failed(
                    task_id,
                    encode_failure("restart_lost_image"),
                )
                if rows == 0:
                    await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                continue

            # audio（TTS）同步、不持久化 job_id、无 resume 入口——与 image 同样降级为
            # [restart_lost]，不重新提交以免重复计费。
            if media_type == "audio":
                logger.warning("孤儿 audio running → [restart_lost]: %s", task_id)
                rows = await self.queue.mark_task_failed(
                    task_id,
                    encode_failure("restart_lost_audio"),
                )
                if rows == 0:
                    await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                continue

            # video 路径：判断 provider 是否支持 resume。优先用持久化的 provider_id：
            # 否则项目配置在重启前后切换时，_extract_provider 会按当前项目重新解析，
            # 可能把原本 Grok/Vidu 孤儿误判成可 resume，或把可 resume 任务路由到错池。
            # 与 _process_resume_task 的 provider 锁定策略保持一致。
            provider_id = task.get("provider_id") or await _extract_provider(task)
            if provider_id in NON_RESUMABLE_VIDEO_PROVIDERS:
                # Grok/Vidu 当前不实现 resume_video——原 job 已发给供应商无接续手段，
                # 重跑会重复扣费。丢弃，由用户手动决定是否重试。
                logger.warning(
                    "孤儿 video running (provider=%s 不支持 resume) → [resume_unsupported]: %s",
                    provider_id,
                    task_id,
                )
                rows = await self.queue.mark_task_failed(
                    task_id,
                    encode_failure("resume_unsupported_provider", provider_id=provider_id),
                )
                if rows == 0:
                    await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                continue

            job_id = task.get("provider_job_id")
            if not job_id:
                logger.warning("孤儿 running 无 job_id → [restart_lost]: %s", task_id)
                rows = await self.queue.mark_task_failed(task_id, encode_failure("restart_lost_no_job_id"))
                if rows == 0:
                    await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                continue

            # 收集到 provider 桶，交给后台 dispatcher 受 pool 容量约束分批处理。
            # 顺便把 resolve 出的 provider_id 写回 task dict，dispatcher 路由用。
            task["provider_id"] = provider_id
            resumable_by_provider.setdefault(provider_id, []).append(task)

        if resumable_by_provider:
            total = sum(len(v) for v in resumable_by_provider.values())
            logger.info(
                "孤儿扫描 fast path 完成：%d 个可 resume video 任务交后台分批 dispatch",
                total,
            )
            # lease 重夺时旧 dispatcher 可能还在跑（典型场景：resume_video 内 poll provider
            # 需要几分钟到 10+ 分钟）。本轮**不 await 不 cancel** 直接覆盖句柄：
            # - 不 await：避免阻塞主循环 → liveness 问题（无法续 lease 心跳/无法响应 cancel API）
            # - 不 cancel：cancel 会让旧 dispatcher 的 _run_one 抛 CancelledError，进入
            #   兜底 mark_task_cancelled 路径，把用户**未主动取消**的 in-flight resume 错误
            #   标为 cancelled，且让 provider 端已扣费 job 失去归属
            # - 直接覆盖：旧 dispatcher_task 的 sub-task 仍由占用台账（SlotTable）持有引用
            #   + asyncio.gather 内部 callback 链持有，旧 task 不会被 GC detached
            # - shutdown 仍能感知：_wait_inflight_completion 经 _slots.all_active_tasks() 等到旧 sub-task
            if self._orphan_dispatcher_task is not None and not self._orphan_dispatcher_task.done():
                logger.warning(
                    "旧 orphan dispatcher 仍在运行，本轮直接覆盖句柄不等待——"
                    "旧 sub-task 由占用台账跟踪，shutdown 时经 _slots.all_active_tasks 兜底"
                )
            self._orphan_dispatcher_task = asyncio.create_task(
                self._dispatch_resume_orphans_background(resumable_by_provider),
                name="orphan-dispatcher",
            )
        else:
            logger.info("孤儿扫描完成，无可 resume 任务")

    async def _dispatch_resume_orphans_background(
        self,
        resumable_by_provider: dict[str, list[dict[str, Any]]],
    ) -> None:
        """后台 dispatcher：按 provider 分桶并发，受 video 容量约束分批入 inflight。

        - 不同 provider 之间无容量耦合 → 并发跑独立 sub-task；
        - 同 provider 内顺序入队：满则 `asyncio.wait(inflight, FIRST_COMPLETED)` 等任一
          完成（精确感知，不 sleep 轮询）；
        - 主循环每 cycle 调 `_drain_finished_tasks` 自动 pop 已 done 的 task → dispatcher
          下次 has_room 判定就有空位（解耦关键假设）；
        - `_stop_event` 触发时 dispatcher 自然退出，不持有 lease 资源。
        """
        if self._stop_event.is_set():
            return
        sub_tasks = [
            asyncio.create_task(
                self._dispatch_provider_bucket(provider_id, tasks),
                name=f"orphan-dispatch-{provider_id}",
            )
            for provider_id, tasks in resumable_by_provider.items()
        ]
        await asyncio.gather(*sub_tasks, return_exceptions=True)
        logger.info("孤儿后台 dispatcher 完成")

    async def _dispatch_provider_bucket(
        self,
        provider_id: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """同 provider 桶并发跑 resume task，pending/inflight 用 phase 标志精确容量与 cancel 跟踪。

        - ``cap <= 0``：fail-fast mark_failed[resume_unsupported]，不进 ``Semaphore(0)``
          死锁；reload 一次兜底，避免启动期 capability 抖动误判。
        - sub-task 由父协程同步预先以 PENDING 登记到占用台账——避免 ``create_task``
          异步调度还未触发时主循环 ``has_room`` 看占用=0 误判可有容量。
        - sem acquire 成功后 ``promote`` 把该占用翻成 INFLIGHT（同一 sub-task，只翻标志）；
          finally ``release``。
        - sem 容量在 dispatch 顶部从 CapacityTable 读一次定型：reload 期间改的容量表
          不影响本批 dispatch 的并发上限。这是已知设计选择，非 bug。
        """
        cap = self._capacity.get(provider_id, "video")
        if cap <= 0:
            # 启动期 reload 兜底：DB 加载可能晚于第一次 orphan 扫描。
            try:
                await self.reload_limits()
            except Exception:
                logger.warning("reload_limits 兜底失败", exc_info=True)
            cap = self._capacity.get(provider_id, "video")
        if cap <= 0:
            for t in tasks:
                rows = await self.queue.mark_task_failed(
                    t["task_id"],
                    encode_failure("resume_unsupported_capacity_zero", provider_id=provider_id),
                )
                if rows == 0:
                    await self.queue.mark_task_cancelled(t["task_id"], cancelled_by="user")
            return

        sem = asyncio.Semaphore(cap)

        async def _run_one(t: dict[str, Any]) -> None:
            task_id = t["task_id"]
            acquired = False
            try:
                await sem.acquire()
                acquired = True
                if self._stop_event.is_set():
                    return
                # 占用对象恒定（SlotTable 引用不被 reload 重建），promote 直接翻 PENDING→INFLIGHT
                self._slots.promote(provider_id, "video", task_id)
                logger.info("已派发 resume video orphan: task_id=%s provider=%s", task_id, provider_id)
                await self._process_resume_task(t)
            except asyncio.CancelledError:
                # 三种 cancel 路径都在这里兜底 mark_task_cancelled——SQL WHERE
                # status IN (queued, cancelling, running) 保证幂等：
                # 1) sem.acquire 等待期 cancel → _process_resume_task 还没跑，必须由此落终态
                # 2) acquired=True 后但 _process_resume_task 内 try 块外（如 _extract_provider
                #    的 await）cancel → 内部 mark 路径不会触发，必须由此落终态
                # 3) _process_resume_task 内部 cancel → 内部已 mark，此处再调 SQL 命中
                #    cancelled 行返回 0 rows，无副作用
                try:
                    await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
                except Exception:
                    logger.exception("sem dispatch cancel 落终态失败 task_id=%s", task_id)
                raise
            finally:
                if acquired:
                    sem.release()
                self._slots.release(provider_id, "video", task_id)

        sub: list[asyncio.Task] = []
        for t in tasks:
            if self._stop_event.is_set():
                break
            # 父协程同步：先 create_task、再立即以 PENDING 登记——避免「create_task
            # 是异步调度，has_room 在调度未发生前看占用=0 误判可有容量」的瞬时 race。
            sub_task = asyncio.create_task(_run_one(t), name=f"resume-video-{t['task_id']}")
            self._slots.register(provider_id, "video", t["task_id"], sub_task, pending=True)
            sub.append(sub_task)
        if sub:
            await asyncio.gather(*sub, return_exceptions=True)
