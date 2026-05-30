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
from sqlalchemy.exc import InterfaceError, OperationalError

from lib.retry import BASE_RETRYABLE_ERRORS, _should_retry, with_retry_async

# `_should_retry` 默认会做字符串模式兜底（"timeout"/"503" 等），
# 而 persist 重试要严格"DB 瞬态错误"语义——业务异常（如
# `ValueError("Connection timed out: rate")`）不该被字符串子串吞掉。
# 显式传 `retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS)` 关掉兜底。

logger = logging.getLogger(__name__)

# DB 瞬态错误集合：sqlite "database is locked"、pg "could not connect" / 连接已关闭。
# 故意不收 DBAPIError 父类——会兜住 IntegrityError/DataError/ProgrammingError 等非瞬态
# 错误（SQL 语法 / 约束违反），重试无意义且拖延 fail-fast。
_PERSIST_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    OperationalError,
    InterfaceError,
    ConnectionError,
    TimeoutError,
)
_PERSIST_BACKOFF_SECONDS: tuple[int, ...] = (1, 2, 4)


@with_retry_async(
    max_attempts=3,
    backoff_seconds=_PERSIST_BACKOFF_SECONDS,
    retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS),
)
async def _persist_with_retry(task_id: str, job_id: str) -> None:
    from lib.generation_queue import get_generation_queue

    await get_generation_queue().persist_provider_job_id(task_id, job_id)


async def persist_provider_job_id(task_id: str, job_id: str, *, provider: str) -> None:
    """Submit 之后立即调：把 job_id 持久化到 DB 让重启可接续。

    Caller 显式传 task_id；DB 瞬态错误最多重试 3 次，业务异常立即抛。
    重试用尽抛异常，由 worker finally 兜底 mark_failed（fail-fast）。
    """
    try:
        await _persist_with_retry(task_id, job_id)
        logger.info("provider_job_id 已持久化 task_id=%s provider=%s job_id=%s", task_id, provider, job_id)
    except Exception as exc:
        logger.error(
            "provider_job_id_persist_failed task_id=%s provider=%s job_id=%s error=%s",
            task_id,
            provider,
            job_id,
            exc,
        )
        raise


@with_retry_async(
    max_attempts=3,
    backoff_seconds=_PERSIST_BACKOFF_SECONDS,
    retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS),
)
async def _persist_api_call_id_with_retry(task_id: str, call_id: int) -> None:
    from lib.generation_queue import get_generation_queue

    await get_generation_queue().persist_api_call_id(task_id, call_id)


async def persist_api_call_id(task_id: str, call_id: int) -> None:
    """Start_call 拿到 call_id 后立即调：把 ApiCall.id 写入 task.payload。

    Resume 路径据此精准翻 pending ApiCall 行而不是按 segment_id+LIMIT 1 模糊匹配。
    与 ``persist_provider_job_id`` 同样走 DB 瞬态错误重试；重试用尽抛异常，由
    media_generator 的外层 try/except 走 finish_call(failed) 翻 pending ApiCall，
    并把异常冒泡给 worker finally 兜底 mark_failed（ADR 0007 fail-fast：未持久化
    的 submit 视为整笔失败——provider 端尚未提交，无需担心「幽灵任务」；若已提交
    则 resume 拿不到 api_call_id 锚定将永远留 pending 账目，必须 fail-fast 让记账
    在原地翻 failed 而不是延后到永远不会发生的 resume）。
    """
    try:
        await _persist_api_call_id_with_retry(task_id, call_id)
        logger.info("api_call_id 已持久化 task_id=%s call_id=%d", task_id, call_id)
    except Exception as exc:
        logger.error(
            "api_call_id_persist_failed task_id=%s call_id=%d error=%s",
            task_id,
            call_id,
            exc,
        )
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


def is_retryable_http_status(status_code: int, *, retry_not_found: bool = False) -> bool:
    """HTTP 状态码 → 是否可重试。

    瞬态错误恒重试：408 Request Timeout / 425 Too Early / 429 Too Many Requests / 5xx。
    404 默认快速失败（确定性"不存在"，如端点拼错）；轮询/下载场景传 retry_not_found=True，
    按"任务提交后短暂未就绪 / 资源未传播"重试。其余 4xx（400/401/403/422 等）确定性客户端
    错误一律快速失败——重试只会拖到 max_wait 超时，白占 worker 槽。
    """
    if status_code in (408, 425, 429):
        return True
    if 500 <= status_code <= 599:
        return True
    if status_code == 404:
        return retry_not_found
    return False


def _retry_http_error(exc: Exception, *, retry_not_found: bool) -> bool:
    """中转视频后端统一重试谓词。

    HTTPStatusError 按 status_code 显式闸门判定，绕开 `_should_retry` 对异常字符串的子串
    兜底——HTTPStatusError 消息含 URL/task_id，其中的 "500"/"503" 子串会被误判为可重试。
    网络/传输错误（RequestError）与基础瞬态错误（Connection/Timeout）重试；其余（含
    ResumeExpiredError 等业务异常）一律快速失败。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code, retry_not_found=retry_not_found)
    return isinstance(exc, (httpx.RequestError, *BASE_RETRYABLE_ERRORS))


def should_retry_submit(exc: Exception) -> bool:
    """创建/提交阶段（POST）重试谓词：404 视为确定性端点错误，快速失败。"""
    return _retry_http_error(exc, retry_not_found=False)


def should_retry_poll(exc: Exception) -> bool:
    """轮询/下载阶段（GET）重试谓词：404 视为"短暂未就绪"，重试。"""
    return _retry_http_error(exc, retry_not_found=True)


async def poll_with_retry[T](
    *,
    poll_fn: Callable[[], Awaitable[T]],
    is_done: Callable[[T], bool],
    is_failed: Callable[[T], str | None],
    poll_interval: float,
    max_wait: float,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    retry_if: Callable[[Exception], bool] | None = None,
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
        retryable_errors: 可重试的异常类型元组（未指定 retry_if 时生效）。
        retry_if: 自定义重试谓词，指定时替代默认的 `_should_retry`，让调用方精确控制
            哪些异常应当重试（如按 HTTP status_code 区分确定性 4xx 与瞬态 5xx）。
        label: 日志前缀（如 "Ark"、"Gemini"）。
        on_progress: 可选的进度回调，每次非终态轮询后调用。
    """
    start = time.monotonic()
    prefix = f"{label} " if label else ""
    predicate = retry_if if retry_if is not None else (lambda e: _should_retry(e, retryable_errors))

    # 先查询再等待：已完成/缓存命中的任务立刻返回，不被 poll_interval 白等一轮。
    while True:
        try:
            result = await poll_fn()
        except Exception as e:
            if not predicate(e):
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

    # Worker 路径下从 task["task_id"] 传入，让 backend submit 后能直接调
    # `persist_provider_job_id(task_id, job_id)` 持久化。
    # 非 worker 路径（grid / 直生 / 测试）保持 None，backend 跳过持久化。
    task_id: str | None = None

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
