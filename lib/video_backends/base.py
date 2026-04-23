"""视频生成服务层核心接口定义与共享工具。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import httpx

from lib.retry import BASE_RETRYABLE_ERRORS, _should_retry, with_retry_async

logger = logging.getLogger(__name__)

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

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= max_wait:
            raise TimeoutError(f"{prefix}任务超时（{max_wait:.0f}秒）")

        await asyncio.sleep(poll_interval)

        try:
            result = await poll_fn()
        except Exception as e:
            if _should_retry(e, retryable_errors):
                logger.warning("%s轮询异常（将重试）: %s - %s", prefix, type(e).__name__, str(e)[:200])
                continue
            raise

        error_msg = is_failed(result)
        if error_msg is not None:
            raise RuntimeError(error_msg)

        if is_done(result):
            return result

        if on_progress is not None:
            on_progress(result, time.monotonic() - start)


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

    # Veo 特有
    negative_prompt: str | None = None

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
