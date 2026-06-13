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
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    persist_provider_job_id,
    poll_with_retry,
    should_retry_download,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)


def _read_image_or_none(path: Path) -> str | None:
    """读成 data URI；缺失（目录/非常规文件，含空串解析出的 "."）或 IO 失败（权限/并发删除）返回 None。"""
    if not path.is_file():
        return None
    try:
        return image_to_data_uri(path)
    except OSError as exc:
        logger.warning("DashScope 图片读取失败: %s (%s)", path, exc)
        return None


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
        # 带首帧的参考生视频是 wan2.7-r2v 的官方形态（_build_media 同请求组装
        # first_frame + reference_image），故声明首帧叠加参考能力。
        VideoCapabilities(
            first_frame=True, reference_images=True, max_reference_images=5, reference_images_with_start_frame=True
        ),
    ),
}

# 未知 model（如代理中转自定义命名）按通用 i2v/t2v 处理，VideoCapabilities() 默认支持首帧。
_DEFAULT_PROFILE: tuple[set[VideoCapability], VideoCapabilities] = (
    {_TV, _IV, _AUDIO, _SEED},
    VideoCapabilities(),
)


def _profile_for_model(model: str | None) -> tuple[set[VideoCapability], VideoCapabilities]:
    """按 model_id 解析能力档：先精确命中，再容忍代理中转的前后缀装饰。

    infer_endpoint 用子串（"happyhorse" / "wan2."）路由到 dashscope-async-video，故此处也须
    子串容忍，否则 "proxy/happyhorse-1.0-r2v" / "wan2.7-r2v-0715" 这类装饰名会退回 _DEFAULT_PROFILE、
    丢掉 r2v 的 reference_images/max_reference_images，_build_media 据此构造出错误 payload。
    仅带系列名而无变体后缀（如裸 "happyhorse"）无法判别 t2v/i2v/r2v，按设计回落通用默认。
    __init__ 与 video_capabilities_for_model 共用本函数，保持单一真相源。
    """
    normalized = (model or "").strip().lower()
    if not normalized:
        return _DEFAULT_PROFILE
    if normalized in _MODEL_PROFILES:
        return _MODEL_PROFILES[normalized]
    # 各 profile key（happyhorse-1.0-{t2v,i2v,r2v} / wan2.7-{t2v,i2v,r2v}）互不为子串，无歧义
    for known, profile in _MODEL_PROFILES.items():
        if known in normalized:
            return profile
    return _DEFAULT_PROFILE


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
        self._capabilities, self._video_capabilities = _profile_for_model(self._model)

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算参考图等 caps —— 不构造 SDK client（无需 api_key）。

        resolver 解析参考图上限时调本方法即可，不必构造整个 backend；instance property 委托至此，
        保持 backend 为单一真相源。
        """
        return _profile_for_model(model)[1]

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

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
        # ratio 仅在无首帧时下传：图生视频/带首帧的参考生视频按首帧定宽高比，上游会忽略 ratio
        # （wan2.7「传 first_frame 时自动忽略」），HappyHorse 图生视频更直接把 ratio 当非法参数拒绝。
        # 默认 aspect_ratio 非空，若不门控会让带首帧的请求被上游拒。首帧缺席（文生视频/无首帧参考）才需 ratio。
        has_first_frame = any(m.get("type") == "first_frame" for m in media)
        if request.aspect_ratio and not has_first_frame:
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
            # fail-loud：声明了首帧图却缺失（目录/非常规文件，含空串解析出的 "."）或读取失败即中止，
            # 不静默忽略 —— 否则用户拿到一个没用上首帧的结果却不知情。
            uri = _read_image_or_none(p)
            if uri is None:
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=p.name)
            media.append({"type": "first_frame", "url": uri})
        if caps.reference_images:
            # r2v 必须有参考图。fail-loud：未提供 → required；任一声明的参考图缺失/不可读（is_file 不过
            # 或 read_bytes 抛 OSError）→ 报错列出文件名中止。不静默退化为无参考/子集生成（会产出错误
            # 结果且照常计费），让用户感知到有图未被使用。
            provided = [r for r in (request.reference_images or []) if r]
            if not provided:
                raise VideoCapabilityError("video_reference_images_required", model=self._model)
            data_uris: list[str] = []
            unreadable: list[str] = []
            for r in provided:
                p = Path(r)
                uri = _read_image_or_none(p)
                if uri is None:
                    unreadable.append(p.name)
                else:
                    data_uris.append(uri)
            if unreadable:
                raise VideoCapabilityError(
                    "video_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
                )
            limit = caps.max_reference_images
            if len(data_uris) > limit:
                logger.warning(
                    "DashScope 参考图数量 %d 超过 model=%s 上限 %d，截断",
                    len(data_uris),
                    self._model,
                    limit,
                )
                data_uris = data_uris[:limit]
            media.extend({"type": "reference_image", "url": uri} for uri in data_uris)
        return media

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        # 创建任务是非幂等的「建任务 + 计费」POST：submit_post 把歧义传输错误（请求可能已送达
        # 服务端但响应在途丢失）转 AmbiguousSubmitError 终态失败，避免自动重试重复建任务 + 重复计费；
        # >=400 由其落 body 日志 + raise_for_status 抛 HTTPStatusError（保留 status_code 供咽喉层识别
        # 413 降档），交 should_retry_submit 按状态码分流——4xx fail-fast、5xx/429 重试。
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}{_VIDEO_ENDPOINT}",
                json=payload,
                headers=dashscope_headers(self._api_key, async_mode=True),
            ),
            provider=PROVIDER_DASHSCOPE,
        )
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
            retry_if=should_retry_poll,
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
        retry_if=should_retry_download,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        await download_video(video_url, output_path)

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
