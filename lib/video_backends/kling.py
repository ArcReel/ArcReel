"""KlingVideoBackend — 可灵 Kling 视频生成后端（JWT 直连 / Bearer 中转双模式，异步轮询）。

走可灵原生视频端点：submit ``POST /v1/videos/{text2video|image2video}`` 取 ``data.task_id`` →
轮询 ``GET /v1/videos/{subpath}/{task_id}`` 至 ``task_status=succeed`` 取
``task_result.videos[0].url`` → 下载本地。复用 base.py 的 submit/poll/download helpers，
自包含异步状态机、不依赖 DashScope async 机制。

双模式（对齐 ``GeminiVideoBackend`` 的 ``backend_type`` 先例）：
- ``auth_mode="jwt"``（内置 provider）：接 access_key + secret_key，走 ``KlingJWTManager``，
  每次 HTTP 调用前检查过期、距过期 <60s 按需重签——异步渲染可能超单 token 寿命。
- ``auth_mode="bearer"``（自定义 endpoint）：接静态 api_key + base_url，旁路 JWT 管理器。

各视频模型能力按 ``_KLING_VIDEO_CAPS`` 表驱动（官方一手核实）：
- ``kling-v2-5-turbo``：文/图生视频含首尾帧，无音频/参考（默认 model）。
- ``kling-v3`` / ``kling-v3-omni``：旗舰，首尾帧 + 4K（``mode="4k"``）；v3-omni 多图主体 R2V。
- ``kling-v2-6``：pro 档支持视频内人声（``enable_audio``）。
- ``kling-video-o1``：图生 + 多图主体 R2V。
未登记 model（bearer 透传原生 model_name）回落保守默认能力。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from lib.kling_backend_base import KlingBackendBase
from lib.kling_shared import (
    extract_kling_video_url,
    image_to_base64,
)
from lib.providers import PROVIDER_KLING
from lib.retry import (
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    VideoCapabilities,
    VideoCapability,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    should_retry_download,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-v2-5-turbo"

_TEXT2VIDEO = "text2video"
_IMAGE2VIDEO = "image2video"
_MULTI_IMAGE2VIDEO = "multi-image2video"
_RESUMABLE_SUBPATHS = frozenset({_TEXT2VIDEO, _IMAGE2VIDEO, _MULTI_IMAGE2VIDEO})

# 多图主体（R2V）参考图上限保守值；同时声明于 registry ModelInfo（编排层裁剪读它）与
# backend caps（生成时防御）。待 app.klingai.com 控制台核对，不硬编当既成事实。
_R2V_MAX_REFERENCE_IMAGES = 4


@dataclass(frozen=True)
class _KlingVideoModelCaps:
    """单个可灵视频模型的能力位（官方一手核实）。"""

    text_to_video: bool
    image_to_video: bool
    last_frame: bool
    reference_images: bool
    max_reference_images: int
    generate_audio: bool  # 能产出视频内人声；官方仅 v2-6（pro 档）标 ✅
    audio_param: bool  # 请求体是否带 enable_audio：v3 代默认有声需显式压制，旧档无此字段


# turbo / 未登记 model（bearer 透传原生 model_name）兜底：文/图生视频、首尾帧，无音频/参考。
_DEFAULT_VIDEO_CAPS = _KlingVideoModelCaps(
    text_to_video=True,
    image_to_video=True,
    last_frame=True,
    reference_images=False,
    max_reference_images=0,
    generate_audio=False,
    audio_param=False,
)

_KLING_VIDEO_CAPS: dict[str, _KlingVideoModelCaps] = {
    "kling-v2-5-turbo": _DEFAULT_VIDEO_CAPS,
    "kling-v3": _KlingVideoModelCaps(
        text_to_video=True,
        image_to_video=True,
        last_frame=True,
        reference_images=False,
        max_reference_images=0,
        generate_audio=False,
        audio_param=True,
    ),
    "kling-v3-omni": _KlingVideoModelCaps(
        text_to_video=True,
        image_to_video=True,
        last_frame=True,
        reference_images=True,
        max_reference_images=_R2V_MAX_REFERENCE_IMAGES,
        generate_audio=False,
        audio_param=True,
    ),
    "kling-v2-6": _KlingVideoModelCaps(
        text_to_video=True,
        image_to_video=True,
        last_frame=True,
        reference_images=False,
        max_reference_images=0,
        generate_audio=True,
        audio_param=True,
    ),
    "kling-video-o1": _KlingVideoModelCaps(
        text_to_video=False,
        image_to_video=True,
        last_frame=True,
        reference_images=True,
        max_reference_images=_R2V_MAX_REFERENCE_IMAGES,
        generate_audio=False,
        audio_param=False,
    ),
}


def _lookup_video_caps(model: str) -> _KlingVideoModelCaps:
    """按 model 取能力位：剥厂商前缀后 + 去首尾空白 + lower 归一化，再做【精确】命中 _KLING_VIDEO_CAPS。
    中转前缀分隔符仅认仓库既有约定 ``/``（``vendor/kling-v3-omni``）与 ``:``（``provider:kling-v3-omni``）
    ——把 ``:`` 统一成 ``/`` 后取最后一段。刻意不把 ``_``/``.`` 当分隔符：它们是 model 名合法字符
    （wan2. / image-01 / kling-v3-omni 都含），当分隔符会切坏真实 model 名。未登记 model（含未来版本
    kling-v4、归一化后仍不精确匹配的中转自定义 id）回落保守默认（首尾帧、无参考/音频）——绝不按子串猜
    未知 model 的能力上限：未知 model 的限额可能与已知档不同，误报参考图能力会在请求期触发 provider 400
    或计费漂移，宁可保守。"""
    key = model.replace(":", "/").rsplit("/", 1)[-1].strip().lower()
    return _KLING_VIDEO_CAPS.get(key, _DEFAULT_VIDEO_CAPS)


_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 60.0
_KLING_VIDEO_POLL_INTERVAL_SECONDS = 10.0


def _encode_job_id(subpath: str, task_id: str, *, generate_audio: bool) -> str:
    """把生成类型子路径 + 有声标志编进持久化 job_id（``subpath:task_id:audio``）。

    可灵查询端点按生成类型分路径（``GET /v1/videos/{text2video|image2video}/{id}``），
    且重启 resume 时请求已无 ``start_image`` 可推断子路径——必须把子路径随 task_id 一起
    持久化，否则 image2video 任务 resume 会误查 text2video 端点取不到任务。

    有声标志（0/1）同理随 task_id 持久化：resume 直接复用 submit 时算定的有声决策，
    不按 resume 时（config 默认/请求可能已漂移）重算，避免有声/无声计费漂移。
    """
    return f"{subpath}:{task_id}:{1 if generate_audio else 0}"


def _decode_job_id(job_id: str) -> tuple[str, str, bool | None]:
    """从持久化 job_id 复原 ``(子路径, task_id, 有声标志)``。

    新格式 ``subpath:task_id:audio``（3 段，audio 为 0/1）；旧格式 ``subpath:task_id``
    （2 段，有声标志未持久化，返回 None 由 caller 重算）；无已知前缀（异常/更旧数据）
    回落 text2video、整串作 task_id。
    """
    parts = job_id.split(":")
    if len(parts) == 3 and parts[0] in _RESUMABLE_SUBPATHS and parts[2] in ("0", "1"):
        return parts[0], parts[1], parts[2] == "1"
    prefix, sep, rest = job_id.partition(":")
    if sep and prefix in _RESUMABLE_SUBPATHS:
        return prefix, rest, None
    return _TEXT2VIDEO, job_id, None


class KlingVideoBackend(KlingBackendBase, ProviderJobIdPersistenceMixin):
    """可灵 Kling 视频后端（异步轮询，JWT / Bearer 双模式）。

    鉴权 / base_url 装配 / submit-poll 骨架由 ``KlingBackendBase`` 共享；``provider_job_id`` 持久化由
    ``ProviderJobIdPersistenceMixin`` 收口。本类只填视频侧差异：子路径派生、能力位查表、resume 与下载。
    """

    _media_label = "视频"

    def __init__(
        self,
        *,
        auth_mode: str = "jwt",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        super().__init__(
            auth_mode=auth_mode,
            access_key=access_key,
            secret_key=secret_key,
            api_key=api_key,
            model=model or DEFAULT_MODEL,
            base_url=base_url,
            http_timeout=http_timeout,
        )
        # 按 model 取能力位（归一化前缀/大小写后精确命中）；未登记 model（bearer 透传）回落保守默认。
        self._caps = _lookup_video_caps(self._model)

    @property
    def capabilities(self) -> set[VideoCapability]:
        caps: set[VideoCapability] = set()
        if self._caps.text_to_video:
            caps.add(VideoCapability.TEXT_TO_VIDEO)
        if self._caps.image_to_video:
            caps.add(VideoCapability.IMAGE_TO_VIDEO)
        if self._caps.generate_audio:
            caps.add(VideoCapability.GENERATE_AUDIO)
        return caps

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        # first_frame 恒真（各档均支持 i2v 首帧）；last_frame / reference_images / 上限按 model 从
        # _KLING_VIDEO_CAPS 读（_lookup_video_caps 归一化前缀/大小写后精确命中，未登记回落保守默认）。
        # max_reference_images 同时声明于 registry ModelInfo（编排层裁剪读它）与此处（生成时防御），取保守
        # 值、待 app.klingai.com 控制台核对。纯函数（不构造 client / 不需 api_key），供 custom endpoint
        # resolver 按 model_id 读上限复用。
        caps = _lookup_video_caps(model)
        return VideoCapabilities(
            first_frame=True,
            last_frame=caps.last_frame,
            reference_images=caps.reference_images,
            max_reference_images=caps.max_reference_images,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    # ── request building ────────────────────────────────────────────────

    def _resolve_mode(self, request: VideoGenerationRequest) -> str:
        """质量档 → mode：resolution=4k 独立成 ``4k`` 档（仅 v3/v3-omni 可达），否则 service_tier→std/pro。

        与 per_second_tiered 定价的档位派生一致（4k 优先于 std/pro），保证请求档与计费档同源。
        """
        if (request.resolution or "").lower() == "4k":
            return "4k"
        return "pro" if (request.service_tier or "").lower() == "pro" else "std"

    def _effective_audio(self, request: VideoGenerationRequest) -> bool:
        """实际是否产出视频内人声：请求要 + model 有 generate_audio 能力 + pro 档（官方仅 v2-6 pro ✅）。

        无能力的 model 恒 False——不被错配有声价（下游 pricing 取 ``result.generate_audio``）。
        """
        return bool(request.generate_audio and self._caps.generate_audio and self._resolve_mode(request) == "pro")

    @staticmethod
    def _valid_frames(images: list[Path] | None) -> list[Path]:
        """过滤出有效（非空）参考图路径；空 / None 归空列表。"""
        if not images:
            return []
        return [Path(img) for img in images if str(img)]

    def _build_payload(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        """返回 (子路径, 请求体)。

        子路径优先级：有 reference_images → multi-image2video（多图主体 R2V）；
        有 start_image → image2video（含可选尾帧）；都无 → text2video。
        """
        payload: dict = {
            "model_name": self._model,
            "prompt": request.prompt,
            "mode": self._resolve_mode(request),
            "duration": str(request.duration_seconds),
            "aspect_ratio": request.aspect_ratio,
        }

        reference_images = self._valid_frames(request.reference_images)
        if reference_images:
            # 生成时防御（fail-loud）：未声明多图主体能力的 model 不得升级到 R2V 子路径，
            # 超上限的参考图数同样拦截——否则会把必然报错的请求发出去且照常计费。
            if not self._caps.reference_images:
                raise VideoCapabilityError("video_reference_images_unsupported", model=self._model)
            if len(reference_images) > self._caps.max_reference_images:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    count=len(reference_images),
                    limit=self._caps.max_reference_images,
                )
            # 多图主体：image_list 为 [{"image": <base64>}]（可灵原生 schema），无单首帧概念。
            payload["image_list"] = [{"image": self._encode_frame(p)} for p in reference_images]
            return _MULTI_IMAGE2VIDEO, payload

        start_image = request.start_image
        if not (isinstance(start_image, (str, Path)) and str(start_image)):
            # 无首帧/无参考 = 文生视频意图；不支持 t2v 的 model（如 kling-video-o1）即拒绝。
            if not self._caps.text_to_video:
                raise VideoCapabilityError("video_capability_missing_t2v", provider=self.name, model=self._model)
            subpath = _TEXT2VIDEO
        else:
            payload["image"] = self._encode_frame(Path(start_image))
            end_image = request.end_image
            if isinstance(end_image, (str, Path)) and str(end_image):
                payload["image_tail"] = self._encode_frame(Path(end_image))
            subpath = _IMAGE2VIDEO

        # enable_audio 仅 text2video / image2video 子路径携带（multi-image2video 原生 schema 不含）；
        # v3 代默认有声，无能力 model 在此显式压制为 False，有能力的 v2-6（pro）按需开启。
        if self._caps.audio_param:
            payload["enable_audio"] = self._effective_audio(request)
        return subpath, payload

    def _encode_frame(self, path: Path) -> str:
        # fail-loud：声明了帧图却缺失/不可读即中止，不静默退化（会产出错误结果且照常计费）。
        if not path.is_file():
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name)
        try:
            return image_to_base64(path)
        except OSError as exc:
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name) from exc

    @staticmethod
    def _safe_log_view(subpath: str, payload: dict) -> dict:
        """预脱敏标量视图，直接喂 logger（避开 format_kwargs_for_log sink）。

        base64 帧图 / prompt 一律不展开：仅记是否存在 + prompt 长度。
        """
        prompt = payload.get("prompt")
        image_list = payload.get("image_list")
        return {
            "endpoint": subpath,
            "model_name": payload.get("model_name"),
            "mode": payload.get("mode"),
            "duration": payload.get("duration"),
            "aspect_ratio": payload.get("aspect_ratio"),
            "enable_audio": bool(payload.get("enable_audio")),
            "has_image": "image" in payload,
            "has_image_tail": "image_tail" in payload,
            "reference_count": len(image_list) if isinstance(image_list, list) else 0,
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    # ── generate / resume ───────────────────────────────────────────────

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        subpath, payload = self._build_payload(request)
        generate_audio = self._effective_audio(request)
        logger.info("调用 Kling 视频 API payload=%s", self._safe_log_view(subpath, payload))
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._submit_task(client, f"videos/{subpath}", payload)
            logger.info("Kling 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            # 持久化「子路径:task_id:有声标志」而非裸 task_id：resume 据此复原查询端点
            # 与 submit 时的有声决策（见 _encode_job_id）。
            await self._persist_provider_job_id(
                request,
                _encode_job_id(subpath, task_id, generate_audio=generate_audio),
                provider=PROVIDER_KLING,
            )
            return await self._poll_and_build(client, subpath, task_id, request, generate_audio=generate_audio)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Kling task：仅轮询 + 取 url + 下载，不重新提交（ADR 0007）。

        查询子路径从持久化 job_id 复原（submit 时编入）——可灵查询端点按生成类型分路径，
        而 resume 请求已无 ``start_image`` 可推断，故不能再从 request 取（见 _encode_job_id）。

        有声标志同样优先取持久化值（submit 时算定）：直连有声/无声计费，避免按 resume 时
        可能已漂移的 config 默认/请求重算。旧 job_id 未持久化时（None）回落重算。
        """
        subpath, task_id, persisted_audio = _decode_job_id(job_id)
        generate_audio = persisted_audio if persisted_audio is not None else self._effective_audio(request)
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, subpath, task_id, request, generate_audio=generate_audio)

    # ── HTTP poll / download ────────────────────────────────────────────

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        subpath: str,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        generate_audio: bool,
    ) -> VideoGenerationResult:
        final = await self._poll_until_terminal(
            lambda: self._poll_query(client, f"videos/{subpath}/{task_id}"),
            poll_interval=_KLING_VIDEO_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
        )

        download_url = extract_kling_video_url(final)
        await self._download_with_retry(download_url, request.output_path)
        logger.info("Kling 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=download_url,
            task_id=task_id,
            # audio 门控后的实际有声标志（下游 finish_call 取它定有声/无声价）。
            generate_audio=generate_audio,
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
