"""OpenAIVideoBackend — OpenAI Sora 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from lib.aspect_size import parse_aspect_ratio
from lib.logging_utils import format_kwargs_for_log
from lib.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    persist_provider_job_id,
    poll_with_retry,
)

_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 600.0
_POLL_TIMEOUT_PER_SECOND = 30.0

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sora-2"

# sora-2 的 size 是固定 4 档枚举（openai SDK VideoSize Literal 确权，v2.38），非任意 WxH：
# 720x1280 / 1280x720 精确 9:16 / 16:9；1024x1792 / 1792x1024 为 4:7 / 7:4（sora 自带近似档）。
# 故不能按 aspect_size 任意计算，只能吸附最近合法档。
_SORA_LEGAL_SIZES: tuple[str, ...] = ("720x1280", "1280x720", "1024x1792", "1792x1024")


def _resolve_size(resolution: str | None, aspect_ratio: str) -> str:
    """比例优先：在 sora 合法档中选比例最接近 aspect_ratio 的；并列取像素更高者（清晰度其次）。

    9:16 → 720x1280、16:9 → 1280x720（均精确）。resolution 不参与选择——sora 的精确
    9:16 / 16:9 只有 720 档、无更高精确档，无法靠 resolution 提清晰度而不破坏比例
    （sora 的尺寸约束，见 docs/adr/0011）。始终返回合法档，size 字段必传以锁定比例，
    不再出现「不传 size → 比例由上游默认决定」。
    """
    aw, ah = parse_aspect_ratio(aspect_ratio)
    target = aw / ah

    def _score(size: str) -> tuple[float, int]:
        w, h = (int(x) for x in size.split("x"))
        return abs(w / h - target), -(w * h)  # 比例差小优先；并列取像素更多

    chosen = min(_SORA_LEGAL_SIZES, key=_score)
    # 9:16 / 16:9 精确命中；其它比例（如 1:1 / 21:9）sora 无对应档，吸附后比例必然偏差，
    # 与上游协议无关地告警，避免静默产出错比例视频（图片路径同样在超界时告警）。
    cw, ch = (int(x) for x in chosen.split("x"))
    if abs(cw / ch - target) > 0.01:
        logger.warning(
            "OpenAI video: aspect_ratio=%s 无精确 sora 档，吸附到 %s（比例偏差，输出非项目设定比例）",
            aspect_ratio,
            chosen,
        )
    return chosen


class OpenAIVideoBackend:
    """OpenAI Sora 视频生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        # Sora input_reference 为单张首帧图，参考图上限为 1。
        return VideoCapabilities(reference_images=True, max_reference_images=1)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": str(request.duration_seconds),
        }
        # 始终下传合法 size 以锁定比例（sora 固定档，不传则上游用自家默认比例）
        kwargs["size"] = _resolve_size(request.resolution, request.aspect_ratio)

        # 收集所有参考图：start_image + reference_images
        refs = []
        if request.start_image and Path(request.start_image).exists():
            refs.append(_encode_start_image(Path(request.start_image)))
        if request.reference_images:
            for ref_path in request.reference_images:
                p = Path(ref_path) if not isinstance(ref_path, Path) else ref_path
                if p.exists():
                    refs.append(_encode_start_image(p))
        if refs:
            # 单张图时保持 tuple 格式（API 兼容），多张时用 list
            kwargs["input_reference"] = refs[0] if len(refs) == 1 else refs

        logger.info("OpenAI 视频生成开始: model=%s, seconds=%s", self._model, kwargs["seconds"])
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))

        video = await self._create_video(**kwargs)
        # submit 成功立即持久化 job_id；持久化失败抛 → finally mark_failed。
        # 非 worker 路径（grid / 直生 / 测试）request.task_id 为 None，跳过持久化。
        if request.task_id is not None:
            await persist_provider_job_id(request.task_id, video.id, provider=PROVIDER_OPENAI)
        final = await self._poll_until_complete(video.id, request.duration_seconds)

        # generate 路径下 expired 是「provider 异常 / 输入参数过期」类失败，
        # 抛 RuntimeError 让 worker mark_failed（不带 [resume_expired] 前缀）。
        if final.status == "expired":
            raise RuntimeError(f"OpenAI Sora job expired during generate: {final.id}")

        return await self._download_and_build_result(final, request, kwargs)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 OpenAI job：仅 poll + 下载，不调 videos.create。"""
        try:
            final = await self._poll_until_complete(job_id, request.duration_seconds)
        except Exception as exc:
            if _is_openai_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_OPENAI) from exc
            raise

        # resume 路径下 expired = provider 端已忘 / 输入资产过期，归类
        # [resume_expired] 让 worker 错误前缀化、不再尝试重启自愈
        if final.status == "expired":
            raise ResumeExpiredError(
                job_id=job_id,
                provider=PROVIDER_OPENAI,
                message=f"OpenAI Sora job expired: {final.id}",
            )

        return await self._download_and_build_result(final, request, {"seconds": str(request.duration_seconds)})

    async def _download_and_build_result(
        self, final, request: VideoGenerationRequest, kwargs: dict
    ) -> VideoGenerationResult:
        content = await self._download_content_with_retry(final.id)

        def _write():
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(content.content)

        await asyncio.to_thread(_write)

        logger.info("OpenAI 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            duration_seconds=int(
                final.seconds if final.seconds is not None else kwargs.get("seconds") or request.duration_seconds
            ),
            task_id=final.id,
        )

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _create_video(self, **kwargs):
        """仅创建视频任务（带重试）；轮询交由 _poll_until_complete 自管。"""
        return await self._client.videos.create(**kwargs)

    async def _poll_until_complete(self, video_id: str, duration_seconds: int):
        """轮询任务直到 status=='completed'。

        不复用 SDK 的 client.videos.poll：它仅识别 in_progress/queued/completed/failed，
        对接返回非标状态（如 NOT_START）的 OpenAI 兼容网关时会提前退出，导致下载未就绪任务。
        """
        max_wait = max(_MIN_POLL_TIMEOUT_SECONDS, float(duration_seconds) * _POLL_TIMEOUT_PER_SECOND)

        # _is_done 是纯谓词：completed / failed / expired 三种状态都视为「已终态」让 poll 返回。
        # caller (generate / resume_video) 拿到 result 后再分流：
        #   - completed → 下载
        #   - failed   → is_failed 已抛 RuntimeError
        #   - expired  → 在 caller 处按 generate vs resume 上下文抛 RuntimeError / ResumeExpiredError
        # 关键不变量：is_failed 不识别 expired，避免覆盖 caller 分流。
        return await poll_with_retry(
            poll_fn=lambda: self._client.videos.retrieve(video_id),
            is_done=lambda v: v.status in ("completed", "failed", "expired"),
            is_failed=lambda v: f"Sora 视频生成失败: {getattr(v, 'error', None)}" if v.status == "failed" else None,
            poll_interval=_POLL_INTERVAL_SECONDS,
            max_wait=max_wait,
            retryable_errors=OPENAI_RETRYABLE_ERRORS,
            label="OpenAI",
            on_progress=lambda v, elapsed: logger.info(
                "OpenAI 视频生成中... 状态: %s, 已等待 %d 秒", v.status, int(elapsed)
            ),
        )

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retryable_errors=OPENAI_RETRYABLE_ERRORS,
    )
    async def _download_content_with_retry(self, video_id: str):
        """单独重试内容下载，避免因下载失败重新触发视频生成。"""
        return await self._client.videos.download_content(video_id)


def _encode_start_image(image_path: Path) -> tuple[str, bytes, str]:
    mime = IMAGE_MIME_TYPES.get(image_path.suffix.lower(), "image/png")
    return (image_path.name, image_path.read_bytes(), mime)


def _is_openai_not_found(exc: BaseException) -> bool:
    """识别 OpenAI/Sora 「job 不存在」响应（NotFoundError / HTTP 404）。

    不再做 ``"not found"`` / ``"expired"`` 子串兜底：``status='expired'`` 已在
    ``_poll_until_complete`` 内直接抛 ``ResumeExpiredError`` 处理（fix #5），
    宽泛字串会把诸如 ``"file not found in storage"`` 等业务错误误判为幽灵任务。
    """
    try:
        from openai import NotFoundError  # pyright: ignore[reportMissingImports]
    except ImportError:
        NotFoundError = None  # noqa: N806

    if NotFoundError is not None and isinstance(exc, NotFoundError):
        return True
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status_code == 404
