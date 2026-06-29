"""AgnesVideoBackend — Agnes 视频生成后端（裸 base64 + 异步轮询 + resume）。

走 apihub 网关上的 OpenAI 风格异步端点：submit ``POST /v1/videos``（JSON）取 task_id →
轮询 ``GET /v1/videos/{task_id}`` 至 ``status=completed`` → 从响应 ``remixed_from_video_id``
字段取成片 mp4 URL → 下载本地。状态机 ``queued → in_progress → completed / failed``。

能力约束：fps 固定 24；时长 1–18s（内部 ``num_frames = 最近的 8n+1``，由秒 × fps 取整对齐，
上限 441 帧）；分辨率经 aspect_size 精确算出并显式下发 ``height`` × ``width``（不显式下发时
上游回落自身默认横屏尺寸）。

关键帧 / 多图映射：无图 → 文生视频；起始图 → 顶层 ``image``；首尾帧 → ``extra_body.image=[s,e]``
+ ``mode="keyframes"``；参考图 → ``extra_body.image=[refs]``。单通道 + mode 不叠加
（``reference_images_with_start_frame=False``）。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from lib.agnes_shared import agnes_base_url, agnes_headers, resolve_agnes_api_key
from lib.aspect_size import VIDEO_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_AGNES
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapability,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
    should_retry_download,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "agnes-video-v2.0"

_VIDEOS_ENDPOINT = "/videos"

# fps 固定 24；num_frames 必须形如 8n+1，上限 441（≈18.4s @24fps）。时长按秒 × fps 取整后
# 对齐到最近的 8n+1。1–3s 会落到 81 帧以下（25/49/73），文档允许的合法值。
_FPS = 24
_FRAME_STEP = 8
_MAX_NUM_FRAMES = 441

# 参考图（多图主体）上限——保守值，与 registry ModelInfo.max_reference_images 同值（编排层裁剪读
# registry、backend 生成时防御读此处）。待 Agnes console / 实测核对，不硬编当既成事实。
_MAX_REFERENCE_IMAGES = 4

# 尺寸约束：长宽被 8 整除、长边收口 1920（保守值，覆盖上游 480p/720p/1080p 三档标准化）。
# 缺 resolution 时按 720p 短边兜底。待 console / 实测核对像素上限，不硬编当既成事实。
_VIDEO_ROUND_TO = 8
_MAX_LONG_EDGE = 1920

# submit 超时 ~300s：覆盖上游争用时的长阻塞，避免可重试的繁忙被 ReadTimeout 包成终态歧义失败。
_SUBMIT_TIMEOUT_SECONDS = 300.0
# 轮询 / 下载用较短超时（幂等 GET 正常秒级返回）。
_POLL_HTTP_TIMEOUT_SECONDS = 60.0

_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 60.0

_KEYFRAMES_MODE = "keyframes"

# 进日志的安全标量白名单；image / extra_body 内的 base64 一律不入日志。
_SAFE_LOG_KEYS = ("model", "height", "width", "num_frames", "frame_rate", "seed")


def _duration_to_num_frames(duration_seconds: int) -> int:
    """秒 → num_frames：秒 × fps 取整后对齐到最近的 ``8n+1``，上限 441。"""
    target = max(1, duration_seconds) * _FPS
    n = round((target - 1) / _FRAME_STEP)
    num_frames = _FRAME_STEP * n + 1
    return max(1, min(num_frames, _MAX_NUM_FRAMES))


def _resolve_size(resolution: str | None, aspect_ratio: str) -> tuple[int, int]:
    """比例优先、清晰度其次：短边来自 resolution（档位 / 自定义 / None 兜底 720p），
    比例精确来自 aspect_ratio、长宽被 8 整除、长边收口 1920。返回 (宽, 高)。
    """
    short = resolution_to_short_edge(resolution, tier_map=VIDEO_TIER_SHORT_EDGE)
    return aspect_size(aspect_ratio, short, round_to=_VIDEO_ROUND_TO, max_long_edge=_MAX_LONG_EDGE)


def _image_to_bare_base64(image_path: Path) -> str:
    """本地图片 → **裸 base64** 字符串（无 ``data:`` 前缀）。

    Agnes 视频端对整串做 base64 解码，带 ``data:`` 前缀会在生成期触发 padding 错误，故不复用
    仓库通用 data-URI helper（图像端接受 data-URI，视频端不接受，二者不可混用）。
    """
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def _safe_body_for_log(body: dict) -> dict:
    """安全日志视图：白名单标量 + prompt 仅长度 + 图像仅计数（base64 不入日志）。"""
    view: dict = {key: body[key] for key in _SAFE_LOG_KEYS if key in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt_len"] = len(prompt)
    if body.get("image"):
        view["image"] = "<start_frame>"
    extra = body.get("extra_body")
    if isinstance(extra, dict) and isinstance(extra.get("image"), list):
        mode = extra.get("mode")
        view["extra_body"] = f"<{len(extra['image'])} img{f', mode={mode}' if mode else ''}>"
    return view


def _extract_task_id(body: dict) -> str:
    """从提交响应取轮询用 task_id（``task_id`` 优先，回落 ``id``）。"""
    for key in ("task_id", "id"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    raise RuntimeError(f"Agnes 视频提交返回体缺少 task_id: {body}")


def _extract_duration_seconds(final: dict, fallback: int) -> int:
    """从轮询终态取实际时长（``usage.duration_seconds`` 优先，回落顶层 ``seconds``，再回落请求值）。"""
    usage = final.get("usage")
    if isinstance(usage, dict):
        parsed = _coerce_int(usage.get("duration_seconds"))
        if parsed is not None:
            return parsed
    parsed = _coerce_int(final.get("seconds"))
    if parsed is not None:
        return parsed
    return fallback


def _coerce_int(value: object) -> int | None:
    """把 ``"10.0"`` / ``10`` 这类时长值归一化为 int；不可解析回 None。"""
    if value is None:
        return None
    try:
        return int(float(value))  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError):
        return None


def _failure_reason(state: dict) -> str | None:
    """``status=failed`` → 错误描述；其余 → None。"""
    if state.get("status") != "failed":
        return None
    err = state.get("error")
    if isinstance(err, dict):
        message = err.get("message") or err.get("code") or "unknown"
    else:
        message = err or "unknown"
    return f"Agnes 视频生成失败: {message}"


class AgnesVideoBackend(ProviderJobIdPersistenceMixin):
    """Agnes 视频后端（异步 submit/poll，裸 base64 图像，支持 resume）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = _POLL_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = resolve_agnes_api_key(api_key)
        self._base_url = agnes_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_AGNES

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        # 首帧 + 尾帧（首尾关键帧）+ 多图主体参考；参考图不与首帧叠加（单通道 + mode 不可叠加）。
        return VideoCapabilities(
            first_frame=True,
            last_frame=True,
            reference_images=True,
            max_reference_images=_MAX_REFERENCE_IMAGES,
            reference_images_with_start_frame=False,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        payload = self._build_payload(request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload)
            logger.info("Agnes 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            await self._persist_provider_job_id(request, task_id, provider=PROVIDER_AGNES)
            return await self._poll_and_build(client, task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Agnes task：仅轮询 + 下载，不重新提交（ADR 0007）。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        """构建提交体。

        通道优先级（单通道，不叠加）：参考图 → ``extra_body.image=[refs]``；首+尾帧 →
        ``extra_body.image=[s,e]`` + ``mode=keyframes``；仅起始图 → 顶层 ``image``；都无 → 文生视频。
        """
        width, height = _resolve_size(request.resolution, request.aspect_ratio)
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "height": height,
            "width": width,
            "num_frames": _duration_to_num_frames(request.duration_seconds),
            "frame_rate": _FPS,
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        reference_images = self._valid_paths(request.reference_images)
        start_image = self._single_path(request.start_image)
        end_image = self._single_path(request.end_image)

        if reference_images:
            if len(reference_images) > _MAX_REFERENCE_IMAGES:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    count=len(reference_images),
                    limit=_MAX_REFERENCE_IMAGES,
                )
            payload["extra_body"] = {"image": [self._encode_reference(p) for p in reference_images]}
        elif start_image is not None and end_image is not None:
            payload["extra_body"] = {
                "image": [self._encode_start(start_image), self._encode_start(end_image)],
                "mode": _KEYFRAMES_MODE,
            }
        elif start_image is not None:
            payload["image"] = self._encode_start(start_image)

        return payload

    @staticmethod
    def _single_path(value: str | Path | None) -> Path | None:
        """把请求里的图像字段归一化成 Path；空 / 空串 → None。"""
        if isinstance(value, (str, Path)) and str(value):
            return Path(value)
        return None

    @staticmethod
    def _valid_paths(values: list[Path] | None) -> list[Path]:
        return [Path(v) for v in (values or []) if v]

    def _encode_start(self, path: Path) -> str:
        """裸 base64 编码首/尾帧；缺失或不可读 fail-loud（不静默退化为文生视频）。"""
        if not path.is_file():
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name)
        try:
            return _image_to_bare_base64(path)
        except OSError as exc:
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name) from exc

    def _encode_reference(self, path: Path) -> str:
        """裸 base64 编码参考图；缺失或不可读 fail-loud（不静默丢弃后照常计费）。"""
        if not path.is_file():
            raise VideoCapabilityError("video_reference_images_unreadable", model=self._model, names=path.name)
        try:
            return _image_to_bare_base64(path)
        except OSError as exc:
            raise VideoCapabilityError("video_reference_images_unreadable", model=self._model, names=path.name) from exc

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        # 非幂等的「建任务 + 计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败，
        # 避免重试重复建任务 + 重复计费；>=400 抛 HTTPStatusError 交 should_retry_submit 按状态码分流
        # （5xx/408/429 重试——含上游繁忙 503；确定性 4xx 快失败）。submit 用长超时覆盖上游长阻塞。
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}{_VIDEOS_ENDPOINT}",
                json=payload,
                headers=agnes_headers(self._api_key),
                timeout=_SUBMIT_TIMEOUT_SECONDS,
            ),
            provider=PROVIDER_AGNES,
        )
        return _extract_task_id(resp.json())

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}{_VIDEOS_ENDPOINT}/{task_id}",
            headers=agnes_headers(self._api_key),
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
        # resume 路径下 404 直接转 ResumeExpiredError：should_retry_poll 把轮询 404 当「短暂未就绪」
        # 重试，对已过期的 resume 任务会一直重到超时、永不落终态，故在此一击转终态异常。非 resume 的
        # 4xx 原样抛出，交 should_retry_poll 按 status_code 分流。
        async def _gated_poll() -> dict:
            try:
                return await self._poll_once(client, task_id)
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=task_id, provider=PROVIDER_AGNES) from exc
                raise

        final = await poll_with_retry(
            poll_fn=_gated_poll,
            is_done=lambda state: state.get("status") in ("completed", "failed"),
            is_failed=_failure_reason,
            poll_interval=_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
            retry_if=should_retry_poll,
            label="Agnes",
            on_progress=lambda v, elapsed: logger.info(
                "Agnes 视频生成中... status=%s progress=%s elapsed=%ds",
                v.get("status"),
                v.get("progress"),
                int(elapsed),
            ),
        )

        video_url = final.get("remixed_from_video_id")
        if not isinstance(video_url, str) or not video_url:
            raise RuntimeError(f"Agnes 任务完成但缺少 remixed_from_video_id 成片 URL: {final}")

        await self._download_with_retry(video_url, request.output_path)
        logger.info("Agnes 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_AGNES,
            model=self._model,
            duration_seconds=_extract_duration_seconds(final, request.duration_seconds),
            video_uri=video_url,
            task_id=task_id,
            seed=request.seed,
            generate_audio=request.generate_audio,
        )

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """下载成片 URL（幂等 GET），独立的下载重试范围，不回退到重跑生成 POST。"""
        await download_video(video_url, output_path)

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
