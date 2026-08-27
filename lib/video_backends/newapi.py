"""NewAPIVideoBackend — NewAPI 统一视频生成端点后端。

对接 NewAPI 的 /v1/video/generations 接口，支持 Sora / Kling / 即梦 / Wan / Veo
等多家厂商模型，靠请求体的 model 字段分发。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.aspect_size import VIDEO_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_NEWAPI
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    TERMINAL_PROVIDER_STATUSES,
    ProviderJobIdPersistenceMixin,
    ProviderJobStatus,
    ResumeExpiredError,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    extract_provider_error_message,
    first_mapping_by_paths,
    first_str_by_paths,
    normalize_provider_status,
    poll_with_retry,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-v1"


# 超过此阈值的起始图会触发 warning，NewAPI 聚合后端常见 4MB 请求体上限
_LARGE_IMAGE_WARN_BYTES = 4 * 1024 * 1024

# 视频标准尺寸对齐 8 的倍数（1920x1080 / 1080x1920 等；1080 非 16 的倍数），主流视频模型通用。
_VIDEO_ROUND_TO = 8

# 状态与视频地址的多路径优先级表：该端点的回包形状跨部署不统一，既有扁平 ``{status, url}``，
# 也有 ``{"code": ..., "data": {...}}`` 包装体（视频地址字段名另有 ``result_url`` 一说）。
# 故按优先级并集探测而非二选一：扁平路径恒排表首，包装路径仅在其缺失时兜底。
_STATUS_PATHS: tuple[tuple[str | int, ...], ...] = (("status",), ("data", "status"))
_VIDEO_URL_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("url",),
    ("result_url",),
    ("data", "url"),
    ("data", "result_url"),
)
# metadata 与状态、视频地址同源：包装体形状下它一并落在 ``data`` 里。实际时长是计费依据，
# 取不到会退回请求时长记账。
_METADATA_PATHS: tuple[tuple[str | int, ...], ...] = (("metadata",), ("data", "metadata"))


def _task_status(state: object) -> ProviderJobStatus:
    """回包 → canonical 状态。端点跨厂商分发，状态串随底层厂商透传，必须过共享归一。"""
    return normalize_provider_status(first_str_by_paths(state, _STATUS_PATHS))


def _resolve_size(resolution: str | None, aspect_ratio: str) -> tuple[int, int]:
    """比例优先、清晰度其次：短边来自 resolution（档位 / 自定义 / None 兜底 720P），
    比例精确来自 aspect_ratio、对齐 8 的倍数。修复旧表 1080 不被整除 + 仅 9:16/16:9 两档。
    """
    short = resolution_to_short_edge(resolution, tier_map=VIDEO_TIER_SHORT_EDGE)
    return aspect_size(aspect_ratio, short, round_to=_VIDEO_ROUND_TO)


class NewAPIVideoBackend(ProviderJobIdPersistenceMixin):
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

    @property
    def name(self) -> str:
        return PROVIDER_NEWAPI

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算 caps —— 不构造 SDK client（无需 api_key）。

        中转端点不接受参考图；当前全系模型能力一致，不按 model_id 分支。
        instance property 委托至此，保持 backend 为单一真相源。

        音轨形态取默认值（开关可控）：中转端点的上游 model 由用户填写、协议本身不约定音轨语义，
        没有可依据的判据。按「无信号不收紧」处理，不把能力不明的 model 谎报成开关失效。
        """
        return VideoCapabilities(max_reference_images=0)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

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
        logger.info("调用 %s 视频 SDK payload=%s", self.name, format_kwargs_for_log(payload))

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            provider_task_id = await self._create_task(client, payload)
            logger.info("NewAPI 任务创建: task_id=%s", provider_task_id)
            await self._persist_provider_job_id(request, provider_task_id, provider=PROVIDER_NEWAPI)
            return await self._poll_and_build(client, provider_task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 NewAPI task：仅 poll + 下载。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        # is_done 纯谓词：成功 / 失败 / 过期三档均视为终态；caller 按 is_resume
        # flag 决定 expired 抛 RuntimeError（generate）还是 ResumeExpiredError（resume）。
        # resume 路径下 404 由 _gated_poll 直接抛 ResumeExpiredError：should_retry_poll 把
        # 轮询 404 当作"短暂未就绪"重试，对已过期的 resume 任务会一直重到 max_wait 超时、
        # 永不落 [resume_expired]，对应 pending ApiCall 也不走 failed/cost=0 路径，故在此一击
        # 转终态异常。非 resume 的 4xx 重新抛出，交 should_retry_poll 按 status_code 分流。
        async def _gated_poll() -> dict:
            try:
                return await self._poll_once(client, task_id)
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=task_id, provider=PROVIDER_NEWAPI) from exc
                raise

        final = await poll_with_retry(
            poll_fn=_gated_poll,
            is_done=lambda state: _task_status(state) in TERMINAL_PROVIDER_STATUSES,
            is_failed=_extract_failure,
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="NewAPI",
        )

        if _task_status(final) is ProviderJobStatus.EXPIRED:
            if is_resume:
                raise ResumeExpiredError(
                    job_id=task_id,
                    provider=PROVIDER_NEWAPI,
                    message=f"NewAPI task expired: {task_id}",
                )
            raise RuntimeError(f"NewAPI task expired during generate: {task_id}")

        video_url = first_str_by_paths(final, _VIDEO_URL_PATHS)
        if not video_url:
            raise RuntimeError(f"NewAPI 任务完成但未能从已知路径提取视频 URL: {final}")

        # 流式下载，不携带 Authorization 头（视频 URL 常为 CDN/OSS，避免 API Key 泄露）
        await self._download_with_retry(video_url, request.output_path)

        meta = first_mapping_by_paths(final, _METADATA_PATHS) or {}
        raw_duration = meta.get("duration")
        duration_seconds = int(float(raw_duration)) if raw_duration is not None else request.duration_seconds
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_NEWAPI,
            model=self._model,
            duration_seconds=duration_seconds,
            task_id=task_id,
            seed=meta.get("seed"),
        )

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}/video/generations",
                json=payload,
                headers=self._headers(),
            ),
            provider=PROVIDER_NEWAPI,
        )
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
        retry_if=should_retry_poll,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """对齐 OpenAI/Ark 的下载重试策略（5 次、5/10/20/40 秒），与生成阶段独立。"""
        await download_video(video_url, output_path)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}


def _extract_failure(state: dict) -> str | None:
    if _task_status(state) is not ProviderJobStatus.FAILED:
        return None
    return f"NewAPI 视频生成失败: {extract_provider_error_message(state)}"
