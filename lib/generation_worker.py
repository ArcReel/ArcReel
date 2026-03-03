"""
Background worker that consumes generation tasks from SQLite queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict

logger = logging.getLogger(__name__)

from lib.generation_queue import (
    GenerationQueue,
    TASK_POLL_INTERVAL_SEC,
    TASK_WORKER_HEARTBEAT_SEC,
    TASK_WORKER_LEASE_TTL_SEC,
    get_generation_queue,
)


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


class GenerationWorker:
    """Queue worker with separate image/video lanes and single-active lease."""

    def __init__(
        self,
        queue: GenerationQueue | None = None,
        lease_name: str = "default",
    ):
        self.queue = queue or get_generation_queue()
        self.lease_name = lease_name
        self.owner_id = f"worker-{uuid.uuid4().hex[:10]}"

        self.image_workers = _read_int_env("STORYBOARD_MAX_WORKERS", 3, minimum=1)
        self.video_workers = _read_int_env("VIDEO_MAX_WORKERS", 2, minimum=1)
        self.lease_ttl = max(1.0, float(TASK_WORKER_LEASE_TTL_SEC))
        self.heartbeat_interval = max(0.5, float(TASK_WORKER_HEARTBEAT_SEC))
        self.poll_interval = max(0.1, float(TASK_POLL_INTERVAL_SEC))

        self._main_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._image_inflight: Dict[str, asyncio.Task] = {}
        self._video_inflight: Dict[str, asyncio.Task] = {}
        self._owns_lease = False

    def reload_limits_from_env(self) -> None:
        """Reload worker concurrency limits from environment variables."""
        self.image_workers = _read_int_env("STORYBOARD_MAX_WORKERS", 3, minimum=1)
        self.video_workers = _read_int_env("VIDEO_MAX_WORKERS", 2, minimum=1)

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

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                had_lease = self._owns_lease
                self._owns_lease = self.queue.acquire_or_renew_worker_lease(
                    name=self.lease_name,
                    owner_id=self.owner_id,
                    ttl_seconds=self.lease_ttl,
                )

                if self._owns_lease and not had_lease:
                    logger.info("获得 worker lease (owner=%s)", self.owner_id)
                if had_lease and not self._owns_lease:
                    logger.warning("失去 worker lease (owner=%s)", self.owner_id)

                await self._drain_finished_tasks()

                # 仅在"新获得 lease 且本实例无在途任务"时回收 running 任务，
                # 避免 lease 短暂抖动时把自己正在执行的任务错误回队。
                if (
                    self._owns_lease
                    and not had_lease
                    and not self._image_inflight
                    and not self._video_inflight
                ):
                    self.queue.requeue_running_tasks()

                if not self._owns_lease:
                    await asyncio.sleep(self.heartbeat_interval)
                    continue

                claimed_any = False

                while len(self._image_inflight) < self.image_workers:
                    task = self.queue.claim_next_task(media_type="image")
                    if not task:
                        break
                    claimed_any = True
                    self._image_inflight[task["task_id"]] = asyncio.create_task(
                        self._process_task(task),
                        name=f"generation-image-{task['task_id']}",
                    )

                while len(self._video_inflight) < self.video_workers:
                    task = self.queue.claim_next_task(media_type="video")
                    if not task:
                        break
                    claimed_any = True
                    self._video_inflight[task["task_id"]] = asyncio.create_task(
                        self._process_task(task),
                        name=f"generation-video-{task['task_id']}",
                    )

                if claimed_any:
                    await asyncio.sleep(0.05)
                else:
                    await asyncio.sleep(self.poll_interval)

            await self._wait_inflight_completion()
        finally:
            if self._owns_lease:
                self.queue.release_worker_lease(name=self.lease_name, owner_id=self.owner_id)
            self._owns_lease = False

    async def _drain_finished_tasks(self) -> None:
        for inflight in (self._image_inflight, self._video_inflight):
            done_ids = [task_id for task_id, task in inflight.items() if task.done()]
            for task_id in done_ids:
                task = inflight.pop(task_id)
                try:
                    await task
                except Exception:
                    logger.debug("已处理的任务 %s 异常已在 _process_task 中记录", task_id)

    async def _wait_inflight_completion(self) -> None:
        pending_tasks = [*self._image_inflight.values(), *self._video_inflight.values()]
        if not pending_tasks:
            return
        await asyncio.gather(*pending_tasks, return_exceptions=True)
        self._image_inflight.clear()
        self._video_inflight.clear()

    async def _process_task(self, task: Dict[str, Any]) -> None:
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")
        logger.info("开始处理任务 %s (type=%s)", task_id, task_type)
        try:
            from server.services.generation_tasks import execute_generation_task

            result = await asyncio.to_thread(execute_generation_task, task)
            self.queue.mark_task_succeeded(task_id, result)
            logger.info("任务完成 %s (type=%s)", task_id, task_type)
        except Exception as exc:
            logger.exception("任务失败 %s (type=%s)", task_id, task_type)
            self.queue.mark_task_failed(task_id, str(exc))
