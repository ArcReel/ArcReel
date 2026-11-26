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
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import update

from lib.billing.ledger import Ledger
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
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
from lib.generation.generation_result import QueuedGenerationError
from lib.generation.render_lane import RENDER_LANE_CONCURRENCY, RENDER_MEDIA_TYPE, RENDER_PROVIDER_ID
from lib.generation.restart_recovery import InterruptedCallSettler, RestartRecovery
from lib.generation.task_failure import encode_failure, parse_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.generation.task_ownership import TaskLease, TaskOwnership
from lib.generation.task_writes import TaskWrites
from lib.generation.text_progress import TaskProgressTable
from lib.generation.text_task_lifecycle import TextTaskLifecycle
from lib.generation.video_resume import ResumeExecutor, VideoResumeRunner, fail_task_of_deleted_project
from lib.infra.async_thread import run_noninterruptible_async
from lib.project.project_manager import get_project_manager
from lib.project.task_project_claim import TaskProjectClaim, claim_task_project
from lib.video_runtime.text import observe_text_provider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lib.config.registry import ProviderMeta

    ProviderProjection = Callable[[dict[str, Any]], Awaitable[str]]


class TaskExecutor(Protocol):
    """任务执行器：按任务类型执行一个已认领的任务，返回任务结果。

    ``claimed_provider_id`` 是认领时路由到的容量桶；图片、音频、视频任务据此在执行入口核对 provider，
    不一致时在调用供应商之前抛 ``DispatchProviderChanged``。
    """

    def __call__(self, task: dict[str, Any], *, claimed_provider_id: str | None) -> Awaitable[dict[str, Any]]: ...


logger = logging.getLogger(__name__)

