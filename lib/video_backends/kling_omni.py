"""KlingOmniVideoBackend — 可灵 Omni 官方视频后端。"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from lib.providers import PROVIDER_KLING
from lib.retry import (
    BASE_RETRYABLE_ERRORS,
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
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
from lib.video_backends.kling_omni_types import (
    KlingOmniFrameType,
    KlingOmniImageInput,
    KlingOmniMode,
    KlingOmniRequestOptions,
    KlingOmniShotType,
    KlingOmniSoundMode,
    KlingOmniVideoReferType,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-video-o1"
DEFAULT_BASE_URL = "https://api-beijing.klingai.com"
_CREATE_PATH = "/v1/videos/omni-video"

_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 600.0
_POLL_TIMEOUT_PER_SECOND = 30.0
_MAX_REFERENCE_IMAGES = 7
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

_KLING_RETRYABLE_ERRORS = BASE_RETRYABLE_ERRORS + (httpx.RequestError, httpx.HTTPStatusError)
_IMAGE_TOKEN_RE = re.compile(r"\[图(\d+)]")


class KlingOmniVideoBackend:
    """可灵 Omni 官方视频后端。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("KlingOmniVideoBackend 需要 api_key")
        self._api_key = api_key
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.GENERATE_AUDIO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_KLING

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(
            first_frame=True,
            last_frame=True,
            reference_images=True,
            max_reference_images=_MAX_REFERENCE_IMAGES,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        payload = self._build_payload(request)
        logger.info("Kling Omni 视频生成开始: model=%s", self._model)
        logger.info(
            "调用 %s 视频 API model=%s mode=%s aspect_ratio=%s duration=%s image_count=%d video_count=%d multi_shot=%s",
            self.name,
            payload.get("model_name"),
            payload.get("mode"),
            payload.get("aspect_ratio"),
            payload.get("duration"),
            len(payload.get("image_list") or []),
            len(payload.get("video_list") or []),
            payload.get("multi_shot") is True,
        )

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload)
            logger.info("Kling Omni 任务创建: task_id=%s", task_id)

            final = await poll_with_retry(
                poll_fn=lambda: self._poll_once(client, task_id),
                is_done=lambda state: state.get("task_status") == "succeed",
                is_failed=_extract_failure,
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=self._max_wait(request.duration_seconds),
                retryable_errors=_KLING_RETRYABLE_ERRORS,
                label="Kling Omni",
            )
            video_url = _extract_video_url(final)

        await download_video(video_url, request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            duration_seconds=_extract_duration(final) or request.duration_seconds,
            task_id=task_id,
            generate_audio=payload.get("sound") == KlingOmniSoundMode.ON.value,
        )

    def _build_payload(self, request: VideoGenerationRequest) -> dict[str, Any]:
        options = self._resolve_options(request)
        payload: dict[str, Any] = {
            "model_name": self._model,
            "mode": options.mode.value,
            "watermark_info": {"enabled": options.watermark_enabled},
        }

        if options.callback_url is not None:
            payload["callback_url"] = options.callback_url
        if options.external_task_id is not None:
            payload["external_task_id"] = options.external_task_id

        image_list = self._build_image_list(options.images)
        if image_list:
            payload["image_list"] = image_list

        if options.elements:
            payload["element_list"] = [{"element_id": element.element_id} for element in options.elements]

        video_list = self._build_video_list(options)
        if video_list:
            payload["video_list"] = video_list

        sound = _resolve_sound_mode(request, options)
        if sound is not None:
            payload["sound"] = sound.value

        if self._supports_aspect_ratio(options):
            payload["aspect_ratio"] = request.aspect_ratio

        if self._supports_duration(options):
            payload["duration"] = str(request.duration_seconds)

        if options.multi_shot:
            payload["multi_shot"] = True
            payload["shot_type"] = options.shot_type.value if options.shot_type is not None else None
            if options.shot_type == KlingOmniShotType.CUSTOMIZE:
                total_duration = sum(shot.duration_seconds for shot in options.shots)
                if self._supports_duration(options) and total_duration != request.duration_seconds:
                    raise ValueError(
                        "Kling Omni customize multi-shot 的 shots 时长之和必须等于 request.duration_seconds"
                    )
                payload["prompt"] = ""
                payload["multi_prompt"] = [
                    {
                        "index": shot.index,
                        "prompt": _render_kling_prompt(shot.prompt),
                        "duration": str(shot.duration_seconds),
                    }
                    for shot in options.shots
                ]
            else:
                if not request.prompt.strip():
                    raise ValueError("Kling Omni intelligence multi-shot 需要非空 prompt")
                payload["prompt"] = _render_kling_prompt(request.prompt)
        else:
            if not request.prompt.strip():
                raise ValueError("Kling Omni 单镜头模式需要非空 prompt")
            payload["prompt"] = _render_kling_prompt(request.prompt)

        return payload

    def _resolve_options(self, request: VideoGenerationRequest) -> KlingOmniRequestOptions:
        if request.kling_omni is not None:
            return request.kling_omni

        images: list[KlingOmniImageInput] = []
        if request.start_image is not None:
            images.append(
                KlingOmniImageInput(image_path=Path(request.start_image), frame_type=KlingOmniFrameType.FIRST_FRAME)
            )
        if request.end_image is not None:
            images.append(
                KlingOmniImageInput(image_path=Path(request.end_image), frame_type=KlingOmniFrameType.END_FRAME)
            )
        if request.reference_images:
            images.extend(KlingOmniImageInput(image_path=Path(path)) for path in request.reference_images)

        return KlingOmniRequestOptions(
            images=tuple(images),
            mode=KlingOmniMode.PRO,
            sound=KlingOmniSoundMode.ON if request.generate_audio else KlingOmniSoundMode.OFF,
        )

    def _build_image_list(self, images: tuple[KlingOmniImageInput, ...]) -> list[dict[str, str]]:
        has_first_frame = any(image.frame_type == KlingOmniFrameType.FIRST_FRAME for image in images)
        has_end_frame = any(image.frame_type == KlingOmniFrameType.END_FRAME for image in images)
        if has_end_frame and not has_first_frame:
            raise ValueError("Kling Omni 暂不支持仅传尾帧；设置 end_frame 时必须同时提供 first_frame")

        image_list: list[dict[str, str]] = []
        for image in images:
            payload_item = {"image_url": _encode_image_value(image)}
            if image.frame_type is not None:
                payload_item["type"] = image.frame_type.value
            image_list.append(payload_item)
        return image_list

    def _build_video_list(self, options: KlingOmniRequestOptions) -> list[dict[str, str]]:
        if not options.videos:
            return []

        has_frame_images = any(image.frame_type is not None for image in options.images)
        if has_frame_images and any(video.refer_type == KlingOmniVideoReferType.BASE for video in options.videos):
            raise ValueError("Kling Omni 视频编辑模式与首尾帧不能同时使用")

        video_list: list[dict[str, str]] = []
        for video in options.videos:
            if video.video_path is not None:
                raise ValueError("Kling Omni 当前仅支持 video_url；本地 video_path 需先上传成可访问 URL")
            if not video.video_url:
                raise ValueError("Kling Omni 当前仅支持可访问的 video_url")
            video_list.append(
                {
                    "video_url": video.video_url,
                    "refer_type": video.refer_type.value,
                    "keep_original_sound": "yes" if video.keep_original_sound else "no",
                }
            )
        return video_list

    def _supports_aspect_ratio(self, options: KlingOmniRequestOptions) -> bool:
        has_frame_images = any(image.frame_type is not None for image in options.images)
        has_base_video = any(video.refer_type == KlingOmniVideoReferType.BASE for video in options.videos)
        return not has_frame_images and not has_base_video

    @staticmethod
    def _supports_duration(options: KlingOmniRequestOptions) -> bool:
        return not any(video.refer_type == KlingOmniVideoReferType.BASE for video in options.videos)

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retryable_errors=_KLING_RETRYABLE_ERRORS,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> str:
        resp = await client.post(
            f"{self._base_url}{_CREATE_PATH}",
            json=payload,
            headers=self._headers(),
        )
        _raise_for_status(resp, operation="创建任务")
        body = resp.json()
        data = _unwrap_kling_body(body)
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"Kling Omni 创建任务返回缺少 task_id: {body}")
        return task_id

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
        resp = await client.get(
            f"{self._base_url}{_CREATE_PATH}/{task_id}",
            headers=self._headers(),
        )
        _raise_for_status(resp, operation="轮询任务")
        return _unwrap_kling_body(resp.json())

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)


