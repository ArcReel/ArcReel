"""KlingModelPathVideoBackend — 可灵新版 API 的视频调用通道（端点 ``kling-video-model-path``）。

新版 API 把模型名写在路径里，只收 API Key（Bearer），出处见 ``docs/api-docs/endpoints/kling-video-model-path.md``：

- 文生视频 ``POST /text-to-video/<模型名>``：顶层 ``prompt`` 加 ``settings.{resolution,duration,aspect_ratio}``。
- 图生视频 ``POST /image-to-video/<模型名>``：``contents[]`` 放提示词（``prompt``）、首帧（``first_frame``）与可选的
  尾帧（``last_frame``），``settings`` 只有 ``resolution`` 与 ``duration``，画面比例跟随首帧。
- 提交响应取 ``data.id``；``GET /tasks?task_ids=<id>`` 轮询到 ``status=succeeded``，取 ``outputs[type=video].url``，
  计费时长取 ``usage.output_duration``。

请求体没有音轨开关，成片固定有声（官方「音画同步与智能分镜无开关，默认开启。API 也同样默认开启」）。
本通道不接参考图、参考视频与主体（全能参考 ``/omni-video``）：带输入视频时官方按「输出×2」与「输入+输出」取大计费，
现有计量表达不了。各型号接受哪些输入、时长与分辨率记在模型条目上，与本通道的声明两侧求值（docs/adr/0104）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    download_resumable_video,
    recording_poll,
    resume_expiry_gate,
)
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.kling_backend_base import KlingBackendBase
from lib.backends.kling_shared import (
    extract_kling_model_path_video_url,
    image_to_base64,
    is_kling_model_path_terminal,
    kling_model_path_base_url,
    kling_model_path_failure_reason,
    kling_model_path_output_duration,
    kling_model_path_status,
    kling_model_path_task_id,
)
from lib.backends.providers import PROVIDER_KLING
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.billing.metering import Usage

logger = logging.getLogger(__name__)

ENDPOINT_KEY = "kling-video-model-path"
DEFAULT_MODEL = "kling-4.0"

TEXT_TO_VIDEO = "text-to-video"
IMAGE_TO_VIDEO = "image-to-video"
TASKS_PATH = "tasks"

# 持久化 job_id 的前缀：新版 API 只有一个按 id 查询的 /tasks，不需要像旧版那样把子路径编进去；
# 前缀用来认出旧版通道写下的 ``text2video:<id>:<audio>`` 一类 job_id，避免把它们发到新版查询端点。
_JOB_ID_PREFIX = "tasks:"

# 未指定分辨率时下发 720p：kling-4.0、kling-4.0-flash、kling-3.0-turbo 都接受，
# 也避免上游按各自默认值（kling-4.0 默认 1080p）出片，与计量的分辨率档位对不上。
_DEFAULT_RESOLUTION = "720p"

# 通道对任何型号都发得出文生视频、首帧与尾帧；参考图与参考音频没有对应的请求字段。
_CHANNEL_CAPABILITIES = VideoCapabilities(
    text_to_video=True,
    first_frame=True,
    last_frame=True,
    max_reference_images=0,
    audio_track=VideoAudioMode.ALWAYS_ON,
)


def _has_image(value: object) -> bool:
    return isinstance(value, (str, Path)) and bool(str(value))


def _route_for(request: VideoGenerationRequest) -> str:
    return IMAGE_TO_VIDEO if _has_image(request.start_image) else TEXT_TO_VIDEO


def _resolution_for(request: VideoGenerationRequest) -> str:
    return (request.resolution or _DEFAULT_RESOLUTION).lower()


def encode_job_id(task_id: str) -> str:
    return f"{_JOB_ID_PREFIX}{task_id}"


def decode_job_id(job_id: str) -> str:
    """从持久化 job_id 复原新版 API 的任务 id；不是本通道写下的 job_id 时拒绝，不拿它去查询。"""
    task_id = job_id.removeprefix(_JOB_ID_PREFIX)
    if task_id == job_id or not task_id:
        raise ValueError(f"可灵新版 API 认不出这个任务 ID，它不是由本调用端点提交的: {job_id}")
    return task_id


class KlingModelPathVideoBackend(EntryBoundCapabilities, KlingBackendBase, ProviderJobIdPersistenceMixin):
    """可灵新版 API 视频后端（Bearer API Key，异步轮询）。"""

    _media_label = "视频"
    endpoint_key = ENDPOINT_KEY

    def __init__(
        self,
        *,
        auth_mode: str = "bearer",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        if auth_mode != "bearer":
            # 官方「接口鉴权」：Access Key / Secret Key 只适用于旧版设计标准的 API，新版只收 API Key。
            raise ValueError("可灵新版 API 只支持 API Key 鉴权，请到系统配置页填写可灵 Kling 的 API Key")
        super().__init__(
            auth_mode=auth_mode,
            access_key=access_key,
            secret_key=secret_key,
            api_key=api_key,
            model=model or DEFAULT_MODEL,
            base_url=base_url,
            http_timeout=http_timeout,
            transport_layer=transport_layer,
        )
        self._base_url = kling_model_path_base_url(base_url)

    # ── 能力与计量 ──────────────────────────────────────────────────────

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """通道事实：文生、首帧、尾帧可发，参考图与参考音频不收，成片固定有声。

        尾帧只有 kling-4.0 接受（kling-4.0-flash 与 kling-3.0-turbo 仅首帧），这是模型事实，由条目收窄。
        """
        return _CHANNEL_CAPABILITIES

    @classmethod
    def effective_generate_audio_for_model(cls, model: str, entry: CatalogEntry | None = None) -> bool:
        return True

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    @classmethod
    def metered_usage(
        cls,
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """这次请求的计量（见 :mod:`lib.backends.video_backends.metering`）。

        分辨率取实际下发的 ``settings.resolution``（未指定时下发 720p），成片固定有声；结算的输出秒数取
        ``usage.output_duration``（官方计费时长，只有 kling-4.0 系列回报），缺失时取下发的时长。
        """
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=_resolution_for(request),
            audio=True,
            reference_images=0,
        )

    # ── 响应解读 ────────────────────────────────────────────────────────

    def _task_id_from(self, submit_payload: dict) -> str:
        return kling_model_path_task_id(submit_payload)

    def _task_status(self, payload: dict) -> str | None:
        return kling_model_path_status(payload)

    def _is_terminal(self, payload: dict) -> bool:
        return is_kling_model_path_terminal(payload)

    def _failure_reason(self, payload: dict) -> str | None:
        return kling_model_path_failure_reason(payload)

    # ── 请求构造 ────────────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        """返回 (提交路径, 请求体)；发不出的输入在付费前拒绝，不静默丢弃。"""
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        if request.reference_images and any(_has_image(p) for p in request.reference_images):
            raise VideoCapabilityError("video_reference_images_unsupported", model=self._model)
        # 请求体没有音轨开关；静音请求只在能力被用户覆盖放宽时走到这里。
        if not request.generate_audio:
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._model)

        caps = request.video_capabilities or self.video_capabilities
        settings: dict = {"resolution": _resolution_for(request), "duration": int(request.duration_seconds)}
        route = _route_for(request)
        model_path = f"{route}/{self._model}"

        if route == TEXT_TO_VIDEO:
            if not caps.text_to_video:
                raise VideoCapabilityError("video_capability_missing_t2v", provider=self.name, model=self._model)
            if _has_image(request.end_image):
                raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)
            settings["aspect_ratio"] = request.aspect_ratio
            return model_path, {"prompt": request.prompt, "settings": settings}

        contents: list[dict] = []
        if request.prompt:
            contents.append({"type": "prompt", "text": request.prompt})
        contents.append({"type": "first_frame", "url": self._encode_frame(Path(cast(Path, request.start_image)))})
        if _has_image(request.end_image):
            if not caps.last_frame:
                raise VideoCapabilityError("video_last_frame_unsupported", provider=self.name, model=self._model)
            contents.append({"type": "last_frame", "url": self._encode_frame(Path(cast(Path, request.end_image)))})
        # 图生视频没有 aspect_ratio 字段，画面比例跟随首帧。
        return model_path, {"contents": contents, "settings": settings}

    def _encode_frame(self, path: Path) -> str:
        # 官方帧图字段接受 URL 或 base64；声明了帧图却不可读即中止，不静默退化成文生视频。
        if not path.is_file():
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name)
        try:
            return image_to_base64(path)
        except OSError as exc:
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name) from exc

    @staticmethod
    def _safe_log_view(path: str, payload: dict) -> dict:
        """预脱敏标量视图：帧图 base64 与提示词不展开。"""
        raw_settings = payload.get("settings")
        settings: dict = raw_settings if isinstance(raw_settings, dict) else {}
        raw_contents = payload.get("contents")
        contents: list = raw_contents if isinstance(raw_contents, list) else []
        frame_types = [c.get("type") for c in contents if isinstance(c, dict) and c.get("type") != "prompt"]
        prompt = payload.get("prompt")
        if not isinstance(prompt, str):
            prompt = next((c.get("text") for c in contents if isinstance(c, dict) and c.get("type") == "prompt"), "")
        return {
            "endpoint": path,
            "resolution": settings.get("resolution"),
            "duration": settings.get("duration"),
            "aspect_ratio": settings.get("aspect_ratio", ""),
            "frames": ",".join(str(t) for t in frame_types),
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    # ── generate / resume ───────────────────────────────────────────────

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        path, payload = await asyncio.to_thread(self._build_payload, request)
        logger.info("调用 Kling 新版视频 API payload=%s", self._safe_log_view(path, payload))
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._submit_task(client, path, payload, request)
            logger.info("Kling 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            # 一并写回本次实际请求的域名：续跑据此回放，否则在途改了 base_url 会按新域名去查旧任务。
            await self._persist_provider_job_id(
                request, encode_job_id(task_id), provider=PROVIDER_KLING, endpoint=self._base_url
            )
            return await self._poll_and_build(client, task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已提交的任务：只轮询、取件，不重新提交（ADR 0007）。"""
        task_id = decode_job_id(job_id)
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            return await self._poll_and_build(client, task_id, request, resume_job_id=job_id)

    async def _query_task(self, client: httpx.AsyncClient, task_id: str, *, base_url: str) -> dict:
        resp = await client.get(f"{base_url}/{TASKS_PATH}", params={"task_ids": task_id}, headers=self._headers())
        raise_for_status_redacted(resp)
        return resp.json()

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        resume_job_id: str | None = None,
    ) -> VideoGenerationResult:
        base_url = (request.submitted_base_url or self._base_url).rstrip("/")
        gated_poll = resume_expiry_gate(
            recording_poll(lambda: self._query_task(client, task_id, base_url=base_url), request),
            resume_job_id=resume_job_id,
            provider=PROVIDER_KLING,
        )
        final = await self._poll_until_terminal(gated_poll, max_wait=request.poll_timeout_seconds)

        download_url = extract_kling_model_path_video_url(final)
        await download_resumable_video(download_url, request.output_path, label="Kling")
        logger.info("Kling 视频下载完成: %s", request.output_path)

        billed_seconds = kling_model_path_output_duration(final)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            duration_seconds=billed_seconds if billed_seconds is not None else request.duration_seconds,
            video_uri=download_url,
            task_id=task_id,
            generate_audio=True,
        )
