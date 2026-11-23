"""OpenAIVideoBackend — OpenAI Sora 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from openai import AsyncOpenAI

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.aspect_size import VIDEO_TIER_SHORT_EDGE, parse_aspect_ratio, resolution_to_short_edge
from arcreel_market_core.video_backend_contract import (
    TERMINAL_PROVIDER_STATUSES,
    ProviderJobStatus,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    normalize_provider_status,
)
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    faststart_video_artifact,
    poll_with_retry,
    read_video_images,
    with_artifact_retry,
)
from lib.backends.input_limits import excess_image_names
from lib.backends.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import (
    EntryBoundCapabilities,
)
from lib.billing.metering import Usage
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

_INPUT_REFERENCE_SLOTS = 1


_SORA_SIZES_720P: tuple[str, ...] = ("720x1280", "1280x720")
_SORA_SIZES_1080P: tuple[str, ...] = ("1080x1920", "1920x1080")
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
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return "unknown"


def _resolve_size(model: str, resolution: str | None, aspect_ratio: str, *, entry: CatalogEntry | None = None) -> str:
    """按条目上的分辨率与端点合法尺寸吸附，比例优先；未收录时只受端点约束。"""
    aw, ah = parse_aspect_ratio(aspect_ratio)
    target = aw / ah
    short = resolution_to_short_edge(resolution, tier_map=VIDEO_TIER_SHORT_EDGE)
    from lib.backends.video_capabilities import entry_video_facts
    from lib.catalog import query as catalog

    facts = entry_video_facts(entry or catalog.find_entry(model, media_type="video"))
    ceiling = max(
        (resolution_to_short_edge(r, tier_map=VIDEO_TIER_SHORT_EDGE) for r in facts.resolutions), default=1080
    )
    supported_shorts = [720, _SORA_1080P_MIN_SHORT] if ceiling >= 1080 else [720]
    achieved_short = min(supported_shorts, key=lambda s: (abs(s - short), -s))
    legal = _SORA_SIZES_1080P if achieved_short == _SORA_1080P_MIN_SHORT else _SORA_SIZES_720P
    if short > achieved_short:
        logger.warning(
            "OpenAI video: model=%s 无法满足分辨率请求 %s（短边 %d），输出封顶到 %dp 档（清晰度让位比例）",
            model,
            resolution,
            short,
            achieved_short,
        )

    def _score(size: str) -> tuple[float, int]:
        w, h = (int(x) for x in size.split("x"))
        return abs(w / h - target), -(w * h)

    chosen = min(legal, key=_score)
    cw, ch = (int(x) for x in chosen.split("x"))
    if abs(cw / ch - target) > 0.01:
        logger.warning(
            "OpenAI video: aspect_ratio=%s 无精确 sora 档，吸附到 %s（比例偏差，输出非项目设定比例）",
            aspect_ratio,
            chosen,
        )
    assert chosen in _SORA_LEGAL_SIZES, f"_resolve_size produced illegal sora size: {chosen}"
    return chosen


class OpenAIVideoBackend(EntryBoundCapabilities, ProviderJobIdPersistenceMixin):
    """OpenAI Sora 视频生成后端。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url, transport_layer=transport_layer)
        if not model:
            raise ValueError("OpenAIVideoBackend 需要指定模型")
        self._model = model

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Sora 调用通道的声明（通道事实）—— 纯计算，不构造 SDK client（无需 api_key）。

        input_reference 为单张首帧图，参考图与首帧共享这一个槽位，通道一次只能承载 1 张；不收尾帧与参考音频。
        各型号接受哪些输入记在模型条目上，与本声明两侧求值（``lib.backends.video_capabilities``，docs/adr/0104）。
        instance property 委托至此，保持 backend 为单一真相源。

        音轨恒有声：Sora 成片自带音轨，``generate`` 组装的 kwargs 里没有音轨开关字段，用户的
        关闭意图无处可下发。
        """
        del model, entry
        return VideoCapabilities(max_reference_images=_INPUT_REFERENCE_SLOTS, audio_track=VideoAudioMode.ALWAYS_ON)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    @property
    def _request_base_url(self) -> str:
        """本实例请求实际发往的域名，取自 SDK client 的公开属性（SDK 会补尾斜杠，此处去掉）。"""
        return str(self._client.base_url).rstrip("/")

    def _client_for(self, request: VideoGenerationRequest) -> AsyncOpenAI:
        """轮询与取件用的 client：续跑且提交域名与当下配置不同时，派生一个绑定提交域名的。

        任务 id 只在创建它的 endpoint 上可查，用户在途改 base_url 后按新域名去轮旧任务会 404，
        被 ``resume_video`` 误判成过期。``with_options`` 派生的 client 沿用同一份凭据、超时、重试
        配置与连接池。提交路径的 ``submitted_base_url`` 恒 None，沿用实例 client。
        """
        submitted = request.submitted_base_url
        if not submitted or submitted.rstrip("/") == self._request_base_url:
            return self._client
        return self._client.with_options(base_url=submitted)

    @staticmethod
    def metered_usage(
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """这次请求的计量（见 :mod:`lib.backends.video_backends.metering`）。

        分辨率取下发的 ``size`` 的短边档（如 ``1024x1792`` 记为 1024p），``size`` 与请求构造按模型对应的同一个
        条目（``entry``）求得。请求没有音轨开关，通道本身不声明音轨维度；模型无声覆盖由共享计量层处理。
        """
        width, _, height = _resolve_size(model, request.resolution, request.aspect_ratio, entry=entry).partition("x")
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=f"{min(int(width), int(height))}p",
            audio=None,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if (
            not request.generate_audio
            and (request.video_capabilities or self.video_capabilities)
            .audio_track_for_route("r2v" if request.reference_images else "i2v")
            .has_audio_track
        ):
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._model)
        if request.end_image:
            raise VideoCapabilityError("video_last_frame_unsupported", provider=self.name, model=self._model)
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        input_count = bool(request.start_image) + len(request.reference_images or [])
        if input_count > _INPUT_REFERENCE_SLOTS:
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                names=excess_image_names(
                    _INPUT_REFERENCE_SLOTS,
                    input_count,
                    ([request.start_image] if request.start_image else []) + (request.reference_images or []),
                ),
                provider=self.name,
                model=self._model,
                limit=_INPUT_REFERENCE_SLOTS,
                count=input_count,
            )
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": str(request.duration_seconds),
        }
        kwargs["size"] = _resolve_size(
            self._model,
            request.resolution,
            request.aspect_ratio,
            entry=self._catalog_entry,
        )

        refs = await asyncio.to_thread(_encode_input_references, request, self._model)
        if refs:
            (kwargs["input_reference"],) = refs

        logger.info("OpenAI 视频生成开始: model=%s, seconds=%s", self._model, kwargs["seconds"])
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))

        video = await self._create_video(**kwargs)
        await self._persist_provider_job_id(
            request, video.id, provider=PROVIDER_OPENAI, endpoint=self._request_base_url
        )
        final = await self._poll_until_complete(self._client, video.id, request.poll_timeout_seconds)

        if _video_status(final) is ProviderJobStatus.EXPIRED:
            raise RuntimeError(f"OpenAI Sora job expired during generate: {final.id}")

        return await self._download_and_build_result(self._client, final, request, kwargs)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 OpenAI job：仅 poll + 下载，不调 videos.create；轮询与取件按提交域名回放。"""
        client = self._client_for(request)
        try:
            final = await self._poll_until_complete(client, job_id, request.poll_timeout_seconds)
        except Exception as exc:
            if _is_openai_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_OPENAI) from exc
            raise

        if _video_status(final) is ProviderJobStatus.EXPIRED:
            raise ResumeExpiredError(
                job_id=job_id,
                provider=PROVIDER_OPENAI,
                message=f"OpenAI Sora job expired: {final.id}",
            )

        return await self._download_and_build_result(client, final, request, {"seconds": str(request.duration_seconds)})

    async def _download_and_build_result(
        self, client: AsyncOpenAI, final, request: VideoGenerationRequest, kwargs: dict
    ) -> VideoGenerationResult:
        content = await self._download_content_with_retry(client, final.id)

        def _write():
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(content.content)

        await run_noninterruptible_sync(_write)
        await faststart_video_artifact(request.output_path)

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

    async def _poll_until_complete(self, client: AsyncOpenAI, video_id: str, poll_timeout_seconds: int):
        """轮询任务直到状态归一到终态。

        不复用 SDK 的 client.videos.poll：它仅识别 in_progress/queued/completed/failed，
        对接返回非标状态（如 NOT_START）的 OpenAI 兼容网关时会提前退出，导致下载未就绪任务。
        """
        return await poll_with_retry(
            poll_fn=lambda: client.videos.retrieve(video_id),
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

    async def _download_content_with_retry(self, client: AsyncOpenAI, video_id: str):
        """单独重试内容下载，避免因下载失败重新触发视频生成。

        SDK 取件抛的不是 ``HTTPStatusError``，故退到按 ``OPENAI_RETRYABLE_ERRORS`` 判定，
        终止条件仍是共用的产物下载预算。
        """
        return await with_artifact_retry(
            lambda: client.videos.download_content(video_id),
            label="OpenAI",
            retry_if=None,
            retryable_errors=OPENAI_RETRYABLE_ERRORS,
        )


def _encode_input_references(request: VideoGenerationRequest, model: str) -> list[tuple[str, bytes, str]]:
    images = read_video_images(request, model)
    paths = ([request.start_image] if request.start_image else []) + list(request.reference_images or [])
    return [images[Path(p)].multipart() for p in paths]


def _is_openai_not_found(exc: BaseException) -> bool:
    """识别 OpenAI/Sora 「job 不存在」响应（NotFoundError / HTTP 404）。

    不做 ``"not found"`` / ``"expired"`` 子串兜底：``status='expired'`` 由 ``resume_video`` 在轮询
    返回终态后直接转 ``ResumeExpiredError``，宽泛字串会把诸如 ``"file not found in storage"`` 等
    业务错误误判为幽灵任务。
    """
    try:
        from openai import NotFoundError
    except ImportError:
        NotFoundError = None

    if NotFoundError is not None and isinstance(exc, NotFoundError):
        return True
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status_code == 404
