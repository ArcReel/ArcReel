"""DashScopeVideoBackend — 阿里百炼 HappyHorse / 万相视频生成后端（异步两步式）。

走原生 video-generation/video-synthesis 异步端点：submit 取 task_id → 轮询
GET /tasks/{id} 至 SUCCEEDED → 下载 video_url。覆盖 happyhorse-1.0 与 wan2.7
系列的 t2v / i2v / r2v。schema 依据 docs/dashscope-docs/ 一手核实快照。

注：t2v/i2v 起始帧用 media[{type:"first_frame"}]（first_frame type 在 r2v media
枚举中确权）；尾帧 / 续写字段在一手 docs 未确权，不臆造，故 i2v 仅声明首帧能力。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.dashscope_shared import (
    DASHSCOPE_POLL_INTERVAL_SECONDS,
    DASHSCOPE_RETRYABLE_ERRORS,
    dashscope_failure_reason,
    dashscope_headers,
    dashscope_native_base_url,
    extract_billing_duration,
    extract_task_id,
    extract_video_url,
    image_to_data_uri,
    is_dashscope_expired,
    is_dashscope_terminal,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_DASHSCOPE
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    persist_provider_job_id,
    poll_with_retry,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "happyhorse-1.0-i2v"

_VIDEO_ENDPOINT = "/services/aigc/video-generation/video-synthesis"

_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 60.0

_TV = VideoCapability.TEXT_TO_VIDEO
_IV = VideoCapability.IMAGE_TO_VIDEO
_AUDIO = VideoCapability.GENERATE_AUDIO
_SEED = VideoCapability.SEED_CONTROL

# 按 model id 派发：(VideoCapability 集合, VideoCapabilities)。
# happyhorse-r2v 仅 reference_image（无 first_frame）；wan2.7-r2v 额外支持首帧。
# 音频恒开（无开关参数），统一声明 GENERATE_AUDIO。
_MODEL_PROFILES: dict[str, tuple[set[VideoCapability], VideoCapabilities]] = {
    "happyhorse-1.0-t2v": ({_TV, _AUDIO, _SEED}, VideoCapabilities(first_frame=False)),
    "happyhorse-1.0-i2v": ({_IV, _AUDIO, _SEED}, VideoCapabilities(first_frame=True)),
    "happyhorse-1.0-r2v": (
        {_IV, _AUDIO, _SEED},
        VideoCapabilities(first_frame=False, reference_images=True, max_reference_images=9),
    ),
    "wan2.7-t2v": ({_TV, _AUDIO, _SEED}, VideoCapabilities(first_frame=False)),
    "wan2.7-i2v": ({_IV, _AUDIO, _SEED}, VideoCapabilities(first_frame=True)),
    "wan2.7-r2v": (
        {_IV, _AUDIO, _SEED},
        VideoCapabilities(first_frame=True, reference_images=True, max_reference_images=5),
    ),
}

# 未知 model（如代理中转自定义命名）按通用 i2v/t2v 处理，VideoCapabilities() 默认支持首帧。
_DEFAULT_PROFILE: tuple[set[VideoCapability], VideoCapabilities] = (
    {_TV, _IV, _AUDIO, _SEED},
    VideoCapabilities(),
)


class DashScopeVideoBackend:
    """阿里百炼视频后端（异步 video-synthesis 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._capabilities, self._video_capabilities = _MODEL_PROFILES.get(self._model, _DEFAULT_PROFILE)

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self._video_capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        payload = self._build_payload(request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload)
            logger.info("DashScope 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            if request.task_id is not None:
                await persist_provider_job_id(request.task_id, task_id, provider=PROVIDER_DASHSCOPE)
            return await self._poll_and_build(client, task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 DashScope task：仅 poll + 下载（ADR 0007）。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        media = self._build_media(request)
        input_block: dict = {"prompt": request.prompt}
        if media:
            input_block["media"] = media

        parameters: dict = {
            "resolution": (request.resolution or "720p").upper(),
            "duration": request.duration_seconds,
            # HappyHorse 默认带 "Happy Horse" 水印，显式关闭
            "watermark": False,
        }
        if request.aspect_ratio:
            parameters["ratio"] = request.aspect_ratio
        if request.seed is not None:
            parameters["seed"] = request.seed

        return {
            "model": self._model,
            "input": input_block,
            "parameters": parameters,
        }

    def _build_media(self, request: VideoGenerationRequest) -> list[dict]:
        caps = self._video_capabilities
        media: list[dict] = []
        if caps.first_frame and request.start_image:
            p = Path(request.start_image)
            if p.exists():
                media.append({"type": "first_frame", "url": image_to_data_uri(p)})
            else:
                logger.warning("DashScope start_image 文件不存在，已忽略: %s", p)
        if caps.reference_images and request.reference_images:
            refs = [p for r in request.reference_images if (p := Path(r)).exists()]
            if not refs:
                logger.warning(
                    "DashScope 参考图全部不存在，r2v 将退化为无参考生成: model=%s count=%d",
                    self._model,
                    len(request.reference_images),
                )
            limit = caps.max_reference_images
            if len(refs) > limit:
                logger.warning(
                    "DashScope 参考图数量 %d 超过 model=%s 上限 %d，截断",
                    len(refs),
                    self._model,
                    limit,
                )
                refs = refs[:limit]
            media.extend({"type": "reference_image", "url": image_to_data_uri(p)} for p in refs)
        return media

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retryable_errors=DASHSCOPE_RETRYABLE_ERRORS,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        resp = await client.post(
            f"{self._base_url}{_VIDEO_ENDPOINT}",
            json=payload,
            headers=dashscope_headers(self._api_key, async_mode=True),
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"DashScope 视频提交返回 {resp.status_code}: {resp.text[:500]}")
        return extract_task_id(resp.json())

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}/tasks/{task_id}",
            headers=dashscope_headers(self._api_key),
        )
        resp.raise_for_status()
        return resp.json()

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        # resume 路径下 GET 返回 404（task 完全不存在）直接转 ResumeExpiredError，
        # 不走 poll_with_retry 重试。task_id 24h 过期表现为 200 + task_status=UNKNOWN，
        # 由下方 is_dashscope_expired 兜底（终态返回后判定）。
        async def _gated_poll() -> dict:
            try:
                return await self._poll_once(client, task_id)
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=task_id, provider=PROVIDER_DASHSCOPE) from exc
                raise

        final = await poll_with_retry(
            poll_fn=_gated_poll,
            is_done=is_dashscope_terminal,
            is_failed=dashscope_failure_reason,
            poll_interval=DASHSCOPE_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
            retryable_errors=DASHSCOPE_RETRYABLE_ERRORS,
            label="DashScope",
            on_progress=lambda v, elapsed: logger.info(
                "DashScope 视频生成中... status=%s elapsed=%ds",
                (v.get("output") or {}).get("task_status"),
                int(elapsed),
            ),
        )

        if is_dashscope_expired(final):
            if is_resume:
                raise ResumeExpiredError(
                    job_id=task_id,
                    provider=PROVIDER_DASHSCOPE,
                    message=f"DashScope task expired: {task_id}",
                )
            raise RuntimeError(f"DashScope task expired during generate: {task_id}")

        video_url = extract_video_url(final)
        await self._download_with_retry(video_url, request.output_path)
        logger.info("DashScope 视频下载完成: %s", request.output_path)

        # usage.duration 是真实计费时长（wan2.7-r2v 含输入视频时长），缺失回落请求时长
        billing_duration = extract_billing_duration(final)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            duration_seconds=billing_duration if billing_duration is not None else request.duration_seconds,
            video_uri=video_url,
            task_id=task_id,
            generate_audio=request.generate_audio,
        )

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retryable_errors=DASHSCOPE_RETRYABLE_ERRORS,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        await download_video(video_url, output_path)

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
