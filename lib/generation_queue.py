"""
Async generation task queue shared by WebUI and skills.

Wraps TaskRepository with a module-level singleton pattern.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.task_repo import TaskRepository
from lib.task_terminal_events import emit_task_terminal_events

if TYPE_CHECKING:
    from lib.config.resolver import ProviderModel, VideoCapability

logger = logging.getLogger(__name__)


async def video_bucket_for_queued_task(
    *,
    project: dict | None,
    project_name: str | None,
    task_type: str,
    payload: dict[str, Any] | None,
    resource_id: str | None,
) -> VideoCapability | None:
    """视频任务的定桶口径，入队派生与 worker 限流投影共用、与执行侧同步（docs/adr/0054）。

    图生视频 / 宫格 → i2v；参考生视频按自包含 unit 的参考集分流——无参考图的退化镜头
    降级 → i2v，其余 → r2v。剧本 / unit 读不到时回退代表桶 r2v：这只影响 claim 池过滤与限流槽
    路由的精度，执行层会按解析后的实际参考图独立精确定桶。表外任务类型返回 None（不定桶）。
    """
    from lib.config.resolver import VIDEO_BUCKET_BY_TASK_TYPE, get_project_manager
    from lib.reference_video.units import find_reference_unit, reference_unit_video_bucket

    if task_type != "reference_video":
        return VIDEO_BUCKET_BY_TASK_TYPE.get(task_type)
    fallback = VIDEO_BUCKET_BY_TASK_TYPE["reference_video"]
    script_file = (payload or {}).get("script_file")
    if not project_name or not script_file or not resource_id:
        return fallback
    try:
        script = await asyncio.to_thread(get_project_manager().load_script, project_name, str(script_file))
    except Exception:
        logger.debug("reference_video 定桶读取剧本失败，回退 %s 桶", fallback, exc_info=True)
        return fallback
    unit = find_reference_unit(script, str(resource_id))
    if unit is None:
        return fallback
    return reference_unit_video_bucket(unit)


async def _derive_execution_model_for_enqueue(
    *,
    project_name: str | None,
    payload: dict[str, Any] | None,
    task_type: str,
    media_type: str,
    resource_id: str | None,
) -> tuple[ProviderModel, VideoCapability | None] | None:
    """入队时按 project + payload 派生该任务的执行身份，视频任务连同定桶结果一并返回。

    ``provider_id`` 落 task 行供 claim SQL 池过滤使用；视频任务的完整身份另锁进 payload
    （见 ``_pin_video_execution_model``）。与 worker ``_extract_provider`` 同套解析逻辑，
    但失败时返回 ``None``（不强行回 DEFAULT_PROVIDER）——让任务走 ``provider_id IS NULL``
    兜底分支，由 worker claim 后做二次校验，比硬塞一个可能错误的 provider 安全。
    """
    is_video = media_type == "video" or task_type in ("video", "reference_video")
    is_audio = media_type == "audio" or task_type == "tts"
    video_capability: VideoCapability | None = None
    try:
        # 局部导入：lib.config 的解析链会拉进 backend 与自定义供应商装配层，入队路径不为此
        # 付模块级导入代价。
        from lib.config.resolver import ConfigResolver, get_project_manager
        from lib.db import async_session_factory

        project: dict | None = None
        if project_name:
            project = await asyncio.to_thread(get_project_manager().load_project, project_name)

        resolver = ConfigResolver(async_session_factory)
        if is_video:
            video_capability = await video_bucket_for_queued_task(
                project=project,
                project_name=project_name,
                task_type=task_type,
                payload=payload,
                resource_id=resource_id,
            )
            resolved = await resolver.resolve_video_backend(project, payload or {}, capability=video_capability)
        elif is_audio:
            resolved = await resolver.resolve_audio_backend(project, payload or {})
        else:
            # image_edit 必然 i2i 且入队即知（唯一例外，见 docs/adr/0001），按 i2i 槽解析；
            # 其余 image 任务 capability 执行时才定，取 t2i 作代表性 provider。
            capability = "i2i" if task_type == "image_edit" else "t2i"
            resolved = await resolver.resolve_image_backend(project, payload or {}, capability=capability)
    except Exception:
        logger.debug("入队时派生执行身份失败，留 NULL 由 worker 兜底", exc_info=True)
        return None
    if not resolved.provider_id:
        return None
    return resolved, video_capability


def _pin_video_execution_model(
    payload: dict[str, Any] | None,
    *,
    capability: VideoCapability | None,
    execution_model: ProviderModel,
) -> dict[str, Any] | None:
    """把入队解析出的执行身份锁进视频任务 payload 的能力桶键，返回新 payload（不改调用方的 dict）。

    锁定的是「任务真正会执行的 model」，执行与中断续跑据此走同一身份：task 行只存
    provider_id，锁不住 model（``docs/adr/0054``「不静默换模型」）。桶键与复合值形态和解析侧
    payload 层同源（``lib.config.resolver``），此处是该组键的唯一写入方。``capability`` 取
    入队派生的定桶结果（参考路线内逐镜头分流后的桶），锁定键因此与解析用的桶一致——resolver
    的 payload 层只认「任务所属那一个桶」的键。

    非视频任务与不定桶的任务（``capability`` 为 None）、以及解析不出 model 时原样返回。
    """
    if capability is None or not execution_model.model_id:
        return payload
    return {**(payload or {}), f"video_provider_{capability}": execution_model.pair_key}


ACTIVE_TASK_STATUSES = ("queued", "running", "cancelling")
TERMINAL_TASK_STATUSES = ("succeeded", "failed", "cancelled")
TASK_WORKER_LEASE_TTL_SEC = 10.0
TASK_WORKER_HEARTBEAT_SEC = 3.0
TASK_POLL_INTERVAL_SEC = 1.0

_QUEUE_LOCK = threading.Lock()
_QUEUE_INSTANCE: GenerationQueue | None = None


WorkerCancelCallback = Callable[[str], bool]


class GenerationQueue:
    """Async queue manager wrapping TaskRepository."""

    def __init__(
        self,
        *,
        session_factory=None,
    ):
        self._session_factory = session_factory or safe_session_factory
        # in-process callback to signal a running asyncio.Task to cancel;
        # set by server.app boot via set_worker_cancel_callback before worker.start()
        self._worker_cancel_callback: WorkerCancelCallback | None = None

    def set_worker_cancel_callback(self, callback: WorkerCancelCallback | None) -> None:
        """Attach in-process worker cancel callback. Must be called before worker.start()
        so cancel API can deliver signals synchronously (ADR 0006 秒级响应)."""
        self._worker_cancel_callback = callback

    @asynccontextmanager
    async def _task_repo(self) -> AsyncIterator[TaskRepository]:
        """打开一条 TaskRepository 会话，退出时把落地的任务终态发上项目事件总线。

        发布放在会话退出之后而非 repo 内部：repo 内直接发会让前端读到尚未提交的状态。
        body 抛异常时会话不提交、发布也不发生——没有终态落库就没有终态可通告。

        本类所有方法一律经此入口拿 repo，终态事件的发布因而是结构保证而非每处记得挂钩：
        新增写终态的方法自动获得发布，不会漏。
        """
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            yield repo
        emit_task_terminal_events(repo.terminal_events)

    async def enqueue_task(
        self,
        *,
        project_name: str,
        task_type: str,
        media_type: str,
        resource_id: str,
        payload: dict[str, Any] | None = None,
        script_file: str | None = None,
        resource_type: str | None = None,
        source: str = "webui",
        dependency_task_id: str | None = None,
        dependency_group: str | None = None,
        dependency_index: int | None = None,
        user_id: str = DEFAULT_USER_ID,
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        # caller 没传 provider_id → 入队时主动派生一次，让 claim 走 SQL 池过滤快路径；
        # 派生失败留 NULL，走 IS NULL 兜底，由 worker claim 后 _extract_provider 二次校验。
        # 派生成功时视频任务同时把执行 model 锁进 payload，中断续跑据此沿用同一 model。
        # 锁定只发生在这条派生分支上：显式传 provider_id 的调用没有配套的 model 可锁，
        # 需要锁定视频执行 model 的入队点走派生路径。
        if provider_id is None:
            derived = await _derive_execution_model_for_enqueue(
                project_name=project_name,
                payload=payload,
                task_type=task_type,
                media_type=media_type,
                resource_id=resource_id,
            )
            if derived is not None:
                execution_model, video_capability = derived
                provider_id = execution_model.provider_id
                payload = _pin_video_execution_model(
                    payload, capability=video_capability, execution_model=execution_model
                )

        async with self._task_repo() as repo:
            result = await repo.enqueue(
                project_name=project_name,
                task_type=task_type,
                media_type=media_type,
                resource_id=resource_id,
                payload=payload,
                script_file=script_file,
                resource_type=resource_type,
                source=source,
                dependency_task_id=dependency_task_id,
                dependency_group=dependency_group,
                dependency_index=dependency_index,
                user_id=user_id,
                provider_id=provider_id,
            )
        if not result.get("deduped"):
            logger.info("任务入队 task_id=%s type=%s", result["task_id"], task_type)
        else:
            logger.debug("任务去重 task_id=%s", result["task_id"])
        return result

    async def get_active_tasks_for_resources(
        self,
        *,
        project_name: str,
        task_type: str,
        resource_ids: list[str],
        script_file: str | None = None,
        resource_type: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._task_repo() as repo:
            return await repo.get_active_tasks_for_resources(
                project_name=project_name,
                task_type=task_type,
                resource_ids=resource_ids,
                script_file=script_file,
                resource_type=resource_type,
            )

    async def claim_next_task(
        self,
        media_type: str,
        *,
        pool_full_providers: frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        async with self._task_repo() as repo:
            task = await repo.claim_next(media_type, pool_full_providers=pool_full_providers)
        if task:
            logger.debug("任务被领取 task_id=%s", task["task_id"])
        return task

    async def requeue_running_tasks(self, *, limit: int = 1000) -> int:
        async with self._task_repo() as repo:
            recovered = await repo.requeue_running(limit=limit)
        if recovered > 0:
            logger.warning("回收 %d 个 running 任务", recovered)
        return recovered

    async def list_orphan_tasks_on_start(self) -> list[dict[str, Any]]:
        async with self._task_repo() as repo:
            return await repo.list_orphan_tasks_on_start()

    async def persist_provider_job_id(
        self,
        task_id: str,
        job_id: str,
        *,
        endpoint: str | None = None,
        base_url: str | None = None,
    ) -> None:
        async with self._task_repo() as repo:
            await repo.persist_provider_job_id(task_id, job_id, endpoint=endpoint, base_url=base_url)

    async def persist_api_call_id(self, task_id: str, call_id: int) -> None:
        async with self._task_repo() as repo:
            await repo.persist_api_call_id(task_id, call_id)

    async def persist_effective_duration(self, task_id: str, duration_seconds: int) -> None:
        async with self._task_repo() as repo:
            await repo.persist_effective_duration(task_id, duration_seconds)

    async def persist_execution_provider_id(self, task_id: str, provider_id: str) -> None:
        async with self._task_repo() as repo:
            await repo.persist_execution_provider_id(task_id, provider_id)

    async def persist_execution_identity(
        self, task_id: str, *, execution_model: ProviderModel, capability: VideoCapability
    ) -> None:
        """执行前把实际执行身份写回投影列与 payload 锁定键。

        入队按 unit 声明近似定桶锁定身份（``_pin_video_execution_model``），执行按解析后的
        实际参考图定桶，二者分裂时（ad 声明了参考但资产全缺图：锁定 r2v、执行 i2v）陈旧
        桶键在 resume 解析里优先于 ``provider_id`` 列注入，只刷新列锁不住轮询 backend——
        故连同锁定键一起改写：清掉其它桶的键、把实际执行身份写进实际桶的键。非分裂场景
        等值改写，幂等。
        """
        from typing import get_args

        from lib.config.resolver import VideoCapability as _VideoCapabilityAlias

        payload_patch: dict[str, Any] = {
            f"video_provider_{cap}": None for cap in get_args(_VideoCapabilityAlias) if cap != capability
        }
        payload_patch[f"video_provider_{capability}"] = execution_model.pair_key
        async with self._task_repo() as repo:
            await repo.persist_execution_identity(task_id, execution_model.provider_id, payload_patch)

    async def mark_task_succeeded(self, task_id: str, result: dict[str, Any] | None) -> int:
        """Returns rows_affected (0 = 已被外部翻成非 running 终/中间态，worker 走 0-rows-cancelled 协议)."""
        async with self._task_repo() as repo:
            affected = await repo.mark_succeeded(task_id, result)
        if affected > 0:
            logger.info("任务成功 task_id=%s", task_id)
        else:
            logger.info("mark_succeeded 0 rows task_id=%s (已被外部翻状态)", task_id)
        return affected

    async def mark_task_failed(self, task_id: str, error_message: str) -> int:
        """Returns rows_affected (0 = 已被外部翻状态，worker 走 0-rows-cancelled 协议)."""
        async with self._task_repo() as repo:
            affected = await repo.mark_failed(task_id, error_message)
        if affected > 0:
            logger.warning("任务失败 task_id=%s error=%s", task_id, error_message[:200])
        else:
            logger.info("mark_failed 0 rows task_id=%s (已被外部翻状态)", task_id)
        return affected

    async def mark_task_cancelled(self, task_id: str, *, cancelled_by: str = "user") -> int:
        """Worker finally 0-rows-cancelled 协议兜底入口（SQL 守卫 status IN queued|cancelling）。

        Repository 返回 ``{"rows", "cancelling"}``：cancelling 是级联出来的 running 下游
        task_id 列表——这里同步调 worker callback 分发 in-process cancel（与 cancel_task
        模式一致），让父任务 finalize 时打到的 running 子任务也能立刻收到 cancel 信号，
        而不必等它跑完整个 provider 调用。返回 rows 兼容现有 0-rows-cancelled 协议 caller。
        """
        async with self._task_repo() as repo:
            result = await repo.finalize_cancelled(task_id, cancelled_by=cancelled_by)

        callback = self._worker_cancel_callback
        if callback is not None:
            for tid in result.get("cancelling", []):
                try:
                    callback(tid)
                except Exception:
                    logger.exception("worker cancel callback 派发失败 task_id=%s (finalize cascade)", tid)

        return int(result.get("rows", 0))

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        async with self._task_repo() as repo:
            result = await repo.cancel_task(task_id)

        # Repository 返回 cancelling 意图列表 → GenerationQueue 同步分发 in-process 信号。
        # callback 同步调用：worker request_cancel 是 asyncio.Task.cancel()，O(1) 无 I/O。
        # 不用 asyncio.create_task fire-and-forget——会让 API 立刻返回但信号延迟到下次调度，
        # 破坏 ADR 0006 「秒级响应」。callback 不命中（task 已不在 inflight，
        # 例如 finally 阶段刚 pop）是 best-effort 失败：DB 已是 cancelling，worker
        # finally 走 mark_cancelled 兜底（SQL 守卫 IN ('queued','cancelling') 接住）。
        callback = self._worker_cancel_callback
        if callback is not None:
            for tid in result.get("cancelling", []):
                try:
                    callback(tid)
                except Exception:
                    logger.exception("worker cancel callback 派发失败 task_id=%s", tid)

        cancelled_count = len(result.get("cancelled", []))
        cancelling_count = len(result.get("cancelling", []))
        if cancelled_count or cancelling_count:
            logger.info(
                "任务取消 task_id=%s cancelled=%d cancelling=%d",
                task_id,
                cancelled_count,
                cancelling_count,
            )
        return result

    async def get_cancel_preview(self, task_id: str) -> dict[str, Any]:
        async with self._task_repo() as repo:
            return await repo.get_cancel_preview(task_id)

    async def cancel_all_queued(self, project_name: str) -> dict[str, Any]:
        async with self._task_repo() as repo:
            result = await repo.cancel_all_queued(project_name)
        if result["cancelled_count"] > 0:
            logger.info("批量取消 project=%s 共取消 %d 个", project_name, result["cancelled_count"])
        return result

    async def get_cancel_all_preview(self, project_name: str) -> int:
        async with self._task_repo() as repo:
            return await repo.get_cancel_all_preview(project_name)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:

        async with self._task_repo() as repo:
            return await repo.get(task_id)

    async def list_tasks(
        self,
        *,
        project_name: str | None = None,
        status: str | None = None,
        task_type: str | None = None,
        source: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:

        async with self._task_repo() as repo:
            return await repo.list_tasks(
                project_name=project_name,
                status=status,
                task_type=task_type,
                source=source,
                page=page,
                page_size=page_size,
            )

    async def get_task_stats(self, project_name: str | None = None) -> dict[str, int]:

        async with self._task_repo() as repo:
            return await repo.get_stats(project_name=project_name)

    async def acquire_or_renew_worker_lease(
        self,
        *,
        name: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> bool:

        async with self._task_repo() as repo:
            return await repo.acquire_or_renew_lease(
                name=name,
                owner_id=owner_id,
                ttl=ttl_seconds,
            )

    async def release_worker_lease(self, *, name: str, owner_id: str) -> None:

        async with self._task_repo() as repo:
            await repo.release_lease(name=name, owner_id=owner_id)

    async def is_worker_online(self, *, name: str = "default") -> bool:

        async with self._task_repo() as repo:
            return await repo.is_worker_online(name=name)

    async def get_worker_lease(self, *, name: str = "default") -> dict[str, Any] | None:

        async with self._task_repo() as repo:
            return await repo.get_worker_lease(name=name)


def get_generation_queue() -> GenerationQueue:
    global _QUEUE_INSTANCE
    if _QUEUE_INSTANCE is not None:
        return _QUEUE_INSTANCE

    with _QUEUE_LOCK:
        if _QUEUE_INSTANCE is None:
            _QUEUE_INSTANCE = GenerationQueue()
        return _QUEUE_INSTANCE


def read_queue_poll_interval() -> float:
    return max(0.1, float(TASK_POLL_INTERVAL_SEC))
