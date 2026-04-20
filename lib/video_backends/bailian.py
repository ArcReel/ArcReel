"""BailianVideoBackend — 百炼视频生成后端（异步任务模式）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from lib.bailian_shared import BAILIAN_RETRYABLE_ERRORS, DEFAULT_DASHSCOPE_BASE_URL, upload_file_and_get_url
from lib.providers import PROVIDER_BAILIAN
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "wan2.7-t2v"
_CREATE_TASK_URL = "/api/v1/services/aigc/video-generation/video-synthesis"
_TASK_URL = "/api/v1/tasks/{task_id}"
_SUPPORTED_MODELS = {"wan2.7-t2v", "wan2.7-i2v"}
_MODEL_CAPABILITIES: dict[str, set[VideoCapability]] = {
    "wan2.7-t2v": {VideoCapability.TEXT_TO_VIDEO, VideoCapability.GENERATE_AUDIO},
    "wan2.7-i2v": {VideoCapability.IMAGE_TO_VIDEO, VideoCapability.GENERATE_AUDIO},
    "wan2.7-r2v": {VideoCapability.GENERATE_AUDIO},
    "wan2.7-videoedit": set(),
}
_MODEL_CAPS: dict[str, VideoCapabilities] = {
    "wan2.7-t2v": VideoCapabilities(),
    "wan2.7-i2v": VideoCapabilities(first_frame=True, last_frame=True),
    "wan2.7-r2v": VideoCapabilities(reference_images=True, max_reference_images=5),
    "wan2.7-videoedit": VideoCapabilities(),
}
_MODEL_DURATION_RANGES: dict[str, range] = {
    "wan2.7-t2v": range(2, 16),
    "wan2.7-i2v": range(2, 16),
    "wan2.7-r2v": range(2, 11),
    "wan2.7-videoedit": range(2, 11),
}
_MODEL_RESOLUTIONS: dict[str, set[str]] = {
    "wan2.7-t2v": {"480p", "720p", "1080p"},
    "wan2.7-i2v": {"480p", "720p", "1080p"},
    "wan2.7-r2v": {"480p", "720p", "1080p"},
    "wan2.7-videoedit": {"720p", "1080p"},
}
_RESOLUTION_MAP: dict[str, str] = {
    "480p": "480P",
    "720p": "720P",
    "1080p": "1080P",
}


class BailianVideoBackend:
    """百炼视频生成后端，首版支持 wan2.7-t2v 与 wan2.7-i2v。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        if not api_key:
            raise ValueError("百炼视频后端需要 api_key")
        self._api_key = api_key
        self._model = model or DEFAULT_MODEL
        self._base_url = base_url or DEFAULT_DASHSCOPE_BASE_URL
        self._capabilities = _MODEL_CAPABILITIES.get(self._model, _MODEL_CAPABILITIES[DEFAULT_MODEL])

    @property
    def name(self) -> str:
        return PROVIDER_BAILIAN

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return _MODEL_CAPS.get(self._model, _MODEL_CAPS[DEFAULT_MODEL])

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        self._validate_request(request)
        task = await self._create_task(request)
        task_id = task["task_id"]
        request_id = task.get("request_id")
        logger.info("百炼视频任务已提交: model=%s task_id=%s", self._model, task_id)

        result = await poll_with_retry(
            poll_fn=lambda: self._query_task(task_id),
            is_done=lambda r: r.get("output", {}).get("task_status") == "SUCCEEDED",
            is_failed=_extract_error,
            poll_interval=5.0,
            max_wait=900.0,
            retryable_errors=BAILIAN_RETRYABLE_ERRORS,
            label="百炼视频",
            on_progress=lambda r, elapsed: logger.info(
                "百炼视频生成中... 状态: %s, 已等待 %d 秒",
                r.get("output", {}).get("task_status", "UNKNOWN"),
                int(elapsed),
            ),
        )

        video_url = _extract_video_url(result)
        await self._download_video_with_retry(video_url, request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_BAILIAN,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            task_id=task_id,
            request_id=request_id or result.get("request_id"),
            generate_audio=request.generate_audio,
        )

    def _validate_request(self, request: VideoGenerationRequest) -> None:
        if self._model not in _SUPPORTED_MODELS:
            raise ValueError(f"百炼视频模型 {self._model} 暂未实现，首版仅支持 wan2.7-t2v 和 wan2.7-i2v")

        supported_durations = _MODEL_DURATION_RANGES.get(self._model, range(2, 16))
        if request.duration_seconds not in supported_durations:
            min_duration = min(supported_durations)
            max_duration = max(supported_durations)
            raise ValueError(f"模型 {self._model} 仅支持 {min_duration}-{max_duration} 秒视频")

        resolution = request.resolution.lower()
        if resolution not in _MODEL_RESOLUTIONS.get(self._model, {"720p"}):
            allowed = "/".join(sorted(v.upper() for v in _MODEL_RESOLUTIONS[self._model]))
            raise ValueError(f"模型 {self._model} 仅支持分辨率 {allowed}")

        if self._model == "wan2.7-t2v" and request.start_image is not None:
            raise ValueError("wan2.7-t2v 不支持首帧图像输入")
        if self._model == "wan2.7-i2v" and request.start_image is None:
            raise ValueError("wan2.7-i2v 需要提供首帧图像")
        if self._model != "wan2.7-i2v" and request.end_image is not None:
            raise ValueError(f"模型 {self._model} 不支持尾帧图像输入")

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8), retryable_errors=BAILIAN_RETRYABLE_ERRORS)
    async def _create_task(self, request: VideoGenerationRequest) -> dict[str, str | None]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        payload = await self._build_payload(request, headers)
        url = f"{self._base_url}{_CREATE_TASK_URL}"
        logger.info(
            "百炼视频创建任务请求: url=%s headers=%s payload=%s",
            url,
            {**headers, "Authorization": "Bearer ***"},
            payload,
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            data = response.json()
        return {
            "task_id": data["output"]["task_id"],
            "request_id": data.get("request_id"),
        }

    async def _build_payload(self, request: VideoGenerationRequest, headers: dict[str, str]) -> dict[str, Any]:
        input_payload: dict[str, Any] = {"prompt": request.prompt}
        media: list[dict[str, str]] = []

        if self._model == "wan2.7-i2v" and request.start_image:
            image_url = await self._resolve_media_url(Path(request.start_image), headers)
            media.append({"type": "first_frame", "url": image_url})

        if self._model == "wan2.7-i2v" and request.end_image:
            image_url = await self._resolve_media_url(Path(request.end_image), headers)
            media.append({"type": "last_frame", "url": image_url})

        if media:
            input_payload["media"] = media

        return {
            "model": self._model,
            "input": input_payload,
            "parameters": {
                "resolution": _RESOLUTION_MAP.get(request.resolution.lower(), "1080P"),
                "ratio": request.aspect_ratio,
                "duration": request.duration_seconds,
                "prompt_extend": True,
                "watermark": False,
            },
        }

    async def _resolve_media_url(self, path: Path, headers: dict[str, str]) -> str:
        raw = str(path)
        if raw.startswith(("http://", "https://", "oss://")):
            if raw.startswith("oss://"):
                headers["X-DashScope-OssResourceResolve"] = "enable"
            return raw
        oss_url = await upload_file_and_get_url(self._api_key, self._model, path, self._base_url)
        headers["X-DashScope-OssResourceResolve"] = "enable"
        logger.info("百炼视频输入资源已上传: %s -> %s", path.name, oss_url)
        return oss_url

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8), retryable_errors=BAILIAN_RETRYABLE_ERRORS)
    async def _query_task(self, task_id: str) -> dict[str, Any]:
        url = f"{self._base_url}{_TASK_URL.format(task_id=task_id)}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()

    @staticmethod
    @with_retry_async(max_attempts=DOWNLOAD_MAX_ATTEMPTS, backoff_seconds=DOWNLOAD_BACKOFF_SECONDS)
    async def _download_video_with_retry(video_url: str, output_path: Path) -> None:
        await download_video(video_url, output_path)


def _extract_error(result_data: dict[str, Any]) -> str | None:
    status = result_data.get("output", {}).get("task_status")
    if status in {"FAILED", "CANCELED", "CANCELLED"}:
        output = result_data.get("output", {})
        message = output.get("message") or output.get("error_message") or result_data.get("message") or "未知错误"
        return f"百炼视频生成失败: {message}"
    return None


def _extract_video_url(result_data: dict[str, Any]) -> str:
    output = result_data.get("output", {})
    video_url = output.get("video_url") or output.get("video")
    if isinstance(video_url, str) and video_url:
        return video_url

    results = output.get("results") or output.get("videos") or []
    for item in results:
        if isinstance(item, dict):
            for key in ("url", "video_url", "video"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value

    choices = output.get("choices") or []
    for choice in choices:
        content = choice.get("message", {}).get("content", [])
        for item in content:
            if item.get("type") == "video" and item.get("video"):
                return item["video"]

    raise RuntimeError("百炼视频任务结果中未找到视频 URL")
