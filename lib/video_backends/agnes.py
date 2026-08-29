"""AgnesVideoBackend — Agnes 视频生成后端（裸 base64 + 异步轮询 + resume）。

走 apihub 网关上的 OpenAI 风格异步端点：submit ``POST /v1/videos``（JSON）取 task_id →
轮询 ``GET /v1/videos/{task_id}`` 至 ``status=completed``。成片 URL 分两级取：完成态响应
自带直接 URL 字段（``url`` / ``video_url`` / ``metadata.url``，或 ``remixed_from_video_id``
恰好是 URL 形态时兼容旧网关）即直接下载；只有 ``video_id`` 时以其向网关根下的成片查询端点
``GET /agnesapi?video_id=...`` 二次查询，从查询响应同样按上述字段取 URL。
``remixed_from_video_id`` 语义是 remix 来源视频 ID，非 URL 形态时一律不当下载地址。
状态机 ``queued → in_progress → completed / failed``。

轮询端点 ``/v1/videos/{task_id}`` 是网关的旧版任务查询接口，仍受支持；成片结果查询归
``/agnesapi?video_id=``（网关文档指明按 video_id 查询，不要拿 task_id 打这个端点）。

能力约束：fps 固定 24；时长 1–18s（内部 ``num_frames = 最近的 8n+1``，由秒 × fps 取整对齐，
上限 441 帧）；分辨率经 aspect_size 精确算出并显式下发 ``height`` × ``width``（不显式下发时
上游回落自身默认横屏尺寸）。

关键帧 / 多图映射：无图 → 文生视频；起始图 → 顶层 ``image``；首尾帧 → ``extra_body.image=[s,e]``
+ ``mode="keyframes"``；参考图 → ``extra_body.image=[refs]``。单通道 + mode 不叠加。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from lib.agnes_shared import agnes_base_url, agnes_headers, agnes_host, resolve_agnes_api_key
from lib.aspect_size import VIDEO_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_AGNES
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
    recording_poll,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "agnes-video-v2.0"

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

# 参考图（多图主体）上限——保守值，编排层裁剪与 backend 生成时防御同读此处（唯一声明处）。
# 取值未经 Agnes console 核对，不硬编当既成事实。
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

# 失败终态集合：除文档化的 failed 外，纳入 error / cancelled / canceled，避免上游以非标准失败态
# 收尾时被当「仍在进行」轮询到超时。
_FAILED_STATUSES = ("failed", "error", "cancelled", "canceled")

# 进日志的安全标量白名单；image / extra_body 内的 base64 一律不入日志。
_SAFE_LOG_KEYS = ("model", "height", "width", "num_frames", "frame_rate", "seed")

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
        当前全系模型能力一致，不按 model_id 分支；instance property 委托至此，
        保持 backend 为单一真相源。

        音轨恒无声：请求体没有音轨字段、成片不带音轨（``generate`` 结算时直接写死
        ``generate_audio=False``），用户的开启意图无处可下发。
        """
        return VideoCapabilities(
            first_frame=True,
            last_frame=True,
            max_reference_images=_MAX_REFERENCE_IMAGES,
            audio_track=VideoAudioMode.ALWAYS_OFF,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 读盘 + base64 编码（首尾帧最多 2 张、参考图最多 4 张，可能数 MB）offload 到线程，
        # 避免阻塞共享 worker 事件循环（与 image 后端及 grok/gemini 视频后端一致）。
        payload = await asyncio.to_thread(self._build_payload, request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload, request)
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

        # 参考图与首/尾帧走互斥的单通道。两者同时给出时
        # fail-loud，而非静默走参考图分支丢掉用户的首/尾帧。
        if reference_images and (start_image is not None or end_image is not None):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)

        # 尾帧仅在 keyframes（首+尾）模式下生效，无独立尾帧通道。只给尾帧时 fail-loud，而非静默
        # 退化为文生视频——video_capabilities.last_frame=True 表示支持首尾帧对，不含单独尾帧。
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

    def _reject_out_of_range_duration(self, duration_seconds: int) -> None:
        """时长越界 [_MIN, _MAX] 时 fail-loud；上游若漏校验，避免静默截帧 + 错记计费时长。"""
        if not _MIN_DURATION_SECONDS <= duration_seconds <= _MAX_DURATION_SECONDS:
            raise VideoCapabilityError(
                "video_duration_not_supported",
                model=self._model,
                duration=duration_seconds,
                supported=f"{_MIN_DURATION_SECONDS}-{_MAX_DURATION_SECONDS}",
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

    def _encode_start(self, path: Path) -> str:
        """裸 base64 编码首帧；缺失或不可读 fail-loud（不静默退化为文生视频）。"""
        return self._encode_image(path, error_code="video_start_image_unreadable", name=path.name or str(path))

    def _encode_end(self, path: Path) -> str:
        """裸 base64 编码尾帧；缺失或不可读 fail-loud（错误指向尾帧而非首帧）。"""
        return self._encode_image(path, error_code="video_end_image_unreadable", name=path.name or str(path))

    def _encode_reference(self, path: Path) -> str:
        """裸 base64 编码参考图；缺失或不可读 fail-loud（不静默丢弃后照常计费）。"""
        return self._encode_image(path, error_code="video_reference_images_unreadable", names=path.name or str(path))

    def _encode_image(self, path: Path, *, error_code: str, **err_params: str) -> str:
        """裸 base64 编码图像；缺失或不可读时按通道 error_code / 参数名 fail-loud。"""
        if not path.is_file():
            raise VideoCapabilityError(error_code, model=self._model, **err_params)
        try:
            return _image_to_bare_base64(path)
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
        return _extract_task_id(resp.json())

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}{_VIDEOS_ENDPOINT}/{task_id}",
            headers=agnes_headers(self._api_key),
        )
        resp.raise_for_status()
        return resp.json()

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_poll,
    )
    async def _query_video(self, client: httpx.AsyncClient, video_id: str, request: VideoGenerationRequest) -> dict:
        """按 ``video_id`` 向成片查询端点二次查询（完成态只含 video_id、无直接 URL 字段时）。

        该端点挂在网关根而非 ``/v1`` 下，且只认 video_id——拿 task_id 打它会排队异常。
        幂等 GET，复用轮询同一套重试判定与留痕边界：它与轮询打的是同一个供应商任务，
        成功与失败响应都要留痕，否则这一步失败时诊断字段停在上一次轮询的响应上。
        """

        async def fetch() -> dict:
            resp = await client.get(
                f"{self._host}{_VIDEO_QUERY_ENDPOINT}",
                params={"video_id": video_id},
                headers=agnes_headers(self._api_key),
            )
            resp.raise_for_status()
            return resp.json()

        return await recording_poll(fetch, request)()

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
        # resume 路径下 404 直接转 ResumeExpiredError：should_retry_poll 把轮询 404 当「短暂未就绪」
        # 重试，对已过期的 resume 任务会一直重到超时、永不落终态，故在此一击转终态异常。非 resume 的
        # 4xx 原样抛出，交 should_retry_poll 按 status_code 分流。
        # 留痕包在闸门里侧：闸门把 404 换成 ResumeExpiredError，包在外侧就再也看不到那个响应。
        recorded_poll = recording_poll(lambda: self._poll_once(client, task_id), request)

        async def _gated_poll() -> dict:
            try:
                return await recorded_poll()
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=task_id, provider=PROVIDER_AGNES) from exc
                raise

        final = await poll_with_retry(
            poll_fn=_gated_poll,
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
            # Agnes 视频无音频能力（未声明 GENERATE_AUDIO、提交体不带音频字段），成片恒无声；
            # 固定 False 与 kling/vidu 无声模型一致，避免下游（计费/版本元数据/剪映导出）误判有声。
            generate_audio=False,
        )

    @staticmethod
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """下载成片 URL（幂等 GET），走共用的产物下载预算，不回退到重跑生成 POST。"""
        await download_video(video_url, output_path, label="Agnes")
