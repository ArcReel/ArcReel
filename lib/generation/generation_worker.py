"""
Background worker that consumes generation tasks from SQLite queue.

Per-provider × media_type 调度，拆成两件独立的东西：CapacityTable（上限，来自
ConfigService 的用户配置）+ SlotTable（运行时占用台账）。

受支持的部署只启动一个 uvicorn 进程；server lifespan 在该进程内创建唯一的
GenerationWorker，二者生命周期一致，孤儿任务只来自进程重启。lease 能在进程短暂重叠时防止
重复认领，并为跨进程接管提供防御，但不让多 uvicorn worker 成为受支持部署；若支持多进程，
须重审孤儿判定（见 ``docs/adr/0007``）。取消只对排队中的任务开放，worker 不接收取消信号，
执行中的任务总是跑到终态（见 ``docs/adr/0006``）。

任务执行器与续跑执行器由应用装配处注入；重启自愈与续跑分别在 ``restart_recovery`` 与
``video_resume``，由 worker 持有。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import update

from lib.billing.ledger import Ledger
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService, read_video_poll_timeout_seconds
from lib.custom_provider.discovery_formats import is_comfyui_protocol
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.db import async_session_factory, safe_session_factory
from lib.db.models.task import Task
from lib.db.repositories.base import rowcount
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_queue import (
    I2I_ONLY_TASK_TYPES,
    TASK_POLL_INTERVAL_SEC,
    TASK_WORKER_HEARTBEAT_SEC,
    TASK_WORKER_LEASE_TTL_SEC,
    DispatchProviderChanged,
    GenerationQueue,
    get_generation_queue,
    resolve_video_execution_for_queued_task,
)
from lib.generation.restart_recovery import InterruptedCallSettler, RestartRecovery
from lib.generation.task_failure import encode_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.generation.video_resume import ResumeExecutor, VideoResumeRunner
from lib.project.project_manager import get_project_manager

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lib.config.registry import ProviderMeta

    ProviderProjection = Callable[[dict[str, Any]], Awaitable[str]]


class TaskExecutor(Protocol):
    """任务执行器：按任务类型执行一个已认领的任务，返回任务结果。

    ``claimed_provider_id`` 是认领时路由到的容量桶；视频任务据此在执行入口校验 provider 漂移。
    """

    def __call__(self, task: dict[str, Any], *, claimed_provider_id: str) -> Awaitable[dict[str, Any]]: ...


logger = logging.getLogger(__name__)

# Lease 丢失超过 ``lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT`` 才认为是真切换 owner
# （另一个 worker 进程曾持过 lease 且写入了新 orphan），需要重扫；短 flap（续约抖动）
# 不触发。lease_ttl 默认 10s → 阈值 30s。常量化便于单测注入与调参。
_ORPHAN_RESCAN_LEASE_LOST_MULT = 3

# Default provider used when a task payload does not specify one.
DEFAULT_PROVIDER = "gemini-aistudio"


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


#: ComfyUI 协议供应商每条 lane 的缺省并发：一台 ComfyUI 服务后面通常只有一张显卡。
COMFYUI_LANE_DEFAULT = 1


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

    @staticmethod
    def _custom_lane_default(provider: Any, column: int | None, global_default: int) -> int:
        """自定义供应商某条 lane 的上限：列有值取列值，列为 NULL 取协议默认。

        ComfyUI 协议的供应商是用户自己的一张显卡，图像与视频各开 1 条即占满；全局默认（视频 3、
        图像 5）会让几个任务同时抢同一张卡，排队都排在远端、本地看不见。用户仍可把列显式调高。
        其余协议面向的是商业 API，按全局默认走。

        与 :meth:`_lane_default` 分开：那一条读的是内置供应商注册表的声明默认，自定义供应商没有
        注册表条目，声明来源只有协议本身。
        """
        if column is not None:
            return column
        return COMFYUI_LANE_DEFAULT if is_comfyui_protocol(provider.discovery_format) else global_default

    @classmethod
    def from_env(cls) -> CapacityTable:
        """从环境变量 / 默认值构造（DB 不可用前或测试用）。"""
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
        return cls(_limits=limits, _defaults={"image": image_max, "video": video_max, "audio": audio_max, "text": 1})

    @classmethod
    async def from_db(cls) -> CapacityTable:
        """从 ConfigService + PROVIDER_REGISTRY + 自定义供应商加载容量表。"""
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
            get_custom_endpoint = CustomEndpointRepository(session).get
            # 同一端点可能挂在多行乃至多个供应商上，媒体类型按端点键缓存，整张表只读一次库。
            media_type_by_endpoint: dict[str, str] = {}
            for provider, models in await repo.list_providers_with_models():
                pid = provider.provider_id  # "custom-{id}"
                # 模型行可以挂 ce- 端点，媒体类型写在它那份定义里，只能解析 spec 取。
                media_types: set[str] = set()
                for m in models:
                    if not m.is_enabled:
                        continue
                    media_type = media_type_by_endpoint.get(m.endpoint)
                    if media_type is None:
                        try:
                            media_type = (await resolve_endpoint_spec(m.endpoint, get_custom_endpoint)).media_type
                        except ValueError:
                            # 端点已不在：该行发起生成必然失败，不凭它开 lane，也不作废整张表的刷新。
                            logger.warning("无法解析模型 endpoint，容量表跳过该行: endpoint=%r", m.endpoint)
                            continue
                        media_type_by_endpoint[m.endpoint] = media_type
                    media_types.add(media_type)
                # 自定义供应商不在内置注册表，无声明默认层 → 两层回退：列有值取列值，
                # 列为 NULL 取协议默认（见 _custom_lane_default）。投影仍交给 _lane_limits
                # 统一处理不支持的 lane。
                image_max = cls._custom_lane_default(provider, provider.image_max_workers, default_image)
                video_max = cls._custom_lane_default(provider, provider.video_max_workers, default_video)
                audio_max = cls._custom_lane_default(provider, provider.audio_max_workers, default_audio)
                limits[pid] = cls._lane_limits(media_types, image_max, video_max, audio_max)

        logger.info("从 DB 加载供应商容量表: %s", limits)
        return cls(
            _limits=limits,
            _defaults={"image": default_image, "video": default_video, "audio": default_audio, "text": 1},
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

    ``capacity`` 的 ``0`` 只有一个含义——该 lane 不受支持，是内部哨兵值。用户契约面的
    并发上限是「≥1 的整数或留空」，``0`` 不可由用户输入抵达（见 ``docs/adr/0043``）。

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

    def drain_finished(self) -> list[tuple[str, asyncio.Future[Any]]]:
        """移除并返回所有 done 的 INFLIGHT 占用（pending 不动）。``(task_id, task)``。"""
        finished: list[tuple[str, asyncio.Future[Any]]] = []
        for key in list(self._slots.keys()):
            bucket = self._slots[key]
            done_ids = [tid for tid, occ in bucket.items() if not occ.pending and occ.task.done()]
            finished.extend((tid, bucket.pop(tid).task) for tid in done_ids)
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
    ``resolve_image_backend``，取 ``.provider_id``。image 任务按 ``generation_type="t2i"`` 取一个
    **代表性** provider——worker 认领时拿不到真实任务类型（见 ``docs/adr/0001``），这点近似不影响
    生成正确性（执行层会独立精确再解析一次）；``I2I_ONLY_TASK_TYPES``（图片编辑与衍生资产图）是
    唯一例外（必然 i2i、入队即知），按 i2i 槽精确解析。两种视频生成模式都忽略 enqueue payload 中的旧身份，分镜视频定桶经
    ``video_bucket_for_queued_task`` 与入队派生共用；reference_video 则重读最新
    project/script/unit，以实际可用资产调用公共 request projection。
    这份 provider 投影只服务 claim 过滤与限流路由，不是执行身份；正常 executor 会在开始时
    再按同一最新状态物化请求。
    解析失败（未配置供应商）时回退到 DEFAULT_PROVIDER 仅供限流，不阻断认领。
    """
    project_name = task.get("project_name")
    raw_payload = task.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    if task.get("task_type") == "reference_video" and isinstance(task.get("script_file"), str):
        # Task.script_file is the immutable queue coordinate. The payload copy is retained only for old/direct
        # callers and must not be able to redirect claim-time current-state projection to a different script.
        payload = {**payload, "script_file": task["script_file"]}
    # 以 media lane 区分 video / audio / image：reference_video 等 task_type 同属 video lane。
    is_text = task.get("media_type") == "text"
    is_video = task.get("media_type") == "video" or task.get("task_type") in ("video", "reference_video")
    is_audio = task.get("media_type") == "audio" or task.get("task_type") == "tts"
    if is_text:
        return "text"

    # 整体兜底：含项目加载（队列里可能残留指向已删除/不可读项目的任务，load_project 会抛
    # FileNotFoundError）在内的任何失败都回退 DEFAULT_PROVIDER，绝不冒泡阻断认领循环（见 docstring）。
    try:
        project: dict | None = None
        if project_name:
            project = await asyncio.to_thread(get_project_manager().load_project, project_name)

        resolver = ConfigResolver(async_session_factory)
        if is_video:
            resolved, _generation_type = await resolve_video_execution_for_queued_task(
                resolver=resolver,
                project=project,
                project_name=project_name,
                task_type=task.get("task_type", ""),
                payload=payload,
                resource_id=task.get("resource_id"),
            )
        elif is_audio:
            resolved = await resolver.resolve_audio_backend(project, payload)
        else:
            generation_type = "i2i" if task.get("task_type") in I2I_ONLY_TASK_TYPES else "t2i"
            resolved = await resolver.resolve_image_backend(project, payload, generation_type=generation_type)
    except Exception:
        logger.debug("provider 解析失败，回退 DEFAULT_PROVIDER 仅供限流路由", exc_info=True)
        return DEFAULT_PROVIDER
    return resolved.provider_id or DEFAULT_PROVIDER


