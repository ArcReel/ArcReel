"""Sub2API Grok asynchronous video backend."""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from lib.config.url_utils import ensure_openai_base_url
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.providers import PROVIDER_GROK
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    ProviderJobIdPersistenceMixin,
    ResumeExpiredError,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    poll_with_retry,
    should_retry_download,
    should_retry_poll,
    submit_post,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 5.0
_MAX_WAIT_SECONDS = 15 * 60.0
_DONE_STATUS = "done"
_FAILED_STATUSES = frozenset({"failed", "cancelled", "canceled", "expired", "error"})
_PENDING_STATUSES = frozenset({"pending", "queued", "processing", "in_progress"})
_KNOWN_STATUSES = _PENDING_STATUSES | _FAILED_STATUSES | {_DONE_STATUS}


def _image_data_uri(path: Path) -> str:
    mime = IMAGE_MIME_TYPES.get(path.suffix.lower(), "image/png")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def normalize_api_root(base_url: str | None) -> str:
    normalized = ensure_openai_base_url(base_url)
    if normalized is None:
        raise ValueError("base_url 不能为空")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url 必须是 http 或 https URL")
    return normalized


def build_request_body(model: str, request: VideoGenerationRequest) -> dict[str, object]:
    if not request.prompt.strip():
        raise ValueError("prompt 不能为空")
    for field in ("end_image", "reference_images", "reference_audio_files"):
        if getattr(request, field):
            raise ValueError(f"Grok Sub2API video 不支持 {field}")
    body: dict[str, object] = {
        "model": model,
        "prompt": request.prompt,
        "duration": request.duration_seconds,
        "aspect_ratio": request.aspect_ratio,
    }
    if request.resolution is not None:
        body["resolution"] = request.resolution
    if request.start_image is not None:
        if not request.start_image.is_file():
            raise ValueError(f"start_image 不存在: {request.start_image}")
        body["image"] = {"url": _image_data_uri(request.start_image)}
    return body


def _request_id(payload: dict[str, Any]) -> str:
    for container_key, key in (
        (None, "request_id"),
        (None, "id"),
        ("data", "request_id"),
        ("data", "id"),
        ("video", "request_id"),
        ("video", "id"),
        (None, "task_id"),
        ("data", "task_id"),
        ("video", "task_id"),
    ):
        container = payload if container_key is None else payload.get(container_key)
        value = container.get(key) if isinstance(container, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("Sub2API 创建响应缺少 request_id")


def _duration(payload: dict[str, Any], fallback: int) -> int:
    video = payload.get("video")
    raw = video.get("duration") if isinstance(video, dict) else payload.get("duration")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return fallback
    try:
        parsed = float(raw)
        if 0 < parsed <= MAX_BILLED_DURATION_SECONDS:
            rounded = int(parsed + 0.5)
            if rounded > 0:
                return rounded
    except (TypeError, ValueError, OverflowError):
        pass
    return fallback


class GrokSub2APIVideoBackend(ProviderJobIdPersistenceMixin):
    """Sub2API Grok OAuth video transport with resumable request IDs."""

    DEFAULT_MODEL = "grok-imagine-video-1.5"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        poll_interval_seconds: float = _POLL_INTERVAL_SECONDS,
        max_wait_seconds: float = _MAX_WAIT_SECONDS,
    ) -> None:
        if api_key is None or not api_key.strip():
            raise ValueError("api_key 不能为空")
        selected_model = model or self.DEFAULT_MODEL
        if selected_model != "grok-imagine-video" and not selected_model.startswith("grok-imagine-video-"):
            raise ValueError("model 必须属于 grok-imagine-video 系列")
        self._api_key = api_key
        self._model = selected_model
        self._api_root = normalize_api_root(base_url)
        self._poll_interval_seconds = poll_interval_seconds
        self._max_wait_seconds = max_wait_seconds

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        return VideoCapabilities(first_frame=True)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        payload = build_request_body(self._model, request)
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            response = await submit_post(
                lambda: client.post(
                    f"{self._api_root}/videos/generations",
                    headers=self._headers,
                    json=payload,
                ),
                provider=PROVIDER_GROK,
            )
            response_payload = response.json()
            if not isinstance(response_payload, dict):
                raise ValueError("Sub2API 创建响应必须是 JSON 对象")
            request_id = _request_id(response_payload)
            await self._persist_provider_job_id(
                request,
                request_id,
                provider=PROVIDER_GROK,
                endpoint=self._api_root,
            )
            return await self._poll_and_download(client, request_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            return await self._poll_and_download(client, job_id, request, resumed=True)

    async def _poll_and_download(
        self,
        client: httpx.AsyncClient,
        request_id: str,
        request: VideoGenerationRequest,
        *,
        resumed: bool = False,
    ) -> VideoGenerationResult:
        encoded_id = quote(request_id, safe="")
        status_url = f"{self._api_root}/videos/generations/{encoded_id}"

        async def _poll() -> dict[str, Any]:
            response = await client.get(status_url, headers=self._headers)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if resumed and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=request_id, provider=PROVIDER_GROK) from exc
                raise
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Sub2API 状态响应必须是 JSON 对象")
            raw_status = payload.get("status")
            status = raw_status.strip().lower() if isinstance(raw_status, str) else ""
            if status not in _KNOWN_STATUSES:
                raise ValueError(f"Sub2API 返回未知视频状态: {raw_status!r}")
            payload["status"] = status
            return payload

        final = await poll_with_retry(
            poll_fn=_poll,
            is_done=lambda state: state.get("status") == _DONE_STATUS,
            is_failed=lambda state: (
                f"Sub2API Grok 视频任务失败: {state.get('status')}" if state.get("status") in _FAILED_STATUSES else None
            ),
            poll_interval=self._poll_interval_seconds,
            max_wait=self._max_wait_seconds,
            retry_if=should_retry_poll,
            label="Sub2API Grok",
        )

        content_url = f"{status_url}/content"
        await self._download(client, content_url, request.output_path)
        logger.info("Sub2API Grok 视频下载完成: request_id=%s", request_id)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            duration_seconds=_duration(final, request.duration_seconds),
            video_uri=content_url,
            task_id=request_id,
            generate_audio=True,
        )

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download(self, client: httpx.AsyncClient, content_url: str, output_path: Path) -> None:
        content_response = await client.get(content_url, headers=self._headers)
        content_response.raise_for_status()
        content = content_response.content
        if not content:
            raise ValueError("Sub2API video content is empty")
        temporary = output_path.with_name(f".{output_path.name}.grok-sub2api.part")

        def _write_atomic() -> None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.unlink(missing_ok=True)
            try:
                temporary.write_bytes(content)
                temporary.replace(output_path)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise

        await asyncio.to_thread(_write_atomic)
