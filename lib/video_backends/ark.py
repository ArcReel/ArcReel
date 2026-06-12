"""ArkVideoBackend — 火山方舟 Ark 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from lib.ark_shared import create_ark_client
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_ARK
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
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


class ArkVideoBackend:
    """Ark (火山方舟) 视频生成后端。"""

    DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

    _BASE_CAPABILITIES: set[VideoCapability] = {
        VideoCapability.TEXT_TO_VIDEO,
        VideoCapability.IMAGE_TO_VIDEO,
        VideoCapability.GENERATE_AUDIO,
        VideoCapability.SEED_CONTROL,
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._client = create_ark_client(api_key=api_key, base_url=base_url)
        self._model = model or self.DEFAULT_MODEL
        # FLEX_TIER（service_tier 参数）仅 seedance-1.x 等老模型支持；seedance-2-0/2.0 系列
        # 上游在 r2v 下会 400 拒绝该参数，必须从能力集中剔除。判定见 _is_seedance_2：用 `in`
        # 子串兼容多套前缀命名（doubao-/dreamina-），但版本号收窄到已验证的 2-0/2.0，
        # 不对未发布版本（如 seedance-2.5）过早假设。
        self._capabilities = set(self._BASE_CAPABILITIES)
        if not self._is_seedance_2(self._model):
            self._capabilities.add(VideoCapability.FLEX_TIER)

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @staticmethod
    def _is_seedance_2(model: str) -> bool:
        """按 model_id 子串识别已验证的 seedance-2-0 / seedance-2.0 系列（含 fast 变体）。

        只匹配已验证不支持 service_tier 的版本号（2-0 与 2.0 两种写法），不对未发布的未来版本
        （如 seedance-2.5）过早假设。用 `in` 子串而非前缀匹配，以兼容上游多套前缀命名
        （doubao- 火山国内站 / dreamina- BytePlus 国际站）。FLEX_TIER 剔除与参考图能力共用
        本判定，避免两条路径口径漂移。
        """
        model_lower = model.lower()
        return "seedance-2-0" in model_lower or "seedance-2.0" in model_lower

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算参考图等 caps —— 不构造 SDK client（无需 api_key）。

        resolver 解析参考图上限时调本方法即可，不必构造整个 backend；instance property 委托至此，
        保持 backend 为单一真相源。
        """
        if ArkVideoBackend._is_seedance_2(model):
            # API 拒绝首帧/尾帧与参考素材混合请求（InvalidParameter: first/last frame content
            # cannot be mixed with reference media content，实测）——参考图是与首尾帧互斥的
            # 参考生视频模式，故不声明首帧叠加参考能力；若上游后续放开混合可重新开启。
            return VideoCapabilities(last_frame=True, reference_images=True, max_reference_images=9)
        return VideoCapabilities()

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。任务创建和轮询阶段分离重试，避免瞬态错误导致重建任务。"""
        provider_task_id = await self._create_task(request)
        if request.task_id is not None:
            await persist_provider_job_id(request.task_id, provider_task_id, provider=PROVIDER_ARK)
        return await self._poll_until_done(provider_task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Ark task：仅 poll + 下载。

        Ark 端 task 不存在/已过期通常表现为 SDK 抛 404 类异常或返回 status='expired'；
        本接口在 _poll_until_done 内识别后者，前者由本方法拦截转 ResumeExpiredError。
        """
        try:
            return await self._poll_until_done(job_id, request)
        except Exception as exc:
            if _is_ark_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_ARK) from exc
            raise

    @with_retry_async()
    async def _create_task(self, request: VideoGenerationRequest) -> str:
        """创建 Ark 视频生成任务（带重试保护）。"""
        # 1. Build content list
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]

        # Ark 视频 API 要求每个 image_url 条目在顶层带 `role` 字段
        # （first_frame / last_frame / reference_image），否则 400 InvalidParameter。
        if request.start_image:
            from lib.image_backends.base import image_to_base64_data_uri

            data_uri = image_to_base64_data_uri(request.start_image)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                    "role": "first_frame",
                }
            )

        if request.end_image and Path(request.end_image).exists():
            from lib.image_backends.base import image_to_base64_data_uri

            data_uri = image_to_base64_data_uri(request.end_image)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                    "role": "last_frame",
                }
            )

        if request.reference_images:
            from lib.image_backends.base import image_to_base64_data_uri

            for ref_path in request.reference_images:
                p = Path(ref_path) if not isinstance(ref_path, Path) else ref_path
                if p.exists():
                    data_uri = image_to_base64_data_uri(p)
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                            "role": "reference_image",
                        }
                    )

        # 2. Build API params
        # 比例优先：ratio 是独立 SDK 字段，由 aspect_ratio 直接决定；resolution 仅清晰度档位，
        # 与比例正交（SDK 内部按 ratio×resolution 算像素），不把比例压进像素 size，故无尺寸 bug。
        create_params = {
            "model": self._model,
            "content": content,
            "ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
            "generate_audio": request.generate_audio,
            "watermark": False,
        }
        if request.resolution is not None:
            create_params["resolution"] = request.resolution
        # seedance-2.0 等模型不接受 service_tier，仅在声明 FLEX_TIER 能力时传入
        if VideoCapability.FLEX_TIER in self._capabilities:
            create_params["service_tier"] = request.service_tier
        if request.seed is not None:
            create_params["seed"] = request.seed

        # 3. Create task (sync SDK call, run in executor)
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(create_params))
        create_result = await asyncio.to_thread(
            self._client.content_generation.tasks.create,
            **create_params,
        )
        logger.info("Ark 任务已创建: %s", create_result.id)
        return create_result.id

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=lambda e: (
            isinstance(e, httpx.HTTPStatusError)
            and e.response.status_code == 400
            and "video_not_ready" in str(e.response.text)
        ),
    )
    async def _download_video_with_retry(video_url: str, output_path) -> None:
        """单独重试视频下载，避免下载失败导致重新生成视频而浪费额度。

        Ark 的视频 URL 在任务 succeeded 后可能仍未就绪（返回 400 video_not_ready），
        仅针对该瞬态状态重试；其余 HTTP 错误及网络瞬态错误由内层 download_video 处理。
        """
        await download_video(video_url, output_path)

    async def _poll_until_done(self, task_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """轮询任务状态直到完成，瞬态错误仅重试当次轮询请求。"""
        poll_interval = 10 if request.service_tier == "default" else 60
        max_wait_time = 600 if request.service_tier == "default" else 3600

        result = await poll_with_retry(
            poll_fn=lambda: asyncio.to_thread(self._client.content_generation.tasks.get, task_id=task_id),
            is_done=lambda r: r.status == "succeeded",
            is_failed=lambda r: (
                f"Ark 视频生成失败(status={r.status}): {getattr(r, 'error', None) or 'Unknown error'}"
                if r.status in ("failed", "expired")
                else None
            ),
            poll_interval=poll_interval,
            max_wait=max_wait_time,
            label="Ark",
            on_progress=lambda r, elapsed: logger.info(
                "Ark 视频生成中... 状态: %s, 已等待 %d 秒", r.status, int(elapsed)
            ),
        )

        # Download video
        video_url = result.content.video_url
        await self._download_video_with_retry(video_url, request.output_path)

        # Extract result metadata
        seed = getattr(result, "seed", None)
        usage_tokens = None
        if hasattr(result, "usage") and result.usage:
            usage_tokens = getattr(result.usage, "completion_tokens", None)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_ARK,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            seed=seed,
            usage_tokens=usage_tokens,
            task_id=task_id,
            generate_audio=request.generate_audio,
        )


def _is_ark_not_found(exc: BaseException) -> bool:
    """识别 Ark 任务「不存在 / 已过期」响应。

    精确匹配官方稳定 ``task_not_found`` 错误码；移除宽泛的 ``"not found"`` 子串兜底，
    避免业务侧错误（如 reference image not found）被误判为 provider 端任务过期。
    ``expired`` 字串保留：Ark 自身 ``_poll_until_done`` 把 status in (failed, expired)
    转成 ``RuntimeError("Ark 任务失败 ... status=expired")``，要靠该字串识别回 ResumeExpiredError。
    """
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status_code == 404:
        return True
    msg = str(exc).lower()
    return "task_not_found" in msg or "tasknotfound" in msg or "expired" in msg