_ORPHAN_RESCAN_LEASE_LOST_MULT = 3
_TEXT_INFLIGHT_LIMIT = 16
_TEXT_CLAIM_LIMIT = 4

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

    _limits: dict[str, dict[str, int]]
    _defaults: dict[str, int]

    def get(self, provider_id: str, media_type: str) -> int:
        """返回 ``(provider, media)`` 的并发上限。

        - provider 已知 + lane 在表 → 登记值（可能 0=不支持该 lane）
        - provider 已知 + lane 不在表 → 0（不支持）
        - provider 整个未知 → ``_defaults[media_type]``（纯查询，不写回表）
        """
        if media_type == RENDER_MEDIA_TYPE:
            return RENDER_LANE_CONCURRENCY if provider_id == RENDER_PROVIDER_ID else 0
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
        limits = {
            pid: cls._lane_limits(
                catalog.provider_media_types(pid),
                cls._lane_default(meta, "image", image_max),
                cls._lane_default(meta, "video", video_max),
                cls._lane_default(meta, "audio", audio_max),
            )
            for pid, meta in PROVIDER_REGISTRY.items()
        }
        return cls(
            _limits=limits,
            _defaults={
                "image": image_max,
                "video": video_max,
                "audio": audio_max,
                RENDER_MEDIA_TYPE: RENDER_LANE_CONCURRENCY,
            },
        )

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
                image_max = _parse_lane_max(
                    config, "image_max_workers", cls._lane_default(meta, "image", default_image), provider_id
                )
                video_max = _parse_lane_max(
                    config, "video_max_workers", cls._lane_default(meta, "video", default_video), provider_id
                )
                audio_max = _parse_lane_max(
                    config, "audio_max_workers", cls._lane_default(meta, "audio", default_audio), provider_id
                )
                limits[provider_id] = cls._lane_limits(
                    catalog.provider_media_types(provider_id), image_max, video_max, audio_max
                )

            repo = CustomProviderRepository(session)
            get_custom_endpoint = CustomEndpointRepository(session).get
            media_type_by_endpoint: dict[str, str] = {}
            for provider, models in await repo.list_providers_with_models():
                pid = provider.provider_id
                media_types: set[str] = set()
                for m in models:
                    if not m.is_enabled:
                        continue
                    media_type = media_type_by_endpoint.get(m.endpoint)
                    if media_type is None:
                        try:
                            media_type = (await resolve_endpoint_spec(m.endpoint, get_custom_endpoint)).media_type
                        except ValueError:
                            logger.warning("无法解析模型 endpoint，容量表跳过该行: endpoint=%r", m.endpoint)
                            continue
                        media_type_by_endpoint[m.endpoint] = media_type
                    media_types.add(media_type)
                image_max = cls._custom_lane_default(provider, provider.image_max_workers, default_image)
                video_max = cls._custom_lane_default(provider, provider.video_max_workers, default_video)
                audio_max = cls._custom_lane_default(provider, provider.audio_max_workers, default_audio)
                limits[pid] = cls._lane_limits(media_types, image_max, video_max, audio_max)

        logger.info("从 DB 加载供应商容量表: %s", limits)
        return cls(
            _limits=limits,
            _defaults={
                "image": default_image,
                "video": default_video,
                "audio": default_audio,
                RENDER_MEDIA_TYPE: RENDER_LANE_CONCURRENCY,
            },
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
    并发上限是「≥1 的整数或留空」，``0`` 不可由用户输入抵达（见 ``docs/adr/0042``）。

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

    图片、音频、视频任务只在排队记录缺失（或记录的 provider 已不支持该媒体类型）时调用一次，
    结果写回记录；本地渲染使用固定槽位。

    这是解析链的薄投影：按 media lane（``media_type``）派发到 ``resolve_video_backend`` /
    ``resolve_image_backend``，取 ``.provider_id``。image 任务按 ``generation_type="t2i"`` 取一个
    **代表性** provider——worker 认领时拿不到真实任务类型（见 ``docs/adr/0001``），这点近似不影响
    生成正确性（执行层会独立精确再解析一次）；``I2I_ONLY_TASK_TYPES``（图片编辑与衍生资产图）是
    唯一例外（必然 i2i、入队即知），按 i2i 槽精确解析。两种视频生成模式都忽略 enqueue payload 中的旧身份，分镜视频定桶经
    ``video_bucket_for_queued_task`` 与入队派生共用；reference_video 则重读最新
    project/script/unit，以实际可用资产调用公共 request projection。
    这份 provider 投影只服务槽位归属与限流路由，不是执行身份；executor 在开始时按最新状态
    物化请求，与认领槽位不一致时回队。
    模型目录错误保留异常里的供应商；其余解析失败（未配置供应商）回退到 DEFAULT_PROVIDER 仅供限流，不阻断认领。
    """
    project_name = task.get("project_name")
    raw_payload = task.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    if task.get("task_type") == "reference_video" and isinstance(task.get("script_file"), str):
        payload = {**payload, "script_file": task["script_file"]}
    is_video = task.get("media_type") == "video" or task.get("task_type") in ("video", "reference_video")
    is_audio = task.get("media_type") == "audio" or task.get("task_type") == "tts"
    if task.get("media_type") == RENDER_MEDIA_TYPE:
        return RENDER_PROVIDER_ID

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
            from lib.video_runtime.audio import AudioRuntime

            resolved = await AudioRuntime(resolver).read_identity(project, payload)
        else:
            from lib.video_runtime.image_resolution import ImageRuntime

            generation_type = "i2i" if task.get("task_type") in I2I_ONLY_TASK_TYPES else "t2i"
            resolved, _ = await ImageRuntime(resolver).read_identity(project, generation_type, payload=payload)
    except ModelNotInCatalogError as exc:
        return exc.provider_id
    except Exception:
        logger.debug("provider 解析失败，回退 DEFAULT_PROVIDER 仅供限流路由", exc_info=True)
        return DEFAULT_PROVIDER
    return resolved.provider_id or DEFAULT_PROVIDER


class WorkerStoppingError(Exception):
    pass


class GenerationWorker:
    """Queue worker with per-provider media lanes, process-wide text and render lanes and single-active lease."""

    def __init__(
        self,
        queue: GenerationQueue | None = None,
        lease_name: str = "default",
        capacity: CapacityTable | None = None,
        slots: SlotTable | None = None,
        provider_projection: ProviderProjection = _extract_provider,
        lanes: tuple[str, ...] = ("image", "video", "audio", "text", RENDER_MEDIA_TYPE),
        *,
        write_backoff: Callable[[float], Awaitable[None]] = asyncio.sleep,
        executor: TaskExecutor,
        resume_executor: ResumeExecutor,
        settle_interrupted_calls: InterruptedCallSettler | None = None,
    ):
        self.queue = queue or get_generation_queue()
        self.text_progress = TaskProgressTable()
        self._provider_projection = provider_projection
        self._executor = executor
        self._lanes = lanes
        self.lease_name = lease_name
        self.owner_id = f"worker-{uuid.uuid4().hex[:10]}"

        self._capacity: CapacityTable = capacity or CapacityTable.from_env()
        self._slots: SlotTable = slots or SlotTable()
        logger.info("Worker 初始容量表: %s", self._capacity._limits)
        self.lease_ttl = max(1.0, float(TASK_WORKER_LEASE_TTL_SEC))
        self.heartbeat_interval = max(0.5, float(TASK_WORKER_HEARTBEAT_SEC))
        self.poll_interval = max(0.1, float(TASK_POLL_INTERVAL_SEC))

        self._main_task: asyncio.Task | None = None
        self._reload_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._owns_lease = False
        self._orphan_handled_once: bool = False
        self._constructed_at = datetime.now(UTC)
        self._startup_settled: bool = False
        self._lease_lost_monotonic: float | None = None

        self._ownership = TaskOwnership()
        self._writes = TaskWrites(self.queue, write_backoff)
        self._call_settlement_lock = asyncio.Lock()
        self._handoffs: set[asyncio.Task] = set()
        self._resume = VideoResumeRunner(
            writes=self._writes,
            queue=self.queue,
            resume_executor=resume_executor,
            provider_projection=provider_projection,
        )
        self._recovery = RestartRecovery(
            ownership=self._ownership,
            request_rescan=self._request_rescan,
            writes=self._writes,
            queue=self.queue,
            capacity=self._capacity,
            slots=self._slots,
            stop_event=self._stop_event,
            reload_limits=lambda: self.reload_limits(),
            provider_projection=provider_projection,
            resume=self._resume,
            settle_interrupted_calls=settle_interrupted_calls or self._settle_interrupted_calls_via_ledger,
        )

    async def reload_limits(self) -> None:
        """Reload per-provider concurrency limits from DB into the CapacityTable.

        只换容量表的数字（``replace``）——占用台账纹丝不动，inflight/pending 容器
        引用恒定，彻底消除"reload 时活体搬运在跑 task"的脆弱性。某 provider 被删后其
        在跑占用照常 drain；新任务的容量判定经 ``CapacityTable.get``：lane 被降级为不
        支持→0（fail-fast），provider 整个消失→懒默认（此时该 provider 已无法解析，
        任务回退 DEFAULT_PROVIDER 或在执行层失败，不会真按默认容量占用它）。读库与替换
        整体串行，并发重载结束后容量表反映最后一次读到的数据库状态。
        """
        async with self._reload_lock:
            try:
                new = await CapacityTable.from_db()
            except Exception:
                logger.warning("从 DB 加载供应商配置失败，保持当前配置", exc_info=True)
                return
            self._capacity.replace(new._limits)
            logger.info("已更新供应商容量表: %s", self._capacity._limits)

    async def start(self) -> None:
        if self._main_task and not self._main_task.done():
            return
        await self.reload_limits()
        self._wake_event.clear()
        self._main_task = asyncio.create_task(self._run_loop(), name="generation-worker")

    def stop_admission(self) -> None:
        self._stop_event.set()
        self.wake()

    async def stop(self) -> None:
        self.stop_admission()
        if self._main_task:
            await self._main_task
            self._main_task = None
        else:
            await self._wait_inflight_completion()

    def _request_rescan(self) -> None:
        self._orphan_handled_once = False

    def wake(self) -> None:
        """Wake the local worker without changing cross-process polling."""
        self._wake_event.set()

    async def _wait_for_wake(self, timeout: float) -> None:  # noqa: ASYNC109 -- 本地唤醒的等待上限，由 asyncio.wait_for 实现；仓库无 trio/anyio cancel scope
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
        self._wake_event.clear()

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self._run_cycle()
                except Exception:
                    logger.exception("worker 主循环本轮出错，%.1fs 后重试", self.heartbeat_interval)
                    self._orphan_handled_once = False
                    if not self._stop_event.is_set():
                        await self._wait_for_wake(self.heartbeat_interval)

            await self._wait_inflight_completion()
        finally:
            if self._owns_lease:
                await self.queue.release_worker_lease(name=self.lease_name, owner_id=self.owner_id)
            self._owns_lease = False

    async def _run_cycle(self) -> None:
        """主循环的一轮：续约 lease、收尾已完成任务、按需收口孤儿任务，持有 lease 时认领新任务。"""
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

        if self._owns_lease and not self._orphan_handled_once:
            self._orphan_handled_once = True
            await self._recovery.handle_orphans()
            async with self._call_settlement_lock:
                await self._recovery.settle_interrupted_calls(
                    taskless_started_before=None if self._startup_settled else self._constructed_at
                )
            self._startup_settled = True

        if not self._owns_lease:
            await self._wait_for_wake(self.heartbeat_interval)
            return

        claimed_any = await self._claim_tasks()

        if self._stop_event.is_set():
            return
        if claimed_any:
            await asyncio.sleep(0.05)
        else:
            await self._wait_for_wake(self.poll_interval)

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

        池满过滤在 SQL 层按 ``pool_full_providers`` 黑名单完成，池满任务始终保持 ``queued``。
        槽位归属只看排队时记录的 provider，认领时不按项目现状推算，池满时一个认领周期的开销
        因此与排队任务数无关。排队期间项目改了供应商，由执行入口核对出漂移、改写记录并原位
        回队，新供应商在下一次认领时才占槽。没有记录的存量任务在认领时推算一次并写回。
        文本任务共享进程槽，单轮限量认领。
        """
        claimed_any = False

        text_claimed = 0
        for media_type in self._lanes:
            while not self._stop_event.is_set():
                if media_type == "text":
                    if text_claimed >= _TEXT_CLAIM_LIMIT or not self._slots.has_room(
                        "text", "text", _TEXT_INFLIGHT_LIMIT
                    ):
                        break
                    pool_full = frozenset()
                else:
                    pool_full = self._pool_full_providers(media_type)
                task = await self.queue.claim_next_task(
                    media_type=media_type,
                    pool_full_providers=pool_full,
                    owned_task_ids=self._ownership.owned_task_ids(),
                )
                if not task:
                    break

                lease = self._ownership.try_acquire(task["task_id"])
                if lease is None:
                    lease = await self._ownership.acquire(task["task_id"])
                transferred = False
                project_name = task.get("project_name")
                try:
                    if self._stop_event.is_set():
                        await self._requeue_single_task(task["task_id"], project_name=project_name)
                        return claimed_any
                    if media_type == "text":
                        provider_id = "text"
                        text_claimed += 1
                    else:
                        provider_id, recorded = await self._slot_provider(task, media_type)
                        cap = self._capacity.get(provider_id, media_type)
                        if self._stop_event.is_set():
                            await self._requeue_single_task(task["task_id"], project_name=project_name)
                            return claimed_any

                        if cap <= 0:
                            logger.warning(
                                "供应商 %s 不支持 %s 生成，任务 %s 标记失败",
                                provider_id,
                                media_type,
                                task["task_id"],
                            )
                            await self._writes.failed(
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
                            logger.info(
                                "供应商 %s 的 %s 池满，task %s 回队等待下一 cycle",
                                provider_id,
                                media_type,
                                task["task_id"],
                            )
                            if not recorded:
                                recorded = await self._record_provider(task["task_id"], provider_id)
                            await self._requeue_single_task(task["task_id"], project_name=project_name)
                            if recorded:
                                continue
                            break

                    claimed_any = True
                    execution = asyncio.create_task(
                        self._process_task(task, claimed_provider_id=provider_id),
                        name=f"generation-{media_type}-{task['task_id']}",
                    )
                    self._slots.register(provider_id, media_type, task["task_id"], execution)
                    execution.add_done_callback(partial(self._execution_done, lease))
                    transferred = True
                    if media_type == RENDER_MEDIA_TYPE:
                        break
                finally:
                    if not transferred:
                        lease.release()

        return claimed_any

    def _execution_done(self, lease: TaskLease, task: asyncio.Task) -> None:
        lease.release()
        if not task.cancelled() and task.exception() is not None:
            self._request_rescan()

    async def _slot_provider(self, task: dict[str, Any], media_type: str) -> tuple[str, bool]:
        """任务的槽位归属：返回 ``(provider_id, 任务行是否已记录该 provider)``。

        有记录且该 provider 支持此媒体类型时直接用记录。没有记录的存量任务，或记录的 provider
        已不支持此媒体类型时，按项目现状推算一次并写回，之后的认领不再推算。本地渲染使用固定槽位。
        """
        if media_type == RENDER_MEDIA_TYPE:
            return await self._provider_projection(task), False
        recorded = task.get("provider_id")
        if isinstance(recorded, str) and recorded and self._capacity.get(recorded, media_type) > 0:
            return recorded, True
        provider_id = await self._provider_projection(task)
        return provider_id, await self._record_provider(task["task_id"], provider_id)

    async def _record_provider(self, task_id: str, provider_id: str) -> bool:
        """best-effort 写回任务行的 provider 记录，返回是否写入成功。"""
        try:
            await self.queue.persist_execution_provider_id(task_id, provider_id)
        except Exception:
            logger.warning("provider 记录写回失败 task_id=%s provider=%s", task_id, provider_id, exc_info=True)
            return False
        return True

    async def _requeue_single_task(self, task_id: str, *, project_name: str | None = None) -> bool:
        """Put a claimed (running) task back to queued status.

        池满任务通常在 claim 阶段被 ``pool_full_providers`` SQL 过滤排除；认领后推算出的
        provider 池满时（``provider_id IS NULL`` 的存量任务等），本方法把任务放回 queued
        等下次 cycle 重试（不可 mark_failed——NULL 不等于"无效任务"）。执行入口发现
        provider 漂移时也复用同一条 guarded UPDATE，并根据返回值决定能否安全退出当前执行槽。
        回队不改 ``queued_at``，任务保留原排队位置。
        """

        async def write() -> bool:
            statement = update(Task).where(Task.task_id == task_id, Task.status == "running")
            if project_name is not None:
                statement = statement.where(Task.project_name == project_name)
            async with self.queue.session_factory() as session:
                result = await session.execute(
                    statement.values(
                        status="queued",
                        started_at=None,
                        updated_at=datetime.now(UTC),
                    )
                )
                affected = rowcount(result)
                await session.commit()
            if affected == 0:
                logger.info("任务 %s 不再符合回队条件", task_id)
                return False
            logger.debug("回队任务 %s (供应商池已满)", task_id)
            return True

        requeued = await self._writes.retry(task_id, write)
        if (
            not requeued
            and project_name is not None
            and await self._writes.running_project_changed(task_id, project_name)
        ):
            await self._writes.failed(task_id, encode_failure("project_deleted_during_task"))
        return requeued

    async def _drain_finished_tasks(self) -> None:
        for task_id, finished_task in self._slots.drain_finished():
            if finished_task.cancelled():
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
        if self._handoffs:
            await asyncio.gather(*self._handoffs, return_exceptions=True)
        await self._recovery.wait_dispatchers()

        active_tasks = self._slots.all_active_tasks()
        if not active_tasks:
            return
        await asyncio.gather(*active_tasks, return_exceptions=True)
        self._slots.clear()

    async def _process_task(self, task: dict[str, Any], *, claimed_provider_id: str) -> None:
        """Run a generation task to a terminal state.

        执行中的任务不可取消，执行结果照常落终态。协程只会被进程级原因（事件循环拆除、
        关停超时等）打断：此时经 ``mark_task_interrupted`` 落 cancelled，避免任务停在
        running、被每次重启的自愈重新拉起。所有 DB 写入都用 ``asyncio.shield`` 包裹，
        打断落在 await 期间时让 UPDATE 跑完再向外传播。

        执行在任务对项目的认领下进行。项目在执行期间被删除时认领作废，执行器在落盘前中止；
        无论执行器以什么结果返回，任务都以 ``project_deleted_during_task`` 失败。
        """
        task_id = task["task_id"]
        with claim_task_project(task_id, task.get("project_name")) as claim:
            if task.get("media_type") == "text":
                lifecycle = TextTaskLifecycle(task, self.queue, self._writes)
                lifecycle.log("running")
                with (
                    observe_text_provider(lifecycle.record_provider),
                    self.text_progress.track(task["project_name"], task_id, task["task_type"]),
                ):
                    await self._run_claimed_task(task, claim, claimed_provider_id=None, lifecycle=lifecycle)
            else:
                await self._run_claimed_task(task, claim, claimed_provider_id=claimed_provider_id)

    async def _run_claimed_task(
        self,
        task: dict[str, Any],
        claim: TaskProjectClaim,
        *,
        claimed_provider_id: str | None,
        lifecycle: TextTaskLifecycle | None = None,
    ) -> None:
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")
        provider_id = claimed_provider_id
        if lifecycle is None:
            logger.info("开始处理任务 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

        try:
            if task_type in ("video", "reference_video"):
                task["video_poll_timeout_seconds"] = await read_video_poll_timeout_seconds()
            result = await self._executor(task, claimed_provider_id=provider_id)
        except asyncio.CancelledError:
            await run_noninterruptible_async(self.queue.mark_task_interrupted(task_id))
            if lifecycle:
                lifecycle.log("cancelled", "interrupted")
            raise
        except Exception as exc:
            if claim.revoked:
                await fail_task_of_deleted_project(self._writes, task)
                if lifecycle:
                    lifecycle.log("failed", "project_deleted_during_task")
                return
            if isinstance(exc, DispatchProviderChanged) and lifecycle is None:
                await self._requeue_changed_provider(task, claim, exc)
                return
            reason = encode_task_failure_message(exc)
            if lifecycle is None:
                logger.exception("任务失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            affected = await run_noninterruptible_async(self._writes.failed(task_id, reason))
            if lifecycle and affected:
                if isinstance(exc, QueuedGenerationError):
                    lifecycle.log("failed", exc.code)
                else:
                    parsed = parse_failure(reason)
                    lifecycle.log("failed", parsed[0] if parsed else "unclassified")
            return

        written = await run_noninterruptible_async(self._writes.succeeded(task_id, result, claim=claim))
        if written is None:
            await fail_task_of_deleted_project(self._writes, task)
            if lifecycle:
                lifecycle.log("failed", "project_deleted_during_task")
            return
        if lifecycle:
            if written:
                lifecycle.log("succeeded")
            return
        logger.info("任务完成 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

    async def _requeue_changed_provider(
        self, task: dict[str, Any], claim: TaskProjectClaim, exc: DispatchProviderChanged
    ) -> None:
        task_id = task["task_id"]
        try:
            await run_noninterruptible_async(self.queue.persist_execution_provider_id(task_id, exc.actual_provider_id))
        except Exception:
            logger.warning(
                "执行 provider 漂移后的投影刷新失败 task_id=%s provider=%s",
                task_id,
                exc.actual_provider_id,
                exc_info=True,
            )
        try:
            requeued = await run_noninterruptible_async(
                self._requeue_single_task(task_id, project_name=claim.project_name)
            )
        except Exception:
            logger.exception("执行 provider 漂移后回队异常 task_id=%s", task_id)
            requeued = False
        if not requeued:
            if claim.revoked:
                await fail_task_of_deleted_project(self._writes, task)
                return
            logger.error(
                "任务 %s 执行 provider 从 %s 变为 %s，但回队失败",
                task_id,
                exc.claimed_provider_id,
                exc.actual_provider_id,
            )
            await run_noninterruptible_async(
                self._writes.failed(
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

    async def _settle_interrupted_calls_via_ledger(self, *, taskless_started_before: datetime | None) -> int:
        ledger = Ledger(session_factory=self.queue.session_factory)
        return await ledger.settle_interrupted_calls(taskless_started_before=taskless_started_before)

    async def retry_artifact_download(self, task_id: str) -> dict[str, Any]:
        if self._stop_event.is_set():
            raise WorkerStoppingError

        async def handoff() -> dict[str, Any]:
            poll_timeout_seconds = await read_video_poll_timeout_seconds()
            if self._stop_event.is_set():
                raise WorkerStoppingError
            lease = self._ownership.try_acquire(task_id)
            if lease is None:
                row = await self.queue.get_task(task_id)
                if row is None or row["status"] != "failed":
                    raise ValueError("task is not eligible for download retry")
                lease = await self._ownership.acquire(task_id)
            transferred = False
            try:
                async with self._call_settlement_lock:
                    if self._stop_event.is_set():
                        raise WorkerStoppingError
                    task = await self.queue.retry_artifact_download(task_id)
                self._recovery.retry_artifact_download(task, poll_timeout_seconds=poll_timeout_seconds, lease=lease)
                transferred = True
                return task
            except WorkerStoppingError:
                raise
            except Exception:
                self._request_rescan()
                raise
            finally:
                if not transferred:
                    lease.release()

        pending = asyncio.create_task(handoff(), name=f"retry-handoff-{task_id}")
        self._handoffs.add(pending)

        def completed(done: asyncio.Task) -> None:
            self._handoffs.discard(done)
            if not done.cancelled():
                done.exception()

        pending.add_done_callback(completed)
        return await asyncio.shield(pending)
