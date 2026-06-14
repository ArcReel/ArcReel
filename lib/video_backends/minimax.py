"""MiniMaxVideoBackend — MiniMax（海螺）Hailuo 视频生成后端（异步两步取 URL）。

走原生视频端点，轮询而非 callback：submit POST /video_generation 取 task_id →
轮询 GET /query/video_generation?task_id= 至 status=Success 取 file_id →
GET /files/retrieve?file_id= 取 download_url → 下载本地。覆盖 MiniMax-Hailuo-2.3
（t2v+i2v）、MiniMax-Hailuo-2.3-Fast（仅 i2v，约半价）与 S2V-01（单脸参考生视频 R2V）。

能力约束：Hailuo resolution ∈ {768P, 1080P}，1080P 仅 6s（10s 仅 768P）；越界抛
VideoCapabilityError，Fast 仅图生视频、无首帧的文生视频请求被能力拒绝。S2V-01 走单脸
subject_reference（reference_images[0]→{"type":"character","image":[...]}），固定输出、
不传 resolution/duration，无参考图即 fail-loud。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.logging_utils import format_kwargs_for_log
from lib.minimax_shared import (
    MINIMAX_VIDEO_POLL_INTERVAL_SECONDS,
    extract_minimax_download_url,
    extract_minimax_file_id,
    extract_minimax_video_task_id,
    image_to_data_uri,
    is_minimax_video_terminal,
    minimax_headers,
    minimax_video_base_url,
    minimax_video_failure_reason,
    resolve_minimax_api_key,
)
from lib.providers import PROVIDER_MINIMAX
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
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

DEFAULT_MODEL = "MiniMax-Hailuo-2.3"

_HAILUO = "MiniMax-Hailuo-2.3"
_HAILUO_FAST = "MiniMax-Hailuo-2.3-Fast"
# S2V-01：单张人脸驱动的角色一致性参考生视频（R2V），走 subject_reference 单脸字段，
# 不接受 first_frame_image / resolution / duration（固定输出）。
_S2V = "S2V-01"

_SUBMIT_ENDPOINT = "/video_generation"
_QUERY_ENDPOINT = "/query/video_generation"
_RETRIEVE_ENDPOINT = "/files/retrieve"

_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 60.0

_TV = VideoCapability.TEXT_TO_VIDEO
_IV = VideoCapability.IMAGE_TO_VIDEO

# 按 model id 派发能力：Hailuo 2.3 文+图生视频；2.3-Fast 仅图生视频；S2V-01 既非 t2v 也非
# i2v（subject_reference 驱动，能力经 VideoCapabilities.reference_images 表达），故为空集。
_MODEL_CAPABILITIES: dict[str, set[VideoCapability]] = {
    _HAILUO: {_TV, _IV},
    _HAILUO_FAST: {_IV},
    _S2V: set(),
}
# 未知 model（代理中转自定义命名）按通用文+图生视频处理。
_DEFAULT_CAPABILITIES: set[VideoCapability] = {_TV, _IV}

# (分辨率小写 → 允许的时长集合)：1080P 仅 6s，768P 支持 6s/10s（两代 Hailuo 同此矩阵）。
_RESOLUTION_DURATIONS: dict[str, set[int]] = {"768p": {6, 10}, "1080p": {6}}

# 进日志的安全标量白名单；first_frame_image / subject_reference（base64）一律不入日志。
_SAFE_LOG_KEYS: frozenset[str] = frozenset({"model", "resolution", "duration"})


def _capabilities_for_model(model: str | None) -> set[VideoCapability]:
    normalized = (model or "").strip()
    return _MODEL_CAPABILITIES.get(normalized, _DEFAULT_CAPABILITIES)


def _safe_body_for_log(body: dict) -> dict:
    """安全日志视图：白名单标量 + prompt 截断；first_frame_image 仅标记是否存在。"""
    safe = {k: v for k, v in body.items() if k in _SAFE_LOG_KEYS}
    if "prompt" in body:
        prompt = body["prompt"] or ""
        safe["prompt"] = prompt[:120] + ("…" if len(prompt) > 120 else "")
    if body.get("first_frame_image"):
        safe["first_frame_image"] = "<data_uri>"
    if body.get("subject_reference"):
        safe["subject_reference"] = "<character_ref>"
    return safe


class MiniMaxVideoBackend:
    """MiniMax 海螺视频后端（异步两步取 URL，轮询）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_video_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._capabilities = _capabilities_for_model(self._model)

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """海螺图生视频走 first_frame_image 首帧；S2V-01 走 subject_reference 单脸参考生视频。

        S2V-01 仅接受单张人脸参考、不接受首帧图，故 first_frame=False + reference_images=True
        + max_reference_images=1；reference_images_with_start_frame 维持 False（参考与首帧不叠加）。
        Hailuo 系列首批不建模尾帧/参考图。
        """
        if model == _S2V:
            return VideoCapabilities(first_frame=False, reference_images=True, max_reference_images=1)
        return VideoCapabilities(first_frame=True)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

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
            logger.info("MiniMax 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            if request.task_id is not None:
                await persist_provider_job_id(request.task_id, task_id, provider=PROVIDER_MINIMAX)
            return await self._poll_and_build(client, task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 MiniMax task：仅轮询 + 取回 + 下载，不重新提交（ADR 0007）。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        # S2V-01 走单脸 subject_reference 路径：不取首帧、不传 resolution/duration（固定输出）。
        if self._model == _S2V:
            return self._build_s2v_payload(request)

        resolution = (request.resolution or "768p").lower()
        duration = request.duration_seconds
        has_start_image = isinstance(request.start_image, (str, Path)) and str(request.start_image)

        # 无首帧 = 文生视频意图；模型不支持 t2v（如 Fast）即拒绝。
        if not has_start_image and _TV not in self._capabilities:
            raise VideoCapabilityError("video_capability_missing_t2v", provider=self.name, model=self._model)

        allowed_durations = _RESOLUTION_DURATIONS.get(resolution, set())
        if duration not in allowed_durations:
            supported = ", ".join(f"{d}s" for d in sorted(allowed_durations)) or "无"
            raise VideoCapabilityError(
                "video_resolution_duration_unsupported",
                model=self._model,
                resolution=resolution.upper(),
                duration=duration,
                supported=supported,
            )

        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "duration": duration,
            "resolution": resolution.upper(),
        }
        if has_start_image:
            p = Path(request.start_image)  # type: ignore[arg-type]
            # fail-loud：声明了首帧图却缺失/不可读即中止，不静默退化为文生视频。
            if not p.is_file():
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=p.name)
            try:
                payload["first_frame_image"] = image_to_data_uri(p)
            except OSError as exc:
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=p.name) from exc
        return payload

    def _build_s2v_payload(self, request: VideoGenerationRequest) -> dict:
        """S2V-01：把 reference_images[0] 映射成单脸 subject_reference。

        编排层已按 registry max_reference_images=1 裁剪，此处防御性仅取首张人脸图。
        fail-loud：未提供参考图 → required；声明的参考图缺失/不可读 → unreadable，
        不静默退化为无参考生成（会产出错误结果且照常计费）。
        """
        provided = [r for r in (request.reference_images or []) if r]
        if not provided:
            raise VideoCapabilityError("video_reference_images_required", model=self._model)
        face = Path(provided[0])
        if not face.is_file():
            raise VideoCapabilityError("video_reference_images_unreadable", model=self._model, names=face.name)
        try:
            data_uri = image_to_data_uri(face)
        except OSError as exc:
            raise VideoCapabilityError("video_reference_images_unreadable", model=self._model, names=face.name) from exc
        return {
            "model": self._model,
            "prompt": request.prompt,
            "subject_reference": [{"type": "character", "image": [data_uri]}],
        }

    # ── HTTP submit / poll / retrieve / download ────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        # 非幂等的「建任务 + 计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError
        # 终态失败，避免重试重复建任务 + 重复计费；>=400 抛 HTTPStatusError 交 should_retry_submit
        # 按状态码分流（4xx fail-fast、5xx/429 重试）。
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}{_SUBMIT_ENDPOINT}",
                json=payload,
                headers=minimax_headers(self._api_key),
            ),
            provider=PROVIDER_MINIMAX,
        )
        return extract_minimax_video_task_id(resp.json())

    async def _poll_query(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}{_QUERY_ENDPOINT}",
            params={"task_id": task_id},
            headers=minimax_headers(self._api_key),
        )
        resp.raise_for_status()
        return resp.json()

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_poll,
    )
    async def _retrieve_download_url(self, client: httpx.AsyncClient, file_id: str) -> str:
        # 取回是幂等 GET（不计费），瞬态错误重试无副作用。
        resp = await client.get(
            f"{self._base_url}{_RETRIEVE_ENDPOINT}",
            params={"file_id": file_id},
            headers=minimax_headers(self._api_key),
        )
        resp.raise_for_status()
        return extract_minimax_download_url(resp.json())

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
    ) -> VideoGenerationResult:
        final = await poll_with_retry(
            poll_fn=lambda: self._poll_query(client, task_id),
            is_done=is_minimax_video_terminal,
            is_failed=minimax_video_failure_reason,
            poll_interval=MINIMAX_VIDEO_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
            retry_if=should_retry_poll,
            label="MiniMax",
            on_progress=lambda v, elapsed: logger.info(
                "MiniMax 视频生成中... status=%s elapsed=%ds", v.get("status"), int(elapsed)
            ),
        )

        file_id = extract_minimax_file_id(final)
        download_url = await self._retrieve_download_url(client, file_id)
        await self._download_with_retry(download_url, request.output_path)
        logger.info("MiniMax 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_MINIMAX,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=download_url,
            task_id=task_id,
            generate_audio=request.generate_audio,
        )

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_with_retry(download_url: str, output_path: Path) -> None:
        await download_video(download_url, output_path)

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
