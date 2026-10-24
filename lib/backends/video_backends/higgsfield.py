"""HiggsfieldVideoBackend — Higgsfield 视频生成后端。

Higgsfield 是聚合多家模型的统一 API，每个模型一组固定端点，按 API 模型名与请求字段分派：

- ``kling-video/v3.0``（可灵 3.0）：``/kling-video/v3.0/{std|pro|4k}/text-to-video`` 与 ``.../image-to-video``，
  档位由分辨率决定（720p→std、1080p→pro、4k→4k）；尾帧走 ``last_image_url``；不收参考图。
- ``kling-video/o3``（可灵 O3）：首帧（可带尾帧）走 ``/kling-video/o3/first-last-frame``，参考图走
  ``/kling-video/o3/image-reference``；档位经 ``mode`` 字段；没有纯文生视频端点。
- ``bytedance/seedance-2.5``（Seedance 2.5）：``/bytedance/seedance-2.5/{text-to-video|image-to-video|reference-to-video}``。

素材字段只收公开 URL，提交前先经文件上传接口换成 URL。提交后按 ``request_id`` 轮询
``/requests/{id}/status``；``request_id`` 落库后重启可接续（``resume_video``）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

import httpx

from arcreel_market_core.video_backend_contract import (
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
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
from lib.backends.higgsfield_shared import (
    HIGGSFIELD_BASE_URL,
    create_higgsfield_client,
    extract_request_id,
    extract_video_url,
    fetch_request_status,
    is_request_done,
    new_idempotency_key,
    request_failure_reason,
    safe_body_for_log,
    upload_image,
)
from lib.backends.providers import PROVIDER_HIGGSFIELD
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import UNRECOGNIZED_MODEL_CAPABILITIES
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

MODEL_KLING_V3 = "kling-video/v3.0"
MODEL_KLING_O3 = "kling-video/o3"
MODEL_SEEDANCE_2_5 = "bytedance/seedance-2.5"
DEFAULT_MODEL = MODEL_SEEDANCE_2_5

_KLING_PROMPT_MAX = 2500
_KLING_ASPECT_RATIOS = frozenset({"16:9", "9:16", "1:1"})
_SEEDANCE_ASPECT_RATIOS = frozenset({"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"})
# 可灵 O3 的 image-reference 文档没有给参考图上限；按可灵官方多图参考的 4 张口径声明。
_KLING_O3_MAX_REFERENCE_IMAGES = 4
_SEEDANCE_MAX_REFERENCE_IMAGES = 30
# 可灵在 Higgsfield 上按端点 / mode 分档，分辨率档位与之一一对应；未指定分辨率按 std 下发。
_KLING_TIER_BY_RESOLUTION: dict[str, str] = {"720p": "std", "1080p": "pro", "4k": "4k"}
_DEFAULT_RESOLUTION = "720p"

_CAPABILITIES: dict[str, VideoCapabilities] = {
    MODEL_KLING_V3: VideoCapabilities(
        text_to_video=True,
        first_frame=True,
        last_frame=True,
        max_reference_images=0,
        max_prompt_chars=_KLING_PROMPT_MAX,
        audio_track=VideoAudioMode.CONTROLLABLE,
    ),
    MODEL_KLING_O3: VideoCapabilities(
        text_to_video=False,
        first_frame=True,
        last_frame=True,
        max_reference_images=_KLING_O3_MAX_REFERENCE_IMAGES,
        max_prompt_chars=_KLING_PROMPT_MAX,
        audio_track=VideoAudioMode.CONTROLLABLE,
    ),
    MODEL_SEEDANCE_2_5: VideoCapabilities(
        text_to_video=True,
        first_frame=True,
        last_frame=True,
        max_reference_images=_SEEDANCE_MAX_REFERENCE_IMAGES,
        audio_track=VideoAudioMode.CONTROLLABLE,
    ),
}


def _known_model(model: str) -> str | None:
    return model if model in _CAPABILITIES else None


class HiggsfieldVideoBackend(ProviderJobIdPersistenceMixin):
    """Higgsfield 视频生成后端，按 API 模型名与请求字段分派端点。"""

    def __init__(
        self,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        self._access_key = access_key
        self._secret_key = secret_key
        self._base_url = (base_url or HIGGSFIELD_BASE_URL).rstrip("/")
        self._transport_layer = transport_layer
        self._model = model or DEFAULT_MODEL
        self._known_model = _known_model(self._model)

    @property
    def name(self) -> str:
        return PROVIDER_HIGGSFIELD

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按模型名纯计算 caps。认不得的型号取统一回落；本通道对认不得的模型按 Seedance 形态下发
        ``generate_audio`` 开关，故音轨声明为可控。"""
        known = _known_model(model)
        if known is None:
            return replace(UNRECOGNIZED_MODEL_CAPABILITIES, audio_track=VideoAudioMode.CONTROLLABLE)
        return _CAPABILITIES[known]

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    @staticmethod
    def metered_usage(
        model: str, request: VideoGenerationRequest, result: VideoGenerationResult | None = None
    ) -> Usage:
        """这次请求的计量（见 :mod:`lib.backends.video_backends.metering`）。

        分辨率取请求档位，未指定按 720p（可灵 std / Seedance 默认档）；三个模型的请求体都带音轨开关。
        """
        refs = _reference_images(request)
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution or _DEFAULT_RESOLUTION,
            audio=bool(request.generate_audio),
            reference_images=len(refs),
            reference_to_video=bool(refs),
        )

    # ── generate / resume ───────────────────────────────────────────────

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 构造里有首尾帧文件存在性检查（阻塞 I/O），整段卸载到线程。
        endpoint, body_without_media = await asyncio.to_thread(self._build_request, request)
        async with self._client() as client:
            body = {**body_without_media, **(await self._upload_media(client, endpoint, request))}
            payload = await self._create_request(client, endpoint, body, new_idempotency_key(), request)
            request_id = extract_request_id(payload)
            logger.info(
                "Higgsfield 视频请求已创建: endpoint=%s request_id=%s model=%s", endpoint, request_id, self._model
            )
            # 落库 + 关闭重提交窗口；提交域名随落库，续跑按它回放。
            await self._persist_provider_job_id(
                request, request_id, provider=PROVIDER_HIGGSFIELD, endpoint=self._base_url
            )
            return await self._poll_and_build(client, request_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已提交的请求：仅轮询 + 下载，不重新提交（ADR 0007）。"""
        async with self._client(base_url=request.submitted_base_url) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    def _client(self, *, base_url: str | None = None) -> httpx.AsyncClient:
        return create_higgsfield_client(
            access_key=self._access_key,
            secret_key=self._secret_key,
            base_url=base_url or self._base_url,
            transport_layer=self._transport_layer,
        )

    async def _poll_and_build(
        self, client: httpx.AsyncClient, request_id: str, request: VideoGenerationRequest, *, is_resume: bool
    ) -> VideoGenerationResult:
        gated_poll = resume_expiry_gate(
            recording_poll(lambda: fetch_request_status(client, request_id), request),
            resume_job_id=request_id if is_resume else None,
            provider=PROVIDER_HIGGSFIELD,
        )
        final = await poll_with_retry(
            poll_fn=gated_poll,
            is_done=is_request_done,
            is_failed=request_failure_reason,
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="Higgsfield",
            on_progress=lambda v, elapsed: logger.info(
                "Higgsfield 视频生成中... status=%s elapsed=%ds", v.get("status"), int(elapsed)
            ),
        )
        url = extract_video_url(final)
        await download_resumable_video(url, request.output_path, label="Higgsfield")
        logger.info("Higgsfield 视频下载完成: %s", request.output_path)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_HIGGSFIELD,
            model=self._model,
            duration_seconds=int(request.duration_seconds),
            seed=request.seed,
            task_id=request_id,
            generate_audio=bool(request.generate_audio),
        )

    # ── request building ────────────────────────────────────────────────

    def _build_request(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        """选端点并构造不含素材的请求体；素材字段由 ``_upload_media`` 在客户端就位后补上。"""
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        if request.start_image is not None and not Path(request.start_image).exists():
            raise FileNotFoundError(f"start_image 文件不存在: {request.start_image}")
        if request.end_image is not None and not Path(request.end_image).exists():
            raise FileNotFoundError(f"end_image 文件不存在: {request.end_image}")
        if request.end_image is not None and request.start_image is None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

        refs = _reference_images(request)
        caps = self.video_capabilities
        if len(refs) > caps.max_reference_images:
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                model=self._model,
                limit=caps.max_reference_images,
                count=len(refs),
            )

        prompt = request.prompt or ""
        if caps.max_prompt_chars is not None and len(prompt) > caps.max_prompt_chars:
            raise VideoCapabilityError(
                "video_prompt_too_long",
                provider=self.name,
                model=self._model,
                limit=caps.max_prompt_chars,
                count=len(prompt),
            )

        body: dict = {"prompt": prompt, "duration": int(request.duration_seconds)}
        model = self._known_model
        if model == MODEL_KLING_V3:
            tier = self._kling_tier(request.resolution)
            if refs:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded", model=self._model, limit=0, count=len(refs)
                )
            endpoint = (
                f"/kling-video/v3.0/{tier}/image-to-video"
                if request.start_image is not None
                else f"/kling-video/v3.0/{tier}/text-to-video"
            )
            body["sound"] = "on" if request.generate_audio else "off"
            if request.start_image is None:
                body["aspect_ratio"] = self._aspect_ratio(request, _KLING_ASPECT_RATIOS)
        elif model == MODEL_KLING_O3:
            if not refs and request.start_image is None:
                raise VideoCapabilityError("video_capability_missing_t2v", provider=self.name, model=self._model)
            endpoint = "/kling-video/o3/image-reference" if refs else "/kling-video/o3/first-last-frame"
            body["mode"] = self._kling_tier(request.resolution)
            body["sound"] = "on" if request.generate_audio else "off"
            body["aspect_ratio"] = self._aspect_ratio(request, _KLING_ASPECT_RATIOS)
        else:
            # Seedance 2.5 与认不得的模型：按 Seedance 形态分派（Higgsfield 多数第三方视频模型同此命名）。
            prefix = f"/{self._model.strip('/')}"
            if refs:
                endpoint = f"{prefix}/reference-to-video"
            elif request.start_image is not None:
                endpoint = f"{prefix}/image-to-video"
            else:
                endpoint = f"{prefix}/text-to-video"
            body["generate_audio"] = bool(request.generate_audio)
            # resolution 以模型目录条目为准，原样透传；越界档位由上游拒绝（docs/adr/0104）
            if request.resolution:
                body["resolution"] = request.resolution
            if request.start_image is None:
                body["aspect_ratio"] = self._aspect_ratio(request, _SEEDANCE_ASPECT_RATIOS if model else None)
        return endpoint, body

    def _kling_tier(self, resolution: str | None) -> str:
        tier = _KLING_TIER_BY_RESOLUTION.get((resolution or _DEFAULT_RESOLUTION).lower())
        if tier is None:
            raise VideoCapabilityError(
                "video_resolution_not_supported",
                model=self._model,
                resolution=resolution,
                supported=", ".join(_KLING_TIER_BY_RESOLUTION),
            )
        return tier

    def _aspect_ratio(self, request: VideoGenerationRequest, allowed: frozenset[str] | None) -> str:
        """端点接受的比例枚举是封闭集合；不在其中就付费前拒绝，而不是悄悄换成供应商默认值。"""
        if allowed is not None and request.aspect_ratio not in allowed:
            raise VideoCapabilityError(
                "video_aspect_ratio_not_supported",
                model=self._model,
                aspect_ratio=request.aspect_ratio,
                supported=", ".join(sorted(allowed)),
            )
        return request.aspect_ratio

    async def _upload_media(self, client: httpx.AsyncClient, endpoint: str, request: VideoGenerationRequest) -> dict:
        """按端点形态把本地素材换成公开 URL 字段。"""
        media: dict = {}
        refs = _reference_images(request)
        if refs:
            media["image_urls"] = [await upload_image(client, p) for p in refs]
        if request.start_image is not None:
            first = await upload_image(client, Path(request.start_image))
            last = await upload_image(client, Path(request.end_image)) if request.end_image is not None else None
            if endpoint.startswith("/kling-video/o3/"):
                media["first_frame_url"] = first
                if last:
                    media["last_frame_url"] = last
            elif endpoint.startswith("/kling-video/"):
                media["image_url"] = first
                if last:
                    media["last_image_url"] = last
            else:
                media["image_url"] = first
                if last:
                    media["end_image_url"] = last
        return media

    # ── HTTP wrappers ───────────────────────────────────────────────────

    @with_retry_async(retry_if=should_retry_submit)
    async def _create_request(
        self, client, endpoint: str, body: dict, idempotency_key: str, request: VideoGenerationRequest
    ) -> dict:
        logger.info(
            "调用 Higgsfield 视频 API endpoint=%s kwargs=%s",
            endpoint,
            format_kwargs_for_log(safe_body_for_log(body)),
        )
        # 提交是「创建 + 计费」调用：submit_post 把歧义态转终态失败、不重试；仅连接建立失败重试，
        # 重试带同一把 Idempotency-Key，供应商据此不重复建任务。
        resp = await submit_post(
            lambda: client.post(endpoint, json=body, headers={"Idempotency-Key": idempotency_key}),
            provider=PROVIDER_HIGGSFIELD,
            request=request,
        )
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Higgsfield 提交响应不是对象: {type(data).__name__}")
        return data


def _reference_images(request: VideoGenerationRequest) -> list[Path]:
    return [Path(p) for p in (request.reference_images or []) if p]
