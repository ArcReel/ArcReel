"""DashScopeVideoBackend — 阿里百炼 HappyHorse / 万相视频生成后端（异步两步式）。

走原生 video-generation/video-synthesis 异步端点：submit 取 task_id → 轮询
GET /tasks/{id} 至 SUCCEEDED → 下载 video_url。覆盖 happyhorse-1.0 / happyhorse-1.1
与 wan2.7 系列的 t2v / i2v / r2v，以及单模型通吃三条路径的 wan3.0。

schema 的确权程度按型号分两档：happyhorse 与 wan2.7 依据 docs/api-docs/providers/dashscope.md 所列一手
官方文档核实；wan3.0 无可用官方页面，其请求形态按 2.7 形状类推，出处与类推范围见 _WAN3_*
常量处的说明。

注：t2v/i2v 起始帧用 media[{type:"first_frame"}]（first_frame type 在 r2v media
枚举中确权）；尾帧 / 续写字段在一手 docs 未确权，故 happyhorse 与 wan2.7 的 i2v 仅
声明首帧能力，不臆造。wan3.0 的尾帧是上述类推档的一部分，不受这条约束。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from types import EllipsisType
from typing import ClassVar

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    ResumeExpiredError,
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
    poll_with_retry,
    read_video_images,
    recording_poll,
    resume_expiry_gate,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)
from lib.backends.dashscope_model_names import (
    WAN3_MODEL_KEY,
    WAN27_PROFILE_KEYS,
    classify_wan_model,
    find_known_profile_key,
)
from lib.backends.dashscope_shared import (
    dashscope_failure_reason,
    dashscope_headers,
    dashscope_native_base_url,
    extract_billing_duration,
    extract_task_id,
    extract_video_url,
    is_dashscope_expired,
    is_dashscope_terminal,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.backends.data_uri import file_to_data_uri
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.input_limits import excess_image_names
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities, match_by_names
from lib.backends.video_capabilities import (
    VideoEndpointCapabilities,
    audio_protocol,
    reference_image_limit,
)
from lib.billing.metering import Usage
from lib.capabilities.kernel import Endpoint
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    with_retry_async,
)

logger = logging.getLogger(__name__)


_REFERENCE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg"}


def _read_reference_audio_or_none(path: Path) -> str | None:
    """参考音频 → base64 data URI；文件缺失或 IO 失败返回 None（格式另由调用方先行拒绝）。"""
    mime = _REFERENCE_AUDIO_MIME_TYPES[path.suffix.lower()]
    if not path.is_file():
        return None
    try:
        return file_to_data_uri(path, mime)
    except OSError as exc:
        logger.warning("DashScope 参考音频读取失败: %s (%s)", path, exc)
        return None


DEFAULT_MODEL = "happyhorse-1.1-i2v"

_VIDEO_ENDPOINT = "/services/aigc/video-generation/video-synthesis"


_WAN27_MAX_PROMPT_CHARS = 5000

_WAN3_MAX_PROMPT_CHARS = 20000


@dataclass(frozen=True)
class DashScopeVideoEndpoint:
    """同一异步 API 下不同的音轨、参考图与音色挂载契约。"""

    audio_switch: bool = False
    reference_required: bool = False
    reference_audio_per_image: bool = False
    wan3_host: bool = False

    @property
    def audio_track(self) -> VideoAudioMode:
        """音轨形态由请求是否下发 ``parameters.audio`` 决定：不下发时上游按默认出有声成片。

        百炼视频请求没有对所有型号通用的音轨开关，官方出处见 ``docs/api-docs/endpoints/dashscope-video.md``。
        能力声明、请求与计量都读这一位，不会各说一套。
        """
        return VideoAudioMode.CONTROLLABLE if self.audio_switch else VideoAudioMode.ALWAYS_ON


_MODEL_PROFILES = {
    **dict.fromkeys(WAN27_PROFILE_KEYS, _WAN27_MAX_PROMPT_CHARS),
    WAN3_MODEL_KEY: _WAN3_MAX_PROMPT_CHARS,
}


def _sent_resolution(request: VideoGenerationRequest) -> str:
    """百炼的分辨率参数使用大写，未指定时下发 720P。"""
    return (request.resolution or "720p").upper()


def _match_profile_key(model: str) -> str | None:
    """按名字解析请求形态档的 key：先精确命中，再容忍代理中转的前后缀装饰；认不得时为 ``None``。

    infer_endpoint 用 classify_wan_model 的同一结论路由到 百炼异步视频端点，故此处也须
    子串容忍，否则 "proxy/happyhorse-1.0-r2v" / "wan2.7-r2v-0715" 这类装饰名会退回通道声明、丢掉 r2v 的请求形态，_build_media 据此构造出错误 payload。
    仅带系列名而无变体后缀（如裸 "happyhorse"）无法判别 t2v/i2v/r2v，按设计认不得。
    """
    normalized = (model or "").strip().lower()
    if not normalized:
        return None
    if normalized in _MODEL_PROFILES:
        return normalized
    classification = classify_wan_model(normalized)
    if not classification.has_known_modality:
        return None
    if classification.profile_key is not None:
        normalized = classification.profile_key
    return find_known_profile_key(normalized, _MODEL_PROFILES)


def _profile_key_for_model(model: str | None, entry: CatalogEntry | None = None) -> str | None:
    """model 的请求形态档 key：名字按 name_family 的规则逐个解析（``entry`` 是对应的条目），都认不得时为 ``None``。"""
    return match_by_names(model or "", _match_profile_key, entry=entry)


class DashScopeVideoBackend(VideoEndpointCapabilities, EntryBoundCapabilities, ProviderJobIdPersistenceMixin):
    """阿里百炼视频后端（异步 video-synthesis 端点）；本类即通用端点 ``dashscope-video``，不下发音轨开关、固定有声。"""

    endpoint: ClassVar[DashScopeVideoEndpoint] = DashScopeVideoEndpoint()

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        wan3_base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        self._wan3_base_url = (wan3_base_url or "").strip().rstrip("/") or None
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def describe_video_endpoint(cls, model: str, entry: CatalogEntry | EllipsisType | None = ...) -> Endpoint:
        if entry is None:
            profile = _match_profile_key(model)
        else:
            profile = _profile_key_for_model(model, None if isinstance(entry, EllipsisType) else entry)
        return Endpoint(
            "video",
            frozenset({"text_to_video", "first_frame", "last_frame", "reference_images", "reference_audio"}),
            audio=audio_protocol(cls.endpoint.audio_track),
            defaults={
                "reference_audio_per_image": cls.endpoint.reference_audio_per_image,
                "max_prompt_chars": _MODEL_PROFILES.get(profile or ""),
            },
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._binding)

    @property
    def _request_base_url(self) -> str:
        """本型号请求实际走的域名。

        提交与轮询共用同一个：任务 id 只在创建它的 endpoint 上可查，两者分家会让 wan3.0
        任务提交成功后轮询到 404。
        """
        if self._wan3_base_url and self.endpoint.wan3_host:
            return self._wan3_base_url
        return self._base_url

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        payload = await asyncio.to_thread(self._build_payload, request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._create_task(client, payload, request)
            logger.info("DashScope 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            await self._persist_provider_job_id(
                request, task_id, provider=PROVIDER_DASHSCOPE, endpoint=self._request_base_url
            )
            return await self._poll_and_build(client, task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 DashScope task：仅 poll + 下载（ADR 0007）。"""
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

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

        分辨率取实际下发的档位（未指定时下发 720P）；结算的输出秒数取 ``usage.duration``。音轨取请求的计费档。参考图张数按执行闸门的生效上限计量。
        """
        caps = request.video_capabilities or cls.video_capabilities_for_model(model, entry)
        image_limit = reference_image_limit(caps)
        provided = [r for r in (request.reference_images or []) if r] if image_limit.accepts_input else []
        bound = image_limit.clamp_bound()
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=_sent_resolution(request),
            audio=request.generate_audio if cls.endpoint.audio_switch else True,
            reference_images=len(provided) if bound is None else min(len(provided), bound),
        )

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        media = self._build_media(request)
        input_block: dict = {"prompt": request.prompt}
        if media:
            input_block["media"] = media

        parameters: dict = {
            "resolution": _sent_resolution(request),
            "duration": request.duration_seconds,
            "watermark": False,
        }
        has_first_frame = any(m.get("type") == "first_frame" for m in media)
        if request.aspect_ratio and not has_first_frame:
            parameters["ratio"] = request.aspect_ratio
        if request.seed is not None:
            parameters["seed"] = request.seed
        if self.endpoint.audio_switch:
            parameters["audio"] = request.generate_audio
        elif (
            not request.generate_audio
            and (request.video_capabilities or self.video_capabilities)
            .audio_track_for_route("r2v" if request.reference_images else "i2v")
            .has_audio_track
        ):
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._model)

        return {
            "model": self._model,
            "input": input_block,
            "parameters": parameters,
        }

    def _build_media(self, request: VideoGenerationRequest) -> list[dict]:
        caps = request.video_capabilities or self.video_capabilities
        media: list[dict] = []
        if request.start_image and not caps.first_frame:
            raise VideoCapabilityError("video_first_frame_unsupported", provider=self.name, model=self._model)
        if request.end_image and not caps.last_frame:
            raise VideoCapabilityError("video_last_frame_unsupported", provider=self.name, model=self._model)
        provided = request.reference_images or []
        reference_items: list[dict] = []
        if not provided and self.endpoint.reference_required:
            raise VideoCapabilityError("video_reference_images_required", model=self._model)
        image_limit = reference_image_limit(caps)
        if not image_limit.accepts_input and provided:
            raise VideoCapabilityError("video_reference_images_unsupported", model=self._model)
        bound = image_limit.clamp_bound()
        if bound is not None and len(provided) > bound:
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                names=excess_image_names(bound, len(provided), provided),
                model=self.model,
                limit=bound,
                count=len(provided),
            )
        images = read_video_images(request, self.model)
        for role, path in (("first_frame", request.start_image), ("last_frame", request.end_image)):
            if path is not None:
                media.append({"type": role, "url": images[Path(path)].data_uri()})
        reference_items = [{"type": "reference_image", "url": images[Path(p)].data_uri()} for p in provided]
        standalone_audio_items: list[dict] = []
        if self.endpoint.reference_audio_per_image:
            self._attach_reference_voices(reference_items, request)
        else:
            standalone_audio_items = self._build_reference_audio_items(request)
        media.extend(reference_items)
        media.extend(standalone_audio_items)
        return media

    def _build_reference_audio_items(self, request: VideoGenerationRequest) -> list[dict]:
        """把参考音频转成 media 数组里的独立 ``reference_audio`` 条目（wan3.0 形态）。

        与逐段挂载形态的差别只在落点：这里每段音频自成一个 media 条目，不占参考素材槽位，
        因而没有 slots 对齐一说。解码与 fail-loud 口径见 :meth:`_decode_reference_audio_uris`。
        """
        return [{"type": "reference_audio", "url": uri} for uri in self._decode_reference_audio_uris(request)]

    def _decode_reference_audio_uris(self, request: VideoGenerationRequest) -> list[str]:
        """把请求里的参考音频逐段读成 data URI，顺序与入参一一对应。

        两种挂载形态（独立 media 条目与逐段 ``reference_voice``）共用本方法，解码口径因而只有
        一处：能力档不支持音频、扩展名不在受支持格式内、任一段读不出来，都在此 fail-loud。
        不跳过任何一段——顺序即 prompt 中「音频N」的指认契约，静默少发一段会让该角色的音色
        声明无声失效且照常计费。段数与总时长上限由 ``gate_video_request`` 在付费前校验。
        """
        audio_files = list(request.reference_audio_files or [])
        if not audio_files:
            return []
        if (request.video_capabilities or self.video_capabilities).reference_audio_mode == ReferenceAudioMode.NONE:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        uris: list[str] = []
        unreadable: list[str] = []
        for audio in audio_files:
            path = Path(audio)
            if path.suffix.lower() not in _REFERENCE_AUDIO_MIME_TYPES:
                raise VideoCapabilityError(
                    "video_reference_audio_format_unsupported",
                    name=path.name,
                    supported=", ".join(sorted(_REFERENCE_AUDIO_MIME_TYPES)),
                )
            uri = _read_reference_audio_or_none(path)
            if uri is None:
                unreadable.append(path.name)
            else:
                uris.append(uri)
        if unreadable:
            raise VideoCapabilityError(
                "video_reference_audio_unreadable", model=self._model, names=", ".join(unreadable)
            )
        return uris

    def _attach_reference_voices(self, reference_items: list[dict], request: VideoGenerationRequest) -> None:
        """把参考音频逐段挂到参考素材项的 ``reference_voice`` 字段上（就地修改）。

        对齐优先用 ``request.reference_audio_targets``（第 i 段音频对应 ``reference_items``
        的哪个下标）——参考音频的顺序是台词 speaker 首现顺序，参考图的顺序是 mention 首现
        顺序，两者独立派生，编排层（``reference_video`` 渲染管线）已算出「谁的声音配哪张图」
        的映射，此处不得自行按位置重新猜测。``reference_audio_targets`` 为 ``None`` 时回退
        按位置对齐（第 N 段音频挂第 N 个参考素材）——两侧同序本身不是契约，回退仅服务未经
        编排层填充的调用方（如手写测试）。

        音频段数多于可挂载的参考素材时硬失败而非丢弃多余段：丢弃会让某个角色的音色声明无声
        失效，用户直到成片才发现该角色声音仍是随机的，且已照常扣费。``reference_audio_targets``
        携带越界下标同样按此硬失败——那意味着编排层算出的映射与实际随请求发出的参考图对不上，
        必须暴露而非静默吞掉。
        """
        audio_files = list(request.reference_audio_files or [])
        if not audio_files:
            return
        if (request.video_capabilities or self.video_capabilities).reference_audio_mode == ReferenceAudioMode.NONE:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)

        targets = request.reference_audio_targets
        if targets is not None:
            valid = (
                len(targets) == len(audio_files)
                and len(set(targets)) == len(targets)
                and all(0 <= t < len(reference_items) for t in targets)
            )
        else:
            valid = len(audio_files) <= len(reference_items)
        if not valid:
            raise VideoCapabilityError(
                "video_reference_audio_slots_insufficient",
                provider=self.name,
                model=self._model,
                slots=len(reference_items),
                count=len(audio_files),
            )
        uris = self._decode_reference_audio_uris(request)
        if targets is not None:
            for idx, uri in zip(targets, uris, strict=True):
                reference_items[idx]["reference_voice"] = uri
        else:
            for item, uri in zip(reference_items, uris, strict=False):
                item["reference_voice"] = uri

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(
        self, client: httpx.AsyncClient, payload: dict, request: VideoGenerationRequest | None = None
    ) -> str:
        resp = await submit_post(
            lambda: client.post(
                f"{self._request_base_url}{_VIDEO_ENDPOINT}",
                json=payload,
                headers=dashscope_headers(self._api_key, async_mode=True),
            ),
            provider=PROVIDER_DASHSCOPE,
            request=request,
        )
        return extract_task_id(resp.json())

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str, base_url: str) -> dict:
        resp = await client.get(
            f"{base_url}/tasks/{task_id}",
            headers=dashscope_headers(self._api_key),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        base_url = request.submitted_base_url or self._request_base_url

        gated_poll = resume_expiry_gate(
            recording_poll(lambda: self._poll_once(client, task_id, base_url), request),
            resume_job_id=task_id if is_resume else None,
            provider=PROVIDER_DASHSCOPE,
        )

        final = await poll_with_retry(
            poll_fn=gated_poll,
            is_done=is_dashscope_terminal,
            is_failed=dashscope_failure_reason,
            max_wait=request.poll_timeout_seconds,
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

        billing_duration = extract_billing_duration(final)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            duration_seconds=billing_duration if billing_duration is not None else request.duration_seconds,
            video_uri=video_url,
            task_id=task_id,
            generate_audio=(request.video_capabilities or self.video_capabilities)
            .audio_track_for_route("r2v" if request.reference_images else "i2v")
            .output_audio(request.generate_audio),
        )

    @staticmethod
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        await download_resumable_video(video_url, output_path, label="DashScope")


class DashScopeAlwaysOnVideoBackend(DashScopeVideoBackend):
    """没有音轨开关且固定有声的异步视频请求（HappyHorse 与 wan2.7 的文生、图生）。

    请求契约与通用端点相同；端点名分开保留，供已知型号的预填与已记下端点的模型沿用。
    """

    endpoint = DashScopeVideoEndpoint()


class DashScopeReferenceVideoBackend(DashScopeVideoBackend):
    """参考生视频请求必须携带参考图。"""

    endpoint = DashScopeVideoEndpoint(reference_required=True)


class DashScopeReferenceVoiceVideoBackend(DashScopeVideoBackend):
    """参考生视频的音色逐项挂在参考图上。"""

    endpoint = DashScopeVideoEndpoint(reference_required=True, reference_audio_per_image=True)


class DashScopeWan3VideoBackend(DashScopeVideoBackend):
    """Wan3：可切换音轨、独立音频条目与 MaaS 域名。"""

    endpoint = DashScopeVideoEndpoint(audio_switch=True, wan3_host=True)


DASHSCOPE_VIDEO_BACKENDS = {
    "dashscope-video": DashScopeVideoBackend,
    "dashscope-always-on-video": DashScopeAlwaysOnVideoBackend,
    "dashscope-reference-video": DashScopeReferenceVideoBackend,
    "dashscope-reference-voice-video": DashScopeReferenceVoiceVideoBackend,
    "dashscope-wan3-video": DashScopeWan3VideoBackend,
}
