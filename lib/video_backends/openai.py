"""OpenAIVideoBackend — OpenAI Sora 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from lib.aspect_size import VIDEO_TIER_SHORT_EDGE, parse_aspect_ratio, resolution_to_short_edge
from lib.logging_utils import format_kwargs_for_log
from lib.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.retry import with_retry_async
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    TERMINAL_PROVIDER_STATUSES,
    ProviderJobIdPersistenceMixin,
    ProviderJobStatus,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    normalize_provider_status,
    poll_with_retry,
    with_artifact_retry,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sora-2"

# sora 合法 size 按 model 能力 + 分辨率档分级（OpenAI 官方 changelog / 模型页）：
# - sora-2（base）：仅 720p —— 9:16 720x1280 / 16:9 1280x720。
# - sora-2-pro：720p 同上 +（2026-03 起）1080p —— 9:16 1080x1920 / 16:9 1920x1080。
# SDK 的 VideoSize Literal 滞后（只列 720/1024 档、漏 1080），以官方模型页为准；
# 不再保留 1024x1792 / 1792x1024（4:7，违背比例优先，且已被 1080p 精确档取代）。
# 非任意 WxH，只能吸附合法档：比例优先选档，分辨率档只决定 720p vs 1080p 子集（清晰度其次）。
_SORA_SIZES_720P: tuple[str, ...] = ("720x1280", "1280x720")
_SORA_SIZES_1080P: tuple[str, ...] = ("1080x1920", "1920x1080")
# 向后兼容的并集导出（外部/测试引用「全部合法档」）。
_SORA_LEGAL_SIZES: tuple[str, ...] = _SORA_SIZES_720P + _SORA_SIZES_1080P
_SORA_1080P_MIN_SHORT = 1080


def _video_status(video: object) -> ProviderJobStatus:
    """SDK Video 对象 → canonical 状态。

    本端点同时服务内置 Sora 与自定义供应商的 openai-video 协议：官方 Sora 只发
    ``queued`` / ``in_progress`` / ``completed`` / ``failed``，而 OpenAI 兼容代理网关转发
    非 Sora 型号时会透传底层厂商的状态串（如 ``succeeded``），故一律过共享归一。
    """
    return normalize_provider_status(getattr(video, "status", None))


def _video_error_message(video: object) -> str:
    """SDK Video 对象 → 供应商失败原因文本；取不到返回 unknown。

    这句话原样落进 ``task.error_message``，是用户在任务面板读到的全部原因，故显式取字段而不是
    插值整个对象：``Video.error`` 是带 ``code`` / ``message`` 的模型，直接插值会把类名与字段名
    一并写给用户；``None``（网关只给 status 不给 error 的常见形态）会写出一句没有原因的失败。
    代理网关透传的裸 dict / 裸字符串同样认，认不出的形态一律 unknown。
    """
    err = getattr(video, "error", None)
    if err is None:
        return "unknown"
    if isinstance(err, str):
        return err.strip() or "unknown"
    if isinstance(err, dict):
        candidates = (err.get("message"), err.get("code"))
    else:
        candidates = (getattr(err, "message", None), getattr(err, "code", None))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
        # 数字错误码（网关常见的裸 HTTP 码）也是原因，别因为不是字符串就丢掉
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    # 认不出的形态说不出原因就说 unknown：把对象自身的 repr 写进任务面板等于没说
    return "unknown"


def _resolve_size(model: str, resolution: str | None, aspect_ratio: str) -> str:
    """比例优先：在 model+分辨率档对应的 sora 合法档中选比例最接近 aspect_ratio 的；并列取像素更高者。

    分辨率档只决定 720p vs 1080p 子集、不决定比例：sora-2-pro 选 1080p 时 9:16→1080x1920（精确且高清），
    sora-2（base）或缺分辨率时落 720p（缺分辨率默认 720P，不擅自升 1080p 以免超额计费）。size 必传以锁定
    比例，绝不出现「不传 size → 上游默认比例」。其它比例（1:1/21:9 等）sora 无对应档，吸附后告警。
    """
    aw, ah = parse_aspect_ratio(aspect_ratio)
    target = aw / ah
    is_pro = "pro" in model.lower()
    short = resolution_to_short_edge(resolution, tier_map=VIDEO_TIER_SHORT_EDGE)
    # sora 支持的短边档：base 仅 720；pro 增 1080。按「最近档」选（等距取更高档），避免把
    # short=1000 这类自定义分辨率值无故降到 720p（floor 会误降，最近档不会）。
    supported_shorts = [720, _SORA_1080P_MIN_SHORT] if is_pro else [720]
    achieved_short = min(supported_shorts, key=lambda s: (abs(s - short), -s))
    legal = _SORA_SIZES_1080P if achieved_short == _SORA_1080P_MIN_SHORT else _SORA_SIZES_720P
    if short > achieved_short:
        # 请求高于模型可达档（base 请 1080p、或 pro 请 4K）：封顶并提示清晰度让位
        logger.warning(
            "OpenAI video: model=%s 无法满足分辨率请求 %s（短边 %d），输出封顶到 %dp 档（清晰度让位比例）",
            model,
            resolution,
            short,
            achieved_short,
        )

    def _score(size: str) -> tuple[float, int]:
        w, h = (int(x) for x in size.split("x"))
        return abs(w / h - target), -(w * h)  # 比例差小优先；并列取像素更多

    chosen = min(legal, key=_score)
    # 9:16 / 16:9 精确命中；其它比例（如 1:1 / 21:9）sora 无对应档，吸附后比例必然偏差，
    # 与上游协议无关地告警，避免静默产出错比例视频（图片路径同样在超界时告警）。
    cw, ch = (int(x) for x in chosen.split("x"))
    if abs(cw / ch - target) > 0.01:
        logger.warning(
            "OpenAI video: aspect_ratio=%s 无精确 sora 档，吸附到 %s（比例偏差，输出非项目设定比例）",
            aspect_ratio,
            chosen,
        )
    # 后置不变量：只返回 sora 合法档全集内的尺寸，防止未来改档逻辑时静默产出非法 size。
    assert chosen in _SORA_LEGAL_SIZES, f"_resolve_size produced illegal sora size: {chosen}"
    return chosen


class OpenAIVideoBackend(ProviderJobIdPersistenceMixin):
    """OpenAI Sora 视频生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算 caps —— 不构造 SDK client（无需 api_key）。

        Sora input_reference 为单张首帧图，参考图上限为 1；首帧与参考共享该单槽位。
        当前全系模型能力一致，不按 model_id 分支；instance property 委托至此，
        保持 backend 为单一真相源。

        音轨恒有声：Sora 成片自带音轨，``generate`` 组装的 kwargs 里没有音轨开关字段，用户的
        关闭意图无处可下发。
        """
        return VideoCapabilities(max_reference_images=1, audio_track=VideoAudioMode.ALWAYS_ON)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": str(request.duration_seconds),
        }
        # 始终下传合法 size 以锁定比例（sora 固定档，不传则上游用自家默认比例）
        kwargs["size"] = _resolve_size(self._model, request.resolution, request.aspect_ratio)

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
        # 非 worker 路径（grid / 直生 / 测试）request.task_id 为 None，统一点内跳过持久化。
        await self._persist_provider_job_id(request, video.id, provider=PROVIDER_OPENAI)
        final = await self._poll_until_complete(video.id, request.poll_timeout_seconds)

        # generate 路径下 expired 是「provider 异常 / 输入参数过期」类失败，
        # 抛 RuntimeError 让 worker mark_failed（不带 [resume_expired] 前缀）。
        if _video_status(final) is ProviderJobStatus.EXPIRED:
            raise RuntimeError(f"OpenAI Sora job expired during generate: {final.id}")

        return await self._download_and_build_result(final, request, kwargs)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 OpenAI job：仅 poll + 下载，不调 videos.create。"""
        try:
            final = await self._poll_until_complete(job_id, request.poll_timeout_seconds)
        except Exception as exc:
            if _is_openai_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_OPENAI) from exc
            raise

        # resume 路径下 expired = provider 端已忘 / 输入资产过期，归类
        # [resume_expired] 让 worker 错误前缀化、不再尝试重启自愈
        if _video_status(final) is ProviderJobStatus.EXPIRED:
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

    async def _poll_until_complete(self, video_id: str, poll_timeout_seconds: int):
        """轮询任务直到状态归一到终态。

        不复用 SDK 的 client.videos.poll：它仅识别 in_progress/queued/completed/failed，
        对接返回非标状态（如 NOT_START）的 OpenAI 兼容网关时会提前退出，导致下载未就绪任务。
        """
        # is_done 是纯谓词：成功 / 失败 / 过期三档都视为「已终态」让 poll 返回。
        # caller (generate / resume_video) 拿到 result 后再分流：
        #   - succeeded → 下载
        #   - failed    → is_failed 已抛 RuntimeError
        #   - expired   → 在 caller 处按 generate vs resume 上下文抛 RuntimeError / ResumeExpiredError
        # 关键不变量：is_failed 不识别 expired，避免覆盖 caller 分流。
        return await poll_with_retry(
            poll_fn=lambda: self._client.videos.retrieve(video_id),
            is_done=lambda v: _video_status(v) in TERMINAL_PROVIDER_STATUSES,
            is_failed=lambda v: (
                f"Sora 视频生成失败: {_video_error_message(v)}"
                if _video_status(v) is ProviderJobStatus.FAILED
                else None
            ),
            max_wait=poll_timeout_seconds,
            retryable_errors=OPENAI_RETRYABLE_ERRORS,
            label="OpenAI",
            on_progress=lambda v, elapsed: logger.info(
                "OpenAI 视频生成中... 状态: %s, 已等待 %d 秒", v.status, int(elapsed)
            ),
        )

    async def _download_content_with_retry(self, video_id: str):
        """单独重试内容下载，避免因下载失败重新触发视频生成。

        SDK 取件抛的不是 ``HTTPStatusError``，故退到按 ``OPENAI_RETRYABLE_ERRORS`` 判定，
        终止条件仍是共用的产物下载预算。
        """
        return await with_artifact_retry(
            lambda: self._client.videos.download_content(video_id),
            label="OpenAI",
            retry_if=None,
            retryable_errors=OPENAI_RETRYABLE_ERRORS,
        )


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
