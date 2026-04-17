"""NewAPIVideoBackend — NewAPI 统一视频生成端点后端。

对接 NewAPI 的 /v1/video/generations 接口，支持 Sora / Kling / 即梦 / Wan / Veo
等多家厂商模型，靠请求体的 model 字段分发。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.providers import PROVIDER_NEWAPI
from lib.retry import (
    BASE_RETRYABLE_ERRORS,
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-v1"

_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 600
_POLL_TIMEOUT_PER_SECOND = 30

# HTTPStatusError 不继承 RequestError，必须显式列出以便 5xx 响应走类型匹配而非字符串匹配
_NEWAPI_RETRYABLE_ERRORS = BASE_RETRYABLE_ERRORS + (httpx.RequestError, httpx.HTTPStatusError)

# 超过此阈值的起始图会触发 warning，NewAPI 聚合后端常见 4MB 请求体上限
_LARGE_IMAGE_WARN_BYTES = 4 * 1024 * 1024

_SIZE_MAP: dict[tuple[str, str], tuple[int, int]] = {
    ("720p", "9:16"): (720, 1280),
    ("720p", "16:9"): (1280, 720),
    ("1080p", "9:16"): (1080, 1920),
    ("1080p", "16:9"): (1920, 1080),
}
_DEFAULT_SIZE: tuple[int, int] = (720, 1280)


def _resolve_size(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    size = _SIZE_MAP.get((resolution, aspect_ratio))
    if size is None:
        logger.warning(
            "NewAPIVideoBackend 未知 resolution+aspect 组合 (%s, %s)，回退到默认 %dx%d",
            resolution,
            aspect_ratio,
            *_DEFAULT_SIZE,
        )
        return _DEFAULT_SIZE
    return size


class NewAPIVideoBackend:
    """NewAPI 统一视频生成端点后端。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("NewAPIVideoBackend 需要 api_key")
        if not base_url:
            raise ValueError("NewAPIVideoBackend 需要 base_url")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_NEWAPI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(reference_images=False, max_reference_images=0)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        width, height = _resolve_size(request.resolution, request.aspect_ratio)
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "width": width,
            "height": height,
            "duration": request.duration_seconds,
            "n": 1,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.negative_prompt:
            payload.setdefault("metadata", {})["negative_prompt"] = request.negative_prompt
        if request.start_image:
            start_path = Path(request.start_image)
            if start_path.exists():
                size_bytes = start_path.stat().st_size
                if size_bytes > _LARGE_IMAGE_WARN_BYTES:
                    logger.warning(
                        "NewAPI start_image 较大 (%.1fMB)，Base64 编码后可能触发服务端请求体限制",
                        size_bytes / 1024 / 1024,
                    )
                # 延迟导入避免 image_backends ↔ video_backends 循环依赖
                from lib.image_backends.base import image_to_base64_data_uri

                payload["image"] = image_to_base64_data_uri(start_path)
            else:
                logger.warning("start_image 文件不存在，已忽略: %s", start_path)
        if request.reference_images:
            logger.warning(
                "NewAPIVideoBackend 不支持多张参考图（reference_images=%d），已忽略",
                len(request.reference_images),
            )

        logger.info("NewAPI 视频生成开始: model=%s, duration=%s", self._model, request.duration_seconds)

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload)
            logger.info("NewAPI 任务创建: task_id=%s", task_id)

            final = await poll_with_retry(
                poll_fn=lambda: self._poll_once(client, task_id),
                is_done=lambda s: s.get("status") == "completed",
                is_failed=_extract_failure,
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=self._max_wait(request.duration_seconds),
                retryable_errors=_NEWAPI_RETRYABLE_ERRORS,
                label="NewAPI",
            )
            video_url = final.get("url")
            if not video_url:
                raise RuntimeError(f"NewAPI 任务完成但缺少 url 字段: {final}")

        # 流式下载，不携带 Authorization 头（视频 URL 常为 CDN/OSS，避免 API Key 泄露）
        await self._download_with_retry(video_url, request.output_path)

        meta = final.get("metadata") or {}
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_NEWAPI,
            model=self._model,
            duration_seconds=int(float(meta.get("duration") or request.duration_seconds)),
            task_id=task_id,
            seed=meta.get("seed"),
        )

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retryable_errors=_NEWAPI_RETRYABLE_ERRORS,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        resp = await client.post(
            f"{self._base_url}/video/generations",
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()
        body = resp.json()
        task_id = body.get("task_id")
        if not task_id:
            raise RuntimeError(f"NewAPI 创建任务返回体缺少 task_id: {body}")
        return task_id

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}/video/generations/{task_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retryable_errors=_NEWAPI_RETRYABLE_ERRORS,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """对齐 OpenAI/Ark 的下载重试策略（5 次、5/10/20/40 秒），与生成阶段独立。"""
        await download_video(video_url, output_path)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)


def _extract_failure(state: dict) -> str | None:
    if state.get("status") != "failed":
        return None
    err = (state.get("error") or {}).get("message") or "unknown"
    return f"NewAPI 视频生成失败: {err}"
