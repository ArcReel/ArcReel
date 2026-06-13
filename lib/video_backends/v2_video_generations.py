"""V2VideoGenerationsBackend —— 通用「流派 C」/v2/video/generations 视频后端。

对接「单端点 + model 字段切换」的中转协议（aimlapi / xAI / getimg.ai / APIMart /
CometAPI 等事实标准）：``POST /v2/video/generations`` 提交拿 generation_id，
``GET /v2/video/generations?generation_id={id}`` 轮询，终态后取视频 URL 下载。

单端点承载多模型（discriminated union by model），各模型可选字段集合不同。本后端
只编码一份通用 canonical I/O 契约 + 多路径容错解析（不背 per-model schema）：

- 请求体取各家公共子集（model / prompt / duration / image_url / last_image_url /
  image_urls / seed / resolution）；尾帧键随模型差异（Veo ``last_image_url`` /
  Kling ``tail_image_url``），generic 取最常见的 ``last_image_url``。
- 状态串归一化：覆盖 aimlapi 官方枚举（queued/generating/completed/error）并并入
  跨厂商同义词，未知串当 running 继续轮询。
- 视频 URL / task_id / status 各用一张多路径优先级表逐个试取，容忍各家回包结构差异。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

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
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)

PROVIDER_V2_VIDEO = "v2-video-generations"

_SUBMIT_PATH = "/v2/video/generations"
_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 600
_POLL_TIMEOUT_PER_SECOND = 30

# generic 端点跨多模型，参考图上限无单一供应商真相；取保守默认，由 resolver 在 endpoint
# cap 未声明（None）时 fallthrough 读取。非供应商核实值，用户按所选模型对齐。
_DEFAULT_MAX_REFERENCE_IMAGES = 4

# 超过此阈值的图触发 warning（base64 编码后易触发中转站请求体上限）
_LARGE_IMAGE_WARN_BYTES = 4 * 1024 * 1024

# 日志摘要里 prompt 截断长度（避免长 prompt 撑爆日志）
_PROMPT_LOG_MAX = 200

# 状态串 → canonical（lowercase 后查表）。覆盖 aimlapi 官方枚举 queued/generating/
# completed/error，并并入跨厂商同义词（流派 C 路由到多家时底层状态串可能透传）。
_STATUS_SYNONYMS: dict[str, str] = {
    "completed": "succeeded",
    "succeeded": "succeeded",
    "succeed": "succeeded",
    "success": "succeeded",
    "failed": "failed",
    "fail": "failed",
    "error": "failed",
    "expired": "failed",
    "canceled": "failed",
    "cancelled": "failed",
    "generating": "running",
    "in_progress": "running",
    "running": "running",
    "processing": "running",
    "queued": "queued",
    "queueing": "queued",
    "preparing": "queued",
    "submitted": "queued",
    "pending": "queued",
    "created": "queued",
}

# 多路径优先级表，配 _dig 逐层走 dict key / list 下标（int 段表 list 下标）。
# 取自参数对齐表「视频 URL 路径 / task_id 路径」的流派 C 并集。
_VIDEO_URL_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("video", "url"),
    ("assets", "video"),
    ("output", "video_url"),
    ("content", "video_url"),
    ("data", "task_result", "videos", 0, "url"),
    ("url",),
)
_TASK_ID_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("generation_id",),
    ("id",),
    ("task_id",),
    ("data", "task_id"),
    ("request_id",),
    ("data", "taskId"),
)
_STATUS_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("status",),
    ("state",),
    ("data", "status"),
    ("data", "state"),
    ("output", "status"),
)


def _dig(payload: object, path: tuple[str | int, ...]) -> object | None:
    """按 path 逐层走 dict key / list 下标，任一层缺失返回 None。"""
    cur: object = payload
    for seg in path:
        if isinstance(seg, int):
            if not isinstance(cur, list) or seg >= len(cur):
                return None
            cur = cur[seg]
        else:
            if not isinstance(cur, dict) or seg not in cur:
                return None
            cur = cur[seg]
    return cur


def _first_str_by_paths(payload: object, paths: tuple[tuple[str | int, ...], ...]) -> str | None:
    """按优先级逐个试取第一个非空字符串值（int 容忍并 str 化）。"""
    for path in paths:
        val = _dig(payload, path)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, int) and not isinstance(val, bool):
            return str(val)
    return None


def normalize_status(raw: object) -> str:
    """raw 状态值 → canonical：queued | running | succeeded | failed。未知串当 running。"""
    if not isinstance(raw, str):
        return "running"
    return _STATUS_SYNONYMS.get(raw.strip().lower(), "running")


def _warn_if_large(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size > _LARGE_IMAGE_WARN_BYTES:
        logger.warning(
            "图片较大 (%.1fMB)，base64 编码后可能触发中转站请求体上限: %s",
            size / 1024 / 1024,
            path,
        )


def build_request_body(model: str, request: VideoGenerationRequest) -> dict:
    """按流派 C canonical 拼请求体；缺省字段一律省略。

    图像走 base64 data URI 内嵌（与 newapi 一致）：首帧 ``image_url``、尾帧
    ``last_image_url``、参考数组 ``image_urls``。已知风险：部分中转站要求公网 URL
    而非 base64，真实接受形态留手动集成测试。
    """
    # 延迟导入避免 image_backends ↔ video_backends 循环依赖
    from lib.image_backends.base import image_to_base64_data_uri

    body: dict = {"model": model, "prompt": request.prompt, "duration": request.duration_seconds}
    # 画幅恒有值（默认 9:16），表达项目朝向意图；与 resolution 同属公共子集的尽力透传，
    # 不识别该字段的中转站会忽略。漏发会让竖屏项目在按 aspect_ratio 出片的供应商上变横屏。
    if request.aspect_ratio:
        body["aspect_ratio"] = request.aspect_ratio
    if request.resolution:
        body["resolution"] = request.resolution
    if request.seed is not None:
        body["seed"] = request.seed

    if request.start_image:
        start = Path(request.start_image)
        if start.exists():
            _warn_if_large(start)
            body["image_url"] = image_to_base64_data_uri(start)
        else:
            logger.warning("start_image 文件不存在，已忽略: %s", start)
    if request.end_image:
        end = Path(request.end_image)
        if end.exists():
            _warn_if_large(end)
            body["last_image_url"] = image_to_base64_data_uri(end)
        else:
            logger.warning("end_image 文件不存在，已忽略: %s", end)
    if request.reference_images:
        refs: list[str] = []
        for ref in request.reference_images:
            p = Path(ref)
            if p.exists():
                _warn_if_large(p)
                refs.append(image_to_base64_data_uri(p))
            else:
                logger.warning("reference_image 文件不存在，已忽略: %s", p)
        if refs:
            body["image_urls"] = refs
    return body


def _log_fields(model: str, request: VideoGenerationRequest) -> dict:
    """日志摘要：只从 request 的标量字段 + 图片「有无/数量」构造，自带 prompt 截断。

    两点刻意为之：① 从 request 而非 build_request_body 的产物 body 派生——body 内嵌的图片
    base64 是污点源，从该 dict 取值会被静态分析判为带污点；图片只记有无/数量。② 直接喂
    logger、不过 format_kwargs_for_log——后者内部含密钥脱敏分支，静态分析把其返回值整体判为
    带 secret 污点；本摘要已自带去敏与截断，无需再过那层。
    """
    prompt = request.prompt
    return {
        "model": model,
        "prompt": prompt if len(prompt) <= _PROMPT_LOG_MAX else f"{prompt[:_PROMPT_LOG_MAX]}…<{len(prompt)} chars>",
        "duration": request.duration_seconds,
        "aspect_ratio": request.aspect_ratio,
        "resolution": request.resolution,
        "seed": request.seed,
        "start_image": bool(request.start_image),
        "end_image": bool(request.end_image),
        "reference_images": len(request.reference_images or []),
    }


def _normalize_root(base_url: str) -> str:
    """归一化为 root 形态：补协议 + 去尾斜杠 + 去末尾版本段（/v1、/v2beta 等）。

    后续显式拼 ``/v2/video/generations``。不能用 ensure_openai_base_url —— 它会在
    缺版本段时追加 ``/v1``，与本端点的 ``/v2`` 路径冲突。无 scheme 的纯域名（如
    ``api.aimlapi.com``）先补 ``https://``，否则 httpx 拒收缺协议的相对 URL。
    """
    s = base_url.strip().rstrip("/")
    if s and "://" not in s:
        s = f"https://{s}"
    return re.sub(r"/v\d+(?:\.\d+)?[a-zA-Z]*$", "", s)


def _extract_failure(state: dict) -> str | None:
    if normalize_status(_first_str_by_paths(state, _STATUS_PATHS)) != "failed":
        return None
    err = _dig(state, ("error",))
    if isinstance(err, dict):
        msg = err.get("message") or err.get("name") or "unknown"
    elif isinstance(err, str) and err.strip():
        msg = err
    else:
        msg = "unknown"
    return f"V2 视频生成失败: {msg}"


class V2VideoGenerationsBackend:
    """流派 C ``/v2/video/generations`` 通用视频后端。"""

    def __init__(self, *, api_key: str, base_url: str, model: str, http_timeout: float = 60.0) -> None:
        if not api_key:
            raise ValueError("V2VideoGenerationsBackend 需要 api_key")
        if not base_url:
            raise ValueError("V2VideoGenerationsBackend 需要 base_url")
        self._api_key = api_key
        self._root = _normalize_root(base_url)
        self._model = model
        self._http_timeout = http_timeout
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.SEED_CONTROL,
        }

    @property
    def name(self) -> str:
        return PROVIDER_V2_VIDEO

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算 caps —— 不构造 client。保留 `model` 形参仅为跨 backend 接口统一，
        generic 端点跨多模型无单一供应商真相，当前取保守默认 `_DEFAULT_MAX_REFERENCE_IMAGES`。
        """
        return VideoCapabilities(
            first_frame=True,
            last_frame=True,
            reference_images=True,
            max_reference_images=_DEFAULT_MAX_REFERENCE_IMAGES,
            # 协议 body 中 image_url（首帧）与 image_urls（参考数组）为共存字段，
            # build_request_body 同请求组装两者，首帧语义保持。
            reference_images_with_start_frame=True,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        body = build_request_body(self._model, request)
        logger.info("V2 视频生成开始: model=%s duration=%s", self._model, request.duration_seconds)
        logger.info("调用 %s 视频接口 payload=%s", self.name, _log_fields(self._model, request))

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            generation_id = await self._create_task(client, body)
            logger.info("V2 任务创建: generation_id=%s", generation_id)
            if request.task_id is not None:
                await persist_provider_job_id(request.task_id, generation_id, provider=PROVIDER_V2_VIDEO)
            return await self._poll_and_build(client, generation_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 V2 task：仅 poll + 下载（generation_id 已持久化）。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, body: dict) -> str:
        resp = await submit_post(
            lambda: client.post(f"{self._root}{_SUBMIT_PATH}", json=body, headers=self._headers()),
            provider=PROVIDER_V2_VIDEO,
        )
        payload = resp.json()
        generation_id = _first_str_by_paths(payload, _TASK_ID_PATHS)
        if not generation_id:
            raise RuntimeError(f"V2 创建任务返回体未能从已知路径提取 task_id: {payload}")
        return generation_id

    async def _poll_once(self, client: httpx.AsyncClient, generation_id: str) -> dict:
        resp = await client.get(
            f"{self._root}{_SUBMIT_PATH}",
            params={"generation_id": generation_id},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        generation_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        # resume 路径下 404 直接抛 ResumeExpiredError：should_retry_poll 把轮询 404 当作
        # "短暂未就绪"重试，对已过期的 resume 任务会一直重到 max_wait 超时、永不落
        # [resume_expired]，故在此一击转终态异常。非 resume 的 4xx 重新抛出，交 should_retry_poll
        # 按 status_code 分流（确定性 4xx 快速失败，404/429/5xx 重试）。
        async def _gated_poll() -> dict:
            try:
                return await self._poll_once(client, generation_id)
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=generation_id, provider=PROVIDER_V2_VIDEO) from exc
                raise

        final = await poll_with_retry(
            poll_fn=_gated_poll,
            is_done=lambda s: normalize_status(_first_str_by_paths(s, _STATUS_PATHS)) == "succeeded",
            is_failed=_extract_failure,
            poll_interval=_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
            retry_if=should_retry_poll,
            label="V2",
        )

        video_url = _first_str_by_paths(final, _VIDEO_URL_PATHS)
        if not video_url:
            raise RuntimeError(f"V2 任务完成但未能从已知路径提取视频 URL: {final}")
        await self._download_with_retry(video_url, request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_V2_VIDEO,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            seed=request.seed,
            task_id=generation_id,
        )

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_poll,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """对齐其它后端的下载重试策略（5 次、5/10/20/40 秒），与生成阶段独立。"""
        await download_video(video_url, output_path)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