class GenerationWorker:
    """Queue worker with per-provider image/video/audio/text lanes and single-active lease."""

    def __init__(
        self,
        queue: GenerationQueue | None = None,
        lease_name: str = "default",
        capacity: CapacityTable | None = None,
        slots: SlotTable | None = None,
        provider_projection: ProviderProjection = _extract_provider,
        lanes: tuple[str, ...] = ("image", "video", "audio", "text"),
        *,
        executor: TaskExecutor,
        resume_executor: ResumeExecutor,
        settle_interrupted_calls: InterruptedCallSettler | None = None,
    ):
        self.queue = queue or get_generation_queue()
        # 认领期与执行期共用的 provider 投影：限流按它的结果路由到对应容量桶。
        self._provider_projection = provider_projection
        self._executor = executor
        self._lanes = lanes
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
        self._wake_event = asyncio.Event()
        self._owns_lease = False
        # 一次性扫描开关：单 lease 互斥架构下，进程一旦扫过 orphan 就不再重扫；
        # 配合 _lease_lost_monotonic 阈值在「真切换 owner」时清零、「短 flap」不清零。
        self._orphan_handled_once: bool = False
        # 本 worker 的构造时刻：早于 web 层开始接受请求。首次收口只收口在此之前发起的无任务
        # 调用行——之后发起的还在本进程里跑；后续重扫发生在进程存活期间，无任务行一律不碰。
        self._constructed_at = datetime.now(UTC)
        self._startup_settled: bool = False
        self._lease_lost_monotonic: float | None = None

        self._resume = VideoResumeRunner(
            queue=self.queue,
            resume_executor=resume_executor,
            provider_projection=provider_projection,
        )
        self._recovery = RestartRecovery(
            queue=self.queue,
            capacity=self._capacity,
            slots=self._slots,
            stop_event=self._stop_event,
            reload_limits=lambda: self.reload_limits(),
            provider_projection=provider_projection,
            resume=self._resume,
            # 启动收口入口（记账层的公开方法）。默认走队列同一处落库接线。
            settle_interrupted_calls=settle_interrupted_calls or self._settle_interrupted_calls_via_ledger,
        )

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
        self._wake_event.clear()
        self._main_task = asyncio.create_task(self._run_loop(), name="generation-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        self.wake()
        if self._main_task:
            await self._main_task
            self._main_task = None

    def wake(self) -> None:
        """Wake the local worker without changing cross-process polling."""
        self._wake_event.set()

    async def _wait_for_wake(self, timeout: float) -> None:  # noqa: ASYNC109 -- 本地唤醒的等待上限，由 asyncio.wait_for 实现；仓库无 trio/anyio cancel scope
        # No local wake is normal; cross-process work is discovered on the polling timeout.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
        self._wake_event.clear()

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
                    await self._recovery.handle_orphans()
                    # 首次收口是进程启动：构造时刻之前发起的无任务 pending 行（文本调用、端点试跑）
                    # 没人接续，一并翻终态。lease 长时间丢失触发的重扫发生在进程存活期间，无任务行
                    # 可能正在本进程里跑，只收口绑定了任务的行。
                    await self._recovery.settle_interrupted_calls(
                        taskless_started_before=None if self._startup_settled else self._constructed_at
                    )
                    self._startup_settled = True
                    self._orphan_handled_once = True

                if not self._owns_lease:
                    await self._wait_for_wake(self.heartbeat_interval)
                    continue

                claimed_any = await self._claim_tasks()

                if claimed_any:
                    await asyncio.sleep(0.05)
                else:
                    await self._wait_for_wake(self.poll_interval)

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

        for media_type in self._lanes:
            attempted_current_state_tasks: set[str] = set()
            while True:
                # 每轮重算池满集合：刚 claim 的任务可能让某 provider 进入满状态
                pool_full = self._pool_full_providers(media_type)
                if attempted_current_state_tasks:
                    task = await self.queue.claim_next_task(
                        media_type=media_type,
                        pool_full_providers=pool_full,
                        exclude_task_ids=frozenset(attempted_current_state_tasks),
                    )
                else:
                    task = await self.queue.claim_next_task(
                        media_type=media_type,
                        pool_full_providers=pool_full,
                    )
                if not task:
                    break

                provider_id = await self._provider_projection(task)
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
                    # 回队前把重派生的 provider 刷回投影列：走到这里说明存量投影与现值
                    # 分裂（NULL 兜底，或入队后剧本参考集 / 供应商配置被改），不刷新的话
                    # 存量值躲过 pool_full 的 SQL 过滤，之后每个 cycle 都重复
                    # claim → requeue → break，满池期间同 lane 其他可跑任务被持续排头阻塞。
                    # best-effort：刷新失败只损失过滤精度，回队重试本身不受影响。
                    try:
                        await self.queue.persist_execution_provider_id(task["task_id"], provider_id)
                    except Exception:
                        logger.warning(
                            "回队前投影刷新失败 task_id=%s provider=%s", task["task_id"], provider_id, exc_info=True
                        )
                    await self._requeue_single_task(task["task_id"])
                    if task.get("task_type") in ("video", "reference_video"):
                        # 两种视频生成模式都必须先重投影才能判断当前 provider；本 cycle 排除已
                        # 重投影且仍池满的任务，继续寻找其它 provider 的可运行任务。
                        attempted_current_state_tasks.add(task["task_id"])
                        continue
                    # 其它任务的 provider 身份在队列中稳定，下一轮 SQL 会按重算的
                    # pool_full 过滤该 provider，避免反复 claim 同一 task。
                    break

                # Dispatch：登记占用（INFLIGHT），bucket 由 register 自动创建
                claimed_any = True
                if task.get("task_type") in ("video", "reference_video"):
                    process_task = self._process_task(task, claimed_provider_id=provider_id)
                else:
                    process_task = self._process_task(task)
                self._slots.register(
                    provider_id,
                    media_type,
                    task["task_id"],
                    asyncio.create_task(
                        process_task,
                        name=f"generation-{media_type}-{task['task_id']}",
                    ),
                )
                if media_type == "text":
                    break

        return claimed_any

    async def _requeue_single_task(self, task_id: str) -> bool:
        """Put a claimed (running) task back to queued status.

        正常路径下大多数池满任务通过 ``pool_full_providers`` SQL 过滤在 claim 阶段被
        排除；当 ``provider_id IS NULL`` 走 IS NULL 兜底而 worker 二次校验发现池满时，
        本方法把任务放回 queued 等下次 cycle 重试（不可 mark_failed——派生 provider 在
        入队后才发生，NULL 不等于"无效任务"）。执行入口发现 provider 漂移时也复用
        同一条 guarded UPDATE，并根据返回值决定能否安全退出当前执行槽。
        """
        try:
            async with safe_session_factory() as session:
                result = await session.execute(
                    update(Task)
                    .where(Task.task_id == task_id, Task.status == "running")
                    .values(
                        status="queued",
                        started_at=None,
                        updated_at=datetime.now(UTC),
                    )
                )
                affected = rowcount(result)
                await session.commit()
            if affected == 0:
                logger.info("回队任务 %s 未命中 running 状态", task_id)
                return False
            logger.debug("回队任务 %s (供应商池已满)", task_id)
            return True
        except Exception:
            logger.warning("回队任务 %s 失败", task_id, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    async def _drain_finished_tasks(self) -> None:
        for task_id, finished_task in self._slots.drain_finished():
            # 同步判定取消/异常：drain_finished() 只返回 done() 的 task，无需 await。
            # 不 await 就没有挂起点，自然不会误吞针对 _run_loop 自身的取消信号。
            if finished_task.cancelled():
                # 子任务被进程级原因打断。正常路径 _process_task 已落终态并 re-raise；
                # 但打断可能落在 _process_task 进入 try 之前（协程尚未开始执行，或仍停在
                # 入口的 provider 投影 await），那一刻子任务来不及落终态。drain 端兜底——
                # SQL 守卫只放行 queued / running，已落终态则 0 rows 无副作用，避免任务
                # 永久停在 running 被重启自愈反复拉起。
                try:
                    await self.queue.mark_task_interrupted(task_id)
                except Exception:
                    logger.warning("drain 兜底落终态失败 task_id=%s", task_id, exc_info=True)
                continue
            try:
                finished_task.result()
            except Exception:
                logger.debug("已处理的任务异常已在 _process_task 中记录")

    async def _wait_inflight_completion(self) -> None:
        # shutdown：先等重启自愈的 dispatcher 派完最后一批 sub-task，再等所有 active task。
        await self._recovery.wait_dispatchers()

        active_tasks = self._slots.all_active_tasks()
        if not active_tasks:
            return
        await asyncio.gather(*active_tasks, return_exceptions=True)
        self._slots.clear()

    async def _process_task(self, task: dict[str, Any], *, claimed_provider_id: str | None = None) -> None:
        """Run a generation task to a terminal state.

        执行中的任务不可取消，执行结果照常落终态。协程只会被进程级原因（事件循环拆除、
        关停超时等）打断：此时经 ``mark_task_interrupted`` 落 cancelled，避免任务停在
        running、被每次重启的自愈重新拉起。所有 DB 写入都用 ``asyncio.shield`` 包裹，
        打断落在 await 期间时让 UPDATE 跑完再向外传播。
        """
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")
        provider_id = claimed_provider_id or await self._provider_projection(task)
        logger.info("开始处理任务 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

        try:
            if task_type in ("video", "reference_video"):
                # 派发时把当下的全局轮询超时写进任务字典，执行器与续跑执行器都从这里读。
                task["video_poll_timeout_seconds"] = await read_video_poll_timeout_seconds()
            result = await self._executor(task, claimed_provider_id=provider_id)
        except asyncio.CancelledError:
            # 进程级打断：用户取消不会打到执行中的任务。
            await asyncio.shield(self.queue.mark_task_interrupted(task_id))
            raise
        except DispatchProviderChanged as exc:
            # 视频任务在执行入口重新读取当前状态；若 provider 已从认领时的槽漂移，
            # 不得占着旧槽提交到新 provider。刷新 advisory 列并回队，让下一 cycle 在新槽
            # 完成容量校验后再执行。此处尚未调用 provider，不会造成重复扣费。
            try:
                await asyncio.shield(self.queue.persist_execution_provider_id(task_id, exc.actual_provider_id))
            except Exception:
                logger.warning(
                    "执行 provider 漂移后的投影刷新失败 task_id=%s provider=%s",
                    task_id,
                    exc.actual_provider_id,
                    exc_info=True,
                )
            requeued = await asyncio.shield(self._requeue_single_task(task_id))
            if not requeued:
                logger.error(
                    "任务 %s 执行 provider 从 %s 变为 %s，但回队失败",
                    task_id,
                    exc.claimed_provider_id,
                    exc.actual_provider_id,
                )
                await asyncio.shield(
                    self.queue.mark_task_failed(
                        task_id,
                        encode_failure(
                            "dispatch_provider_requeue_failed",
                            claimed_provider_id=exc.claimed_provider_id,
                            actual_provider_id=exc.actual_provider_id,
                        ),
                    )
                )
                return
            logger.info(
                "任务 %s 执行 provider 从 %s 变为 %s，已回队等待新槽",
                task_id,
                exc.claimed_provider_id,
                exc.actual_provider_id,
            )
            return
        except Exception as exc:
            logger.exception("任务失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            await asyncio.shield(self.queue.mark_task_failed(task_id, encode_task_failure_message(exc)))
            return

        try:
            await asyncio.shield(self.queue.mark_task_succeeded(task_id, result))
        except asyncio.CancelledError:
            # mark_succeeded 期间被进程级打断：shield 让 inner 跑完；inner 已落 succeeded 时
            # 兜底命中终态返回 0 rows，无副作用。
            await asyncio.shield(self.queue.mark_task_interrupted(task_id))
            raise
        except Exception:
            # mark_succeeded 自身抛错（DB 超时 / OperationalError）：上层 _drain_finished_tasks
            # 只吞掉异常 debug 日志，stack trace 会丢失，因此在这里显式 logger.exception 保留现场。
            logger.exception("标记任务成功失败 %s", task_id)
            raise
        logger.info("任务完成 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

    # ------------------------------------------------------------------
    # Restart recovery
    # ------------------------------------------------------------------

    async def _settle_interrupted_calls_via_ledger(self, *, taskless_started_before: datetime | None) -> int:
        # 与队列共用同一处 session factory：任务行与它的调用行必须落在同一个库里。
        ledger = Ledger(session_factory=self.queue.session_factory)
        return await ledger.settle_interrupted_calls(taskless_started_before=taskless_started_before)

    async def read_video_poll_timeout_seconds(self) -> int:
        """派发前解析全局轮询超时。

        调用方在翻任务状态之前先取它：状态一旦提交就没有回滚点，派发侧此后再抛错任务就永久
        停在 running 上无人接手（与 ``provider_id`` 同为资格条件，见 ``retry_artifact_download``）。
        """
        return await read_video_poll_timeout_seconds()

    async def retry_artifact_download(self, task: dict[str, Any], *, poll_timeout_seconds: int) -> None:
        """按原 provider job 派发一次下载恢复，不重新提交供应商任务。"""
        await self._recovery.retry_artifact_download(task, poll_timeout_seconds=poll_timeout_seconds)