def _encode_image_value(image: KlingOmniImageInput) -> str:
    if image.image_url:
        return image.image_url
    image_path = image.image_path
    if image_path is None or not image_path.exists():
        raise FileNotFoundError(f"Kling Omni 图片不存在: {image_path}")
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def _resolve_sound_mode(
    request: VideoGenerationRequest,
    options: KlingOmniRequestOptions,
) -> KlingOmniSoundMode | None:
    if options.videos:
        return KlingOmniSoundMode.OFF
    if request.kling_omni is not None:
        return options.sound
    return KlingOmniSoundMode.ON if request.generate_audio else KlingOmniSoundMode.OFF


def _raise_for_status(resp: httpx.Response, *, operation: str) -> None:
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in _RETRYABLE_STATUS_CODES:
            raise
        detail = _response_preview(exc.response)
        message = f"Kling Omni {operation} HTTP {status_code}"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc


def _response_preview(response: httpx.Response) -> str | None:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text[:300]
    try:
        body = response.json()
    except Exception:
        return None
    return str(body)[:300]


def _unwrap_kling_body(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise RuntimeError(f"Kling Omni API 返回格式错误 (非 dict): {body}")
    code = body.get("code", 0)
    if code != 0:
        raise RuntimeError(f"Kling Omni API 错误: {body.get('message') or body}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Kling Omni 返回缺少 data: {body}")
    return data


def _extract_failure(state: dict[str, Any]) -> str | None:
    if state.get("task_status") != "failed":
        return None
    return f"Kling Omni 视频生成失败: {state.get('task_status_msg') or 'unknown'}"


def _extract_video_url(state: dict[str, Any]) -> str:
    task_result = state.get("task_result") or {}
    if not isinstance(task_result, dict):
        raise RuntimeError(f"Kling Omni 任务结果格式错误: {state}")
    videos = task_result.get("videos") or []
    if not isinstance(videos, list) or not videos:
        raise RuntimeError(f"Kling Omni 任务完成但缺少视频 URL: {state}")
    first_video = videos[0]
    if not isinstance(first_video, dict):
        raise RuntimeError(f"Kling Omni 任务完成但缺少视频 URL: {state}")
    video_url = first_video.get("url")
    if not isinstance(video_url, str) or not video_url:
        raise RuntimeError(f"Kling Omni 任务完成但缺少视频 URL: {state}")
    return video_url


def _extract_duration(state: dict[str, Any]) -> int | None:
    task_result = state.get("task_result") or {}
    if not isinstance(task_result, dict):
        return None
    videos = task_result.get("videos") or []
    if not isinstance(videos, list) or not videos:
        return None
    first_video = videos[0]
    if not isinstance(first_video, dict):
        return None
    raw = first_video.get("duration")
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _render_kling_prompt(prompt: str) -> str:
    return _IMAGE_TOKEN_RE.sub(r"<<<image_\1>>>", prompt)
