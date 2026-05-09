"""ViduVideoBackend — Vidu 视频生成后端。

按 request 字段分派到 4 个 Vidu API 端点：
- 仅 prompt              → ``/text2video``
- ``start_image``        → ``/img2video``
- ``start_image + end``  → ``/start-end2video``
- ``reference_images``   → ``/reference2video``
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_VIDU
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
)
from lib.vidu_shared import (
    VIDU_RETRYABLE_ERRORS,
    assert_vidu_body_size,
    create_vidu_client,
    extract_vidu_url,
    fetch_vidu_task,
    image_to_data_uri,
    is_vidu_done,
    safe_body_for_log,
    vidu_failure_reason,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "viduq3-turbo"
_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 90.0
_MAX_REFERENCE_IMAGES = 7
_PROMPT_MAX_TEXT2VIDEO = 5000
_PROMPT_MAX_REFERENCE2VIDEO = 2000

# q3 系列模型默认开启音视频直出，q2/q1/2.0 默认静音；audio 仅传 q3 时才生效
_Q3_MODELS = frozenset(
    {
        "viduq3-pro",
        "viduq3-turbo",
        "viduq3-pro-fast",
        "viduq3",
        "viduq3-mix",
    }
)

# 按 (model, endpoint) 列出合法 duration —— 文档逐端点列出，差异很大
_DURATION_RULES: dict[tuple[str, str], list[int]] = {
    # /text2video
    ("viduq3-pro", "/text2video"): list(range(1, 17)),
    ("viduq3-turbo", "/text2video"): list(range(1, 17)),
    ("viduq2", "/text2video"): list(range(1, 11)),
    ("viduq1", "/text2video"): [5],
    # /img2video
    ("viduq3-pro", "/img2video"): list(range(1, 17)),
    ("viduq3-turbo", "/img2video"): list(range(1, 17)),
    ("viduq3-pro-fast", "/img2video"): list(range(1, 17)),
    ("viduq2-pro-fast", "/img2video"): list(range(1, 9)),
    ("viduq2-pro", "/img2video"): list(range(1, 9)),
    ("viduq2-turbo", "/img2video"): list(range(1, 9)),
    ("viduq1", "/img2video"): [5],
    ("viduq1-classic", "/img2video"): [5],
    ("vidu2.0", "/img2video"): [4, 8],
    # /start-end2video
    ("viduq3-pro", "/start-end2video"): list(range(1, 17)),
    ("viduq3-turbo", "/start-end2video"): list(range(1, 17)),
    ("viduq2-pro-fast", "/start-end2video"): list(range(1, 9)),
    ("viduq2-pro", "/start-end2video"): list(range(1, 9)),
    ("viduq2-turbo", "/start-end2video"): list(range(1, 9)),
    ("viduq1", "/start-end2video"): [5],
    ("viduq1-classic", "/start-end2video"): [5],
    ("vidu2.0", "/start-end2video"): [4, 8],
    # /reference2video (非主体)
    ("viduq3-turbo", "/reference2video"): list(range(3, 17)),
    ("viduq3", "/reference2video"): list(range(3, 17)),
    ("viduq3-mix", "/reference2video"): list(range(3, 17)),
    ("viduq2-pro", "/reference2video"): list(range(0, 11)),  # 0=自动
    ("viduq2", "/reference2video"): list(range(1, 11)),
    ("viduq1", "/reference2video"): [5],
    ("vidu2.0", "/reference2video"): [4],
}

# 端点支持的模型集合 —— 用于早期失败提示（依据 Vidu 官方文档 docs/vidu-docs/*）
_ENDPOINT_MODELS: dict[str, frozenset[str]] = {
    "/text2video": frozenset({"viduq3-turbo", "viduq3-pro", "viduq2", "viduq1"}),
    "/img2video": frozenset(
        {
            "viduq3-turbo",
            "viduq3-pro",
            "viduq3-pro-fast",
            "viduq2-pro-fast",
            "viduq2-pro",
            "viduq2-turbo",
            "viduq1",
            "viduq1-classic",
            "vidu2.0",
        }
    ),
    "/start-end2video": frozenset(
        {
            "viduq3-turbo",
            "viduq3-pro",
            "viduq2-pro-fast",
            "viduq2-pro",
            "viduq2-turbo",
            "viduq1",
            "viduq1-classic",
            "vidu2.0",
        }
    ),
    "/reference2video": frozenset(
        {"viduq3-mix", "viduq3-turbo", "viduq3", "viduq2-pro", "viduq2", "viduq1", "vidu2.0"}
    ),
}

# 端点是否接受 aspect_ratio
_ENDPOINTS_WITH_ASPECT_RATIO = frozenset({"/text2video", "/reference2video"})

# 按 model 列分辨率白名单（按 /img2video & /reference2video 的并集，最宽松）
_RESOLUTION_WHITELIST: dict[str, list[str]] = {
    "viduq3-pro": ["540p", "720p", "1080p"],
    "viduq3-pro-fast": ["540p", "720p", "1080p"],
    "viduq3-turbo": ["540p", "720p", "1080p"],
    "viduq3": ["540p", "720p", "1080p"],
    "viduq3-mix": ["720p", "1080p"],
    "viduq2": ["540p", "720p", "1080p"],
    "viduq2-pro": ["540p", "720p", "1080p"],
    "viduq2-pro-fast": ["720p", "1080p"],
    "viduq2-turbo": ["540p", "720p", "1080p"],
    "viduq1": ["1080p"],
    "viduq1-classic": ["1080p"],
    "vidu2.0": ["360p", "720p", "1080p"],
}
_DEFAULT_RESOLUTION = "720p"


class ViduVideoBackend:
    """Vidu 视频生成后端，按 request 字段分派到不同端点。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model or DEFAULT_MODEL

        caps: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.SEED_CONTROL,
        }
        if self._model in _Q3_MODELS:
            caps.add(VideoCapability.GENERATE_AUDIO)
        self._capabilities = caps

    @property
    def name(self) -> str:
        return PROVIDER_VIDU

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(
            first_frame=True,
            last_frame=True,
            reference_images=True,
            max_reference_images=_MAX_REFERENCE_IMAGES,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        endpoint, body = self._build_request(request)
        # _build_request 已把 duration 归一化到 body["duration"]，统一使用以避免 None 崩溃及结果不一致。
        coerced_duration = int(body["duration"])
        max_wait = max(_MIN_POLL_TIMEOUT_SECONDS, float(coerced_duration) * _POLL_TIMEOUT_PER_SECOND)

        async with create_vidu_client(api_key=self._api_key, base_url=self._base_url) as client:
            payload = await self._create_task(client, endpoint, body)
            task_id = payload["task_id"]
            credits = payload.get("credits")
            logger.info(
                "Vidu 视频任务已创建: endpoint=%s task_id=%s credits=%s model=%s",
                endpoint,
                task_id,
                credits,
                self._model,
            )

            final = await poll_with_retry(
                poll_fn=lambda: fetch_vidu_task(client, task_id),
                is_done=is_vidu_done,
                is_failed=vidu_failure_reason,
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=max_wait,
                retryable_errors=VIDU_RETRYABLE_ERRORS,
                label="Vidu",
                on_progress=lambda v, elapsed: logger.info(
                    "Vidu 视频生成中... state=%s elapsed=%ds", v.get("state"), int(elapsed)
                ),
            )
            url = extract_vidu_url(final)
            # credits=0 是合法值，不能用 `or` 合并；显式判 None 兜底为顶层 credits。
            final_credits = final.get("credits")
            actual_credits = final_credits if final_credits is not None else credits

        await self._download_video(url, request.output_path)
        logger.info("Vidu 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_VIDU,
            model=self._model,
            duration_seconds=coerced_duration,
            seed=request.seed,
            usage_tokens=int(actual_credits) if actual_credits is not None else None,
            task_id=task_id,
            generate_audio=request.generate_audio if self._model in _Q3_MODELS else False,
        )

    # ── request building ────────────────────────────────────────────────

    def _build_request(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        endpoint = self._select_endpoint(request)

        # 1) 校验模型 × 端点（提前失败比 400 含义更清晰）
        endpoint_models = _ENDPOINT_MODELS.get(endpoint, frozenset())
        if endpoint_models and self._model not in endpoint_models:
            raise RuntimeError(f"Vidu 模型 {self._model} 不支持 {endpoint} 端点；该端点支持: {sorted(endpoint_models)}")

        # 2) duration 在合法集合内取最近值
        duration = _coerce_duration(self._model, endpoint, request.duration_seconds)

        # 3) prompt 截断（reference2video 上限 2000，其他 5000）
        prompt = request.prompt or ""
        prompt_max = _PROMPT_MAX_REFERENCE2VIDEO if endpoint == "/reference2video" else _PROMPT_MAX_TEXT2VIDEO
        if len(prompt) > prompt_max:
            logger.warning("Vidu prompt 长度 %d 超限 %d，截断", len(prompt), prompt_max)
            prompt = prompt[:prompt_max]

        body: dict = {
            "model": self._model,
            "prompt": prompt,
            "duration": duration,
        }

        # 4) resolution 白名单兜底
        resolution = _coerce_resolution(self._model, request.resolution)
        if resolution:
            body["resolution"] = resolution

        # 5) seed
        if request.seed is not None:
            body["seed"] = request.seed

        # 6) audio 仅 q3 系列接受
        if self._model in _Q3_MODELS:
            body["audio"] = bool(request.generate_audio)

        # 7) aspect_ratio 仅 text2video / reference2video 接受
        if endpoint in _ENDPOINTS_WITH_ASPECT_RATIO and request.aspect_ratio:
            body["aspect_ratio"] = request.aspect_ratio

        # 8) images 按端点形态填充
        if endpoint == "/reference2video":
            refs = [Path(p) for p in (request.reference_images or []) if p]
            if len(refs) > _MAX_REFERENCE_IMAGES:
                logger.warning("Vidu 参考图数量 %d 超过上限 %d，截断", len(refs), _MAX_REFERENCE_IMAGES)
                refs = refs[:_MAX_REFERENCE_IMAGES]
            body["images"] = [image_to_data_uri(p) for p in refs]
        elif endpoint == "/start-end2video":
            body["images"] = [
                image_to_data_uri(Path(request.start_image)),
                image_to_data_uri(Path(request.end_image)),
            ]
        elif endpoint == "/img2video":
            body["images"] = [image_to_data_uri(Path(request.start_image))]
        # /text2video 不带 images

        return endpoint, body

    def _select_endpoint(self, request: VideoGenerationRequest) -> str:
        refs = [p for p in (request.reference_images or []) if p]
        # 若用户显式传了 start/end_image 但文件不存在，应直接失败而不是静默降级到 text2video。
        if request.start_image is not None and not Path(request.start_image).exists():
            raise FileNotFoundError(f"start_image 文件不存在: {request.start_image}")
        if request.end_image is not None and not Path(request.end_image).exists():
            raise FileNotFoundError(f"end_image 文件不存在: {request.end_image}")
        has_start = request.start_image is not None
        has_end = request.end_image is not None
        if refs:
            return "/reference2video"
        if has_start and has_end:
            return "/start-end2video"
        if has_start:
            return "/img2video"
        return "/text2video"

    # ── HTTP wrappers ───────────────────────────────────────────────────

    @with_retry_async(retryable_errors=VIDU_RETRYABLE_ERRORS)
    async def _create_task(self, client, endpoint: str, body: dict) -> dict:
        assert_vidu_body_size(body)
        logger.info(
            "调用 Vidu 视频 API endpoint=%s kwargs=%s",
            endpoint,
            format_kwargs_for_log(safe_body_for_log(body)),
        )
        resp = await client.post(endpoint, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"Vidu 视频接口 {endpoint} 返回 {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        if not data.get("task_id"):
            raise RuntimeError(f"Vidu 视频任务创建响应缺少 task_id: {data}")
        return data

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retryable_errors=VIDU_RETRYABLE_ERRORS,
    )
    async def _download_video(self, url: str, output_path: Path) -> None:
        await download_video(url, output_path)


def _coerce_duration(model: str, endpoint: str, requested: int | None) -> int:
    """按 (model, endpoint) 的合法集合，把请求时长校正到最近值。"""
    allowed = _DURATION_RULES.get((model, endpoint))
    if not allowed:
        # 表里无项时不强校（让接口按默认处理），但若给了值就透传
        return int(requested) if requested else 5
    if requested is None:
        return allowed[0] if 5 not in allowed else 5
    if requested in allowed:
        return int(requested)
    nearest = min(allowed, key=lambda v: abs(v - requested))
    logger.warning(
        "Vidu duration %s 不在 model=%s endpoint=%s 合法集合 %s，使用最近值 %s",
        requested,
        model,
        endpoint,
        allowed,
        nearest,
    )
    return int(nearest)


def _coerce_resolution(model: str, requested: str | None) -> str | None:
    """白名单内透传，否则降级到模型默认 720p（viduq1 默认 1080p）。"""
    whitelist = _RESOLUTION_WHITELIST.get(model)
    if not whitelist:
        return requested
    if requested and requested in whitelist:
        return requested
    if requested:
        logger.warning(
            "Vidu resolution %s 不在 model=%s 白名单 %s，降级",
            requested,
            model,
            whitelist,
        )
    fallback = _DEFAULT_RESOLUTION if _DEFAULT_RESOLUTION in whitelist else whitelist[0]
    return fallback
