"""视频生成服务层核心接口定义与共享工具。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import httpx

from lib.retry import BASE_RETRYABLE_ERRORS, _should_retry, with_retry_async

logger = logging.getLogger(__name__)


# Worker 在 _process_task / _process_resume_task 入口 set 当前 task_id；
# backend.generate 拿到 job_id 后调 persist_provider_job_id 让 ADR 0007
# 「重启接续轮询不重 submit」可达。非 worker 路径（测试 / grid / 直生）
# get(None) 默认返回 None，helper 自然 no-op。
_CURRENT_TASK_ID: ContextVar[str | None] = ContextVar("arcreel_current_task_id", default=None)

# 重启自愈：worker _process_resume_task 入口 set 上轮已持久化的 job_id；
# backend.generate 检测到该 var 时跳过 submit、直接 resume_video（接续轮询）。
# 走完后 worker 清空 var。
_RESUME_JOB_ID: ContextVar[str | None] = ContextVar("arcreel_resume_job_id", default=None)


def set_current_task_id(task_id: str | None) -> object:
    """Worker 入口 set 当前 task_id；返回 token，由 caller reset。"""
    return _CURRENT_TASK_ID.set(task_id)


def reset_current_task_id(token: object) -> None:
    _CURRENT_TASK_ID.reset(token)  # pyright: ignore[reportArgumentType]


def set_resume_job_id(job_id: str | None) -> object:
    return _RESUME_JOB_ID.set(job_id)


def reset_resume_job_id(token: object) -> None:
    _RESUME_JOB_ID.reset(token)  # pyright: ignore[reportArgumentType]


def get_resume_job_id() -> str | None:
    return _RESUME_JOB_ID.get(None)


async def persist_job_id_if_in_task_context(job_id: str) -> None:
    """Submit 之后立即调：把 job_id 持久化到 DB 让重启可接续。

    非 worker 路径 (contextvar 未 set) → no-op；worker 路径失败抛异常，
    由 worker finally 兜底 mark_failed（ADR 0007 fail-fast）。
    """
    tid = _CURRENT_TASK_ID.get(None)
    if tid is None:
        return
    try:
        from lib.generation_queue import get_generation_queue

        await get_generation_queue().persist_provider_job_id(tid, job_id)
        logger.info("provider_job_id 已持久化 task_id=%s job_id=%s", tid, job_id)
    except Exception:
        logger.exception("provider_job_id 持久化失败 task_id=%s job_id=%s", tid, job_id)
        raise


class ResumeExpiredError(RuntimeError):
    """Provider 端 job 已过期或未找到——重启自愈无法接续，须走 mark_failed。

    Worker finally 据 ``isinstance(exc, ResumeExpiredError)`` 给 error_message
    加 ``[resume_expired]`` 前缀（agent-facing，i18n 豁免），运维分析可见。
    """

    def __init__(self, *, job_id: str, provider: str, message: str = "") -> None:
        self.job_id = job_id
        self.provider = provider
        super().__init__(message or f"resume job {job_id} expired or not found on provider {provider}")


# 图片后缀 → MIME 类型映射（多个后端共用）
IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


async def poll_with_retry[T](
    *,
    poll_fn: Callable[[], Awaitable[T]],
    is_done: Callable[[T], bool],
    is_failed: Callable[[T], str | None],
    poll_interval: float,
    max_wait: float,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    label: str = "",
    on_progress: Callable[[T, float], None] | None = None,
) -> T:
    """通用异步轮询辅助函数，带瞬态错误重试和超时控制。

    Args:
        poll_fn: 每次轮询调用的异步函数，返回最新状态。
        is_done: 判断轮询结果是否表示任务完成。
        is_failed: 判断轮询结果是否表示任务失败，返回错误信息或 None。
        poll_interval: 两次轮询之间的间隔（秒）。
        max_wait: 最大等待时间（秒），超时抛出 TimeoutError。
        retryable_errors: 可重试的异常类型元组。
        label: 日志前缀（如 "Ark"、"Gemini"）。
        on_progress: 可选的进度回调，每次非终态轮询后调用。
    """
    start = time.monotonic()
    prefix = f"{label} " if label else ""

    # 先查询再等待：已完成/缓存命中的任务立刻返回，不被 poll_interval 白等一轮。
    while True:
        try:
            result = await poll_fn()
        except Exception as e:
            if not _should_retry(e, retryable_errors):
                raise
            logger.warning("%s轮询异常（将重试）: %s - %s", prefix, type(e).__name__, str(e)[:200])
        else:
            error_msg = is_failed(result)
            if error_msg is not None:
                raise RuntimeError(error_msg)
            if is_done(result):
                return result
            if on_progress is not None:
                on_progress(result, time.monotonic() - start)

        if time.monotonic() - start >= max_wait:
            raise TimeoutError(f"{prefix}任务超时（{max_wait:.0f}秒）")
        await asyncio.sleep(poll_interval)


@with_retry_async()
async def download_video(url: str, output_path: Path, *, timeout: int = 120) -> None:
    """从 URL 流式下载视频到本地文件（含瞬态错误重试）。"""
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    async with httpx.AsyncClient() as http_client:
        async with http_client.stream("GET", url, timeout=timeout) as resp:
            if resp.status_code >= 400:
                # 流式模式下需先读取响应体，否则 HTTPStatusError.response.text 不可用
                await resp.aread()
            resp.raise_for_status()
            # 异步流式读取所有 chunk，然后一次 to_thread 完成整段写入，
            # 避免对每个 64KB 分片调度一次线程池任务（评审反馈 #279）。
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                chunks.append(chunk)

            def _write_all() -> None:
                with open(output_path, "wb") as f:
                    for chunk in chunks:
                        f.write(chunk)

            await asyncio.to_thread(_write_all)


class VideoCapabilityError(RuntimeError):
    """视频后端能力不匹配（如 duration ↔ supported_durations）。

    与 ImageCapabilityError 对称：不携带本地化字符串，只带稳定 code + 上下文 params；
    Worker 捕获后用 i18n_translate(code, **params) 渲染到 task.error_message。
    """

    def __init__(self, code: str, **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


@dataclass
class VideoCapabilities:
    """Declares what a video backend supports."""

    first_frame: bool = True
    last_frame: bool = False
    reference_images: bool = False
    max_reference_images: int = 0


class VideoCapability(StrEnum):
    """视频后端支持的能力枚举。"""

    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    GENERATE_AUDIO = "generate_audio"
    NEGATIVE_PROMPT = "negative_prompt"
    VIDEO_EXTEND = "video_extend"
    SEED_CONTROL = "seed_control"
    FLEX_TIER = "flex_tier"


@dataclass
class VideoGenerationRequest:
    """通用视频生成请求。各 Backend 忽略不支持的字段。"""

    prompt: str
    output_path: Path
    aspect_ratio: str = "9:16"
    duration_seconds: int = 5
    resolution: str | None = None
    start_image: Path | None = None
    end_image: Path | None = None  # For first_last mode
    reference_images: list[Path] | None = None  # For multi-reference mode
    generate_audio: bool = True

    # 项目上下文（用于构建文件服务 URL 等）
    project_name: str | None = None

    # Seedance 特有
    service_tier: str = "default"
    seed: int | None = None


@dataclass
class VideoGenerationResult:
    """通用视频生成结果。"""

    video_path: Path
    provider: str
    model: str
    duration_seconds: int

    video_uri: str | None = None
    seed: int | None = None
    usage_tokens: int | None = None
    task_id: str | None = None
    generate_audio: bool | None = None


class VideoBackend(Protocol):
    """视频生成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> set[VideoCapability]: ...

    @property
    def video_capabilities(self) -> VideoCapabilities: ...

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult: ...

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续 provider 上已发起的 job：轮询 + 下载，不重新 submit（ADR 0007）。

        未实现的 backend 抛 ``NotImplementedError``；orphan handler 据此走
        ``[resume_unsupported]``。provider 端 job 过期/未找到抛 ``ResumeExpiredError``
        走 ``[resume_expired]``。
        """
        raise NotImplementedError
