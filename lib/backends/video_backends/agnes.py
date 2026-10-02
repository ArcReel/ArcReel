"""AgnesVideoBackend — Agnes 视频生成后端（异步轮询 + resume）。

v2.0 走 ``POST /v1/videos`` 后轮询 ``GET /v1/videos/{task_id}``，请求以 WxH / 帧数描述输出，
媒体输入为裸 base64。2.5 走同一提交端点，但请求改为 ``mode`` + 秒数字符串 + 分辨率档位，
媒体输入为 Data URI，并以 ``GET /agnesapi?video_id=...&model_name=...`` 轮询。

两种契约都以 ``status`` 判定终态，以顶层 ``url`` 或查询响应 URL 下载成片；2.5 从响应的
``seconds`` 取实际计费时长。关键帧 / 多图映射遵循单通道约束：无图为文生视频，首帧或首尾帧走
``keyframe``，参考图走 ``reference``，参考图不与帧混用。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from arcreel_market_core.aspect_size import VIDEO_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from arcreel_market_core.video_backend_contract import (
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.agnes_shared import agnes_base_url, agnes_headers, agnes_host, resolve_agnes_api_key
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    download_resumable_video,
    poll_with_retry,
    recording_poll,
    resume_expiry_gate,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.image_backends.base import image_to_base64_data_uri
from lib.backends.providers import PROVIDER_AGNES
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    with_retry_async,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "agnes-video-2.5-flash"

_VIDEOS_ENDPOINT = "/videos"
# 成片查询端点，挂在网关根（不在 /v1 下），按 video_id 查询。
_VIDEO_QUERY_ENDPOINT = "/agnesapi"

# fps 固定 24；num_frames 必须形如 8n+1，上限 441（≈18.4s @24fps）。时长按秒 × fps 取整后
# 对齐到最近的 8n+1。1–3s 会落到 81 帧以下（25/49/73），文档允许的合法值。
_FPS = 24
_FRAME_STEP = 8
_MAX_NUM_FRAMES = 441

# 后端防御时长边界，与 registry agnes-video-v2.0 的 supported_durations（1..18s）同步。越界请求
# fail-loud，而非静默截断到 _MAX_NUM_FRAMES——否则 30s 请求实际只生成约 18s，却按原请求秒数计费。
_MIN_DURATION_SECONDS = 1
_MAX_DURATION_SECONDS = 18

_V25_MIN_DURATION_SECONDS = 4
_V25_MAX_DURATION_SECONDS = 12
_V25_FLASH_MODEL = "agnes-video-2.5-flash"
_V25_MODEL = "agnes-video-2.5"
_V25_ASPECT_RATIOS = frozenset({"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"})
_V25_RESOLUTIONS = {"720P": "720P", "1080P": "1080P", "2K": "2K"}
_V25_FLASH_RESOLUTIONS = {"720P": "720P"}
_V25_REFERENCE_LIMITS = {_V25_FLASH_MODEL: 5, _V25_MODEL: 8}

# v2.0 参考图（多图主体）上限；2.5 的 flash / 完整版上限见 _V25_REFERENCE_LIMITS。
_MAX_REFERENCE_IMAGES = 4

# 尺寸约束：长宽被 8 整除、长边收口 1920（保守值，覆盖上游 480p/720p/1080p 三档标准化）。
# 缺 resolution 时按 720p 短边兜底。像素上限未经 Agnes console 核对，不硬编当既成事实。
_VIDEO_ROUND_TO = 8
_MAX_LONG_EDGE = 1920

# submit 超时 ~300s：覆盖上游争用时的长阻塞，避免可重试的繁忙被 ReadTimeout 包成终态歧义失败。
_SUBMIT_TIMEOUT_SECONDS = 300.0
# 轮询 / 下载用较短超时（幂等 GET 正常秒级返回）。
_POLL_HTTP_TIMEOUT_SECONDS = 60.0

_KEYFRAMES_MODE = "keyframes"
_V25_TEXT_MODE = "text"
_V25_KEYFRAME_MODE = "keyframe"
_V25_REFERENCE_MODE = "reference"

# 失败终态集合：除文档化的 failed 外，纳入 error / cancelled / canceled，避免上游以非标准失败态
# 收尾时被当「仍在进行」轮询到超时。
_FAILED_STATUSES = ("failed", "error", "cancelled", "canceled")

# 进日志的安全标量白名单；image / extra_body 内的 base64 一律不入日志。
_SAFE_LOG_KEYS = (
    "model",
    "height",
    "width",
    "num_frames",
    "frame_rate",
    "seconds",
    "size",
    "aspect_ratio",
    "mode",
    "seed",
)

# 完成态响应中可能承载成片 URL 的权威字段，按优先级探测（顶层与 metadata 同权，顶层优先）。
_PRIMARY_URL_FIELDS = ("url", "video_url")

# remix 来源视频 ID 字段。语义不是 URL（非 URL 形态时不当下载地址），列在此处仅为兼容部分
# 网关把成片 URL 直接回填在该字段的行为；优先级低于 _PRIMARY_URL_FIELDS——顶层与 metadata
# 的权威字段任一命中都优先于本字段，避免它抢在真正的成片 URL 前面被当下载地址。
_COMPAT_URL_FIELD = "remixed_from_video_id"


def _looks_like_url(value: str) -> bool:
    """粗粒度 URL 形态校验：http/https scheme + 非空 netloc。用于把 remixed_from_video_id
    这类语义不是 URL 的字段与真正的下载地址区分开，避免把 remix 来源 ID 误当 URL 下载。
    """
    if not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _first_url_field(body: dict) -> str | None:
    """探测响应体中形态为 URL 的成片地址，按 _PRIMARY_URL_FIELDS 优先、_COMPAT_URL_FIELD
    兜底的顺序；每一级顶层与 ``metadata``（网关成片查询把下载地址放在 metadata.url）同权、
    顶层优先，无命中返回 None。

    _COMPAT_URL_FIELD 兜底级必须整体排在 _PRIMARY_URL_FIELDS 之后：否则顶层的兼容字段会
    抢在 metadata 里的权威 URL 字段前面命中，误把兼容字段值当下载地址。
    """
    metadata = body.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    for key in _PRIMARY_URL_FIELDS:
        for source in (body, metadata):
            value = source.get(key)
            if isinstance(value, str) and _looks_like_url(value):
                return value

    for source in (body, metadata):
        value = source.get(_COMPAT_URL_FIELD)
        if isinstance(value, str) and _looks_like_url(value):
            return value

    return None


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


def _image_to_data_uri(image_path: Path) -> str:
    """本地图片 → ``data:image/...;base64,...``；2.5 契约的媒体输入统一使用该形态。"""
    return image_to_base64_data_uri(image_path)


def _uses_v25_contract(model: str) -> bool:
    return model in {_V25_MODEL, _V25_FLASH_MODEL}


def _safe_body_for_log(body: dict) -> dict:
    """安全日志视图：白名单标量 + prompt 仅长度 + 图像仅计数（base64 不入日志）。"""
    view: dict = {key: body[key] for key in _SAFE_LOG_KEYS if key in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt_len"] = len(prompt)
    if body.get("image"):
        view["image"] = "<start_frame>"
    if body.get("first_frame"):
        view["first_frame"] = "<first_frame>"
    if body.get("last_frame"):
        view["last_frame"] = "<last_frame>"
    images = body.get("images")
    if isinstance(images, list):
        view["images"] = f"<{len(images)} ref>"
    extra = body.get("extra_body")
    if isinstance(extra, dict) and isinstance(extra.get("image"), list):
        mode = extra.get("mode")
        view["extra_body"] = f"<{len(extra['image'])} img{f', mode={mode}' if mode else ''}>"
    return view


def _extract_task_id(body: dict, *, prefer_video_id: bool) -> str:
    """从提交响应取轮询用任务 id；2.5 轮询 video_id，v2.0 路径轮询 task_id。"""
    keys = ("video_id", "task_id", "id") if prefer_video_id else ("task_id", "video_id", "id")
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    # 仅暴露字段名，不回显整串响应（可能含 prompt / 签名 URL 等敏感字段，与 _safe_body_for_log 同口径）。
    raise RuntimeError(f"Agnes 视频提交返回体缺少 task_id（字段: {sorted(body)}）")


def _extract_duration_seconds(final: dict, queried: dict | None, fallback: int) -> int:
    """从轮询终态取实际成片时长（顶层 ``seconds``），缺失时改读 video_id 二次查询响应的
    ``seconds``（完成态只带 video_id 时才有此响应），两处均缺失或不可解析才回落请求时长。

    不读 ``usage.duration_seconds``——该字段是任务处理耗时，与成片时长无关，读它会错记
    计费与元数据。
    """
    parsed = _coerce_duration(final.get("seconds"))
    if parsed is not None:
        return parsed
    if queried is not None:
        parsed = _coerce_duration(queried.get("seconds"))
        if parsed is not None:
            return parsed
    return fallback


def _coerce_duration(value: object) -> int | None:
    """把 ``"10.0"`` / ``10`` 这类时长值归一化为计费秒数：half-up 取整（4.5→5，不少计），
    非正值 / 超 24h 上限（防 DB Integer 列溢出）/ 不可解析一律回 None，由 caller 回落请求时长。
    """
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
        if not 0 < decimal_value <= MAX_BILLED_DURATION_SECONDS:
            return None
        return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _failure_reason(state: dict) -> str | None:
    """失败终态（failed / error / cancelled / canceled）→ 错误描述；其余 → None。

    不止认 ``failed``：上游若以其他失败态收尾，仅认 completed/failed 会把它当「仍在进行」轮询到
    max_wait 才抛误导性 TimeoutError，白占 worker 通道；显式枚举失败态让其快速失败。
    """
    if state.get("status") not in _FAILED_STATUSES:
        return None
    err = state.get("error")
    message = (err.get("message") or err.get("code") or "unknown") if isinstance(err, dict) else (err or "unknown")
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
        self._host = agnes_host(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_AGNES

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算 caps —— 不构造 SDK client（无需 api_key）。

        首帧 + 尾帧（首尾关键帧）+ 多图主体参考；参考图不与首帧叠加（单通道 + mode 不可叠加）。
        2.5 系列固定产出音频，flash 与完整版参考图上限分别为 5 / 8；v2.0 保持无声与 4 图上限。

        instance property 委托至此，保持 backend 为单一真相源。
        """
        if model in (_V25_FLASH_MODEL, _V25_MODEL):
            max_reference_images = _V25_REFERENCE_LIMITS[model]
            audio_track = VideoAudioMode.ALWAYS_ON
        else:
            max_reference_images = _MAX_REFERENCE_IMAGES
            audio_track = VideoAudioMode.ALWAYS_OFF
        return VideoCapabilities(
            first_frame=True,
            last_frame=True,
            max_reference_images=max_reference_images,
            audio_track=audio_track,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 读盘 + 编码（首尾帧最多 2 张、参考图最多 8 张，可能数 MB）offload 到线程，
        # 避免阻塞共享 worker 事件循环（与 image 后端及 grok/gemini 视频后端一致）。
        payload = await asyncio.to_thread(self._build_payload, request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload, request)
            logger.info("Agnes 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            await self._persist_provider_job_id(request, task_id, provider=PROVIDER_AGNES)
            return await self._poll_and_build(client, task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Agnes task：仅轮询 + 下载，不重新提交（ADR 0007）。"""
        async with artifact_http_client(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        """按 model 分派旧 v2.0 或 2.5 请求契约。"""
        if _uses_v25_contract(self._model):
            return self._build_v25_payload(request)
        return self._build_v20_payload(request)

    def _build_v20_payload(self, request: VideoGenerationRequest) -> dict:
        """构建 v2.0 提交体，保持既有 WxH / 帧数 / 裸 base64 契约不变。"""
        self._reject_out_of_range_duration(request.duration_seconds)
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

        if reference_images and (start_image is not None or end_image is not None):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
        if end_image is not None and start_image is None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

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
                "image": [self._encode_start(start_image), self._encode_end(end_image)],
                "mode": _KEYFRAMES_MODE,
            }
        elif start_image is not None:
            payload["image"] = self._encode_start(start_image)

        return payload

    def _build_v25_payload(self, request: VideoGenerationRequest) -> dict:
        """构建 2.5 提交体：mode + 秒数字符串 + 分辨率档位 + Data URI 媒体。"""
        self._reject_out_of_range_duration(request.duration_seconds)
        self._reject_v25_aspect_ratio(request.aspect_ratio)
        size = self._normalize_v25_resolution(request.resolution)

        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "seconds": str(request.duration_seconds),
            "size": size,
            "aspect_ratio": request.aspect_ratio,
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        reference_images = self._valid_paths(request.reference_images)
        start_image = self._single_path(request.start_image)
        end_image = self._single_path(request.end_image)

        if reference_images and (start_image is not None or end_image is not None):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
        if end_image is not None and start_image is None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

        if reference_images:
            limit = _V25_REFERENCE_LIMITS[self._model]
            if len(reference_images) > limit:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    count=len(reference_images),
                    limit=limit,
                )
            payload["mode"] = _V25_REFERENCE_MODE
            payload["images"] = [self._encode_reference(p, data_uri=True) for p in reference_images]
        elif start_image is not None:
            payload["mode"] = _V25_KEYFRAME_MODE
            payload["first_frame"] = self._encode_start(start_image, data_uri=True)
            if end_image is not None:
                payload["last_frame"] = self._encode_end(end_image, data_uri=True)
        else:
            payload["mode"] = _V25_TEXT_MODE

        return payload

    def _normalize_v25_resolution(self, resolution: str | None) -> str:
        allowed = _V25_FLASH_RESOLUTIONS if self._model == _V25_FLASH_MODEL else _V25_RESOLUTIONS
        normalized = (resolution or "720p").upper()
        if normalized not in allowed:
            raise VideoCapabilityError(
                "video_resolution_not_supported",
                model=self._model,
                resolution=resolution,
                supported=", ".join(allowed),
            )
        return allowed[normalized]

    def _reject_v25_aspect_ratio(self, aspect_ratio: str) -> None:
        if aspect_ratio not in _V25_ASPECT_RATIOS:
            raise VideoCapabilityError(
                "video_aspect_ratio_not_supported",
                model=self._model,
                aspect_ratio=aspect_ratio,
                supported=", ".join(sorted(_V25_ASPECT_RATIOS)),
            )

    def _reject_out_of_range_duration(self, duration_seconds: int) -> None:
        """时长越界时 fail-loud；2.5 为 4–12s，v2.0 保持 1–18s。"""
        if _uses_v25_contract(self._model):
            minimum = _V25_MIN_DURATION_SECONDS
            maximum = _V25_MAX_DURATION_SECONDS
        else:
            minimum = _MIN_DURATION_SECONDS
            maximum = _MAX_DURATION_SECONDS
        if not minimum <= duration_seconds <= maximum:
            raise VideoCapabilityError(
                "video_duration_not_supported",
                model=self._model,
                duration=duration_seconds,
                supported=f"{minimum}-{maximum}",
            )

    @staticmethod
    def _single_path(value: str | Path | None) -> Path | None:
        """把请求里的图像字段归一化成 Path；空 / 空串 / 空 Path（``Path("")`` 会塌成 ``Path(".")``）→ None。"""
        if value is None:
            return None
        text = str(value)
        if not text or text == ".":
            return None
        return Path(text)

    @classmethod
    def _valid_paths(cls, values: list[Path] | None) -> list[Path]:
        """归一化参考图列表：剔除空 / 空 Path（``[Path(v) for v if v]`` 对 Path 恒真，不起过滤作用）。"""
        return [p for v in (values or []) if (p := cls._single_path(v)) is not None]

    def _encode_start(self, path: Path, *, data_uri: bool = False) -> str:
        """编码首帧；缺失或不可读 fail-loud（不静默退化为文生视频）。"""
        return self._encode_image(
            path,
            error_code="video_start_image_unreadable",
            name=path.name or str(path),
            data_uri=data_uri,
        )

    def _encode_end(self, path: Path, *, data_uri: bool = False) -> str:
        """编码尾帧；缺失或不可读 fail-loud（错误指向尾帧而非首帧）。"""
        return self._encode_image(
            path,
            error_code="video_end_image_unreadable",
            name=path.name or str(path),
            data_uri=data_uri,
        )

    def _encode_reference(self, path: Path, *, data_uri: bool = False) -> str:
        """编码参考图；缺失或不可读 fail-loud（不静默丢弃后照常计费）。"""
        return self._encode_image(
            path,
            error_code="video_reference_images_unreadable",
            names=path.name or str(path),
            data_uri=data_uri,
        )

    def _encode_image(self, path: Path, *, error_code: str, data_uri: bool = False, **err_params: str) -> str:
        """编码图像；缺失或不可读时按通道 error_code / 参数名 fail-loud。"""
        if not path.is_file():
            raise VideoCapabilityError(error_code, model=self._model, **err_params)
        try:
            return _image_to_data_uri(path) if data_uri else _image_to_bare_base64(path)
        except OSError as exc:
            raise VideoCapabilityError(error_code, model=self._model, **err_params) from exc

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(
        self, client: httpx.AsyncClient, payload: dict, request: VideoGenerationRequest | None = None
    ) -> str:
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
            request=request,
        )
        return _extract_task_id(resp.json(), prefer_video_id=_uses_v25_contract(self._model))

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}{_VIDEOS_ENDPOINT}/{task_id}",
            headers=agnes_headers(self._api_key),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    async def _query_video_once(self, client: httpx.AsyncClient, video_id: str) -> dict:
        """单次按 ``video_id`` 查询；重试由调用方选择内层装饰器或外层轮询统一承担。"""
        params = {"video_id": video_id}
        if _uses_v25_contract(self._model):
            params["model_name"] = self._model
        resp = await client.get(
            f"{self._host}{_VIDEO_QUERY_ENDPOINT}",
            params=params,
            headers=agnes_headers(self._api_key),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_poll,
    )
    async def _query_video(
        self,
        client: httpx.AsyncClient,
        video_id: str,
        request: VideoGenerationRequest,
        *,
        record: bool = True,
    ) -> dict:
        """按 ``video_id`` 查询成片任务；2.5 同时携带 ``model_name``。

        该端点是 2.5 的主轮询接口；v2.0 只在终态无直接 URL、带 video_id 时用它二次查询。
        幂等 GET，复用轮询同一套重试判定与留痕边界。
        """

        async def fetch() -> dict:
            return await self._query_video_once(client, video_id)

        if not record:
            return await fetch()
        return await recording_poll(fetch, request, stage="result")()

    async def _resolve_video_url(
        self, client: httpx.AsyncClient, final: dict, request: VideoGenerationRequest
    ) -> tuple[str, dict | None]:
        """成片 URL 两级来源：完成态直接字段命中即用；否则用 video_id 二次查询取 URL。

        命中二次查询时一并返回该查询响应体（未查询则 None），供调用方从中补解析成片时长——
        终态响应可能不带 ``seconds``，只有二次查询响应才带。

        两级来源均不可用时报错信息只列字段名，不回显响应体（可能含签名 URL 等敏感字段，
        与 _safe_body_for_log 同口径）。
        """
        video_url = _first_url_field(final)
        if video_url is not None:
            return video_url, None

        video_id = final.get("video_id")
        if isinstance(video_id, str) and video_id:
            queried = await self._query_video(client, video_id, request)
            video_url = _first_url_field(queried)
            if video_url is not None:
                return video_url, queried
            raise RuntimeError(f"Agnes 任务完成但 video_id 查询响应缺少成片 URL（字段: {sorted(queried)}）")

        raise RuntimeError(f"Agnes 任务完成但缺少成片 URL 与 video_id（字段: {sorted(final)}）")

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        async def poll_fn() -> dict:
            if _uses_v25_contract(self._model):
                return await self._query_video_once(client, task_id)
            return await self._poll_once(client, task_id)

        gated_poll = resume_expiry_gate(
            recording_poll(poll_fn, request),
            resume_job_id=task_id if is_resume else None,
            provider=PROVIDER_AGNES,
        )

        final = await poll_with_retry(
            poll_fn=gated_poll,
            is_done=lambda state: state.get("status") in ("completed", "failed"),
            is_failed=_failure_reason,
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="Agnes",
            on_progress=lambda v, elapsed: logger.info(
                "Agnes 视频生成中... status=%s progress=%s elapsed=%ds",
                v.get("status"),
                v.get("progress"),
                int(elapsed),
            ),
        )

        video_url, queried = await self._resolve_video_url(client, final, request)

        await self._download_with_retry(video_url, request.output_path)
        logger.info("Agnes 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_AGNES,
            model=self._model,
            duration_seconds=_extract_duration_seconds(final, queried, request.duration_seconds),
            video_uri=video_url,
            task_id=task_id,
            seed=request.seed,
            generate_audio=_uses_v25_contract(self._model),
        )

    @staticmethod
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """下载成片 URL（幂等 GET），走共用的产物下载预算，不回退到重跑生成 POST。"""
        await download_resumable_video(video_url, output_path, label="Agnes")
