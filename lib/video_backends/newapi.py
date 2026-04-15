"""NewAPIVideoBackend — NewAPI 统一视频生成端点后端。

对接 NewAPI 的 /v1/video/generations 接口，支持 Sora / Kling / 即梦 / Wan / Veo
等多家厂商模型，靠请求体的 model 字段分发。
"""

from __future__ import annotations

import asyncio
import base64
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
    IMAGE_MIME_TYPES,
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-v1"

_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 600
_POLL_TIMEOUT_PER_SECOND = 30

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


def _encode_image_to_data_uri(path: Path) -> str:
    mime = IMAGE_MIME_TYPES.get(path.suffix.lower(), "image/png")
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


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
                payload["image"] = _encode_image_to_data_uri(start_path)
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

            final = await self._poll_until_done(
                client, task_id=task_id, max_wait=self._max_wait(request.duration_seconds)
            )
            video_url = final.get("url")
            if not video_url:
                raise RuntimeError(f"NewAPI 任务完成但缺少 url 字段: {final}")

            await self._download(client, video_url, request.output_path)

        meta = final.get("metadata") or {}
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_NEWAPI,
            model=self._model,
            duration_seconds=int(meta.get("duration") or request.duration_seconds),
            task_id=task_id,
            seed=meta.get("seed"),
        )

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retryable_errors=BASE_RETRYABLE_ERRORS + (httpx.HTTPError,),
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

    async def _poll_until_done(self, client: httpx.AsyncClient, *, task_id: str, max_wait: float) -> dict:
        elapsed = 0.0
        while True:
            state = await self._poll_once(client, task_id)
            status = state.get("status")
            if status == "completed":
                return state
            if status == "failed":
                err = (state.get("error") or {}).get("message") or "unknown"
                raise RuntimeError(f"NewAPI 视频生成失败: {err}")
            if elapsed >= max_wait:
                raise TimeoutError(f"NewAPI 视频任务超时（{max_wait:.0f}秒）: task_id={task_id}")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retryable_errors=BASE_RETRYABLE_ERRORS + (httpx.HTTPError,),
    )
    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}/video/generations/{task_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retryable_errors=BASE_RETRYABLE_ERRORS + (httpx.HTTPError,),
    )
    async def _download(self, client: httpx.AsyncClient, url: str, output_path: Path) -> None:
        resp = await client.get(url, headers=self._headers())
        resp.raise_for_status()

        def _write():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)

        await asyncio.to_thread(_write)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
